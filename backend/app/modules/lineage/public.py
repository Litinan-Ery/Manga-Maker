"""Only supported cross-module import surface for lineage."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    ArtifactDependencySnapshotV1,
    ArtifactImpactV1,
    ArtifactVersionRefV1,
    InvalidateArtifactCommandV1,
    InvalidationResultV1,
    RegisterArtifactCommandV1,
    RegisterDependencyCommandV1,
)
from .errors import ArtifactNotFoundError


class LineageFacade(Protocol):
    def register_artifact(
        self, command: RegisterArtifactCommandV1
    ) -> ArtifactVersionRefV1: ...

    def register_dependency(
        self, command: RegisterDependencyCommandV1
    ) -> ArtifactDependencySnapshotV1: ...

    def dependencies_for(
        self,
        artifact_type: str,
        artifact_id: str,
        version: int,
    ) -> tuple[ArtifactDependencySnapshotV1, ...]: ...

    def impact_preview(self, origin: ArtifactVersionRefV1) -> tuple[ArtifactImpactV1, ...]: ...

    def invalidate(self, command: InvalidateArtifactCommandV1) -> InvalidationResultV1: ...


__all__ = [
    "ArtifactDependencySnapshotV1",
    "ArtifactImpactV1",
    "ArtifactNotFoundError",
    "ArtifactVersionRefV1",
    "InvalidateArtifactCommandV1",
    "InvalidationResultV1",
    "LineageFacade",
    "RegisterArtifactCommandV1",
    "RegisterDependencyCommandV1",
]
