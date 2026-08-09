from __future__ import annotations

import errno
import hashlib
from typing import Any

from fastapi.testclient import TestClient

from backend.app.safety import redact_sensitive
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_pages_api import prepare_page


def test_export_scans_unlocked_real_credential_bytes_and_preserves_old_export(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, _page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    chapter_id = prepared["chapter"]["chapter_id"]
    plan = export_preflight(client, session_headers, project_id, chapter_id)
    successful = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    ).json()
    assert successful["secret_scan"]["status"] == "passed"
    assert successful["secret_scan"]["matches"] == 0
    old_hashes = {
        item["export_file_id"]: hashlib.sha256(
            download_file(client, session_headers, project_id, successful, item)
        ).hexdigest()
        for item in successful["files"]
    }

    synthetic_secret = "unit-novelai-secret"
    workspace = client.app.state.projects.workspace_path(project_id)
    (workspace / "audit" / "injected-leak.txt").write_text(synthetic_secret, encoding="utf-8")
    failed = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    )
    assert failed.status_code == 422
    assert failed.json()["error"]["code"] == "EXPORT_SECRET_DETECTED"
    assert synthetic_secret not in failed.text
    exports = client.get(f"/api/v1/projects/{project_id}/exports").json()
    assert exports[0]["status"] == "failed"
    assert exports[0]["failure_code"] == "EXPORT_SECRET_DETECTED"
    current_success = next(item for item in exports if item["status"] == "completed")
    for item in current_success["files"]:
        assert (
            hashlib.sha256(
                download_file(client, session_headers, project_id, current_success, item)
            ).hexdigest()
            == old_hashes[item["export_file_id"]]
        )
    assert provider.generation_calls == 1


def test_locked_vault_blocks_export_before_staging(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, _provider, _page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    plan = export_preflight(client, session_headers, project_id, prepared["chapter"]["chapter_id"])
    client.app.state.vault.lock()
    response = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    )
    assert response.status_code == 423
    assert response.json()["error"]["code"] == "VAULT_UNLOCK_REQUIRED_FOR_EXPORT_SCAN"
    assert client.get(f"/api/v1/projects/{project_id}/exports").json() == []
    workspace = client.app.state.projects.workspace_path(project_id)
    assert not list((workspace / "exports").glob(".staging-*"))


def test_disk_full_is_normalized_and_previous_export_stays_available(
    client: TestClient,
    session_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    prepared, _provider, _page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    plan = export_preflight(client, session_headers, project_id, prepared["chapter"]["chapter_id"])
    successful = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    ).json()

    def disk_full(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(client.app.state.exports, "_write_pdf", disk_full)
    failed = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    )
    assert failed.status_code == 500
    assert "synthetic" not in failed.text
    exports = client.get(f"/api/v1/projects/{project_id}/exports").json()
    assert exports[0]["failure_code"] == "LOCAL_STORAGE_FULL"
    assert any(item["export_revision_id"] == successful["export_revision_id"] for item in exports)


def test_sensitive_diagnostics_are_recursively_redacted() -> None:
    source = {
        "job_id": "safe-id",
        "authorization": "Bearer unit-test-value",
        "nested": [
            {"prompt": "full prompt"},
            {"token": "unit-token", "count": 2},
        ],
    }
    redacted = redact_sensitive(source)
    assert redacted == {
        "job_id": "safe-id",
        "authorization": "[REDACTED]",
        "nested": [
            {"prompt": "[REDACTED]"},
            {"token": "[REDACTED]", "count": 2},
        ],
    }
    assert "unit-test-value" not in str(redacted)
