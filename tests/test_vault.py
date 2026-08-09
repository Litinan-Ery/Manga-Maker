from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.vault import (
    CredentialVault,
    KdfParameters,
    VaultAuthenticationError,
    VaultLockedError,
)


@pytest.fixture
def vault(tmp_path: Path) -> CredentialVault:
    return CredentialVault(
        tmp_path / "secrets" / "credentials.vault",
        kdf=KdfParameters(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1),
    )


def test_vault_round_trip_and_permissions(vault: CredentialVault) -> None:
    vault.create("correct horse battery staple")
    result = vault.upsert_secret(
        "novelai-main",
        provider="novelai",
        label="NovelAI",
        secret="unit-test-credential-value",
    )

    assert result["fingerprint"] == "…alue"
    assert vault.get_secret("novelai-main") == "unit-test-credential-value"
    assert "unit-test-credential-value" not in vault.path.read_text(errors="ignore")
    assert os.stat(vault.path).st_mode & 0o777 == 0o600
    assert os.stat(vault.path.parent).st_mode & 0o777 == 0o700

    vault.lock()
    with pytest.raises(VaultLockedError):
        vault.get_secret("novelai-main")

    vault.unlock("correct horse battery staple")
    assert vault.get_secret("novelai-main") == "unit-test-credential-value"


def test_wrong_password_and_tampering_fail_closed(vault: CredentialVault) -> None:
    vault.create("correct horse battery staple")
    vault.lock()

    with pytest.raises(VaultAuthenticationError):
        vault.unlock("incorrect password value")

    content = bytearray(vault.path.read_bytes())
    content[-1] ^= 0x01
    vault.path.write_bytes(content)
    with pytest.raises(VaultAuthenticationError):
        vault.unlock("correct horse battery staple")


def test_reset_preserves_recoverable_encrypted_backup(vault: CredentialVault) -> None:
    vault.create("correct horse battery staple")
    backup = vault.reset()

    assert backup is not None
    assert backup.is_file()
    assert not vault.path.exists()
    assert vault.is_unlocked is False
