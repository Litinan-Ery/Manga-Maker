from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Sha256:
    value: str

    def __post_init__(self) -> None:
        if SHA256_PATTERN.fullmatch(self.value) is None:
            raise ValueError("SHA-256 must be 64 lowercase hexadecimal characters")

    @classmethod
    def digest(cls, payload: bytes) -> Sha256:
        return cls(hashlib.sha256(payload).hexdigest())

    def __str__(self) -> str:
        return self.value
