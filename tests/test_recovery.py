from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.generation.assets import canonical_json
from backend.app.ids import uuid7
from backend.app.main import create_app
from tests.test_generation_queue import prepare_job, transition


def test_restart_fails_closed_preserves_partial_work_and_never_calls_provider(
    app_data_dir: Path,
) -> None:
    settings = Settings(app_data_dir=app_data_dir, environment="test")
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        headers = session_headers(first)
        prepared = prepare_job(first, headers, title_suffix="崩溃恢复")
        started = transition(first, headers, prepared["project_id"], prepared["job"], "start")
        attempt = first.app.state.generation_queue.claim_next(started["job_id"])
        assert attempt is not None
        assert first.app.state.generation_queue.mark_provider_request_started(attempt["attempt_id"])
        workspace = first.app.state.projects.workspace_path(prepared["project_id"])
        partial_asset = workspace / "assets" / ".staging" / "partial-response"
        partial_asset.mkdir(mode=0o700, parents=True)
        (partial_asset / "original.png.part").write_bytes(b"partial")
        interrupted_workspace = settings.projects_dir / ".staging-interrupted-project"
        interrupted_workspace.mkdir(mode=0o700)

        export_revision_id = str(uuid7())
        partial_export = workspace / "exports" / f".staging-{export_revision_id}"
        partial_export.mkdir(mode=0o700)
        (partial_export / "manga.pdf.part").write_bytes(b"partial")
        with first.app.state.database.writer() as connection:
            connection.execute(
                """INSERT INTO export_revisions(
                       export_revision_id, project_id, chapter_id, status,
                       schema_version, page_selection_json, selection_sha256
                   ) VALUES (?, ?, ?, 'staging', '1.0', ?, ?)""",
                (
                    export_revision_id,
                    prepared["project_id"],
                    prepared["chapter"]["chapter_id"],
                    canonical_json([]),
                    "a" * 64,
                ),
            )

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        report = second.get("/api/v1/system/recovery")
        assert report.status_code == 200
        payload = report.json()
        assert payload["trigger"] == "startup"
        assert payload["status"] == "needs_attention"
        assert payload["queue_recovery"] == {"needs_review": 1, "paused": 0}
        assert payload["export_recovery"]["interrupted_exports_failed_closed"] == 1
        assert payload["project_recovery"]["interrupted_workspaces_preserved"] == 1
        assert payload["integrity"]["staging_items"] == 1
        assert payload["provider_requests_started"] == 0
        assert payload["external_requests_started"] == 0
        assert "workspace_path" not in report.text

        job = second.app.state.generation_queue.get_job(prepared["project_id"], started["job_id"])
        assert job["status"] == "needs_review"
        assert job["calls_started"] == 1
        assert second.app.state.generation_queue.claim_next(started["job_id"]) is None
        with second.app.state.database.reader() as connection:
            export = connection.execute(
                "SELECT status, failure_code FROM export_revisions WHERE export_revision_id = ?",
                (export_revision_id,),
            ).fetchone()
        assert dict(export) == {"status": "failed", "failure_code": "PROCESS_RESTARTED"}
        assert not partial_export.exists()
        assert list((workspace / "exports").glob(f".failed-{export_revision_id}*"))
        assert not interrupted_workspace.exists()
        assert list(settings.projects_dir.glob(".orphan-recovery-*"))


def test_manual_integrity_check_is_protected_redacted_and_persisted(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = client.post(
        "/api/v1/projects", headers=session_headers, json={"title": "完整性检查"}
    ).json()
    workspace = client.app.state.projects.workspace_path(project["project_id"])
    forbidden = workspace / "secrets" / "credentials.vault"
    forbidden.parent.mkdir(mode=0o700)
    forbidden.write_bytes(b"encrypted-looking-test-data")

    assert client.post("/api/v1/system/recovery").status_code == 401
    checked = client.post("/api/v1/system/recovery", headers=session_headers)
    assert checked.status_code == 200
    report = checked.json()
    assert report["trigger"] == "manual"
    assert report["status"] == "needs_attention"
    assert report["integrity"]["forbidden_project_files"] == 1
    assert report["integrity"]["critical_findings"] >= 1
    assert report["provider_requests_started"] == 0
    assert "credentials.vault" not in checked.text
    assert (
        client.get("/api/v1/system/recovery").json()["recovery_run_id"] == report["recovery_run_id"]
    )
    with client.app.state.database.reader() as connection:
        stored = connection.execute(
            "SELECT summary_json FROM recovery_runs WHERE recovery_run_id = ?",
            (report["recovery_run_id"],),
        ).fetchone()
    assert json.loads(str(stored["summary_json"]))["provider_requests_started"] == 0


def session_headers(client: TestClient) -> dict[str, str]:
    return {
        "X-Manga-Maker-Session": client.app.state.local_session.token,
        "X-CSRF-Token": client.app.state.local_session.csrf_token,
    }
