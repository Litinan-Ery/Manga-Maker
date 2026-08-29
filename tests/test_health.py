from fastapi.testclient import TestClient


def test_health_reports_local_components(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.2.1",
        "environment": "test",
        "database": "ok",
        "schema_version": 32,
        "vault_configured": False,
        "vault_unlocked": False,
    }
    assert "app-data" not in response.text
