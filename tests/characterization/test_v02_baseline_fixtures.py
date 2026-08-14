from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.errors import ApplicationError
from backend.app.exports.service import ExportService

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "v0.2"
DATABASE_FIXTURE = FIXTURE_DIR / "schema16.db.fixture"
PACKAGE_FIXTURE = FIXTURE_DIR / "project-v1.4.manga-maker.zip"
METADATA_FIXTURE = FIXTURE_DIR / "fixture-metadata.json"


def load_metadata() -> dict[str, Any]:
    value = json.loads(METADATA_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_schema16_fixture_is_frozen_portable_and_relationally_valid() -> None:
    metadata = load_metadata()
    expected = metadata["database"]

    assert sha256(DATABASE_FIXTURE) == expected["sha256"]
    assert DATABASE_FIXTURE.stat().st_size == expected["byte_size"]
    connection = sqlite3.connect(f"file:{DATABASE_FIXTURE}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 16
        assert connection.execute("SELECT workspace_path FROM projects").fetchone()[0] == (
            "/MANGA_MAKER_PROJECT"
        )
        assert {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT credential_profile_id FROM generation_jobs"
            )
        } == {"restore-required"}
        assert {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT operation_kind FROM generation_jobs"
            )
        } == {"chapter_generate", "panel_reroll", "inpaint"}
        assert connection.execute("SELECT COUNT(*) FROM asset_versions").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM page_versions").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM mask_assets").fetchone()[0] == 1
    finally:
        connection.close()


def test_package_v14_fixture_has_safe_paths_hashes_and_legacy_prompt_shape() -> None:
    metadata = load_metadata()
    expected = metadata["package"]

    assert sha256(PACKAGE_FIXTURE) == expected["sha256"]
    with zipfile.ZipFile(PACKAGE_FIXTURE) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("records.json"))
        assert manifest["schema_version"] == "1.4"
        assert records["schema_version"] == "1.4"
        assert records["database_schema"] == 16
        assert manifest["credentials_included"] is False
        assert records["credentials_included"] is False
        assert len(manifest["selected_pages"]) == 1
        assert len(records["tables"]["asset_versions"]) == 3
        assert len(records["tables"]["page_versions"]) == 3
        assert len(records["tables"]["mask_assets"]) == 1

        prompt_document = json.loads(
            records["tables"]["prompt_bundle_versions"][0]["document_json"]
        )
        assert prompt_document["schema_version"] == "1.0"
        package = prompt_document["packages"][0]
        assert "compiled_prompt" in package
        assert "prompt_plan" not in package

        listed = {item["path"]: item for item in manifest["files"]}
        assert set(listed) == {
            name
            for name in archive.namelist()
            if name != "manifest.json" and not name.endswith("/")
        }
        for name, item in listed.items():
            path = Path(name)
            assert not path.is_absolute()
            assert ".." not in path.parts
            payload = archive.read(name)
            assert len(payload) == item["byte_size"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]


def test_package_v14_fixture_dry_run_restores_with_remapped_ids(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    preflight_response = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={
            "file": (
                PACKAGE_FIXTURE.name,
                PACKAGE_FIXTURE.read_bytes(),
                "application/zip",
            )
        },
    )
    assert preflight_response.status_code == 201, preflight_response.text
    preflight = preflight_response.json()
    assert preflight["schema_version"] == "1.4"
    assert preflight["writes_performed"] == 0
    assert preflight["requires_confirmation"] is True

    restored_response = client.post(
        f"/api/v1/imports/{preflight['import_preflight_id']}/restore",
        headers=session_headers,
        json={"confirmed": True},
    )
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()
    assert restored["id_conflict_remapped"] is False
    assert restored["project_id"] == preflight["source_project_id"]
    chapters = client.get(
        f"/api/v1/projects/{restored['project_id']}/source/chapters"
    ).json()
    pages = client.get(
        f"/api/v1/projects/{restored['project_id']}/pages",
        params={"chapter_id": chapters["chapters"][0]["chapter_id"]},
    ).json()
    assert len(pages) == 1
    versions = client.get(
        f"/api/v1/projects/{restored['project_id']}/pages/{pages[0]['page_id']}/versions"
    ).json()
    assert [version["version"] for version in versions] == [3, 2, 1]

    duplicate_preflight = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={
            "file": (
                f"duplicate-{PACKAGE_FIXTURE.name}",
                PACKAGE_FIXTURE.read_bytes(),
                "application/zip",
            )
        },
    ).json()
    duplicate_restore = client.post(
        f"/api/v1/imports/{duplicate_preflight['import_preflight_id']}/restore",
        headers=session_headers,
        json={"confirmed": True},
    )
    assert duplicate_restore.status_code == 200, duplicate_restore.text
    assert duplicate_restore.json()["id_conflict_remapped"] is True
    assert duplicate_restore.json()["project_id"] != restored["project_id"]


def test_package_v14_fixture_rejects_unknown_version_without_writes() -> None:
    with tempfile.TemporaryDirectory(prefix="manga-maker-v02-tamper-") as temporary:
        target = Path(temporary) / "unsupported.zip"
        with zipfile.ZipFile(PACKAGE_FIXTURE) as source, zipfile.ZipFile(
            target,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as destination:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == "manifest.json":
                    manifest = json.loads(payload)
                    manifest["schema_version"] = "999.0"
                    payload = json.dumps(manifest).encode()
                destination.writestr(info, payload)

        with zipfile.ZipFile(target) as archive, pytest.raises(ApplicationError) as raised:
            ExportService._validate_package_documents(
                json.loads(archive.read("manifest.json")),
                json.loads(archive.read("records.json")),
            )
        assert raised.value.code == "PROJECT_PACKAGE_SCHEMA_UNSUPPORTED"
