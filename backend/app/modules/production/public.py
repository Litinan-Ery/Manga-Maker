"""Public import surface for production contracts."""

from __future__ import annotations

from typing import Protocol

from .contracts import ProviderCharacterCaption, ProviderExecutionSpec
from .errors import ProviderMappingError


class ProductionFacade(Protocol):
    def get_execution_spec(self, spec_id: str, version: int) -> ProviderExecutionSpec: ...


__all__ = [
    "ProductionFacade",
    "ProviderCharacterCaption",
    "ProviderExecutionSpec",
    "ProviderMappingError",
]
