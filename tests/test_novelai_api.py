from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.novelai.contracts import (
    CONNECTION_TEST_PATH,
    CONTRACT_SHA256,
    MAPPING_VERSION,
    MODEL_PROFILES,
    contract_payload,
)
from backend.app.novelai.mock import MockNovelAIClient


def test_pinned_contract_snapshot_matches_runtime_profile() -> None:
    snapshot_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "novelai"
        / "image-api.contract.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert snapshot["sha256"] == CONTRACT_SHA256
    assert snapshot["mapping_version"] == MAPPING_VERSION
    assert snapshot["source_url"].endswith("/docs/doc.json")
    assert snapshot["allowed_paths"]["connection_test"] == CONNECTION_TEST_PATH
    assert len(MODEL_PROFILES) == 6
    assert sum(profile.recommended for profile in MODEL_PROFILES) == 1
    assert [profile.label for profile in MODEL_PROFILES if profile.supports_precise_reference] == [
        "Anime V4.5 Full",
        "Anime V4.5 Curated",
    ]
    assert contract_payload()["sha256"] == snapshot["sha256"]


def test_configuration_and_explicit_mock_connection_test_do_not_persist_secret(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id = create_project(client, session_headers)
    secret = "unit-novelai-secret"
    create_vault_profile(client, session_headers, provider="novelai", secret=secret)

    capabilities = client.get(
        f"/api/v1/projects/{project_id}/novelai/capabilities"
    )
    assert capabilities.status_code == 200
    assert capabilities.json()["sha256"] == CONTRACT_SHA256

    saved = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=session_headers,
        json={
            "provider_model_id": "nai-diffusion-4-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 20,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["credential_fingerprint"] == "…cret"
    assert saved.json()["contract_sha256"] == CONTRACT_SHA256
    assert secret not in saved.text

    mock = MockNovelAIClient()
    client.app.state.novelai.provider_factory = lambda _configuration, _secret_reader: mock
    tested = client.post(
        f"/api/v1/projects/{project_id}/novelai/connection-test",
        headers=session_headers,
    )
    assert tested.status_code == 200
    assert tested.json()["generated_images"] == 0
    assert tested.json()["suggestion_count"] == 1
    assert mock.connection_calls == 1

    loaded = client.get(f"/api/v1/projects/{project_id}/novelai/config")
    assert loaded.json()["last_connection_status"] == "ok"
    assert loaded.json()["last_connection_at"] is not None

    with client.app.state.database.reader() as connection:
        config_rows = connection.execute("SELECT * FROM novelai_configs").fetchall()
        audit_rows = connection.execute(
            "SELECT event_type, payload_json FROM audit_events WHERE event_type LIKE 'novelai.%'"
        ).fetchall()
    persisted = json.dumps(
        [dict(row) for row in config_rows] + [dict(row) for row in audit_rows],
        ensure_ascii=False,
    )
    assert secret not in persisted
    assert "unit-novelai-secret" not in persisted


def test_configuration_requires_unlocked_novelai_profile(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id = create_project(client, session_headers)
    create_vault_profile(
        client,
        session_headers,
        provider="openai-compatible",
        secret="text-model-secret",
    )
    mismatch = save_configuration(client, session_headers, project_id)
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "NOVELAI_CREDENTIAL_PROVIDER_MISMATCH"

    client.post("/api/v1/vault/lock", headers=session_headers)
    locked = save_configuration(client, session_headers, project_id)
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "VAULT_LOCKED"


def create_project(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/projects", headers=headers, json={"title": "NovelAI 契约测试"}
    )
    assert response.status_code == 201
    return str(response.json()["project_id"])


def create_vault_profile(
    client: TestClient,
    headers: dict[str, str],
    *,
    provider: str,
    secret: str,
) -> None:
    created = client.post(
        "/api/v1/vault",
        headers=headers,
        json={"master_password": "unit test master password"},
    )
    assert created.status_code == 201
    saved = client.put(
        "/api/v1/vault/profiles/novelai",
        headers=headers,
        json={"provider": provider, "label": "NovelAI", "secret": secret},
    )
    assert saved.status_code == 200


def save_configuration(
    client: TestClient, headers: dict[str, str], project_id: str
):
    return client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=headers,
        json={
            "provider_model_id": "nai-diffusion-4-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 30,
        },
    )
