from __future__ import annotations

import copy
import io
import json
import zipfile
from typing import Any

from fastapi.testclient import TestClient

from tests.test_adaptation_api import configure_vault_and_model, install_stub
from tests.test_bibles_api import approve_complete_bibles, generate_bibles
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_pages_api import prepare_page


def test_continuity_ledger_versions_sequential_chapters_and_reports_impact(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapters = prepare_two_approved_chapters(client, session_headers)
    endpoint = f"/api/v1/projects/{project_id}/continuity"

    denied = client.post(
        f"{endpoint}/draft", json={"chapter_id": chapters[0]["chapter_id"]}
    )
    assert denied.status_code == 401
    drafted = client.post(
        f"{endpoint}/draft",
        headers=session_headers,
        json={"chapter_id": chapters[0]["chapter_id"]},
    )
    assert drafted.status_code == 201, drafted.text
    first = drafted.json()
    assert first["version"] == 1
    assert first["approval_status"] == "draft"
    assert first["through_chapter_ordinal"] == 1
    assert first["external_requests_started"] == 0
    assert {entry["kind"] for entry in first["document"]["entries"]} == {
        "character",
        "outfit",
        "location",
        "plot",
    }

    approved = client.post(
        f"{endpoint}/{first['continuity_version_id']}/approve",
        headers=session_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "approved"

    edited_document = copy.deepcopy(approved.json()["document"])
    outfit = next(entry for entry in edited_document["entries"] if entry["kind"] == "outfit")
    stable_entry_id = outfit["entry_id"]
    outfit["attributes"]["items"] = "深色风衣、红色围巾"
    impact = client.post(
        f"{endpoint}/{approved.json()['continuity_version_id']}/impact",
        headers=session_headers,
        json={"document": edited_document},
    )
    assert impact.status_code == 200, impact.text
    impact_payload = impact.json()
    assert impact_payload["requires_future_review"] is True
    assert [item["ordinal"] for item in impact_payload["affected_chapters"]] == [2]
    assert len(impact_payload["affected_panel_ids"]) == 1
    assert impact_payload["external_requests_started"] == 0

    revised = client.post(
        f"{endpoint}/{approved.json()['continuity_version_id']}/revisions",
        headers=session_headers,
        json={"document": edited_document},
    )
    assert revised.status_code == 201, revised.text
    second = revised.json()
    assert second["version"] == 2
    assert second["approval_status"] == "draft"
    assert second["impact"]["affected_chapters"][0]["ordinal"] == 2

    blocked = client.post(
        f"{endpoint}/draft",
        headers=session_headers,
        json={"chapter_id": chapters[1]["chapter_id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CONTINUITY_APPROVAL_REQUIRED"
    client.post(
        f"{endpoint}/{second['continuity_version_id']}/approve",
        headers=session_headers,
    )
    third_response = client.post(
        f"{endpoint}/draft",
        headers=session_headers,
        json={"chapter_id": chapters[1]["chapter_id"]},
    )
    assert third_response.status_code == 201, third_response.text
    third = third_response.json()
    assert third["version"] == 3
    assert third["through_chapter_ordinal"] == 2
    carried_outfit = next(
        entry for entry in third["document"]["entries"] if entry["kind"] == "outfit"
    )
    assert carried_outfit["entry_id"] == stable_entry_id
    assert carried_outfit["attributes"]["items"] == "浅色衬衫、深色长裤"
    assert carried_outfit["source_chapter_ids"] == [
        chapters[0]["chapter_id"],
        chapters[1]["chapter_id"],
    ]
    history = client.get(f"{endpoint}/versions").json()
    assert [item["version"] for item in history] == [3, 2, 1]
    assert history[1]["approval_status"] == "approved"


def test_continuity_approval_fails_when_approved_source_changes(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapters = prepare_two_approved_chapters(client, session_headers)
    endpoint = f"/api/v1/projects/{project_id}/continuity"
    ledger = client.post(
        f"{endpoint}/draft",
        headers=session_headers,
        json={"chapter_id": chapters[0]["chapter_id"]},
    ).json()
    bundle = client.get(
        f"/api/v1/projects/{project_id}/bibles",
        params={"chapter_id": chapters[0]["chapter_id"]},
    ).json()
    changed = copy.deepcopy(bundle["character_bible"]["document"])
    changed["characters"][0]["hair"] = "齐耳短发"
    revised = client.post(
        f"/api/v1/projects/{project_id}/bibles/characters/"
        f"{bundle['character_bible']['version_id']}/revisions",
        headers=session_headers,
        json={"document": changed},
    )
    assert revised.status_code == 201

    stale = client.post(
        f"{endpoint}/{ledger['continuity_version_id']}/approve",
        headers=session_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONTINUITY_SOURCE_STALE"


def test_continuity_is_included_in_package_and_restored_with_remapped_ids(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, _page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    chapter_id = prepared["chapter"]["chapter_id"]
    endpoint = f"/api/v1/projects/{project_id}/continuity"
    ledger = client.post(
        f"{endpoint}/draft",
        headers=session_headers,
        json={"chapter_id": chapter_id},
    ).json()
    approved = client.post(
        f"{endpoint}/{ledger['continuity_version_id']}/approve",
        headers=session_headers,
    ).json()
    plan = export_preflight(client, session_headers, project_id, chapter_id)
    exported = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    ).json()
    package_file = next(
        item for item in exported["files"] if item["kind"] == "engineering_package"
    )
    package = download_file(client, session_headers, project_id, exported, package_file)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        records = json.loads(archive.read("records.json"))
    assert records["schema_version"] == "1.1"
    assert len(records["tables"]["continuity_ledger_versions"]) == 1

    preflight = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={"file": ("continuity.manga-maker.zip", package, "application/zip")},
    ).json()
    restored = client.post(
        f"/api/v1/imports/{preflight['import_preflight_id']}/restore",
        headers=session_headers,
        json={"confirmed": True},
    )
    assert restored.status_code == 200, restored.text
    restored_project_id = restored.json()["project_id"]
    restored_ledger = client.get(
        f"/api/v1/projects/{restored_project_id}/continuity"
    ).json()
    assert restored_ledger["continuity_ledger_id"] != approved["continuity_ledger_id"]
    assert restored_ledger["document_sha256"] != ""
    assert restored_ledger["approval_status"] == "approved"
    assert provider.generation_calls == 1


def prepare_two_approved_chapters(
    client: TestClient, headers: dict[str, str]
) -> tuple[str, list[dict[str, Any]]]:
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "连续性测试"}
    ).json()
    project_id = project["project_id"]
    source_text = (
        "第一章 雨夜\n林夏推开旧屋的门。\n"
        "第二章 清晨\n林夏戴着围巾回到旧屋房间。\n"
    )
    preflight = client.post(
        f"/api/v1/projects/{project_id}/source/preflight",
        headers=headers,
        files={"file": ("owned-fixture.txt", source_text.encode(), "text/plain")},
    ).json()
    source = client.post(
        f"/api/v1/projects/{project_id}/source/confirm",
        headers=headers,
        json={"preflight_id": preflight["preflight_id"], "encoding": "utf-8"},
    ).json()
    chapters = source["chapters"]
    assert len(chapters) == 2
    configure_vault_and_model(client, headers, project_id)
    install_stub(client)
    for chapter in chapters:
        client.post(
            f"/api/v1/projects/{project_id}/source/chapters/"
            f"{chapter['chapter_id']}/story-beats/draft",
            headers=headers,
        )
        storyboard = client.post(
            f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
            headers=headers,
            json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
        ).json()
        approved = client.post(
            f"/api/v1/projects/{project_id}/adaptation/storyboards/"
            f"{storyboard['storyboard_version_id']}/approve",
            headers=headers,
        )
        assert approved.status_code == 200
        bundle = generate_bibles(
            client, headers, project_id, storyboard["storyboard_version_id"]
        )
        approve_complete_bibles(client, headers, project_id, bundle)
    return project_id, chapters
