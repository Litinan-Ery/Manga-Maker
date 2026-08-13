from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DependencyEdgeType = Literal[
    "source_chapter_to_storyboard",
    "story_beat_to_storyboard",
    "storyboard_to_layout",
    "storyboard_to_bible",
    "layout_to_bible",
    "layout_to_prompt",
    "frame_to_prompt",
    "character_bible_to_prompt",
    "character_tags_to_prompt",
    "style_bible_to_prompt",
    "prompt_to_generation_spec",
    "layout_to_generation_spec",
    "generation_spec_to_provider_spec",
    "generation_spec_to_candidate_set",
    "asset_to_candidate_set",
    "candidate_set_to_review",
    "quality_finding_to_review",
    "review_to_page_version",
    "layout_to_page_version",
    "review_to_page_approval",
    "page_version_to_page_approval",
    "quality_finding_to_page_approval",
    "page_approval_to_export",
]


class LineageContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ArtifactVersionRefV1(LineageContract):
    contract_version: Literal["1.0"] = "1.0"
    project_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:/-]+$")
    artifact_type: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    artifact_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:/-]+$")
    version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    is_stale: bool = False


class RegisterArtifactCommandV1(LineageContract):
    artifact: ArtifactVersionRefV1

    @model_validator(mode="after")
    def new_artifact_cannot_arrive_stale(self) -> RegisterArtifactCommandV1:
        if self.artifact.is_stale:
            raise ValueError("new artifact registration cannot pre-mark the artifact stale")
        return self


class RegisterDependencyCommandV1(LineageContract):
    upstream: ArtifactVersionRefV1
    downstream: ArtifactVersionRefV1
    edge_type: DependencyEdgeType

    @model_validator(mode="after")
    def same_project_and_distinct_nodes(self) -> RegisterDependencyCommandV1:
        if self.upstream.project_id != self.downstream.project_id:
            raise ValueError("dependency artifacts must belong to the same project")
        if (
            self.upstream.artifact_type,
            self.upstream.artifact_id,
            self.upstream.version,
        ) == (
            self.downstream.artifact_type,
            self.downstream.artifact_id,
            self.downstream.version,
        ):
            raise ValueError("artifact cannot depend on itself")
        return self


class ArtifactDependencySnapshotV1(LineageContract):
    contract_version: Literal["1.0"] = "1.0"
    dependency_id: UUID
    project_id: str = Field(min_length=1, max_length=64)
    upstream: ArtifactVersionRefV1
    downstream: ArtifactVersionRefV1
    edge_type: DependencyEdgeType


class ImpactPathStepV1(LineageContract):
    artifact: ArtifactVersionRefV1
    via_edge_type: DependencyEdgeType | None = None


class ArtifactImpactV1(LineageContract):
    artifact: ArtifactVersionRefV1
    path: tuple[ImpactPathStepV1, ...] = Field(min_length=2)
    marked_stale: bool

    @model_validator(mode="after")
    def path_ends_at_artifact(self) -> ArtifactImpactV1:
        if self.path[-1].artifact != self.artifact:
            raise ValueError("impact path must end at the impacted artifact")
        if self.path[0].via_edge_type is not None:
            raise ValueError("impact origin cannot have an incoming edge type")
        return self


class InvalidateArtifactCommandV1(LineageContract):
    source_event_id: UUID
    origin: ArtifactVersionRefV1
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")


class InvalidationResultV1(LineageContract):
    contract_version: Literal["1.0"] = "1.0"
    invalidation_event_id: UUID
    source_event_id: UUID
    project_id: str
    origin: ArtifactVersionRefV1
    reason_code: str
    impacts: tuple[ArtifactImpactV1, ...]
