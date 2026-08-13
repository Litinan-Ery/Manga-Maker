from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .hashes import Sha256


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_type: str
    artifact_id: UUID
    version: int
    content_sha256: Sha256
    schema_version: str

    def __post_init__(self) -> None:
        if not self.artifact_type or len(self.artifact_type) > 100:
            raise ValueError("artifact type must be non-empty and no longer than 100 characters")
        if self.version < 1:
            raise ValueError("artifact version must be positive")
        if not self.schema_version or len(self.schema_version) > 50:
            raise ValueError("artifact schema version must be non-empty")
