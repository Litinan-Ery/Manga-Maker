from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TextExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TokenBudget(TextExecutionContract):
    context_window_tokens: int = Field(ge=1)
    reserved_output_tokens: int = Field(ge=1)
    planned_input_tokens: int = Field(ge=0)
    hard_constraint_tokens: int = Field(ge=0)
    status: Literal["fits", "exceeds"]

    @model_validator(mode="after")
    def status_matches_numbers(self) -> TokenBudget:
        fits = self.planned_input_tokens + self.reserved_output_tokens <= self.context_window_tokens
        if fits != (self.status == "fits"):
            raise ValueError("token budget status must match the declared token counts")
        if self.hard_constraint_tokens > self.planned_input_tokens:
            raise ValueError("hard constraint tokens cannot exceed planned input tokens")
        return self


class TruncationItem(TextExecutionContract):
    field_path: str = Field(min_length=1, max_length=300)
    policy: Literal["preserved", "summarized", "omitted", "rejected"]
    original_tokens: int = Field(ge=0)
    retained_tokens: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def retained_does_not_grow(self) -> TruncationItem:
        if self.retained_tokens > self.original_tokens:
            raise ValueError("truncation cannot retain more tokens than the original field")
        return self


class TruncationReport(TextExecutionContract):
    schema_version: Literal["1.0"] = "1.0"
    report_id: UUID
    version: int = Field(ge=1)
    items: list[TruncationItem] = Field(default_factory=list, max_length=1000)
    hard_constraints_preserved: bool
    content_sha256: Sha256


class TextStageRun(TextExecutionContract):
    schema_version: Literal["1.0"] = "1.0"
    text_stage_run_id: UUID
    version: int = Field(ge=1)
    chapter_id: UUID
    stage: Literal["adaptation", "world_bible", "character_tags", "prompt_plan"]
    state: Literal[
        "planned",
        "running",
        "completed",
        "failed",
        "needs_review",
        "cancelled",
    ]
    shard_index: int = Field(ge=0)
    shard_count: int = Field(ge=1)
    capability_snapshot_id: UUID
    capability_snapshot_sha256: Sha256
    token_budget: TokenBudget
    prompt_template_version: str = Field(min_length=1, max_length=100)
    input_sha256: Sha256
    output_sha256: Sha256 | None = None
    checkpoint_id: UUID | None = None
    truncation_report: TruncationReport
    content_sha256: Sha256

    @model_validator(mode="after")
    def valid_stage_state(self) -> TextStageRun:
        if self.shard_index >= self.shard_count:
            raise ValueError("shard index must be smaller than shard count")
        if self.state == "completed" and self.output_sha256 is None:
            raise ValueError("completed text stage runs require an output hash")
        if self.state != "completed" and self.output_sha256 is not None:
            raise ValueError("only completed text stage runs may expose an output hash")
        if not self.truncation_report.hard_constraints_preserved and self.state == "completed":
            raise ValueError("a run that lost hard constraints cannot be completed")
        if self.token_budget.status == "exceeds" and self.state in {"running", "completed"}:
            raise ValueError("over-budget text runs cannot start")
        return self
