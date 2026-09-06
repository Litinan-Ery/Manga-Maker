from __future__ import annotations

from typing import Any, Protocol

from .contracts import ArtifactPointer, TaskSnapshot, UnitReference


class ContextDomainReader(Protocol):
    def capture(self, project_id: str, objective: str) -> TaskSnapshot: ...

    def source_matches(self, project_id: str, source: ArtifactPointer) -> bool: ...

    def inspect_unit(self, project_id: str, unit: UnitReference) -> dict[str, Any]: ...
