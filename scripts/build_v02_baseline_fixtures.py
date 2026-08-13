from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_generation_queue import transition
from tests.test_pages_api import prepare_page
from tests.test_revisions_api import (
    create_revision_job,
    estimate_revision,
    mask_bytes,
    upload_mask,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "v0.2"
DATABASE_FIXTURE = FIXTURE_DIR / "schema16.db.fixture"
PACKAGE_FIXTURE = FIXTURE_DIR / "project-v1.4.manga-maker.zip"
METADATA_FIXTURE = FIXTURE_DIR / "fixture-metadata.json"
PORTABLE_WORKSPACE = "/MANGA_MAKER_PROJECT"
REENTRY_PROFILE = "restore-required"


class _ClientApp(Protocol):
    state: Any


def _state(client: TestClient) -> Any:
    return cast(_ClientApp, client.app).state


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _session_headers(client: TestClient) -> dict[str, str]:
    state = _state(client)
    return {
        "X-Manga-Maker-Session": str(state.local_session.token),
        "X-CSRF-Token": str(state.local_session.csrf_token),
    }


def _execute_revision(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    estimate = estimate_revision(client, headers, project_id, request)
    response = create_revision_job(client, headers, project_id, estimate)
    if response.status_code != 201:
        raise RuntimeError(f"revision fixture job failed: {response.text}")
    started = transition(client, headers, project_id, response.json(), "start")
    state = _state(client)
    asyncio.run(state.generation_executor.run_until_blocked(started["job_id"]))
    job = cast(dict[str, Any], state.generation_queue.get_job(project_id, started["job_id"]))
    if job["status"] != "completed":
        raise RuntimeError(f"revision fixture did not complete: {job['status']}")
    return job


def _copy_and_normalize_database(source: Path, destination: Path, workspace: Path) -> None:
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA foreign_keys = ON")
        destination_connection.execute(
            "UPDATE projects SET workspace_path = ?",
            (PORTABLE_WORKSPACE,),
        )
        for table, columns in (
            ("source_preflights", ("staging_path",)),
            ("source_files", ("original_path", "normalized_path")),
        ):
            for column in columns:
                destination_connection.execute(
                    f"UPDATE {table} SET {column} = replace({column}, ?, ?)",
                    (str(workspace), PORTABLE_WORKSPACE),
                )
        for table in ("text_model_configs", "novelai_configs", "generation_jobs"):
            destination_connection.execute(
                f"UPDATE {table} SET credential_profile_id = ?",
                (REENTRY_PROFILE,),
            )
        violations = destination_connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"fixture normalization broke foreign keys: {violations[:5]}")
        destination_connection.commit()
        destination_connection.execute("PRAGMA journal_mode = DELETE")
        destination_connection.execute("VACUUM")
    finally:
        destination_connection.close()
        source_connection.close()


def _database_metadata(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        schema_version = int(
            connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        )
        table_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            if not str(row[0]).startswith("sqlite_")
        ]
        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in table_names
        }
        operation_kinds = [
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT operation_kind FROM generation_jobs ORDER BY operation_kind"
            )
        ]
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()
    return {
        "schema_version": schema_version,
        "quick_check": quick_check,
        "table_count": len(table_names),
        "table_names": table_names,
        "row_counts": counts,
        "operation_kinds": operation_kinds,
    }


def _package_metadata(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("records.json"))
        names = archive.namelist()
    return {
        "schema_version": manifest["schema_version"],
        "database_schema": records["database_schema"],
        "credentials_included": manifest["credentials_included"],
        "file_count": len(names),
        "record_counts": manifest["record_counts"],
        "selected_page_count": len(manifest["selected_pages"]),
    }


def build(*, force: bool) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    outputs = (DATABASE_FIXTURE, PACKAGE_FIXTURE, METADATA_FIXTURE)
    existing = [path for path in outputs if path.exists()]
    if existing and not force:
        paths = ", ".join(str(path.relative_to(ROOT)) for path in existing)
        raise FileExistsError(f"refusing to overwrite frozen fixtures without --force: {paths}")
    for path in existing:
        path.unlink()

    with tempfile.TemporaryDirectory(prefix="manga-maker-v02-fixture-") as temporary:
        app_data_dir = Path(temporary) / "app-data"
        settings = Settings(app_data_dir=app_data_dir, environment="test")
        app = create_app(settings)
        with TestClient(app) as client:
            headers = _session_headers(client)
            prepared, provider, page = prepare_page(client, headers)
            project_id = str(prepared["project_id"])
            chapter_id = str(prepared["chapter"]["chapter_id"])
            panel = page["document"]["panels"][0]

            _execute_revision(
                client,
                headers,
                project_id,
                {
                    "operation": "panel_reroll",
                    "page_id": page["page_id"],
                    "panel_id": panel["panel_id"],
                    "per_panel_cost_ceiling_anlas": 9,
                },
            )
            rerolled_page = client.get(
                f"/api/v1/projects/{project_id}/pages/{page['page_id']}/current"
            ).json()
            rerolled_panel = rerolled_page["document"]["panels"][0]
            mask_response = upload_mask(
                client,
                headers,
                project_id,
                rerolled_panel["panel_id"],
                rerolled_panel["asset_version_id"],
                mask_bytes(832, 1216, box=(100, 100, 300, 300)),
            )
            if mask_response.status_code != 201:
                raise RuntimeError(f"fixture mask upload failed: {mask_response.text}")
            mask = mask_response.json()
            _execute_revision(
                client,
                headers,
                project_id,
                {
                    "operation": "inpaint",
                    "page_id": rerolled_page["page_id"],
                    "panel_id": rerolled_panel["panel_id"],
                    "mask_asset_id": mask["mask_asset_id"],
                    "edit_prompt": "fixture: repair the hand while preserving composition",
                    "inpaint_strength": 0.65,
                    "per_panel_cost_ceiling_anlas": 12,
                },
            )
            current_page = client.get(
                f"/api/v1/projects/{project_id}/pages/{page['page_id']}/current"
            ).json()
            if current_page["version"] != 3 or provider.generation_calls != 3:
                raise RuntimeError("fixture revision history is incomplete")

            preflight = export_preflight(client, headers, project_id, chapter_id)
            export_response = client.post(
                f"/api/v1/projects/{project_id}/exports",
                headers=headers,
                json=export_request(preflight),
            )
            if export_response.status_code != 201:
                raise RuntimeError(f"fixture export failed: {export_response.text}")
            exported = export_response.json()
            package_file = next(
                item for item in exported["files"] if item["kind"] == "engineering_package"
            )
            package_bytes = download_file(
                client,
                headers,
                project_id,
                exported,
                package_file,
            )
            PACKAGE_FIXTURE.write_bytes(package_bytes)
            workspace = cast(Path, _state(client).projects.workspace_path(project_id))

        _copy_and_normalize_database(settings.database_path, DATABASE_FIXTURE, workspace)

    metadata = {
        "fixture_version": "v0.2-baseline-1",
        "source_code_baseline": "main@40f2cb9",
        "contains_real_credentials": False,
        "contains_real_provider_calls": False,
        "database": {
            **_database_metadata(DATABASE_FIXTURE),
            "filename": DATABASE_FIXTURE.name,
            "byte_size": DATABASE_FIXTURE.stat().st_size,
            "sha256": _sha256(DATABASE_FIXTURE),
        },
        "package": {
            **_package_metadata(PACKAGE_FIXTURE),
            "filename": PACKAGE_FIXTURE.name,
            "byte_size": PACKAGE_FIXTURE.stat().st_size,
            "sha256": _sha256(PACKAGE_FIXTURE),
        },
    }
    METADATA_FIXTURE.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for path in outputs:
        os.chmod(path, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build frozen v0.2 migration fixtures.")
    parser.add_argument("--force", action="store_true", help="replace existing frozen fixtures")
    arguments = parser.parse_args()
    build(force=arguments.force)


if __name__ == "__main__":  # pragma: no cover - manual fixture command
    main()
