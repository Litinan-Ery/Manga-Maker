from fastapi.testclient import TestClient


def test_vault_write_requires_local_session(client: TestClient) -> None:
    response = client.post(
        "/api/v1/vault",
        json={"master_password": "correct horse battery staple"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LOCAL_SESSION_REQUIRED"


def test_vault_api_never_returns_secret(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    create_response = client.post(
        "/api/v1/vault",
        headers=session_headers,
        json={"master_password": "correct horse battery staple"},
    )
    assert create_response.status_code == 201

    secret = "unit-test-credential-value"
    save_response = client.put(
        "/api/v1/vault/profiles/novelai-main",
        headers=session_headers,
        json={"provider": "novelai", "label": "NovelAI", "secret": secret},
    )
    assert save_response.status_code == 200
    assert secret not in save_response.text
    assert save_response.json()["fingerprint"] == "…alue"

    status_response = client.get("/api/v1/vault")
    assert status_response.status_code == 200
    assert secret not in status_response.text
    assert status_response.json()["profiles"][0]["profile_id"] == "novelai-main"

    lock_response = client.post("/api/v1/vault/lock", headers=session_headers)
    assert lock_response.status_code == 200
    assert lock_response.json()["profiles"] == []

    wrong_password = client.post(
        "/api/v1/vault/unlock",
        headers=session_headers,
        json={"master_password": "incorrect password value"},
    )
    assert wrong_password.status_code == 401
    assert secret not in wrong_password.text
