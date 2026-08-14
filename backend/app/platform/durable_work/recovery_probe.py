from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ...shared_kernel import Clock, IdFactory, Sha256
from ..recovery.contracts import ProbeFinding, RecoverySeverity, RecoveryTrigger
from .outbox import (
    OutboxEventRequest,
    SafeEventAttribute,
    SQLiteOutboxAdapter,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recovery timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


class DurableRuntimeIntegrityProbe:
    name = "durable_work"

    def __init__(self, clock: Clock, id_factory: IdFactory) -> None:
        self._clock = clock
        self._id_factory = id_factory

    def reconcile(
        self, connection: sqlite3.Connection, trigger: RecoveryTrigger
    ) -> None:
        if trigger is not RecoveryTrigger.STARTUP:
            return
        now = _utc(self._clock.now())
        expired = connection.execute(
            """
            SELECT l.lease_token, l.work_item_id, l.work_attempt_id,
                   a.external_request_started, w.revision
            FROM worker_leases l
            JOIN work_attempts a ON a.work_attempt_id = l.work_attempt_id
            JOIN work_items w ON w.work_item_id = l.work_item_id
            WHERE l.expires_at <= ? AND w.state = 'running'
            ORDER BY l.expires_at, l.lease_id
            """,
            (_iso(now),),
        ).fetchall()
        for row in expired:
            self._fail_closed_running(
                connection,
                work_item_id=str(row["work_item_id"]),
                work_attempt_id=str(row["work_attempt_id"]),
                external_request_started=bool(row["external_request_started"]),
                revision=int(row["revision"]),
                now=now,
                error_code=(
                    "EXTERNAL_RESULT_UNKNOWN"
                    if bool(row["external_request_started"])
                    else "LEASE_EXPIRED"
                ),
            )
            connection.execute(
                "DELETE FROM worker_leases WHERE lease_token = ?",
                (str(row["lease_token"]),),
            )

        orphaned = connection.execute(
            """
            SELECT w.work_item_id, w.revision, a.work_attempt_id,
                   COALESCE(a.external_request_started, 0) AS external_request_started
            FROM work_items w
            LEFT JOIN work_attempts a
              ON a.work_item_id = w.work_item_id AND a.state = 'running'
            LEFT JOIN worker_leases l ON l.work_item_id = w.work_item_id
            WHERE w.state = 'running' AND l.work_item_id IS NULL
            ORDER BY w.created_at, w.work_item_id
            """
        ).fetchall()
        for row in orphaned:
            attempt_id = row["work_attempt_id"]
            self._fail_closed_running(
                connection,
                work_item_id=str(row["work_item_id"]),
                work_attempt_id=(str(attempt_id) if attempt_id is not None else None),
                external_request_started=bool(row["external_request_started"]),
                revision=int(row["revision"]),
                now=now,
                error_code=(
                    "EXTERNAL_RESULT_UNKNOWN"
                    if bool(row["external_request_started"])
                    else "PROCESS_RESTARTED"
                ),
            )

        connection.execute(
            """
            UPDATE work_items
            SET requires_user_action = 1, revision = revision + 1,
                last_safe_error_code = 'PROCESS_RESTARTED',
                last_safe_error_message = '应用重启后需要显式恢复本地工作。',
                last_safe_error_retryable = 1, updated_at = ?
            WHERE state = 'queued' AND requires_user_action = 0
            """,
            (_iso(now),),
        )

    def inspect(self, connection: sqlite3.Connection) -> tuple[ProbeFinding, ...]:
        findings: list[ProbeFinding] = []
        for row in connection.execute(
            """
            SELECT work_item_id, last_safe_error_code FROM work_items
            WHERE state = 'queued' AND requires_user_action = 1
            ORDER BY created_at, work_item_id
            """
        ):
            findings.append(
                ProbeFinding(
                    owner=self.name,
                    code="DURABLE_LOCAL_RESUME_REQUIRED",
                    severity=RecoverySeverity.WARNING,
                    subject_type="work_item",
                    subject_id=str(row["work_item_id"]),
                    repair_command="resume_local_work",
                )
            )
        for row in connection.execute(
            """
            SELECT work_item_id FROM work_items
            WHERE state = 'needs_review'
                AND last_safe_error_code = 'EXTERNAL_RESULT_UNKNOWN'
            ORDER BY updated_at, work_item_id
            """
        ):
            findings.append(
                ProbeFinding(
                    owner=self.name,
                    code="DURABLE_EXTERNAL_RESULT_UNKNOWN",
                    severity=RecoverySeverity.CRITICAL,
                    subject_type="work_item",
                    subject_id=str(row["work_item_id"]),
                    repair_command=None,
                )
            )
        for row in connection.execute(
            """
            SELECT publish_attempt_id FROM outbox_publish_attempts
            WHERE state = 'sending' ORDER BY started_at, publish_attempt_id
            """
        ):
            findings.append(
                ProbeFinding(
                    owner=self.name,
                    code="OUTBOX_DELIVERY_UNCONFIRMED",
                    severity=RecoverySeverity.WARNING,
                    subject_type="outbox_publish_attempt",
                    subject_id=str(row["publish_attempt_id"]),
                    repair_command="retry_outbox_delivery",
                )
            )
        for row in connection.execute(
            """
            SELECT w.work_item_id FROM work_items w
            WHERE w.state = 'completed' AND w.result_content_sha256 IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM outbox_events e
                  WHERE e.event_type = 'durable_work.completed'
                    AND e.aggregate_id = w.work_item_id
                    AND e.aggregate_version = w.revision
                    AND e.aggregate_sha256 = w.result_content_sha256
              )
            ORDER BY w.updated_at, w.work_item_id
            """
        ):
            findings.append(
                ProbeFinding(
                    owner=self.name,
                    code="DURABLE_COMPLETION_EVENT_MISSING",
                    severity=RecoverySeverity.WARNING,
                    subject_type="work_item",
                    subject_id=str(row["work_item_id"]),
                    repair_command="emit_completion_event",
                )
            )
        return tuple(findings)

    def repair(self, connection: sqlite3.Connection, finding: ProbeFinding) -> str:
        if finding.repair_command == "resume_local_work":
            updated = connection.execute(
                """
                UPDATE work_items
                SET requires_user_action = 0, revision = revision + 1, updated_at = ?
                WHERE work_item_id = ? AND state = 'queued' AND requires_user_action = 1
                """,
                (_iso(self._clock.now()), finding.subject_id),
            ).rowcount
            if updated not in {0, 1}:
                raise RuntimeError("unexpected durable work resume row count")
            return "LOCAL_WORK_RESUMED" if updated == 1 else "LOCAL_WORK_ALREADY_RESUMED"
        if finding.repair_command == "retry_outbox_delivery":
            row = connection.execute(
                """
                SELECT event_id FROM outbox_publish_attempts
                WHERE publish_attempt_id = ? AND state = 'sending'
                """,
                (finding.subject_id,),
            ).fetchone()
            if row is None:
                return "OUTBOX_DELIVERY_ALREADY_RECONCILED"
            now = _iso(self._clock.now())
            connection.execute(
                """
                UPDATE outbox_publish_attempts
                SET state = 'failed', safe_error_code = 'DELIVERY_CONFIRMATION_UNKNOWN',
                    safe_error_message = '发布确认未知，已等待显式重试。', finished_at = ?
                WHERE publish_attempt_id = ? AND state = 'sending'
                """,
                (now, finding.subject_id),
            )
            connection.execute(
                """
                UPDATE outbox_events
                SET publish_state = 'pending', published_at = NULL,
                    last_safe_error_code = 'DELIVERY_CONFIRMATION_UNKNOWN',
                    last_safe_error_message = '发布确认未知，已等待显式重试。'
                WHERE event_id = ?
                """,
                (str(row["event_id"]),),
            )
            return "OUTBOX_DELIVERY_REQUEUED"
        if finding.repair_command == "emit_completion_event":
            row = connection.execute(
                """
                SELECT * FROM work_items WHERE work_item_id = ? AND state = 'completed'
                """,
                (finding.subject_id,),
            ).fetchone()
            if row is None or row["result_content_sha256"] is None:
                return "COMPLETION_EVENT_NOT_APPLICABLE"
            event = SQLiteOutboxAdapter(connection, self._clock, self._id_factory).append(
                OutboxEventRequest(
                    project_id=str(row["project_id"]),
                    event_type="durable_work.completed",
                    event_version="1.0",
                    aggregate_type="work_item",
                    aggregate_id=str(row["work_item_id"]),
                    aggregate_version=int(row["revision"]),
                    aggregate_sha256=Sha256(str(row["result_content_sha256"])),
                    deduplication_key=(
                        f"work-completed:{row['work_item_id']}:{row['revision']}"
                    ),
                    attributes=(
                        SafeEventAttribute("state", "completed"),
                        SafeEventAttribute(
                            "result_type", str(row["result_artifact_type"])
                        ),
                    ),
                )
            )
            return f"COMPLETION_EVENT_EMITTED:{event.event_id}"
        raise ValueError(f"unsupported durable recovery command {finding.repair_command!r}")

    @staticmethod
    def _fail_closed_running(
        connection: sqlite3.Connection,
        *,
        work_item_id: str,
        work_attempt_id: str | None,
        external_request_started: bool,
        revision: int,
        now: datetime,
        error_code: str,
    ) -> None:
        if external_request_started:
            work_state = "needs_review"
            attempt_state = "needs_review"
            retryable = 0
            message = "外部请求结果未知，需要人工检查。"
            requires_user_action = 1
        else:
            work_state = "queued"
            attempt_state = "failed"
            retryable = 1
            message = "应用重启后需要显式恢复本地工作。"
            requires_user_action = 1
        if work_attempt_id is not None:
            connection.execute(
                """
                UPDATE work_attempts
                SET state = ?, safe_error_code = ?, safe_error_message = ?,
                    safe_error_retryable = ?, finished_at = ?
                WHERE work_attempt_id = ? AND state = 'running'
                """,
                (
                    attempt_state,
                    error_code,
                    message,
                    retryable,
                    _iso(now),
                    work_attempt_id,
                ),
            )
        connection.execute(
            """
            UPDATE work_items
            SET state = ?, revision = revision + 1, requires_user_action = ?,
                not_before = ?, last_safe_error_code = ?,
                last_safe_error_message = ?, last_safe_error_retryable = ?, updated_at = ?
            WHERE work_item_id = ? AND revision = ? AND state = 'running'
            """,
            (
                work_state,
                requires_user_action,
                _iso(now),
                error_code,
                message,
                retryable,
                _iso(now),
                work_item_id,
                revision,
            ),
        )
