from __future__ import annotations

import asyncio
import copy
import io
import json
import zipfile

from fastapi.testclient import TestClient
from PIL import Image

from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_generation_queue import transition
from tests.test_pages_api import prepare_page
from tests.test_revisions_api import create_revision_job, estimate_revision


def test_authorized_synthetic_chapter_mock_end_to_end(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    """One user-owned synthetic fixture traverses the complete P0 workflow."""
    prepared, provider, first_page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    chapter_id = prepared["chapter"]["chapter_id"]

    edited_document = copy.deepcopy(first_page["document"])
    edited_document["text_layers"][0]["text"] = "本地改字"
    edited = client.post(
        f"/api/v1/projects/{project_id}/pages/{first_page['page_id']}/versions",
        headers=session_headers,
        json={
            "expected_revision": first_page["page_revision"],
            "document": edited_document,
        },
    )
    assert edited.status_code == 201, edited.text
    edited_page = edited.json()
    assert provider.generation_calls == 1

    panel = edited_page["document"]["panels"][0]
    estimate = estimate_revision(
        client,
        session_headers,
        project_id,
        {
            "operation": "panel_reroll",
            "page_id": edited_page["page_id"],
            "panel_id": panel["panel_id"],
            "per_panel_cost_ceiling_anlas": 8,
        },
    )
    reroll_job = create_revision_job(client, session_headers, project_id, estimate).json()
    started = transition(client, session_headers, project_id, reroll_job, "start")
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))
    completed = client.app.state.generation_queue.get_job(project_id, started["job_id"])
    assert completed["status"] == "completed"
    assert provider.generation_calls == 2

    rerolled = client.get(
        f"/api/v1/projects/{project_id}/pages/{edited_page['page_id']}/current"
    ).json()
    rerolled_asset_id = rerolled["document"]["panels"][0]["asset_version_id"]
    original_asset_id = panel["asset_version_id"]
    restored_asset = client.post(
        f"/api/v1/projects/{project_id}/generation/assets/{original_asset_id}/activate",
        headers=session_headers,
        json={
            "panel_id": panel["panel_id"],
            "expected_current_asset_version_id": rerolled_asset_id,
        },
    )
    assert restored_asset.status_code == 200
    restored_page = client.post(
        f"/api/v1/projects/{project_id}/pages/{edited_page['page_id']}/versions/"
        f"{edited_page['page_version_id']}/activate",
        headers=session_headers,
        json={"expected_revision": rerolled["page_revision"]},
    )
    assert restored_page.status_code == 200
    assert provider.generation_calls == 2

    plan = export_preflight(client, session_headers, project_id, chapter_id)
    exported_response = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    )
    assert exported_response.status_code == 201, exported_response.text
    exported = exported_response.json()
    assert exported["secret_scan"]["matches"] == 0
    assert [item["kind"] for item in exported["files"]] == [
        "engineering_package",
        "png",
        "pdf",
        "cbz",
    ]
    downloads = {
        item["kind"]: download_file(client, session_headers, project_id, exported, item)
        for item in exported["files"]
    }
    with Image.open(io.BytesIO(downloads["png"])) as image:
        assert image.size == (2048, 3072)
    assert downloads["pdf"].startswith(b"%PDF-")
    with zipfile.ZipFile(io.BytesIO(downloads["cbz"])) as archive:
        assert archive.namelist() == ["ComicInfo.xml", "001.png"]
    with zipfile.ZipFile(io.BytesIO(downloads["engineering_package"])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["credentials_included"] is False
        assert b"unit-novelai-secret" not in downloads["engineering_package"]

    dry_run = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={
            "file": (
                "authorized-fixture.manga-maker.zip",
                downloads["engineering_package"],
                "application/zip",
            )
        },
    )
    assert dry_run.status_code == 201, dry_run.text
    assert dry_run.json()["writes_performed"] == 0
    recovery = client.post("/api/v1/system/recovery", headers=session_headers)
    assert recovery.status_code == 200
    assert recovery.json()["status"] == "healthy"
    assert recovery.json()["provider_requests_started"] == 0
    assert provider.generation_calls == 2
