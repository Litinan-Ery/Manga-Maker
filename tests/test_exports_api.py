from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from typing import Any
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from PIL import Image

from backend.app.errors import ApplicationError
from backend.app.exports.service import ExportService
from tests.test_pages_api import prepare_page


def test_four_format_export_is_pinned_and_package_restores_with_remapped_ids(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    chapter_id = prepared["chapter"]["chapter_id"]
    calls_before = provider.generation_calls

    preflight = export_preflight(client, session_headers, project_id, chapter_id)
    assert preflight["page_count"] == 1
    assert preflight["pages"][0]["page_version_id"] == page["page_version_id"]
    assert preflight["external_requests_started"] == 0

    denied = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight, confirmed=False),
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "EXPORT_CONFIRMATION_REQUIRED"

    response = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    )
    assert response.status_code == 201, response.text
    exported = response.json()
    assert exported["status"] == "completed"
    assert [item["kind"] for item in exported["files"]] == [
        "engineering_package",
        "png",
        "pdf",
        "cbz",
    ]
    assert provider.generation_calls == calls_before

    downloads = {
        item["kind"]: download_file(client, session_headers, project_id, exported, item)
        for item in exported["files"]
    }
    with Image.open(io.BytesIO(downloads["png"])) as image:
        assert image.size == (2048, 3072)
    assert downloads["pdf"].startswith(b"%PDF-")
    with zipfile.ZipFile(io.BytesIO(downloads["cbz"])) as archive:
        assert archive.namelist() == ["ComicInfo.xml", "001.png"]
        assert archive.read("001.png") == downloads["png"]
    with zipfile.ZipFile(io.BytesIO(downloads["engineering_package"])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("records.json"))
        assert manifest["credentials_included"] is False
        assert records["tables"]["projects"][0]["workspace_path"] == "/MANGA_MAKER_PROJECT"
        assert all(
            row.get("credential_profile_id", "restore-required") == "restore-required"
            for rows in records["tables"].values()
            for row in rows
        )
        assert not any(name.startswith("secrets/") for name in archive.namelist())
        assert records["tables"]["generation_approvals"]
        assert records["tables"]["provider_execution_specs"]
        assert records["tables"]["page_layout_drafts"]
        assert records["tables"]["layout_approvals"]
        assert records["tables"]["dimension_selections"]
        assert records["tables"]["artifact_versions"]
        assert any(name.startswith("project/layouts/") for name in archive.namelist())

    original_download_hashes = {
        kind: hashlib.sha256(content).hexdigest() for kind, content in downloads.items()
    }
    revised_document = copy.deepcopy(page["document"])
    revised_document["text_layers"][0]["text"] = "新版本"
    revised = client.post(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions",
        headers=session_headers,
        json={
            "expected_revision": page["page_revision"],
            "document": revised_document,
        },
    )
    assert revised.status_code == 201, revised.text
    for item in exported["files"]:
        content = download_file(client, session_headers, project_id, exported, item)
        assert hashlib.sha256(content).hexdigest() == original_download_hashes[item["kind"]]

    import_preflight = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={
            "file": (
                "project.manga-maker.zip",
                downloads["engineering_package"],
                "application/zip",
            )
        },
    )
    assert import_preflight.status_code == 201, import_preflight.text
    dry_run = import_preflight.json()
    assert dry_run["writes_performed"] == 0
    assert dry_run["requires_confirmation"] is True

    restored_response = client.post(
        f"/api/v1/imports/{dry_run['import_preflight_id']}/restore",
        headers=session_headers,
        json={"confirmed": True},
    )
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()
    assert restored["id_conflict_remapped"] is True
    assert restored["project_id"] != project_id
    restored_chapters = client.get(
        f"/api/v1/projects/{restored['project_id']}/source/chapters"
    ).json()
    restored_pages = client.get(
        f"/api/v1/projects/{restored['project_id']}/pages",
        params={"chapter_id": restored_chapters["chapters"][0]["chapter_id"]},
    )
    assert restored_pages.status_code == 200, restored_pages.text
    restored_page = restored_pages.json()[0]
    assert restored_page["render_sha256"] == page["render_sha256"]
    restored_content = client.get(
        f"/api/v1/projects/{restored['project_id']}/pages/{restored_page['page_id']}"
        f"/versions/{restored_page['page_version_id']}/content",
        headers=session_headers,
    )
    assert restored_content.content == downloads["png"]
    restored_layouts = client.get(
        f"/api/v1/projects/{restored['project_id']}/layouts",
        params={"chapter_id": restored_chapters["chapters"][0]["chapter_id"]},
    )
    assert restored_layouts.status_code == 200, restored_layouts.text
    assert len(restored_layouts.json()) == 1
    restored_layout = restored_layouts.json()[0]
    restored_approval = client.get(
        f"/api/v1/projects/{restored['project_id']}/layouts/"
        f"{restored_layout['page_layout_draft_version_id']}/approval"
    )
    assert restored_approval.status_code == 200, restored_approval.text
    assert restored_approval.json()["state"] == "active"
    restored_prompting = client.get(
        f"/api/v1/projects/{restored['project_id']}/prompting",
        params={"chapter_id": restored_chapters["chapters"][0]["chapter_id"]},
    )
    assert restored_prompting.status_code == 200, restored_prompting.text
    restored_workflow = restored_prompting.json()
    assert restored_workflow["prompt_bundle"]["approval_status"] == "approved"
    assert restored_workflow["prompt_bundle"]["compatibility"]["kind"] == "prompt_plan_v2"
    assert restored_workflow["generation_readiness"]["structured_prompt_ready"] is True
    with client.app.state.database.reader() as connection:
        source_counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            for table in (
                "generation_approvals",
                "page_layout_drafts",
                "layout_approvals",
                "artifact_versions",
            )
        }
        restored_counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
                (restored["project_id"],),
            ).fetchone()[0]
            for table in source_counts
        }
        restored_job = connection.execute(
            """SELECT j.generation_approval_id, j.provider_model_id,
                      a.state AS generation_approval_state
               FROM generation_jobs j JOIN generation_approvals a
                 ON a.generation_approval_id = j.generation_approval_id
               WHERE j.project_id = ? LIMIT 1""",
            (restored["project_id"],),
        ).fetchone()
        source_layout = connection.execute(
            "SELECT page_layout_draft_id FROM page_layout_drafts WHERE project_id = ? LIMIT 1",
            (project_id,),
        ).fetchone()
        restored_layout = connection.execute(
            "SELECT page_layout_draft_id FROM page_layout_drafts WHERE project_id = ? LIMIT 1",
            (restored["project_id"],),
        ).fetchone()
        restored_item = connection.execute(
            """SELECT i.page_layout_draft_id
               FROM generation_job_items i JOIN generation_jobs j ON j.job_id = i.job_id
               WHERE j.project_id = ? LIMIT 1""",
            (restored["project_id"],),
        ).fetchone()
        source_config = connection.execute(
            "SELECT provider_model_id FROM novelai_configs WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    assert restored_counts == source_counts
    assert restored_job["generation_approval_id"] is not None
    assert restored_job["generation_approval_state"] == "stale"
    assert restored_job["provider_model_id"] == source_config["provider_model_id"]
    assert restored_layout["page_layout_draft_id"] != source_layout["page_layout_draft_id"]
    assert restored_item["page_layout_draft_id"] == restored_layout["page_layout_draft_id"]
    assert provider.generation_calls == calls_before


def test_failed_export_does_not_modify_previous_success(
    client: TestClient, session_headers: dict[str, str], monkeypatch: Any
) -> None:
    prepared, _provider, _page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    preflight = export_preflight(
        client, session_headers, project_id, prepared["chapter"]["chapter_id"]
    )
    successful = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    ).json()
    previous_hashes = {item["export_file_id"]: item["sha256"] for item in successful["files"]}

    def fail_pdf(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected local PDF failure")

    monkeypatch.setattr(client.app.state.exports, "_write_pdf", fail_pdf)
    failed = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    )
    assert failed.status_code == 500
    exports = client.get(f"/api/v1/projects/{project_id}/exports").json()
    assert [item["status"] for item in exports] == ["failed", "completed"]
    current_success = next(item for item in exports if item["status"] == "completed")
    assert {
        item["export_file_id"]: item["sha256"] for item in current_success["files"]
    } == previous_hashes


def test_color_vertical_strip_exports_preserve_profile_and_dimensions(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    templates = client.get(f"/api/v1/projects/{project_id}/pages/templates").json()
    strip = next(item for item in templates if item["template_id"] == "strip-1")
    document = copy.deepcopy(page["document"])
    document.update(
        {
            "schema_version": "2.0",
            "width": strip["width"],
            "height": strip["height"],
            "reading_direction": "top_to_bottom",
            "color_mode": "color",
            "background_color": "#fff4df",
            "template_id": strip["template_id"],
            "show_page_number": False,
            "text_layers": [],
        }
    )
    document["panels"][0]["frame"] = strip["frames"][0]
    revised_response = client.post(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions",
        headers=session_headers,
        json={"expected_revision": page["page_revision"], "document": document},
    )
    assert revised_response.status_code == 201, revised_response.text
    revised = revised_response.json()
    rendered = client.get(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions/"
        f"{revised['page_version_id']}/content",
        headers=session_headers,
    )
    with Image.open(io.BytesIO(rendered.content)) as image:
        assert image.size == (1440, 1804)

    preflight = export_preflight(
        client,
        session_headers,
        project_id,
        prepared["chapter"]["chapter_id"],
    )
    assert preflight["schema_version"] == "1.1"
    assert preflight["pages"][0]["width"] == 1440
    assert preflight["pages"][0]["height"] == 1804
    assert preflight["pages"][0]["reading_direction"] == "top_to_bottom"
    assert preflight["pages"][0]["color_mode"] == "color"
    exported = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    )
    assert exported.status_code == 201, exported.text
    png_file = next(item for item in exported.json()["files"] if item["kind"] == "png")
    payload = download_file(client, session_headers, project_id, exported.json(), png_file)
    with Image.open(io.BytesIO(payload)) as image:
        assert image.size == (1440, 1804)
    assert provider.generation_calls == 1


def test_right_to_left_page_sets_cbz_reading_metadata(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    document = copy.deepcopy(page["document"])
    document.update(
        {
            "schema_version": "2.0",
            "reading_direction": "right_to_left",
            "text_layers": [],
        }
    )
    revised = client.post(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions",
        headers=session_headers,
        json={"expected_revision": page["page_revision"], "document": document},
    )
    assert revised.status_code == 201, revised.text
    preflight = export_preflight(
        client,
        session_headers,
        project_id,
        prepared["chapter"]["chapter_id"],
    )
    assert preflight["pages"][0]["reading_direction"] == "right_to_left"
    exported = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    ).json()
    cbz_file = next(item for item in exported["files"] if item["kind"] == "cbz")
    cbz = download_file(client, session_headers, project_id, exported, cbz_file)
    with zipfile.ZipFile(io.BytesIO(cbz)) as archive:
        comic_info = ElementTree.fromstring(archive.read("ComicInfo.xml"))
    assert comic_info.findtext("Manga") == "YesAndRightToLeft"
    assert provider.generation_calls == 1


def test_import_preflight_rejects_zip_slip_symlink_and_compression_bomb(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    slip = make_zip({"../escape": b"bad", "manifest.json": b"{}", "records.json": b"{}"})
    response = upload_package(client, session_headers, slip)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_PACKAGE_PATH_INVALID"

    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("project/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "target")
        archive.writestr("manifest.json", "{}")
        archive.writestr("records.json", "{}")
    response = upload_package(client, session_headers, symlink.getvalue())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_PACKAGE_SYMLINK_REJECTED"

    bomb = make_zip(
        {
            "manifest.json": b"{}",
            "records.json": b"{}",
            "project/repeated.bin": b"0" * (1024 * 1024),
        }
    )
    response = upload_package(client, session_headers, bomb)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_PACKAGE_COMPRESSION_BOMB"


def test_package_scope_and_credential_fields_fail_closed() -> None:
    for name in ("project/secrets/token.txt", "outside.bin", "project/assets/staging/x"):
        try:
            ExportService._validate_package_file_scope(name)
        except ApplicationError as error:
            assert error.code == "PROJECT_PACKAGE_FILE_SCOPE_INVALID"
        else:
            raise AssertionError(f"unsafe package path accepted: {name}")
    try:
        ExportService._validate_package_json_safety('{"authorization":"Bearer value"}')
    except ApplicationError as error:
        assert error.code == "PROJECT_PACKAGE_CONTAINS_CREDENTIAL"
    else:
        raise AssertionError("credential field accepted")


def export_preflight(
    client: TestClient, headers: dict[str, str], project_id: str, chapter_id: str
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/exports/preflight",
        headers=headers,
        json={"chapter_id": chapter_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def export_request(preflight: dict[str, Any], confirmed: bool = True) -> dict[str, Any]:
    return {
        "chapter_id": preflight["chapter_id"],
        "page_version_ids": [item["page_version_id"] for item in preflight["pages"]],
        "plan_fingerprint": preflight["plan_fingerprint"],
        "confirmed": confirmed,
    }


def download_file(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    exported: dict[str, Any],
    item: dict[str, Any],
) -> bytes:
    path = (
        f"/api/v1/projects/{project_id}/exports/{exported['export_revision_id']}"
        f"/files/{item['export_file_id']}"
    )
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert hashlib.sha256(response.content).hexdigest() == item["sha256"]
    return response.content


def upload_package(client: TestClient, headers: dict[str, str], content: bytes) -> Any:
    return client.post(
        "/api/v1/imports/preflight",
        headers=headers,
        files={"file": ("unsafe.zip", content, "application/zip")},
    )


def make_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()
