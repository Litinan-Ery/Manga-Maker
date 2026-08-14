from __future__ import annotations

from typing import Any


class ProviderMappingError(ValueError):
    """A local provider contract failed before credentials may be read."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
