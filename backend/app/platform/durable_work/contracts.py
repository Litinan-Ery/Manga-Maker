from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from ...shared_kernel import ArtifactRef, Sha256

SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SAFE_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
SENSITIVE_ERROR_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "apikey",
    "token=",
    "base64,",
    "/users/",
    "https://",
    "http://",
)


def validate_safe_key(name: str, value: str) -> None:
    if SAFE_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be an opaque identifier of at most 128 characters")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class WorkState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    NEEDS_REVIEW = "needs_review"


class WorkAttemptState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    NEEDS_REVIEW = "needs_review"


class WorkExecutionSafety(StrEnum):
    LOCAL_IDEMPOTENT = "local_idempotent"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


@dataclass(frozen=True, slots=True)
class SafeWorkError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if SAFE_ERROR_CODE_PATTERN.fullmatch(self.code) is None:
            raise ValueError("safe error code must use uppercase snake case")
        if not self.message or len(self.message) > 500 or "\n" in self.message:
            raise ValueError("safe error message must be one line and at most 500 characters")
        lowered = self.message.lower()
        if any(marker in lowered for marker in SENSITIVE_ERROR_MARKERS):
            raise ValueError("safe error message contains a sensitive or absolute-path marker")


@dataclass(frozen=True, slots=True)
class WorkCommandReference:
    contract: str
    contract_version: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload_sha256: Sha256

    def __post_init__(self) -> None:
        validate_safe_key("contract", self.contract)
        validate_safe_key("contract_version", self.contract_version)
        validate_safe_key("aggregate_type", self.aggregate_type)
        validate_safe_key("aggregate_id", self.aggregate_id)
        if self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")


@dataclass(frozen=True, slots=True)
class EnqueueWorkRequest:
    project_id: str
    kind: str
    idempotency_key: str
    command: WorkCommandReference
    attempt_limit: int
    not_before: datetime
    requires_user_action: bool = False
    execution_safety: WorkExecutionSafety = WorkExecutionSafety.LOCAL_IDEMPOTENT

    def __post_init__(self) -> None:
        validate_safe_key("project_id", self.project_id)
        validate_safe_key("kind", self.kind)
        validate_safe_key("idempotency_key", self.idempotency_key)
        if not 1 <= self.attempt_limit <= 100:
            raise ValueError("attempt_limit must be between 1 and 100")
        _require_aware("not_before", self.not_before)


@dataclass(frozen=True, slots=True)
class WorkItemSnapshot:
    work_item_id: UUID
    project_id: str
    kind: str
    execution_safety: WorkExecutionSafety
    idempotency_key: str
    command: WorkCommandReference
    state: WorkState
    revision: int
    attempt_limit: int
    attempts_started: int
    not_before: datetime
    requires_user_action: bool
    last_safe_error: SafeWorkError | None
    result_ref: ArtifactRef | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WorkAttemptSnapshot:
    work_attempt_id: UUID
    work_item_id: UUID
    ordinal: int
    state: WorkAttemptState
    external_request_started: bool
    safe_error: SafeWorkError | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkStartSnapshot:
    item: WorkItemSnapshot
    attempt: WorkAttemptSnapshot


@dataclass(frozen=True, slots=True)
class WorkHandlerReceiptSnapshot:
    receipt_id: UUID
    work_item_id: UUID
    handler_version: str
    completed_revision: int
    result_content_sha256: Sha256
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkLeaseSnapshot:
    lease_id: UUID
    work_item_id: UUID
    work_attempt_id: UUID
    lease_owner: str
    lease_token: UUID
    lease_revision: int
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LeasedWorkSnapshot:
    item: WorkItemSnapshot
    attempt: WorkAttemptSnapshot
    lease: WorkLeaseSnapshot
