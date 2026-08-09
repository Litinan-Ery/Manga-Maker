from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.config import Settings


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.1.2.3"])
def test_loopback_hosts_are_allowed(host: str, tmp_path: Path) -> None:
    settings = Settings(bind_host=host, app_data_dir=tmp_path)
    assert settings.bind_host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_non_loopback_hosts_are_rejected(host: str, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(bind_host=host, app_data_dir=tmp_path)
