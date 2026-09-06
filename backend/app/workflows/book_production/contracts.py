from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...platform.context_budget import ContextObservation

PAGE_REVIEW_RULE_VERSION = "page-review-1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactPointer(Contract):
    kind: Literal["source", "generation", "page", "evidence"]
    artifact_id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewRecord(Contract):
    result: Literal["pending", "accepted", "needs_fix"] = "pending"
    scope: str = Field(min_length=1, max_length=240)
    rule_version: str = Field(min_length=1, max_length=80)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    render_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_id: UUID | None = None
    plan_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    renderer_version: str | None = Field(default=None, min_length=1, max_length=80)
    font_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class UnitReference(Contract):
    unit_id: str = Field(min_length=1, max_length=100)
    page_number: int = Field(ge=1, le=10000)
    generation_id: UUID | None = None
    plan_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    page_id: UUID | None = None
    review: ReviewRecord | None = None

    @model_validator(mode="after")
    def frozen_generation(self) -> UnitReference:
        if (self.generation_id is None) != (self.plan_sha256 is None):
            raise ValueError("generation ID and frozen plan hash must be supplied together")
        return self


class TaskSnapshot(Contract):
    schema_version: Literal["1.0"] = "1.0"
    objective: str = Field(min_length=1, max_length=2000)
    constraints: tuple[str, ...] = Field(default=(), max_length=40)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=40)
    stage: str = Field(min_length=1, max_length=80)
    current_unit: str | None = Field(default=None, max_length=100)
    next_action: str = Field(min_length=1, max_length=1000)
    source_refs: tuple[ArtifactPointer, ...] = Field(default=(), max_length=100)
    units: tuple[UnitReference, ...] = Field(default=(), max_length=1000)
    open_questions: tuple[str, ...] = Field(default=(), max_length=40)
    check_refs: tuple[UUID, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def bounded_unique(self) -> TaskSnapshot:
        for values in (self.constraints, self.acceptance_criteria, self.open_questions):
            if any(not value.strip() or len(value) > 1000 for value in values):
                raise ValueError("checkpoint statements must contain 1-1000 characters")
        ids = [unit.unit_id for unit in self.units]
        pages = [unit.page_number for unit in self.units]
        if len(ids) != len(set(ids)) or len(pages) != len(set(pages)):
            raise ValueError("unit IDs and page numbers must be unique")
        if self.current_unit is not None and self.current_unit not in ids:
            raise ValueError("current unit must exist in checkpoint")
        return self


class Lease(Contract):
    writer_id: str = Field(min_length=1, max_length=80)
    fencing_token: int = Field(ge=1)
    expires_at: float
    revision: int = Field(ge=0)


class RuntimeState(Contract):
    project_id: UUID
    run_id: UUID
    revision: int
    state: Literal[
        "collecting", "checkpointing", "compacting", "backoff", "handoff_required", "rehydrating"
    ] = "collecting"
    context_epoch: int = 0
    compaction_attempts: int = 0
    retry_at: float = 0
    last_error: str | None = None
    observation: ContextObservation | None = None
    latest_checkpoint_id: UUID | None = None
    writer_id: str | None = None
    fencing_token: int = 0
    lease_expiry: float = 0


class StoredCheckpoint(Contract):
    checkpoint_id: UUID
    revision: int
    sha256: str
    snapshot: TaskSnapshot
