from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorDescriptor:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.code or len(self.code) > 100:
            raise ValueError("error code must be non-empty and no longer than 100 characters")
        if not self.message:
            raise ValueError("error message must be non-empty")
