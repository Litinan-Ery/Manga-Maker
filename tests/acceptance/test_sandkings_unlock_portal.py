from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.vault import CredentialVault, KdfParameters
from scripts.run_sandkings_v5_acceptance import _build_unlock_portal


def test_local_unlock_portal_fails_closed_then_unlocks_without_echoing_password(
    tmp_path: Path,
) -> None:
    password = "correct local master password"
    vault = CredentialVault(
        tmp_path / "credentials.vault",
        kdf=KdfParameters(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1),
    )
    vault.create(password)
    vault.lock()
    unlocked = threading.Event()
    token = "one-time-test-token"
    portal = _build_unlock_portal(
        vault,
        one_time_token=token,
        unlocked_event=unlocked,
    )

    with TestClient(portal) as client:
        page = client.get(f"/unlock/{token}")
        assert page.status_code == 200
        assert 'type="password"' in page.text
        assert page.headers["cache-control"] == "no-store"
        assert client.get("/unlock/not-the-token").status_code == 404

        rejected = client.post(
            f"/unlock/{token}",
            data={"master_password": "incorrect master password"},
        )
        assert rejected.status_code == 401
        assert "incorrect master password" not in rejected.text
        assert not vault.is_unlocked
        assert not unlocked.is_set()

        accepted = client.post(
            f"/unlock/{token}",
            data={"master_password": password},
        )
        assert accepted.status_code == 200
        assert password not in accepted.text
        assert vault.is_unlocked
        assert unlocked.is_set()
