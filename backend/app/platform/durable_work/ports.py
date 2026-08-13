from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from ...shared_kernel import ArtifactRef
from .contracts import (
    EnqueueWorkRequest,
    LeasedWorkSnapshot,
    SafeWorkError,
    WorkAttemptSnapshot,
    WorkHandlerReceiptSnapshot,
    WorkItemSnapshot,
    WorkLeaseSnapshot,
    WorkStartSnapshot,
)


class DurableWorkPort(Protocol):
    def enqueue(self, request: EnqueueWorkRequest) -> WorkItemSnapshot: ...

    def get(self, work_item_id: UUID) -> WorkItemSnapshot: ...

    def start(self, work_item_id: UUID, *, expected_revision: int) -> WorkStartSnapshot: ...

    def complete(
        self,
        work_item_id: UUID,
        *,
        expected_revision: int,
        work_attempt_id: UUID,
        result_ref: ArtifactRef,
        handler_version: str,
    ) -> WorkItemSnapshot: ...

    def fail(
        self,
        work_item_id: UUID,
        *,
        expected_revision: int,
        work_attempt_id: UUID,
        error: SafeWorkError,
        retry_not_before: datetime | None = None,
    ) -> WorkItemSnapshot: ...

    def cancel(self, work_item_id: UUID, *, expected_revision: int) -> WorkItemSnapshot: ...

    def list_attempts(self, work_item_id: UUID) -> tuple[WorkAttemptSnapshot, ...]: ...

    def list_handler_receipts(
        self, work_item_id: UUID
    ) -> tuple[WorkHandlerReceiptSnapshot, ...]: ...


class WorkLeasePort(Protocol):
    def claim_next(
        self, lease_owner: str, lease_duration: timedelta
    ) -> LeasedWorkSnapshot | None: ...

    def renew(self, lease_token: UUID, lease_duration: timedelta) -> WorkLeaseSnapshot: ...

    def verify(self, lease_token: UUID) -> WorkLeaseSnapshot: ...

    def mark_external_request_started(self, lease_token: UUID) -> WorkAttemptSnapshot: ...

    def move_to_needs_review(
        self, lease_token: UUID, error: SafeWorkError
    ) -> WorkItemSnapshot: ...

    def release(self, lease_token: UUID) -> None: ...

    def recover_expired(self) -> tuple[WorkItemSnapshot, ...]: ...
