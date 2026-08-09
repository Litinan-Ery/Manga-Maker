from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def app_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "app-data"


@pytest.fixture
def client(app_data_dir: Path) -> Iterator[TestClient]:
    settings = Settings(app_data_dir=app_data_dir, environment="test")
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_headers(client: TestClient) -> dict[str, str]:
    app_state = client.app.state
    return {
        "X-Manga-Maker-Session": app_state.local_session.token,
        "X-CSRF-Token": app_state.local_session.csrf_token,
    }
