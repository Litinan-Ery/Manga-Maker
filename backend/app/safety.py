from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

from .errors import ApplicationError
from .vault import CredentialVault, VaultLockedError

SCAN_CHUNK_BYTES = 1024 * 1024
MAX_SECRET_SCAN_ENTRY_BYTES = 512 * 1024 * 1024


class SecretScanner:
    """Fail-closed export scanner that never returns or logs credential bytes."""

    def __init__(self, vault: CredentialVault) -> None:
        self.vault = vault

    def assert_ready(self) -> dict[str, int | str]:
        return {
            "status": "ready",
            "credential_count": len(self._credential_bytes()),
        }

    def assert_files_safe(self, paths: Iterable[Path]) -> dict[str, int | str]:
        secrets = self._credential_bytes()
        scanned_files = 0
        scanned_bytes = 0
        for path in paths:
            if not path.is_file():
                raise ApplicationError(
                    "SECRET_SCAN_FILE_MISSING", "秘密扫描找不到待发布文件。", 500
                )
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        if info.file_size > MAX_SECRET_SCAN_ENTRY_BYTES:
                            raise ApplicationError(
                                "SECRET_SCAN_LIMIT_EXCEEDED",
                                "待发布归档中的单个文件超过秘密扫描上限。",
                                422,
                            )
                        with archive.open(info) as handle:
                            self._assert_stream_safe(handle, secrets)
                        scanned_files += 1
                        scanned_bytes += info.file_size
            else:
                with path.open("rb") as handle:
                    self._assert_stream_safe(handle, secrets)
                scanned_files += 1
                scanned_bytes += path.stat().st_size
        return {
            "status": "passed",
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "credential_count": len(secrets),
            "matches": 0,
        }

    def _credential_bytes(self) -> tuple[bytes, ...]:
        if not self.vault.is_configured:
            return ()
        if not self.vault.is_unlocked:
            raise ApplicationError(
                "VAULT_UNLOCK_REQUIRED_FOR_EXPORT_SCAN",
                "请先解锁本地凭证库, 再执行导出零泄露扫描。",
                423,
            )
        try:
            profiles = self.vault.list_profiles()
            values = {
                self.vault.get_secret(str(profile["profile_id"])).encode() for profile in profiles
            }
        except (VaultLockedError, KeyError) as exc:
            raise ApplicationError(
                "VAULT_UNLOCK_REQUIRED_FOR_EXPORT_SCAN",
                "请先解锁本地凭证库, 再执行导出零泄露扫描。",
                423,
            ) from exc
        return tuple(sorted(values, key=len, reverse=True))

    @staticmethod
    def _assert_stream_safe(handle: IO[bytes], secrets: tuple[bytes, ...]) -> None:
        if not secrets:
            return
        overlap = max(len(secret) for secret in secrets) - 1
        tail = b""
        while chunk := handle.read(SCAN_CHUNK_BYTES):
            window = tail + chunk
            for secret in secrets:
                if _contains(window, secret):
                    raise ApplicationError(
                        "EXPORT_SECRET_DETECTED",
                        "待发布内容命中了本地凭证, 导出已阻止且旧版本不受影响。",
                        422,
                    )
            tail = window[-overlap:] if overlap > 0 else b""


def redact_sensitive(value: Any) -> Any:
    """Recursively redact data before it can be emitted to diagnostics or logs."""
    sensitive_keys = {
        "api_key",
        "api_token",
        "authorization",
        "bearer_token",
        "csrf",
        "csrf_token",
        "password",
        "prompt",
        "negative_prompt",
        "secret",
        "session",
        "session_token",
        "source_text",
        "token",
    }
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold() in sensitive_keys else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def _contains(haystack: bytes, needle: bytes) -> bool:
    return bool(needle) and needle in haystack
