from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from ...shared_kernel import ArtifactRef, Clock, IdFactory
from .contracts import (
    EnqueueWorkRequest,
    SafeWorkError,
    WorkAttemptSnapshot,
    WorkAttemptState,
    WorkHandlerReceiptSnapshot,
    WorkItemSnapshot,
    WorkStartSnapshot,
    WorkState,
    validate_safe_key,
)
from .errors import (
    HandlerReceiptConflictError,
    IdempotencyConflictError,
    InvalidWorkTransitionError,
    WorkAttemptLimitExceededError,
    WorkAttemptMismatchError,
    WorkItemNotFoundError,
    WorkNotReadyError,
    WorkRequiresUserActionError,
    WorkRevisionConflictError,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("durable work timestamps must be timezone-aware")
    return value.astimezone(UTC)


class InMemoryDurableWorkAdapter:
    """Deterministic fake that obeys the same CAS and transition contract as SQLite."""

    def __init__(self, clock: Clock, id_factory: IdFactory) -> None:
        self._clock = clock
        self._id_factory = id_factory
        self._items: dict[UUID, WorkItemSnapshot] = {}
        self._idempotency: dict[tuple[str, str], UUID] = {}
        self._attempts: dict[UUID, list[WorkAttemptSnapshot]] = {}
        self._receipts: dict[UUID, list[WorkHandlerReceiptSnapshot]] = {}

    def enqueue(self, request: EnqueueWorkRequest) -> WorkItemSnapshot:
        key = (request.project_id, request.idempotency_key)
        existing_id = self._idempotency.get(key)
        if existing_id is not None:
            existing = self._items[existing_id]
            if not self._matches_request(existing, request):
                raise IdempotencyConflictError(
                    "idempotency key is already bound to a different work command"
                )
            return existing

        now = _utc(self._clock.now())
        item = WorkItemSnapshot(
            work_item_id=self._id_factory.new(),
            project_id=request.project_id,
            kind=request.kind,
            execution_safety=request.execution_safety,
            idempotency_key=request.idempotency_key,
            command=request.command,
            state=WorkState.QUEUED,
            revision=1,
            attempt_limit=request.attempt_limit,
            attempts_started=0,
            not_before=_utc(request.not_before),
            requires_user_action=request.requires_user_action,
            last_safe_error=None,
            result_ref=None,
            created_at=now,
            updated_at=now,
        )
        self._items[item.work_item_id] = item
        self._idempotency[key] = item.work_item_id
        self._attempts[item.work_item_id] = []
        self._receipts[item.work_item_id] = []
        return item

    def get(self, work_item_id: UUID) -> WorkItemSnapshot:
        return self._current(work_item_id)

    def start(self, work_item_id: UUID, *, expected_revision: int) -> WorkStartSnapshot:
        current = self._current(work_item_id)
        self._assert_revision(current, expected_revision)
        if current.attempts_started >= current.attempt_limit:
            raise WorkAttemptLimitExceededError("work item exhausted its attempt limit")
        if current.state is not WorkState.QUEUED:
            raise InvalidWorkTransitionError(f"cannot start work in state {current.state}")
        if current.requires_user_action:
            raise WorkRequiresUserActionError("work item requires an explicit user action")
        now = _utc(self._clock.now())
        if current.not_before > now:
            raise WorkNotReadyError("work item not_before is still in the future")

        attempt = WorkAttemptSnapshot(
            work_attempt_id=self._id_factory.new(),
            work_item_id=work_item_id,
            ordinal=current.attempts_started + 1,
            state=WorkAttemptState.RUNNING,
            external_request_started=False,
            safe_error=None,
            started_at=now,
            finished_at=None,
        )
        updated = replace(
            current,
            state=WorkState.RUNNING,
            revision=current.revision + 1,
            attempts_started=current.attempts_started + 1,
            updated_at=now,
        )
        self._items[work_item_id] = updated
        self._attempts[work_item_id].append(attempt)
        return WorkStartSnapshot(updated, attempt)

    def complete(
        self,
        work_item_id: UUID,
        *,
        expected_revision: int,
        work_attempt_id: UUID,
        result_ref: ArtifactRef,
        handler_version: str,
    ) -> WorkItemSnapshot:
        validate_safe_key("handler_version", handler_version)
        current = self._current(work_item_id)
        self._assert_revision(current, expected_revision)
        if current.state is not WorkState.RUNNING:
            raise InvalidWorkTransitionError(f"cannot complete work in state {current.state}")
        attempt = self._running_attempt(work_item_id, work_attempt_id)
        if any(
            receipt.handler_version == handler_version
            for receipt in self._receipts[work_item_id]
        ):
            raise HandlerReceiptConflictError("handler version already has a receipt")

        now = _utc(self._clock.now())
        completed_revision = current.revision + 1
        self._replace_attempt(
            replace(attempt, state=WorkAttemptState.COMPLETED, finished_at=now)
        )
        updated = replace(
            current,
            state=WorkState.COMPLETED,
            revision=completed_revision,
            result_ref=result_ref,
            updated_at=now,
        )
        self._items[work_item_id] = updated
        self._receipts[work_item_id].append(
            WorkHandlerReceiptSnapshot(
                receipt_id=self._id_factory.new(),
                work_item_id=work_item_id,
                handler_version=handler_version,
                completed_revision=completed_revision,
                result_content_sha256=result_ref.content_sha256,
                created_at=now,
            )
        )
        return updated

    def fail(
        self,
        work_item_id: UUID,
        *,
        expected_revision: int,
        work_attempt_id: UUID,
        error: SafeWorkError,
        retry_not_before: datetime | None = None,
    ) -> WorkItemSnapshot:
        current = self._current(work_item_id)
        self._assert_revision(current, expected_revision)
        if current.state is not WorkState.RUNNING:
            raise InvalidWorkTransitionError(f"cannot fail work in state {current.state}")
        attempt = self._running_attempt(work_item_id, work_attempt_id)
        now = _utc(self._clock.now())
        if retry_not_before is not None and (
            retry_not_before.tzinfo is None or retry_not_before.utcoffset() is None
        ):
            raise ValueError("retry_not_before must be timezone-aware")
        can_retry = error.retryable and current.attempts_started < current.attempt_limit
        next_state = WorkState.QUEUED if can_retry else WorkState.FAILED
        next_not_before = _utc(retry_not_before) if retry_not_before is not None else now
        self._replace_attempt(
            replace(
                attempt,
                state=WorkAttemptState.FAILED,
                safe_error=error,
                finished_at=now,
            )
        )
        updated = replace(
            current,
            state=next_state,
            revision=current.revision + 1,
            not_before=next_not_before,
            last_safe_error=error,
            updated_at=now,
        )
        self._items[work_item_id] = updated
        return updated

    def cancel(self, work_item_id: UUID, *, expected_revision: int) -> WorkItemSnapshot:
        current = self._current(work_item_id)
        self._assert_revision(current, expected_revision)
        if current.state not in {WorkState.QUEUED, WorkState.RUNNING}:
            raise InvalidWorkTransitionError(f"cannot cancel work in state {current.state}")
        now = _utc(self._clock.now())
        if current.state is WorkState.RUNNING:
            running = next(
                attempt
                for attempt in reversed(self._attempts[work_item_id])
                if attempt.state is WorkAttemptState.RUNNING
            )
            self._replace_attempt(
                replace(running, state=WorkAttemptState.CANCELED, finished_at=now)
            )
        updated = replace(
            current,
            state=WorkState.CANCELED,
            revision=current.revision + 1,
            updated_at=now,
        )
        self._items[work_item_id] = updated
        return updated

    def list_attempts(self, work_item_id: UUID) -> tuple[WorkAttemptSnapshot, ...]:
        self._current(work_item_id)
        return tuple(self._attempts[work_item_id])

    def list_handler_receipts(
        self, work_item_id: UUID
    ) -> tuple[WorkHandlerReceiptSnapshot, ...]:
        self._current(work_item_id)
        return tuple(self._receipts[work_item_id])

    def _current(self, work_item_id: UUID) -> WorkItemSnapshot:
        try:
            return self._items[work_item_id]
        except KeyError as exc:
            raise WorkItemNotFoundError(work_item_id) from exc

    @staticmethod
    def _assert_revision(current: WorkItemSnapshot, expected_revision: int) -> None:
        if current.revision != expected_revision:
            raise WorkRevisionConflictError(
                f"expected work revision {expected_revision}, current is {current.revision}"
            )

    def _running_attempt(
        self, work_item_id: UUID, work_attempt_id: UUID
    ) -> WorkAttemptSnapshot:
        for attempt in self._attempts[work_item_id]:
            if attempt.work_attempt_id == work_attempt_id:
                if attempt.state is not WorkAttemptState.RUNNING:
                    break
                return attempt
        raise WorkAttemptMismatchError("attempt is not the running attempt for this work item")

    def _replace_attempt(self, replacement: WorkAttemptSnapshot) -> None:
        attempts = self._attempts[replacement.work_item_id]
        for index, attempt in enumerate(attempts):
            if attempt.work_attempt_id == replacement.work_attempt_id:
                attempts[index] = replacement
                return
        raise WorkAttemptMismatchError("work attempt was not found")

    @staticmethod
    def _matches_request(item: WorkItemSnapshot, request: EnqueueWorkRequest) -> bool:
        return (
            item.project_id == request.project_id
            and item.kind == request.kind
            and item.execution_safety is request.execution_safety
            and item.idempotency_key == request.idempotency_key
            and item.command == request.command
            and item.attempt_limit == request.attempt_limit
            and item.not_before == _utc(request.not_before)
            and item.requires_user_action == request.requires_user_action
        )
