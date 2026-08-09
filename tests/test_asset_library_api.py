from __future__ import annotations

import copy
import io
import json
import zipfile

from fastapi.testclient import TestClient

from backend.app.pages.models import PageDocument
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_pages_api import prepare_page


def test_reusable_asset_library_versions_metadata_and_archives_without_generation(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    asset_version_id = page["document"]["panels"][0]["asset_version_id"]
    endpoint = f"/api/v1/projects/{project_id}/asset-library"
    calls_before = provider.generation_calls

    denied = client.post(
        endpoint,
        json={
            "source_asset_version_id": asset_version_id,
            "kind": "location",
            "name": "雨夜旧宅",
            "tags": ["雨夜", "外景"],
            "notes": "可跨页复用",
        },
    )
    assert denied.status_code == 401
    created = client.post(
        endpoint,
        headers=session_headers,
        json={
            "source_asset_version_id": asset_version_id,
            "kind": "location",
            "name": "雨夜旧宅",
            "tags": ["雨夜", "外景", "雨夜"],
            "notes": "可跨页复用",
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["tags"] == ["雨夜", "外景"]
    assert item["external_requests_started"] == 0
    assert client.get(endpoint).json() == [item]

    content_url = f"{endpoint}/{item['library_item_id']}/content"
    assert client.get(content_url).status_code == 401
    library_content = client.get(content_url, headers=session_headers)
    source_content = client.get(
        f"/api/v1/projects/{project_id}/generation/assets/{asset_version_id}/content",
        headers=session_headers,
    )
    assert library_content.content == source_content.content

    updated = client.put(
        f"{endpoint}/{item['library_item_id']}",
        headers=session_headers,
        json={
            "kind": "location",
            "name": "旧宅门廊",
            "tags": ["夜景"],
            "notes": "固定雨夜构图",
            "expected_revision": item["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    item = updated.json()
    assert item["revision"] == 2

    cross_panel_document = copy.deepcopy(page["document"])
    cross_panel_document["panels"][0]["panel_id"] = "another-panel"
    for layer in cross_panel_document["text_layers"]:
        layer["panel_id"] = "another-panel"
    validated_paths = client.app.state.pages._validate_assets(
        project_id, PageDocument.model_validate(cross_panel_document)
    )
    assert asset_version_id in validated_paths

    archived = client.post(
        f"{endpoint}/{item['library_item_id']}/archive",
        headers=session_headers,
        json={"expected_revision": item["revision"]},
    )
    assert archived.status_code == 200
    item = archived.json()
    assert item["status"] == "archived"
    assert client.get(endpoint).json() == []
    assert client.get(f"{endpoint}?include_archived=true").json()[0]["status"] == "archived"

    restored = client.post(
        f"{endpoint}/{item['library_item_id']}/restore",
        headers=session_headers,
        json={"expected_revision": item["revision"]},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"

    preflight = export_preflight(
        client, session_headers, project_id, prepared["chapter"]["chapter_id"]
    )
    exported = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    ).json()
    package_file = next(
        candidate for candidate in exported["files"]
        if candidate["kind"] == "engineering_package"
    )
    package = download_file(
        client, session_headers, project_id, exported, package_file
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        records = json.loads(archive.read("records.json"))
    assert records["schema_version"] == "1.3"
    assert len(records["tables"]["asset_library_items"]) == 1

    dry_run = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={"file": ("library.manga-maker.zip", package, "application/zip")},
    ).json()
    restored_project = client.post(
        f"/api/v1/imports/{dry_run['import_preflight_id']}/restore",
        headers=session_headers,
        json={"confirmed": True},
    ).json()
    restored_items = client.get(
        f"/api/v1/projects/{restored_project['project_id']}/asset-library"
    ).json()
    assert len(restored_items) == 1
    assert restored_items[0]["library_item_id"] != item["library_item_id"]
    assert restored_items[0]["source_asset_version_id"] != asset_version_id
    assert provider.generation_calls == calls_before
