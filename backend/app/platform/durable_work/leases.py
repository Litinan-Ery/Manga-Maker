from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ...shared_kernel import Clock, IdFactory
from .contracts import (
    LeasedWorkSnapshot,
    SafeWorkError,
    WorkAttemptSnapshot,
    WorkAttemptState,
    WorkExecutionSafety,
    WorkItemSnapshot,
    WorkLeaseSnapshot,
    WorkState,
    validate_safe_key,
)
from .errors import WorkLeaseLostError, WorkRevisionConflictError
from .retry import RetryPolicy
from .sqlite import SQLiteDurableWorkAdapter


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("durable work timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("lease timestamp is not timezone-aware")
    return _utc(parsed)


def _validate_duration(value: timedelta) -> None:
    if value <= timedelta(0) or value > timedelta(minutes=15):
        raise ValueError("lease duration must be positive and no longer than 15 minutes")


class SQLiteWorkLeaseAdapter:
    """CAS lease coordination bound to the same SQLite UnitOfWork as work state."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        work: SQLiteDurableWorkAdapter,
        clock: Clock,
        id_factory: IdFactory,
        retry_policy: RetryPolicy,
    ) -> None:
        self._connection = connection
        self._work = work
        self._clock = clock
        self._id_factory = id_factory
        self._retry_policy = retry_policy

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        self._connection.execute("SAVEPOINT durable_work_lease_operation")
        try:
            yield
        except Exception:
            self._connection.execute("ROLLBACK TO durable_work_lease_operation")
            self._connection.execute("RELEASE durable_work_lease_operation")
            raise
        else:
            self._connection.execute("RELEASE durable_work_lease_operation")

    def claim_next(
        self, lease_owner: str, lease_duration: timedelta
    ) -> LeasedWorkSnapshot | None:
        validate_safe_key("lease_owner", lease_owner)
        _validate_duration(lease_duration)
        with self._atomic():
            now = _utc(self._clock.now())
            row = self._connection.execute(
                """
                SELECT w.work_item_id, w.revision
                FROM work_items w
                LEFT JOIN worker_leases l ON l.work_item_id = w.work_item_id
                WHERE w.state = 'queued' AND w.requires_user_action = 0
                    AND w.not_before <= ? AND w.attempts_started < w.attempt_limit
                    AND l.work_item_id IS NULL
                ORDER BY w.created_at, w.work_item_id
                LIMIT 1
                """,
                (_iso(now),),
            ).fetchone()
            if row is None:
                return None

            work_item_id = UUID(str(row["work_item_id"]))
            started = self._work.start(work_item_id, expected_revision=int(row["revision"]))
            attempt = started.attempt
            if started.item.execution_safety is WorkExecutionSafety.EXTERNAL_SIDE_EFFECT:
                self._connection.execute(
                    """
                    UPDATE work_attempts SET external_request_started = 1
                    WHERE work_attempt_id = ? AND state = 'running'
                    """,
                    (str(attempt.work_attempt_id),),
                )
                attempt = next(
                    candidate
                    for candidate in self._work.list_attempts(work_item_id)
                    if candidate.work_attempt_id == attempt.work_attempt_id
                )

            lease_id = self._id_factory.new()
            lease_token = self._id_factory.new()
            expires_at = now + lease_duration
            self._connection.execute(
                """
                INSERT INTO worker_leases(
                    lease_id, work_item_id, work_attempt_id, lease_owner, lease_token,
                    lease_revision, acquired_at, renewed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    str(lease_id),
                    str(work_item_id),
                    str(attempt.work_attempt_id),
                    lease_owner,
                    str(lease_token),
                    _iso(now),
                    _iso(now),
                    _iso(expires_at),
                ),
            )
            lease = self._lease_by_token(lease_token, require_active=True)
            return LeasedWorkSnapshot(started.item, attempt, lease)

    def renew(self, lease_token: UUID, lease_duration: timedelta) -> WorkLeaseSnapshot:
        _validate_duration(lease_duration)
        with self._atomic():
            current = self._lease_by_token(lease_token, require_active=True)
            now = _utc(self._clock.now())
            updated = self._connection.execute(
                """
                UPDATE worker_leases
                SET lease_revision = lease_revision + 1, renewed_at = ?, expires_at = ?
                WHERE lease_token = ? AND lease_revision = ? AND expires_at > ?
                """,
                (
                    _iso(now),
                    _iso(now + lease_duration),
                    str(lease_token),
                    current.lease_revision,
                    _iso(now),
                ),
            ).rowcount
            if updated != 1:
                raise WorkLeaseLostError("lease changed or expired during renewal")
            return self._lease_by_token(lease_token, require_active=True)

    def verify(self, lease_token: UUID) -> WorkLeaseSnapshot:
        return self._lease_by_token(lease_token, require_active=True)

    def mark_external_request_started(self, lease_token: UUID) -> WorkAttemptSnapshot:
        with self._atomic():
            lease = self._lease_by_token(lease_token, require_active=True)
            updated = self._connection.execute(
                """
                UPDATE work_attempts SET external_request_started = 1
                WHERE work_attempt_id = ? AND work_item_id = ? AND state = 'running'
                """,
                (str(lease.work_attempt_id), str(lease.work_item_id)),
            ).rowcount
            if updated != 1:
                raise WorkLeaseLostError("leased attempt is no longer running")
            return next(
                attempt
                for attempt in self._work.list_attempts(lease.work_item_id)
                if attempt.work_attempt_id == lease.work_attempt_id
            )

    def move_to_needs_review(
        self, lease_token: UUID, error: SafeWorkError
    ) -> WorkItemSnapshot:
        with self._atomic():
            lease = self._lease_by_token(lease_token, require_active=True)
            current = self._work.get(lease.work_item_id)
            if current.state is not WorkState.RUNNING:
                raise WorkLeaseLostError("leased work item is no longer running")
            now = _utc(self._clock.now())
            attempt_updated = self._connection.execute(
                """
                UPDATE work_attempts
                SET state = 'needs_review', safe_error_code = ?, safe_error_message = ?,
                    safe_error_retryable = ?, finished_at = ?
                WHERE work_attempt_id = ? AND state = 'running'
                """,
                (
                    error.code,
                    error.message,
                    int(error.retryable),
                    _iso(now),
                    str(lease.work_attempt_id),
                ),
            ).rowcount
            if attempt_updated != 1:
                raise WorkLeaseLostError("leased attempt is no longer running")
            item_updated = self._connection.execute(
                """
                UPDATE work_items
                SET state = 'needs_review', revision = revision + 1,
                    last_safe_error_code = ?, last_safe_error_message = ?,
                    last_safe_error_retryable = ?, updated_at = ?
                WHERE work_item_id = ? AND revision = ? AND state = 'running'
                """,
                (
                    error.code,
                    error.message,
                    int(error.retryable),
                    _iso(now),
                    str(lease.work_item_id),
                    current.revision,
                ),
            ).rowcount
            if item_updated != 1:
                raise WorkRevisionConflictError("work item changed while marking needs_review")
            self._delete_lease(lease_token)
            return self._work.get(lease.work_item_id)

    def release(self, lease_token: UUID) -> None:
        with self._atomic():
            self._delete_lease(lease_token)

    def recover_expired(self) -> tuple[WorkItemSnapshot, ...]:
        recovered: list[WorkItemSnapshot] = []
        with self._atomic():
            now = _utc(self._clock.now())
            rows = self._connection.execute(
                """
                SELECT l.lease_token, l.work_item_id, l.work_attempt_id,
                       a.external_request_started
                FROM worker_leases l
                JOIN work_attempts a ON a.work_attempt_id = l.work_attempt_id
                WHERE l.expires_at <= ?
                ORDER BY l.expires_at, l.lease_id
                """,
                (_iso(now),),
            ).fetchall()
            for row in rows:
                lease_token = UUID(str(row["lease_token"]))
                work_item_id = UUID(str(row["work_item_id"]))
                attempt_id = UUID(str(row["work_attempt_id"]))
                current = self._work.get(work_item_id)
                if current.state is not WorkState.RUNNING:
                    self._delete_lease(lease_token)
                    continue
                if bool(row["external_request_started"]):
                    next_state = WorkState.NEEDS_REVIEW
                    attempt_state = WorkAttemptState.NEEDS_REVIEW
                    error = SafeWorkError(
                        "EXTERNAL_RESULT_UNKNOWN",
                        "外部请求结果未知，需要人工检查。",
                    )
                    next_not_before = current.not_before
                else:
                    can_retry = current.attempts_started < current.attempt_limit
                    next_state = WorkState.QUEUED if can_retry else WorkState.FAILED
                    attempt_state = WorkAttemptState.FAILED
                    error = SafeWorkError(
                        "LEASE_EXPIRED",
                        "本地工作租约已过期。",
                        retryable=True,
                    )
                    next_not_before = now + self._retry_policy.delay_for(
                        current.attempts_started
                    )

                self._connection.execute(
                    """
                    UPDATE work_attempts
                    SET state = ?, safe_error_code = ?, safe_error_message = ?,
                        safe_error_retryable = ?, finished_at = ?
                    WHERE work_attempt_id = ? AND state = 'running'
                    """,
                    (
                        attempt_state.value,
                        error.code,
                        error.message,
                        int(error.retryable),
                        _iso(now),
                        str(attempt_id),
                    ),
                )
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
                        current.revision,
                    ),
                ).rowcount
                if item_updated != 1:
                    raise WorkRevisionConflictError(
                        "work item changed during expired lease recovery"
                    )
                self._delete_lease(lease_token)
                recovered.append(self._work.get(work_item_id))
        return tuple(recovered)

    def _lease_by_token(
        self, lease_token: UUID, *, require_active: bool
    ) -> WorkLeaseSnapshot:
        row = self._connection.execute(
            "SELECT * FROM worker_leases WHERE lease_token = ?", (str(lease_token),)
        ).fetchone()
        if row is None:
            raise WorkLeaseLostError("worker lease does not exist")
        lease = self._lease(row)
        if require_active and lease.expires_at <= _utc(self._clock.now()):
            raise WorkLeaseLostError("worker lease has expired")
        return lease

    def _delete_lease(self, lease_token: UUID) -> None:
        deleted = self._connection.execute(
            "DELETE FROM worker_leases WHERE lease_token = ?", (str(lease_token),)
        ).rowcount
        if deleted != 1:
            raise WorkLeaseLostError("worker lease was already lost")

    @staticmethod
    def _lease(row: sqlite3.Row) -> WorkLeaseSnapshot:
        return WorkLeaseSnapshot(
            lease_id=UUID(str(row["lease_id"])),
            work_item_id=UUID(str(row["work_item_id"])),
            work_attempt_id=UUID(str(row["work_attempt_id"])),
            lease_owner=str(row["lease_owner"]),
            lease_token=UUID(str(row["lease_token"])),
            lease_revision=int(row["lease_revision"]),
            acquired_at=_datetime(row["acquired_at"]),
            renewed_at=_datetime(row["renewed_at"]),
            expires_at=_datetime(row["expires_at"]),
        )
