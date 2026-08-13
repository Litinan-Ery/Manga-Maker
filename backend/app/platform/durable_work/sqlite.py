from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from ...shared_kernel import ArtifactRef, Clock, IdFactory, Sha256
from .contracts import (
    EnqueueWorkRequest,
    SafeWorkError,
    WorkAttemptSnapshot,
    WorkAttemptState,
    WorkCommandReference,
    WorkExecutionSafety,
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


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("durable work timestamp is not timezone-aware")
    return _utc(parsed)


class SQLiteDurableWorkAdapter:
    """Transaction-bound SQLite implementation; it never commits its caller's UoW."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock, id_factory: IdFactory) -> None:
        self._connection = connection
        self._clock = clock
        self._id_factory = id_factory

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        self._connection.execute("SAVEPOINT durable_work_operation")
        try:
            yield
        except Exception:
            self._connection.execute("ROLLBACK TO durable_work_operation")
            self._connection.execute("RELEASE durable_work_operation")
            raise
        else:
            self._connection.execute("RELEASE durable_work_operation")

    def enqueue(self, request: EnqueueWorkRequest) -> WorkItemSnapshot:
        with self._atomic():
            existing = self._connection.execute(
                """
                SELECT * FROM work_items
                WHERE project_id = ? AND idempotency_key = ?
                """,
                (request.project_id, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                snapshot = self._item(existing)
                if not self._matches_request(snapshot, request):
                    raise IdempotencyConflictError(
                        "idempotency key is already bound to a different work command"
                    )
                return snapshot

            work_item_id = self._id_factory.new()
            now = _utc(self._clock.now())
            self._connection.execute(
                """
                INSERT INTO work_items(
                    work_item_id, project_id, kind, aggregate_type, aggregate_id,
                    aggregate_version, command_contract, command_contract_version,
                    payload_sha256, idempotency_key, state, revision, attempt_limit,
                    attempts_started, not_before, requires_user_action, execution_safety,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 1, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    str(work_item_id),
                    request.project_id,
                    request.kind,
                    request.command.aggregate_type,
                    request.command.aggregate_id,
                    request.command.aggregate_version,
                    request.command.contract,
                    request.command.contract_version,
                    str(request.command.payload_sha256),
                    request.idempotency_key,
                    request.attempt_limit,
                    _iso(request.not_before),
                    int(request.requires_user_action),
                    request.execution_safety.value,
                    _iso(now),
                    _iso(now),
                ),
            )
            return self.get(work_item_id)

    def get(self, work_item_id: UUID) -> WorkItemSnapshot:
        row = self._connection.execute(
            "SELECT * FROM work_items WHERE work_item_id = ?", (str(work_item_id),)
        ).fetchone()
        if row is None:
            raise WorkItemNotFoundError(work_item_id)
        return self._item(row)

    def start(self, work_item_id: UUID, *, expected_revision: int) -> WorkStartSnapshot:
        with self._atomic():
            current = self.get(work_item_id)
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

            attempt_id = self._id_factory.new()
            ordinal = current.attempts_started + 1
            updated = self._connection.execute(
                """
                UPDATE work_items
                SET state = 'running', revision = revision + 1,
                    attempts_started = attempts_started + 1, updated_at = ?
                WHERE work_item_id = ? AND revision = ? AND state = 'queued'
                    AND attempts_started < attempt_limit
                """,
                (_iso(now), str(work_item_id), expected_revision),
            ).rowcount
            if updated != 1:
                raise WorkRevisionConflictError("work item changed during start")
            self._connection.execute(
                """
                INSERT INTO work_attempts(
                    work_attempt_id, work_item_id, ordinal, state,
                    external_request_started, started_at
                ) VALUES (?, ?, ?, 'running', 0, ?)
                """,
                (str(attempt_id), str(work_item_id), ordinal, _iso(now)),
            )
            attempt_row = self._connection.execute(
                "SELECT * FROM work_attempts WHERE work_attempt_id = ?", (str(attempt_id),)
            ).fetchone()
            assert attempt_row is not None
            return WorkStartSnapshot(self.get(work_item_id), self._attempt(attempt_row))

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
        with self._atomic():
            current = self.get(work_item_id)
            self._assert_revision(current, expected_revision)
            if current.state is not WorkState.RUNNING:
                raise InvalidWorkTransitionError(
                    f"cannot complete work in state {current.state}"
                )
            self._require_running_attempt(work_item_id, work_attempt_id)
            receipt_exists = self._connection.execute(
                """
                SELECT 1 FROM work_handler_receipts
                WHERE work_item_id = ? AND handler_version = ?
                """,
                (str(work_item_id), handler_version),
            ).fetchone()
            if receipt_exists is not None:
                raise HandlerReceiptConflictError("handler version already has a receipt")

            now = _utc(self._clock.now())
            completed_revision = current.revision + 1
            attempt_updated = self._connection.execute(
                """
                UPDATE work_attempts
                SET state = 'completed', finished_at = ?
                WHERE work_attempt_id = ? AND work_item_id = ? AND state = 'running'
                """,
                (_iso(now), str(work_attempt_id), str(work_item_id)),
            ).rowcount
            if attempt_updated != 1:
                raise WorkAttemptMismatchError("work attempt changed during completion")
            item_updated = self._connection.execute(
                """
                UPDATE work_items
                SET state = 'completed', revision = revision + 1,
                    result_artifact_type = ?, result_artifact_id = ?,
                    result_artifact_version = ?, result_content_sha256 = ?,
                    result_schema_version = ?, updated_at = ?
                WHERE work_item_id = ? AND revision = ? AND state = 'running'
                """,
                (
                    result_ref.artifact_type,
                    str(result_ref.artifact_id),
                    result_ref.version,
                    str(result_ref.content_sha256),
                    result_ref.schema_version,
                    _iso(now),
                    str(work_item_id),
                    expected_revision,
                ),
            ).rowcount
            if item_updated != 1:
                raise WorkRevisionConflictError("work item changed during completion")
            self._connection.execute(
                """
                INSERT INTO work_handler_receipts(
                    receipt_id, work_item_id, handler_version, completed_revision,
                    result_content_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(self._id_factory.new()),
                    str(work_item_id),
                    handler_version,
                    completed_revision,
                    str(result_ref.content_sha256),
                    _iso(now),
                ),
            )
            return self.get(work_item_id)

    def fail(
        self,
        work_item_id: UUID,
        *,
        expected_revision: int,
        work_attempt_id: UUID,
        error: SafeWorkError,
        retry_not_before: datetime | None = None,
    ) -> WorkItemSnapshot:
        if retry_not_before is not None and (
            retry_not_before.tzinfo is None or retry_not_before.utcoffset() is None
        ):
            raise ValueError("retry_not_before must be timezone-aware")
        with self._atomic():
            current = self.get(work_item_id)
            self._assert_revision(current, expected_revision)
            if current.state is not WorkState.RUNNING:
                raise InvalidWorkTransitionError(f"cannot fail work in state {current.state}")
            self._require_running_attempt(work_item_id, work_attempt_id)
            now = _utc(self._clock.now())
            can_retry = error.retryable and current.attempts_started < current.attempt_limit
            next_state = WorkState.QUEUED if can_retry else WorkState.FAILED
            next_not_before = _utc(retry_not_before) if retry_not_before is not None else now
            attempt_updated = self._connection.execute(
                """
                UPDATE work_attempts
                SET state = 'failed', safe_error_code = ?, safe_error_message = ?,
                    safe_error_retryable = ?, finished_at = ?
                WHERE work_attempt_id = ? AND work_item_id = ? AND state = 'running'
                """,
                (
                    error.code,
                    error.message,
                    int(error.retryable),
                    _iso(now),
                    str(work_attempt_id),
                    str(work_item_id),
                ),
            ).rowcount
            if attempt_updated != 1:
                raise WorkAttemptMismatchError("work attempt changed during failure")
            item_updated = self._connection.execute(
                """
                UPDATE work_items
                SET state = ?, revision = revision + 1, not_before = ?,
                    last_safe_error_code = ?, last_safe_error_message = ?,
                    last_safe_error_retryable = ?, updated_at = ?
                WHERE work_item_id = ? AND revision = ? AND state = 'running'
                """,
                (
                    next_state.value,
                    _iso(next_not_before),
                    error.code,
                    error.message,
                    int(error.retryable),
                    _iso(now),
                    str(work_item_id),
                    expected_revision,
                ),
            ).rowcount
            if item_updated != 1:
                raise WorkRevisionConflictError("work item changed during failure")
            return self.get(work_item_id)

    def cancel(self, work_item_id: UUID, *, expected_revision: int) -> WorkItemSnapshot:
        with self._atomic():
            current = self.get(work_item_id)
            self._assert_revision(current, expected_revision)
            if current.state not in {WorkState.QUEUED, WorkState.RUNNING}:
                raise InvalidWorkTransitionError(f"cannot cancel work in state {current.state}")
            now = _utc(self._clock.now())
            if current.state is WorkState.RUNNING:
                self._connection.execute(
                    """
                    UPDATE work_attempts SET state = 'canceled', finished_at = ?
                    WHERE work_item_id = ? AND state = 'running'
                    """,
                    (_iso(now), str(work_item_id)),
                )
            updated = self._connection.execute(
                """
                UPDATE work_items
                SET state = 'canceled', revision = revision + 1, updated_at = ?
                WHERE work_item_id = ? AND revision = ? AND state IN ('queued', 'running')
                """,
                (_iso(now), str(work_item_id), expected_revision),
            ).rowcount
            if updated != 1:
                raise WorkRevisionConflictError("work item changed during cancellation")
            return self.get(work_item_id)

    def list_attempts(self, work_item_id: UUID) -> tuple[WorkAttemptSnapshot, ...]:
        self.get(work_item_id)
        rows = self._connection.execute(
            """
            SELECT * FROM work_attempts
            WHERE work_item_id = ? ORDER BY ordinal
            """,
            (str(work_item_id),),
        ).fetchall()
        return tuple(self._attempt(row) for row in rows)

    def list_handler_receipts(
        self, work_item_id: UUID
    ) -> tuple[WorkHandlerReceiptSnapshot, ...]:
        self.get(work_item_id)
        rows = self._connection.execute(
            """
            SELECT * FROM work_handler_receipts
            WHERE work_item_id = ? ORDER BY created_at, receipt_id
            """,
            (str(work_item_id),),
        ).fetchall()
        return tuple(self._receipt(row) for row in rows)

    def _require_running_attempt(
        self, work_item_id: UUID, work_attempt_id: UUID
    ) -> None:
        row = self._connection.execute(
            """
            SELECT state FROM work_attempts
            WHERE work_attempt_id = ? AND work_item_id = ?
            """,
            (str(work_attempt_id), str(work_item_id)),
        ).fetchone()
        if row is None or str(row["state"]) != WorkAttemptState.RUNNING.value:
            raise WorkAttemptMismatchError("attempt is not the running attempt for this work item")

    @staticmethod
    def _assert_revision(current: WorkItemSnapshot, expected_revision: int) -> None:
        if current.revision != expected_revision:
            raise WorkRevisionConflictError(
                f"expected work revision {expected_revision}, current is {current.revision}"
            )

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

    @staticmethod
    def _item(row: sqlite3.Row) -> WorkItemSnapshot:
        error = None
        if row["last_safe_error_code"] is not None:
            error = SafeWorkError(
                code=str(row["last_safe_error_code"]),
                message=str(row["last_safe_error_message"]),
                retryable=bool(row["last_safe_error_retryable"]),
            )
        result_ref = None
        if row["result_artifact_id"] is not None:
            result_ref = ArtifactRef(
                artifact_type=str(row["result_artifact_type"]),
                artifact_id=UUID(str(row["result_artifact_id"])),
                version=int(row["result_artifact_version"]),
                content_sha256=Sha256(str(row["result_content_sha256"])),
                schema_version=str(row["result_schema_version"]),
            )
        return WorkItemSnapshot(
            work_item_id=UUID(str(row["work_item_id"])),
            project_id=str(row["project_id"]),
            kind=str(row["kind"]),
            execution_safety=WorkExecutionSafety(str(row["execution_safety"])),
            idempotency_key=str(row["idempotency_key"]),
            command=WorkCommandReference(
                contract=str(row["command_contract"]),
                contract_version=str(row["command_contract_version"]),
                aggregate_type=str(row["aggregate_type"]),
                aggregate_id=str(row["aggregate_id"]),
                aggregate_version=int(row["aggregate_version"]),
                payload_sha256=Sha256(str(row["payload_sha256"])),
            ),
            state=WorkState(str(row["state"])),
            revision=int(row["revision"]),
            attempt_limit=int(row["attempt_limit"]),
            attempts_started=int(row["attempts_started"]),
            not_before=_datetime(row["not_before"]),
            requires_user_action=bool(row["requires_user_action"]),
            last_safe_error=error,
            result_ref=result_ref,
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> WorkAttemptSnapshot:
        error = None
        if row["safe_error_code"] is not None:
            error = SafeWorkError(
                code=str(row["safe_error_code"]),
                message=str(row["safe_error_message"]),
                retryable=bool(row["safe_error_retryable"]),
            )
        finished = row["finished_at"]
        return WorkAttemptSnapshot(
            work_attempt_id=UUID(str(row["work_attempt_id"])),
            work_item_id=UUID(str(row["work_item_id"])),
            ordinal=int(row["ordinal"]),
            state=WorkAttemptState(str(row["state"])),
            external_request_started=bool(row["external_request_started"]),
            safe_error=error,
            started_at=_datetime(row["started_at"]),
            finished_at=_datetime(finished) if finished is not None else None,
        )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> WorkHandlerReceiptSnapshot:
        return WorkHandlerReceiptSnapshot(
            receipt_id=UUID(str(row["receipt_id"])),
            work_item_id=UUID(str(row["work_item_id"])),
            handler_version=str(row["handler_version"]),
            completed_revision=int(row["completed_revision"]),
            result_content_sha256=Sha256(str(row["result_content_sha256"])),
            created_at=_datetime(row["created_at"]),
        )


class SQLiteWriter(Protocol):
    def writer(self) -> AbstractContextManager[sqlite3.Connection]: ...


@dataclass(frozen=True, slots=True)
class SQLiteDurableWorkSession:
    connection: sqlite3.Connection
    work: SQLiteDurableWorkAdapter


class SQLiteDurableWorkUnitOfWork:
    def __init__(self, database: SQLiteWriter, clock: Clock, id_factory: IdFactory) -> None:
        self._database = database
        self._clock = clock
        self._id_factory = id_factory

    @contextmanager
    def transaction(self) -> Iterator[SQLiteDurableWorkSession]:
        with self._database.writer() as connection:
            yield SQLiteDurableWorkSession(
                connection=connection,
                work=SQLiteDurableWorkAdapter(connection, self._clock, self._id_factory),
            )
