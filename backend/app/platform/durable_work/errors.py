from __future__ import annotations

from uuid import UUID


class DurableWorkError(RuntimeError):
    code = "DURABLE_WORK_ERROR"


class WorkItemNotFoundError(DurableWorkError):
    code = "WORK_ITEM_NOT_FOUND"

    def __init__(self, work_item_id: UUID) -> None:
        super().__init__(f"work item {work_item_id} was not found")


class IdempotencyConflictError(DurableWorkError):
    code = "WORK_IDEMPOTENCY_CONFLICT"


class WorkRevisionConflictError(DurableWorkError):
    code = "WORK_REVISION_CONFLICT"


class InvalidWorkTransitionError(DurableWorkError):
    code = "WORK_STATE_INVALID"


class WorkAttemptLimitExceededError(DurableWorkError):
    code = "WORK_ATTEMPT_LIMIT_EXCEEDED"


class WorkNotReadyError(DurableWorkError):
    code = "WORK_NOT_READY"


class WorkRequiresUserActionError(DurableWorkError):
    code = "WORK_REQUIRES_USER_ACTION"


class WorkAttemptMismatchError(DurableWorkError):
    code = "WORK_ATTEMPT_MISMATCH"


class HandlerReceiptConflictError(DurableWorkError):
    code = "WORK_HANDLER_RECEIPT_CONFLICT"


class WorkLeaseLostError(DurableWorkError):
    code = "WORK_LEASE_LOST"
