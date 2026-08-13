from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..layout.contracts import NormalizedRect

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PanelCandidate(ReviewContract):
    schema_version: Literal["1.0"] = "1.0"
    candidate_id: UUID
    version: int = Field(ge=1)
    candidate_set_id: UUID
    panel_id: UUID
    asset_version_id: UUID
    generation_target_sha256: Sha256
    asset_sha256: Sha256
    state: Literal["ready", "stale", "withdrawn"] = "ready"
    content_sha256: Sha256


class PanelCandidateSet(ReviewContract):
    schema_version: Literal["1.0"] = "1.0"
    candidate_set_id: UUID
    version: int = Field(ge=1)
    panel_id: UUID
    generation_target_sha256: Sha256
    candidate_ids: list[UUID] = Field(min_length=1, max_length=20)
    content_sha256: Sha256

    @model_validator(mode="after")
    def unique_candidate_ids(self) -> PanelCandidateSet:
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("candidate set entries must be unique")
        return self


class FindingWaiver(ReviewContract):
    user_action_id: UUID
    reason: str = Field(min_length=1, max_length=1000)
    waived_evidence_sha256: Sha256


class QualityFinding(ReviewContract):
    schema_version: Literal["1.0"] = "1.0"
    quality_finding_id: UUID
    version: int = Field(ge=1)
    candidate_id: UUID
    rule_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Z][A-Z0-9_]*$")
    rule_version: str = Field(min_length=1, max_length=50)
    severity: Literal["blocker", "warning", "info"]
    region: NormalizedRect | None = None
    confidence: float = Field(ge=0, le=1)
    status: Literal["open", "resolved", "waived"]
    waiver: FindingWaiver | None = None
    evidence_sha256: Sha256
    content_sha256: Sha256

    @model_validator(mode="after")
    def valid_waiver(self) -> QualityFinding:
        if (self.status == "waived") != (self.waiver is not None):
            raise ValueError(
                "waived findings require waiver evidence and only waived findings may have it"
            )
        return self


class ReviewDecision(ReviewContract):
    schema_version: Literal["1.0"] = "1.0"
    review_decision_id: UUID
    version: int = Field(ge=1)
    candidate_set_id: UUID
    asset_version_id: UUID
    decision: Literal["accepted", "rejected", "needs_fix"]
    dependency_sha256: Sha256
    user_action_id: UUID
    reason: str = Field(min_length=1, max_length=1000)
    content_sha256: Sha256


class PageApproval(ReviewContract):
    schema_version: Literal["1.0"] = "1.0"
    page_approval_id: UUID
    version: int = Field(ge=1)
    page_id: UUID
    page_version_id: UUID
    dependency_sha256: Sha256
    finding_snapshot_sha256: Sha256
    state: Literal["valid", "stale", "revoked"]
    stale_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    content_sha256: Sha256

    @model_validator(mode="after")
    def valid_stale_reason(self) -> PageApproval:
        if self.state == "stale" and self.stale_reason is None:
            raise ValueError("stale page approval requires a stale reason")
        if self.state != "stale" and self.stale_reason is not None:
            raise ValueError("only stale page approval may contain a stale reason")
        return self


class ReviewSnapshot(ReviewContract):
    schema_version: Literal["1.0"] = "1.0"
    review_snapshot_id: UUID
    version: int = Field(ge=1)
    candidate_set: PanelCandidateSet
    candidates: list[PanelCandidate] = Field(min_length=1, max_length=20)
    quality_findings: list[QualityFinding] = Field(default_factory=list, max_length=500)
    decision_history: list[ReviewDecision] = Field(default_factory=list, max_length=500)
    page_approval: PageApproval | None = None
    content_sha256: Sha256

    @model_validator(mode="after")
    def valid_references(self) -> ReviewSnapshot:
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("review candidate ids must be unique")
        if set(candidate_ids) != set(self.candidate_set.candidate_ids):
            raise ValueError("review candidates must exactly match the candidate set")
        for candidate in self.candidates:
            if (
                candidate.candidate_set_id != self.candidate_set.candidate_set_id
                or candidate.panel_id != self.candidate_set.panel_id
                or candidate.generation_target_sha256
                != self.candidate_set.generation_target_sha256
            ):
                raise ValueError("review candidate metadata must match its candidate set")
        if any(finding.candidate_id not in set(candidate_ids) for finding in self.quality_findings):
            raise ValueError("quality finding references an unknown candidate")
        if any(
            decision.candidate_set_id != self.candidate_set.candidate_set_id
            for decision in self.decision_history
        ):
            raise ValueError("review decision references an unknown candidate set")
        decision_ids = [decision.review_decision_id for decision in self.decision_history]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("review decision ids must be unique")
        return self
