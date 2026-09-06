from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextPolicy(Contract):
    version: Literal["context-budget-1"] = "context-budget-1"
    checkpoint_ratio: float = Field(default=0.5, gt=0, lt=1)
    compact_ratio: float = Field(default=0.6, gt=0, lt=1)
    stop_ratio: float = Field(default=0.7, gt=0, lt=1)
    request_ratio: float = Field(default=0.8, gt=0, lt=1)
    recovered_ratio: float = Field(default=0.4, gt=0, lt=1)
    output_reserve: int = Field(default=16_000, ge=1)
    output_reserve_ratio: float = Field(default=0.1, gt=0, lt=1)
    aggregate_text_tokens: int = Field(default=6000, ge=512, le=6000)
    single_result_tokens: int = Field(default=2000, ge=128, le=2000)
    images_per_call: int = Field(default=2, ge=1, le=2)
    images_per_window: int = Field(default=12, ge=1, le=12)

    @model_validator(mode="after")
    def ordered_watermarks(self) -> ContextPolicy:
        if not (
            self.recovered_ratio
            < self.checkpoint_ratio
            < self.compact_ratio
            < self.stop_ratio
            < self.request_ratio
        ):
            raise ValueError("context watermarks must be strictly ordered")
        return self


class ContextObservation(Contract):
    window_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(default="unknown", min_length=1, max_length=128)
    context_limit: int | None = Field(default=None, gt=0)
    current_tokens: int | None = Field(default=None, ge=0)
    counted_through: int = Field(default=0, ge=0)
    images_in_window: int = Field(default=0, ge=0)
    measurement: Literal["actual", "estimated", "stale", "unknown"] = "unknown"
    observed_at: str = Field(default="", max_length=80)


class InputEvent(Contract):
    sequence: int = Field(ge=1)
    text_tokens: int = Field(default=0, ge=0)
    image_tokens: int | None = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)


class BudgetDecision(Contract):
    action: Literal["continue", "checkpoint", "compact", "handoff"]
    reason: str
    policy_version: str
    measurement: str
    current_tokens: int | None
    projected_tokens: int | None
    available_input_tokens: int
    counted_through: int


def token_upper_bound(text: str) -> int:
    """Conservative UTF-8 byte bound, not a claim of model-exact tokenization."""
    return len(text.encode("utf-8"))


def decide_budget(
    observation: ContextObservation,
    pending: tuple[InputEvent, ...] = (),
    *,
    planned_output: int = 0,
    policy: ContextPolicy | None = None,
) -> BudgetDecision:
    policy = policy or ContextPolicy()
    if planned_output < 0:
        raise ValueError("planned output must be nonnegative")
    sequences = [event.sequence for event in pending]
    if len(sequences) != len(set(sequences)):
        raise ValueError("input event sequences must be unique")
    fresh = [event for event in pending if event.sequence > observation.counted_through]
    through = max([observation.counted_through, *(event.sequence for event in fresh)])
    current, window = observation.current_tokens, observation.context_limit
    reserve = max(
        policy.output_reserve, int((window or 0) * policy.output_reserve_ratio), planned_output
    )
    delta = sum(event.text_tokens + (event.image_tokens or 0) for event in fresh)
    projected = None if current is None else current + delta + reserve
    available = max(0, int((window or 0) * policy.request_ratio) - (current or 0) - reserve)

    def result(
        action: Literal["continue", "checkpoint", "compact", "handoff"], reason: str
    ) -> BudgetDecision:
        return BudgetDecision(
            action=action,
            reason=reason,
            policy_version=policy.version,
            measurement=observation.measurement,
            current_tokens=current,
            projected_tokens=projected,
            available_input_tokens=available,
            counted_through=through,
        )

    if window is None or current is None or observation.measurement == "unknown":
        return result("handoff", "CONTEXT_USAGE_UNKNOWN")
    if observation.measurement == "stale":
        return result("checkpoint", "CONTEXT_OBSERVATION_STALE")
    if any(event.images and event.image_tokens is None for event in fresh):
        return result("checkpoint", "IMAGE_USAGE_UNKNOWN")
    if sum(event.images for event in fresh) > policy.images_per_call:
        return result("checkpoint", "IMAGE_BATCH_TOO_LARGE")
    if current >= window * policy.stop_ratio:
        return result("handoff", "CONTEXT_STOP_WATERMARK")
    if projected is not None and projected > window * policy.request_ratio:
        return result("handoff", "CONTEXT_OUTPUT_RESERVE_INSUFFICIENT")
    if current >= window * policy.compact_ratio:
        return result("compact", "CONTEXT_COMPACT_WATERMARK")
    if (
        observation.images_in_window + sum(event.images for event in fresh)
        >= policy.images_per_window
    ):
        return result("compact", "CONTEXT_IMAGE_WINDOW_LIMIT")
    if current >= window * policy.checkpoint_ratio:
        return result("checkpoint", "CONTEXT_CHECKPOINT_WATERMARK")
    return result("continue", "CONTEXT_BUDGET_AVAILABLE")
