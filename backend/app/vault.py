from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from argon2.low_level import Type, hash_secret_raw
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
    crypto_aead_xchacha20poly1305_ietf_KEYBYTES,
    crypto_aead_xchacha20poly1305_ietf_NPUBBYTES,
)
from nacl.exceptions import CryptoError

MAGIC = b"MMVAULT1"
FORMAT_VERSION = 1
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class VaultError(Exception):
    """Base class for credential vault failures."""


class VaultAlreadyExistsError(VaultError):
    pass


class VaultNotConfiguredError(VaultError):
    pass


class VaultLockedError(VaultError):
    pass


class VaultAuthenticationError(VaultError):
    pass


class VaultFormatError(VaultError):
    pass


@dataclass(frozen=True, slots=True)
class KdfParameters:
    time_cost: int = 2
    memory_cost_kib: int = 64 * 1024
    parallelism: int = 1


class CredentialVault:
    """Application-owned encrypted credential store.

    The unlock key is held only for the process session. Secrets are decrypted on
    demand and are never persisted outside the authenticated ciphertext.
    """

    def __init__(self, path: Path, *, kdf: KdfParameters | None = None) -> None:
        self.path = path
        self.kdf = kdf or KdfParameters()
        self._key: bytearray | None = None

    @property
    def is_configured(self) -> bool:
        return self.path.is_file()

    @property
    def is_unlocked(self) -> bool:
        return self._key is not None

    def create(self, master_password: str) -> None:
        if self.path.exists():
            raise VaultAlreadyExistsError("credential vault already exists")
        self._validate_master_password(master_password)
        salt = secrets.token_bytes(16)
        key = self._derive_key(master_password, salt, self.kdf)
        self._key = bytearray(key)
        self._write_payload({}, salt=salt, parameters=self.kdf)

    def unlock(self, master_password: str) -> None:
        if not self.path.is_file():
            raise VaultNotConfiguredError("credential vault is not configured")
        header, ciphertext = self._read_container()
        salt = self._decode_header_bytes(header, "salt")
        parameters = KdfParameters(
            time_cost=int(header["kdf"]["time_cost"]),
            memory_cost_kib=int(header["kdf"]["memory_cost_kib"]),
            parallelism=int(header["kdf"]["parallelism"]),
        )
        key = self._derive_key(master_password, salt, parameters)
        try:
            self._decrypt_payload(header, ciphertext, key)
        except (CryptoError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultAuthenticationError("vault authentication failed") from exc
        self.lock()
        self._key = bytearray(key)

    def lock(self) -> None:
        if self._key is not None:
            for index in range(len(self._key)):
                self._key[index] = 0
        self._key = None

    def list_profiles(self) -> list[dict[str, str]]:
        payload, _header = self._load_payload()
        profiles = payload.get("profiles", {})
        return [
            {
                "profile_id": profile_id,
                "provider": str(item["provider"]),
                "label": str(item["label"]),
                "fingerprint": str(item["fingerprint"]),
            }
            for profile_id, item in sorted(profiles.items())
        ]

    def upsert_secret(
        self,
        profile_id: str,
        *,
        provider: str,
        label: str,
        secret: str,
    ) -> dict[str, str]:
        self._validate_profile_id(profile_id)
        normalized_secret = secret.strip()
        if not normalized_secret:
            raise ValueError("secret must not be empty")
        payload, header = self._load_payload()
        profiles = payload.setdefault("profiles", {})
        fingerprint = f"…{normalized_secret[-4:]}"
        profiles[profile_id] = {
            "provider": provider.strip(),
            "label": label.strip() or profile_id,
            "secret": normalized_secret,
            "fingerprint": fingerprint,
            "updated_at": int(time.time()),
        }
        salt = self._decode_header_bytes(header, "salt")
        parameters = self._parameters_from_header(header)
        self._write_payload(payload, salt=salt, parameters=parameters)
        return {
            "profile_id": profile_id,
            "provider": provider.strip(),
            "label": label.strip() or profile_id,
            "fingerprint": fingerprint,
        }

    def get_secret(self, profile_id: str) -> str:
        self._validate_profile_id(profile_id)
        payload, _header = self._load_payload()
        try:
            return str(payload["profiles"][profile_id]["secret"])
        except KeyError as exc:
            raise KeyError(profile_id) from exc

    def delete_secret(self, profile_id: str) -> bool:
        self._validate_profile_id(profile_id)
        payload, header = self._load_payload()
        profiles = payload.setdefault("profiles", {})
        removed = profiles.pop(profile_id, None) is not None
        if removed:
            salt = self._decode_header_bytes(header, "salt")
            parameters = self._parameters_from_header(header)
            self._write_payload(payload, salt=salt, parameters=parameters)
        return removed

    def reset(self) -> Path | None:
        self.lock()
        if not self.path.exists():
            return None
        backup = self.path.with_name(f"{self.path.name}.reset-{int(time.time())}.bak")
        os.replace(self.path, backup)
        return backup

    def _load_payload(self) -> tuple[dict[str, Any], dict[str, Any]]:
        key = self._require_key()
        header, ciphertext = self._read_container()
        try:
            payload = self._decrypt_payload(header, ciphertext, key)
        except (CryptoError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultAuthenticationError("vault authentication failed") from exc
        if not isinstance(payload, dict):
            raise VaultFormatError("vault payload must be an object")
        return payload, header

    def _write_payload(
        self,
        payload: dict[str, Any],
        *,
        salt: bytes,
        parameters: KdfParameters,
    ) -> None:
        key = self._require_key()
        nonce = secrets.token_bytes(crypto_aead_xchacha20poly1305_ietf_NPUBBYTES)
        header = {
            "version": FORMAT_VERSION,
            "cipher": "xchacha20poly1305",
            "kdf": {
                "name": "argon2id",
                "time_cost": parameters.time_cost,
                "memory_cost_kib": parameters.memory_cost_kib,
                "parallelism": parameters.parallelism,
            },
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
        }
        header_bytes = self._serialize_header(header)
        plaintext = json.dumps(
            {"profiles": payload.get("profiles", {})},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, header_bytes, nonce, key)
        container = MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + ciphertext
        self._atomic_write(container)

    def _read_container(self) -> tuple[dict[str, Any], bytes]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise VaultNotConfiguredError("credential vault is not configured") from exc
        minimum = len(MAGIC) + 4 + 16
        if len(raw) < minimum or not raw.startswith(MAGIC):
            raise VaultFormatError("invalid vault container")
        header_length = int.from_bytes(raw[len(MAGIC) : len(MAGIC) + 4], "big")
        header_start = len(MAGIC) + 4
        header_end = header_start + header_length
        if header_length <= 0 or header_end >= len(raw):
            raise VaultFormatError("invalid vault header length")
        try:
            header = json.loads(raw[header_start:header_end].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultFormatError("invalid vault header") from exc
        if header.get("version") != FORMAT_VERSION:
            raise VaultFormatError("unsupported vault version")
        if header.get("cipher") != "xchacha20poly1305":
            raise VaultFormatError("unsupported vault cipher")
        if header.get("kdf", {}).get("name") != "argon2id":
            raise VaultFormatError("unsupported vault KDF")
        return header, raw[header_end:]

    def _decrypt_payload(
        self, header: dict[str, Any], ciphertext: bytes, key: bytes
    ) -> dict[str, Any]:
        nonce = self._decode_header_bytes(header, "nonce")
        if len(nonce) != crypto_aead_xchacha20poly1305_ietf_NPUBBYTES:
            raise VaultFormatError("invalid vault nonce")
        header_bytes = self._serialize_header(header)
        plaintext = crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, header_bytes, nonce, key)
        decoded = json.loads(plaintext.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise VaultFormatError("vault payload must be an object")
        return cast(dict[str, Any], decoded)

    def _atomic_write(self, content: bytes) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
            directory_descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _serialize_header(header: dict[str, Any]) -> bytes:
        return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _derive_key(master_password: str, salt: bytes, parameters: KdfParameters) -> bytes:
        return hash_secret_raw(
            secret=master_password.encode("utf-8"),
            salt=salt,
            time_cost=parameters.time_cost,
            memory_cost=parameters.memory_cost_kib,
            parallelism=parameters.parallelism,
            hash_len=crypto_aead_xchacha20poly1305_ietf_KEYBYTES,
            type=Type.ID,
        )

    @staticmethod
    def _validate_master_password(master_password: str) -> None:
        if len(master_password) < 10:
            raise ValueError("master password must contain at least 10 characters")

    @staticmethod
    def _validate_profile_id(profile_id: str) -> None:
        if not PROFILE_PATTERN.fullmatch(profile_id):
            raise ValueError("invalid credential profile id")

    @staticmethod
    def _decode_header_bytes(header: dict[str, Any], field: str) -> bytes:
        try:
            return base64.b64decode(header[field], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise VaultFormatError(f"invalid vault {field}") from exc

    @staticmethod
    def _parameters_from_header(header: dict[str, Any]) -> KdfParameters:
        try:
            return KdfParameters(
                time_cost=int(header["kdf"]["time_cost"]),
                memory_cost_kib=int(header["kdf"]["memory_cost_kib"]),
                parallelism=int(header["kdf"]["parallelism"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VaultFormatError("invalid vault KDF parameters") from exc

    def _require_key(self) -> bytes:
        if self._key is None:
            raise VaultLockedError("credential vault is locked")
        return bytes(self._key)
