from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.database import Database
from backend.app.platform.durable_work.outbox import (
    EventHandlerRunner,
    OutboxEventRequest,
    OutboxIdempotencyConflictError,
    OutboxPublishState,
    SafeEventAttribute,
    SQLiteOutboxStore,
)
from backend.app.platform.durable_work.publisher import OutboxPublisher
from backend.app.shared_kernel import Sha256

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequentialIdFactory:
    def __init__(self, start: int = 10_000) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def event_request(
    project_id: str,
    deduplication_key: str,
    *,
    state: str = "completed",
) -> OutboxEventRequest:
    return OutboxEventRequest(
        project_id=project_id,
        event_type="quality.run.changed",
        event_version="1.0",
        aggregate_type="quality_run",
        aggregate_id=f"quality-{deduplication_key}",
        aggregate_version=1,
        aggregate_sha256=Sha256.digest(f"aggregate:{deduplication_key}".encode()),
        deduplication_key=deduplication_key,
        attributes=(
            SafeEventAttribute("state", state),
            SafeEventAttribute("finding_count", 2),
            SafeEventAttribute("requires_review", True),
        ),
    )


def test_outbox_sequence_idempotency_project_isolation_and_transaction_rollback(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "outbox.db")
    database.migrate()
    store = SQLiteOutboxStore(database, FixedClock(), SequentialIdFactory())
    with database.writer() as connection:
        connection.execute(
            "CREATE TABLE test_event_domain(record_id TEXT PRIMARY KEY)"
        )
        first = store.bind(connection).append(event_request("project-a", "event-1"))
        second = store.bind(connection).append(event_request("project-a", "event-2"))
        other = store.bind(connection).append(event_request("project-b", "event-1"))
        assert store.bind(connection).append(event_request("project-a", "event-1")) == first
        with pytest.raises(OutboxIdempotencyConflictError):
            store.bind(connection).append(
                event_request("project-a", "event-1", state="failed")
            )

    with (
        pytest.raises(RuntimeError, match="rollback event and domain state"),
        database.writer() as connection,
    ):
        connection.execute(
            "INSERT INTO test_event_domain(record_id) VALUES ('rolled-back')"
        )
        store.bind(connection).append(event_request("project-a", "event-rollback"))
        raise RuntimeError("rollback event and domain state")

    with database.writer() as connection:
        third = store.bind(connection).append(event_request("project-a", "event-3"))

    assert [event.project_sequence for event in store.replay("project-a")] == [1, 2, 3]
    assert [event.event_id for event in store.replay("project-a")] == [
        first.event_id,
        second.event_id,
        third.event_id,
    ]
    assert store.replay("project-b") == (other,)
    assert [event.project_sequence for event in store.replay("project-a", after_sequence=1)] == [
        2,
        3,
    ]
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM test_event_domain").fetchone()[0] == 0


def test_concurrent_outbox_writers_allocate_gapless_unique_project_sequences(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "outbox-concurrent.db")
    database.migrate()

    def append(index: int) -> int:
        store = SQLiteOutboxStore(
            database,
            FixedClock(),
            SequentialIdFactory(20_000 + index * 10),
        )
        with database.writer() as connection:
            event = store.bind(connection).append(
                event_request("project-concurrent", f"event-{index}")
            )
        return event.project_sequence

    with ThreadPoolExecutor(max_workers=4) as executor:
        allocated = list(executor.map(append, range(12)))
    assert sorted(allocated) == list(range(1, 13))


class RestartableIdempotentSink:
    def __init__(self) -> None:
        self.delivered: set[UUID] = set()
        self.calls: list[UUID] = []
        self.fail_after_first_delivery = True

    async def publish(self, event: object) -> None:
        event_id = event.event_id
        self.calls.append(event_id)
        already_delivered = event_id in self.delivered
        self.delivered.add(event_id)
        if self.fail_after_first_delivery and not already_delivered:
            self.fail_after_first_delivery = False
            raise RuntimeError("Bearer secret /Users/private must not reach the database")


def test_publisher_failure_is_separate_from_domain_commit_and_restart_is_idempotent(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "publisher.db")
    database.migrate()
    store = SQLiteOutboxStore(database, FixedClock(), SequentialIdFactory(30_000))
    with database.writer() as connection:
        connection.execute("CREATE TABLE test_publish_domain(record_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO test_publish_domain VALUES ('committed')")
        first = store.bind(connection).append(event_request("project-publish", "event-1"))
        second = store.bind(connection).append(event_request("project-publish", "event-2"))

    sink = RestartableIdempotentSink()
    first_batch = asyncio.run(OutboxPublisher(store, sink).publish_pending())
    assert first_batch.attempted == 2
    assert first_batch.failed == 1
    assert first_batch.published == 1
    assert len(sink.delivered) == 2

    pending = store.list_pending()
    assert len(pending) == 1 and pending[0].event_id == first.event_id
    assert pending[0].last_safe_error is not None
    assert "secret" not in pending[0].last_safe_error.message.lower()
    assert "/Users/" not in pending[0].last_safe_error.message
    assert store.replay("project-publish")[1].event_id == second.event_id
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM test_publish_domain").fetchone()[0] == 1

    restarted = OutboxPublisher(store, sink)
    second_batch = asyncio.run(restarted.publish_pending())
    assert second_batch.published == 1 and second_batch.failed == 0
    assert store.list_pending() == ()
    assert len(sink.delivered) == 2
    assert sink.calls.count(first.event_id) == 2
    assert all(
        event.publish_state is OutboxPublishState.PUBLISHED
        for event in store.replay("project-publish")
    )


def test_event_handler_receipt_applies_projection_once_per_handler_version(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "handler-receipt.db")
    database.migrate()
    store = SQLiteOutboxStore(database, FixedClock(), SequentialIdFactory(40_000))
    with database.writer() as connection:
        connection.execute(
            "CREATE TABLE test_projection("
            "event_id TEXT, handler_version TEXT, PRIMARY KEY(event_id, handler_version))"
        )
        event = store.bind(connection).append(event_request("project-handler", "event-1"))

    calls = 0

    def project(connection: object, received: object) -> Sha256:
        nonlocal calls
        calls += 1
        connection.execute(
            "INSERT INTO test_projection(event_id, handler_version) VALUES (?, 'projection-v1')",
            (str(received.event_id),),
        )
        return Sha256.digest(b"projection-result")

    runner = EventHandlerRunner(store, database)
    first = runner.handle_once(event.event_id, "projection-v1", project)
    duplicate = runner.handle_once(event.event_id, "projection-v1", project)
    assert duplicate == first
    assert calls == 1
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM test_projection").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM handled_events").fetchone()[0] == 1


def test_sse_last_event_id_replays_committed_events_without_cross_project_leakage(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    container = client.app.state.container
    with container.database.writer() as connection:
        first = container.outbox.bind(connection).append(
            event_request("project-sse", "event-1")
        )
        second = container.outbox.bind(connection).append(
            event_request("project-sse", "event-2")
        )
        other = container.outbox.bind(connection).append(
            event_request("project-private", "event-1")
        )

    endpoint = "/api/v1/projects/project-sse/events"
    assert client.get(endpoint).status_code == 401
    response = client.get(endpoint, headers=session_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" in response.text and "id: 2\n" in response.text
    assert str(first.event_id) in response.text and str(second.event_id) in response.text
    assert str(other.event_id) not in response.text

    replayed = client.get(endpoint, headers={**session_headers, "Last-Event-ID": "1"})
    assert "id: 1\n" not in replayed.text
    assert "id: 2\n" in replayed.text
    assert str(first.event_id) not in replayed.text
    assert str(second.event_id) in replayed.text
    exhausted = client.get(endpoint, headers={**session_headers, "Last-Event-ID": "2"})
    assert exhausted.text == "retry: 2000\n\n"
    assert client.get(
        endpoint, headers={**session_headers, "Last-Event-ID": "-1"}
    ).status_code == 422


def test_event_contract_rejects_content_secrets_and_absolute_paths(tmp_path: Path) -> None:
    for unsafe in (
        "full prompt text",
        "Bearer-secret",
        "/Users/private/project",
        "https://provider.example/secret",
    ):
        with pytest.raises(ValueError, match=r"opaque identifier|sensitive"):
            SafeEventAttribute("unsafe", unsafe)

    database = Database(tmp_path / "outbox-schema.db")
    database.migrate()
    store = SQLiteOutboxStore(database, FixedClock(), SequentialIdFactory(50_000))
    with database.writer() as connection:
        event = store.bind(connection).append(event_request("project-safe", "event-1"))
        row = connection.execute(
            "SELECT event_json FROM outbox_events WHERE event_id = ?", (str(event.event_id),)
        ).fetchone()
        columns = {
            str(info[1]) for info in connection.execute("PRAGMA table_info(outbox_events)")
        }
    payload = json.loads(str(row[0]))
    assert set(payload) == {
        "aggregate",
        "attributes",
        "event_id",
        "event_type",
        "project_id",
        "project_sequence",
        "schema_version",
    }
    forbidden_columns = {"body", "content", "image_bytes", "prompt", "token"}
    assert columns.isdisjoint(forbidden_columns)
