from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ...shared_kernel import Clock, IdFactory, Sha256, canonical_json_bytes, canonical_sha256
from .contracts import SafeWorkError, validate_safe_key

SENSITIVE_EVENT_MARKERS = (
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "token",
    "prompt",
    "base64",
    "/users/",
    "http://",
    "https://",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outbox timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("outbox timestamp is not timezone-aware")
    return _utc(parsed)


class OutboxPublishState(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"


@dataclass(frozen=True, slots=True)
class SafeEventAttribute:
    key: str
    value: str | int | bool

    def __post_init__(self) -> None:
        validate_safe_key("event attribute key", self.key)
        if isinstance(self.value, str):
            validate_safe_key("event attribute value", self.value)
            if any(marker in self.value.lower() for marker in SENSITIVE_EVENT_MARKERS):
                raise ValueError("event attribute contains a sensitive content marker")
        elif not isinstance(self.value, (int, bool)):
            raise ValueError("event attributes only support safe strings, integers, and booleans")


@dataclass(frozen=True, slots=True)
class OutboxEventRequest:
    project_id: str
    event_type: str
    event_version: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    aggregate_sha256: Sha256
    deduplication_key: str
    attributes: tuple[SafeEventAttribute, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("project_id", self.project_id),
            ("event_type", self.event_type),
            ("event_version", self.event_version),
            ("aggregate_type", self.aggregate_type),
            ("aggregate_id", self.aggregate_id),
            ("deduplication_key", self.deduplication_key),
        ):
            validate_safe_key(name, value)
        if self.aggregate_version < 1:
            raise ValueError("aggregate_version must be positive")
        keys = [attribute.key for attribute in self.attributes]
        if len(keys) != len(set(keys)) or len(keys) > 32:
            raise ValueError("event attributes must have at most 32 unique keys")


@dataclass(frozen=True, slots=True)
class OutboxEventSnapshot:
    event_id: UUID
    project_id: str
    project_sequence: int
    event_type: str
    event_version: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    aggregate_sha256: Sha256
    deduplication_key: str
    attributes: tuple[SafeEventAttribute, ...]
    event_sha256: Sha256
    publish_state: OutboxPublishState
    publish_attempts: int
    last_safe_error: SafeWorkError | None
    created_at: datetime
    published_at: datetime | None

    def public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.event_version,
            "event_id": str(self.event_id),
            "project_id": self.project_id,
            "project_sequence": self.project_sequence,
            "event_type": self.event_type,
            "aggregate": {
                "type": self.aggregate_type,
                "id": self.aggregate_id,
                "version": self.aggregate_version,
                "sha256": str(self.aggregate_sha256),
            },
            "attributes": {attribute.key: attribute.value for attribute in self.attributes},
        }


@dataclass(frozen=True, slots=True)
class HandledEventReceipt:
    event_id: UUID
    handler_version: str
    receipt_id: UUID
    result_sha256: Sha256
    handled_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxPublishAttemptSnapshot:
    publish_attempt_id: UUID
    event_id: UUID
    ordinal: int
    state: str
    safe_error: SafeWorkError | None
    started_at: datetime
    finished_at: datetime | None


class OutboxError(RuntimeError):
    pass


class OutboxEventNotFoundError(OutboxError):
    pass


class OutboxIdempotencyConflictError(OutboxError):
    pass


class OutboxCorruptError(OutboxError):
    pass


class OutboxPublishInFlightError(OutboxError):
    pass


class SQLiteOutboxAdapter:
    def __init__(self, connection: sqlite3.Connection, clock: Clock, id_factory: IdFactory) -> None:
        self._connection = connection
        self._clock = clock
        self._id_factory = id_factory

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        self._connection.execute("SAVEPOINT durable_outbox_operation")
        try:
            yield
        except Exception:
            self._connection.execute("ROLLBACK TO durable_outbox_operation")
            self._connection.execute("RELEASE durable_outbox_operation")
            raise
        else:
            self._connection.execute("RELEASE durable_outbox_operation")

    def append(self, request: OutboxEventRequest) -> OutboxEventSnapshot:
        with self._atomic():
            existing = self._connection.execute(
                """
                SELECT * FROM outbox_events
                WHERE project_id = ? AND deduplication_key = ?
                """,
                (request.project_id, request.deduplication_key),
            ).fetchone()
            if existing is not None:
                snapshot = self._event(existing)
                if not self._matches_request(snapshot, request):
                    raise OutboxIdempotencyConflictError(
                        "outbox deduplication key is bound to another event"
                    )
                return snapshot

            self._connection.execute(
                """
                INSERT INTO outbox_project_sequences(project_id, last_sequence)
                VALUES (?, 0) ON CONFLICT(project_id) DO NOTHING
                """,
                (request.project_id,),
            )
            self._connection.execute(
                """
                UPDATE outbox_project_sequences
                SET last_sequence = last_sequence + 1 WHERE project_id = ?
                """,
                (request.project_id,),
            )
            sequence_row = self._connection.execute(
                "SELECT last_sequence FROM outbox_project_sequences WHERE project_id = ?",
                (request.project_id,),
            ).fetchone()
            assert sequence_row is not None
            sequence = int(sequence_row["last_sequence"])
            event_id = self._id_factory.new()
            attributes = tuple(sorted(request.attributes, key=lambda attribute: attribute.key))
            payload = {
                "schema_version": request.event_version,
                "event_id": str(event_id),
                "project_id": request.project_id,
                "project_sequence": sequence,
                "event_type": request.event_type,
                "aggregate": {
                    "type": request.aggregate_type,
                    "id": request.aggregate_id,
                    "version": request.aggregate_version,
                    "sha256": str(request.aggregate_sha256),
                },
                "attributes": {attribute.key: attribute.value for attribute in attributes},
            }
            event_json = canonical_json_bytes(payload).decode("utf-8")
            event_sha256 = canonical_sha256(payload)
            now = _utc(self._clock.now())
            self._connection.execute(
                """
                INSERT INTO outbox_events(
                    event_id, project_id, project_sequence, event_type, event_version,
                    aggregate_type, aggregate_id, aggregate_version, aggregate_sha256,
                    event_json, event_sha256, deduplication_key, publish_state,
                    publish_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (
                    str(event_id),
                    request.project_id,
                    sequence,
                    request.event_type,
                    request.event_version,
                    request.aggregate_type,
                    request.aggregate_id,
                    request.aggregate_version,
                    str(request.aggregate_sha256),
                    event_json,
                    event_sha256,
                    request.deduplication_key,
                    _iso(now),
                ),
            )
            return self.get(event_id)

    def get(self, event_id: UUID) -> OutboxEventSnapshot:
        row = self._connection.execute(
            "SELECT * FROM outbox_events WHERE event_id = ?", (str(event_id),)
        ).fetchone()
        if row is None:
            raise OutboxEventNotFoundError(f"outbox event {event_id} was not found")
        return self._event(row)

    def replay(
        self, project_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> tuple[OutboxEventSnapshot, ...]:
        validate_safe_key("project_id", project_id)
        if after_sequence < 0 or not 1 <= limit <= 1_000:
            raise ValueError("invalid outbox replay cursor or limit")
        rows = self._connection.execute(
            """
            SELECT * FROM outbox_events
            WHERE project_id = ? AND project_sequence > ?
            ORDER BY project_sequence LIMIT ?
            """,
            (project_id, after_sequence, limit),
        ).fetchall()
        return tuple(self._event(row) for row in rows)

    def list_pending(self, *, limit: int = 100) -> tuple[OutboxEventSnapshot, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("outbox publish limit must be between 1 and 1000")
        rows = self._connection.execute(
            """
            SELECT e.* FROM outbox_events e
            WHERE e.publish_state = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM outbox_publish_attempts a
                  WHERE a.event_id = e.event_id AND a.state = 'sending'
              )
            ORDER BY created_at, event_id LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return tuple(self._event(row) for row in rows)

    def begin_publish(self, event_id: UUID) -> OutboxPublishAttemptSnapshot:
        with self._atomic():
            current = self.get(event_id)
            if current.publish_state is OutboxPublishState.PUBLISHED:
                raise OutboxPublishInFlightError("outbox event is already published")
            in_flight = self._connection.execute(
                """
                SELECT 1 FROM outbox_publish_attempts
                WHERE event_id = ? AND state = 'sending'
                """,
                (str(event_id),),
            ).fetchone()
            if in_flight is not None:
                raise OutboxPublishInFlightError("outbox event has an unconfirmed attempt")
            attempt_id = self._id_factory.new()
            ordinal = current.publish_attempts + 1
            now = _utc(self._clock.now())
            self._connection.execute(
                """
                INSERT INTO outbox_publish_attempts(
                    publish_attempt_id, event_id, ordinal, state, started_at
                ) VALUES (?, ?, ?, 'sending', ?)
                """,
                (str(attempt_id), str(event_id), ordinal, _iso(now)),
            )
            self._connection.execute(
                "UPDATE outbox_events SET publish_attempts = ? WHERE event_id = ?",
                (ordinal, str(event_id)),
            )
            return self._publish_attempt_by_id(attempt_id)

    def confirm_publish(self, publish_attempt_id: UUID) -> OutboxEventSnapshot:
        with self._atomic():
            attempt = self._publish_attempt_by_id(publish_attempt_id)
            event = self.get(attempt.event_id)
            if attempt.state == "confirmed":
                return event
            if attempt.state != "sending":
                raise OutboxPublishInFlightError("publish attempt is not sending")
            now = _utc(self._clock.now())
            self._connection.execute(
                """
                UPDATE outbox_publish_attempts
                SET state = 'confirmed', finished_at = ?
                WHERE publish_attempt_id = ? AND state = 'sending'
                """,
                (_iso(now), str(publish_attempt_id)),
            )
            self._connection.execute(
                """
                UPDATE outbox_events
                SET publish_state = 'published', last_safe_error_code = NULL,
                    last_safe_error_message = NULL, published_at = ?
                WHERE event_id = ? AND publish_state = 'pending'
                """,
                (_iso(now), str(attempt.event_id)),
            )
            return self.get(attempt.event_id)

    def fail_publish(
        self, publish_attempt_id: UUID, error: SafeWorkError
    ) -> OutboxEventSnapshot:
        with self._atomic():
            attempt = self._publish_attempt_by_id(publish_attempt_id)
            event = self.get(attempt.event_id)
            if attempt.state == "failed":
                return event
            if attempt.state != "sending":
                raise OutboxPublishInFlightError("publish attempt is not sending")
            now = _utc(self._clock.now())
            self._connection.execute(
                """
                UPDATE outbox_publish_attempts
                SET state = 'failed', safe_error_code = ?, safe_error_message = ?,
                    finished_at = ?
                WHERE publish_attempt_id = ? AND state = 'sending'
                """,
                (error.code, error.message, _iso(now), str(publish_attempt_id)),
            )
            self._connection.execute(
                """
                UPDATE outbox_events
                SET last_safe_error_code = ?, last_safe_error_message = ?
                WHERE event_id = ? AND publish_state = 'pending'
                """,
                (error.code, error.message, str(attempt.event_id)),
            )
            return self.get(attempt.event_id)

    def mark_published(self, event_id: UUID) -> OutboxEventSnapshot:
        with self._atomic():
            current = self.get(event_id)
            if current.publish_state is OutboxPublishState.PUBLISHED:
                return current
            now = _utc(self._clock.now())
            self._connection.execute(
                """
                UPDATE outbox_events
                SET publish_state = 'published', publish_attempts = publish_attempts + 1,
                    last_safe_error_code = NULL, last_safe_error_message = NULL,
                    published_at = ?
                WHERE event_id = ? AND publish_state = 'pending'
                """,
                (_iso(now), str(event_id)),
            )
            return self.get(event_id)

    def record_publish_failure(
        self, event_id: UUID, error: SafeWorkError
    ) -> OutboxEventSnapshot:
        with self._atomic():
            current = self.get(event_id)
            if current.publish_state is OutboxPublishState.PUBLISHED:
                return current
            self._connection.execute(
                """
                UPDATE outbox_events
                SET publish_attempts = publish_attempts + 1,
                    last_safe_error_code = ?, last_safe_error_message = ?
                WHERE event_id = ? AND publish_state = 'pending'
                """,
                (error.code, error.message, str(event_id)),
            )
            return self.get(event_id)

    def _publish_attempt_by_id(
        self, publish_attempt_id: UUID
    ) -> OutboxPublishAttemptSnapshot:
        row = self._connection.execute(
            """
            SELECT * FROM outbox_publish_attempts WHERE publish_attempt_id = ?
            """,
            (str(publish_attempt_id),),
        ).fetchone()
        if row is None:
            raise OutboxEventNotFoundError(
                f"outbox publish attempt {publish_attempt_id} was not found"
            )
        error = None
        if row["safe_error_code"] is not None:
            error = SafeWorkError(
                str(row["safe_error_code"]),
                str(row["safe_error_message"]),
                retryable=True,
            )
        finished_at = row["finished_at"]
        return OutboxPublishAttemptSnapshot(
            publish_attempt_id=UUID(str(row["publish_attempt_id"])),
            event_id=UUID(str(row["event_id"])),
            ordinal=int(row["ordinal"]),
            state=str(row["state"]),
            safe_error=error,
            started_at=_datetime(row["started_at"]),
            finished_at=_datetime(finished_at) if finished_at is not None else None,
        )

    @staticmethod
    def _matches_request(
        event: OutboxEventSnapshot, request: OutboxEventRequest
    ) -> bool:
        return (
            event.project_id == request.project_id
            and event.event_type == request.event_type
            and event.event_version == request.event_version
            and event.aggregate_type == request.aggregate_type
            and event.aggregate_id == request.aggregate_id
            and event.aggregate_version == request.aggregate_version
            and event.aggregate_sha256 == request.aggregate_sha256
            and event.deduplication_key == request.deduplication_key
            and event.attributes
            == tuple(sorted(request.attributes, key=lambda attribute: attribute.key))
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> OutboxEventSnapshot:
        event_json = str(row["event_json"])
        try:
            payload = json.loads(event_json)
        except json.JSONDecodeError as exc:
            raise OutboxCorruptError("outbox event JSON is invalid") from exc
        if canonical_sha256(payload) != str(row["event_sha256"]):
            raise OutboxCorruptError("outbox event hash does not match its canonical payload")
        try:
            aggregate = payload["aggregate"]
            raw_attributes = payload["attributes"]
            attributes = tuple(
                SafeEventAttribute(str(key), value)
                for key, value in sorted(raw_attributes.items())
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OutboxCorruptError("outbox event payload does not match its contract") from exc
        expected = (
            str(row["event_id"]),
            str(row["project_id"]),
            int(row["project_sequence"]),
            str(row["event_type"]),
            str(row["event_version"]),
            str(row["aggregate_type"]),
            str(row["aggregate_id"]),
            int(row["aggregate_version"]),
            str(row["aggregate_sha256"]),
        )
        observed = (
            str(payload.get("event_id")),
            str(payload.get("project_id")),
            int(payload.get("project_sequence", 0)),
            str(payload.get("event_type")),
            str(payload.get("schema_version")),
            str(aggregate.get("type")),
            str(aggregate.get("id")),
            int(aggregate.get("version", 0)),
            str(aggregate.get("sha256")),
        )
        if observed != expected:
            raise OutboxCorruptError("outbox columns disagree with the canonical event payload")
        error = None
        if row["last_safe_error_code"] is not None:
            error = SafeWorkError(
                str(row["last_safe_error_code"]),
                str(row["last_safe_error_message"]),
                retryable=True,
            )
        published_at = row["published_at"]
        return OutboxEventSnapshot(
            event_id=UUID(str(row["event_id"])),
            project_id=str(row["project_id"]),
            project_sequence=int(row["project_sequence"]),
            event_type=str(row["event_type"]),
            event_version=str(row["event_version"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            aggregate_version=int(row["aggregate_version"]),
            aggregate_sha256=Sha256(str(row["aggregate_sha256"])),
            deduplication_key=str(row["deduplication_key"]),
            attributes=attributes,
            event_sha256=Sha256(str(row["event_sha256"])),
            publish_state=OutboxPublishState(str(row["publish_state"])),
            publish_attempts=int(row["publish_attempts"]),
            last_safe_error=error,
            created_at=_datetime(row["created_at"]),
            published_at=_datetime(published_at) if published_at is not None else None,
        )


class OutboxDatabase(Protocol):
    def reader(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def writer(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SQLiteOutboxStore:
    def __init__(self, database: OutboxDatabase, clock: Clock, id_factory: IdFactory) -> None:
        self._database = database
        self._clock = clock
        self._id_factory = id_factory

    def bind(self, connection: sqlite3.Connection) -> SQLiteOutboxAdapter:
        return SQLiteOutboxAdapter(connection, self._clock, self._id_factory)

    def replay(
        self, project_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> tuple[OutboxEventSnapshot, ...]:
        with self._database.reader() as connection:
            return self.bind(connection).replay(
                project_id, after_sequence=after_sequence, limit=limit
            )

    def list_pending(self, *, limit: int = 100) -> tuple[OutboxEventSnapshot, ...]:
        with self._database.reader() as connection:
            return self.bind(connection).list_pending(limit=limit)

    def mark_published(self, event_id: UUID) -> OutboxEventSnapshot:
        with self._database.writer() as connection:
            return self.bind(connection).mark_published(event_id)

    def begin_publish(self, event_id: UUID) -> OutboxPublishAttemptSnapshot:
        with self._database.writer() as connection:
            return self.bind(connection).begin_publish(event_id)

    def confirm_publish(self, publish_attempt_id: UUID) -> OutboxEventSnapshot:
        with self._database.writer() as connection:
            return self.bind(connection).confirm_publish(publish_attempt_id)

    def fail_publish(
        self, publish_attempt_id: UUID, error: SafeWorkError
    ) -> OutboxEventSnapshot:
        with self._database.writer() as connection:
            return self.bind(connection).fail_publish(publish_attempt_id, error)

    def record_publish_failure(
        self, event_id: UUID, error: SafeWorkError
    ) -> OutboxEventSnapshot:
        with self._database.writer() as connection:
            return self.bind(connection).record_publish_failure(event_id, error)


EventReceiptHandler = Callable[[sqlite3.Connection, OutboxEventSnapshot], Sha256]


class EventHandlerRunner:
    """Runs a local projection and writes its receipt in the same transaction."""

    def __init__(self, store: SQLiteOutboxStore, database: OutboxDatabase) -> None:
        self._store = store
        self._database = database

    def handle_once(
        self,
        event_id: UUID,
        handler_version: str,
        handler: EventReceiptHandler,
    ) -> HandledEventReceipt:
        validate_safe_key("handler_version", handler_version)
        with self._database.writer() as connection:
            existing = connection.execute(
                """
                SELECT * FROM handled_events
                WHERE event_id = ? AND handler_version = ?
                """,
                (str(event_id), handler_version),
            ).fetchone()
            if existing is not None:
                return self._receipt(existing)
            event = self._store.bind(connection).get(event_id)
            result_sha256 = handler(connection, event)
            receipt_id = self._store._id_factory.new()
            handled_at = _utc(self._store._clock.now())
            connection.execute(
                """
                INSERT INTO handled_events(
                    event_id, handler_version, receipt_id, result_sha256, handled_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(event_id),
                    handler_version,
                    str(receipt_id),
                    str(result_sha256),
                    _iso(handled_at),
                ),
            )
            return HandledEventReceipt(
                event_id,
                handler_version,
                receipt_id,
                result_sha256,
                handled_at,
            )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> HandledEventReceipt:
        return HandledEventReceipt(
            event_id=UUID(str(row["event_id"])),
            handler_version=str(row["handler_version"]),
            receipt_id=UUID(str(row["receipt_id"])),
            result_sha256=Sha256(str(row["result_sha256"])),
            handled_at=_datetime(row["handled_at"]),
        )
