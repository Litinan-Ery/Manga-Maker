from __future__ import annotations

import sqlite3
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.database import Database
from backend.app.platform.durable_work.contracts import (
    EnqueueWorkRequest,
    SafeWorkError,
    WorkCommandReference,
)
from backend.app.platform.durable_work.errors import (
    WorkNotReadyError,
    WorkRequiresUserActionError,
)
from backend.app.platform.durable_work.fake import InMemoryDurableWorkAdapter
from backend.app.platform.durable_work.sqlite import SQLiteDurableWorkUnitOfWork
from backend.app.shared_kernel import Sha256
from tests.contracts.durable_work_port_harness import (
    assert_durable_work_port_contract,
    work_request,
)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class SequentialIdFactory:
    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


NOW = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)


def test_same_contract_suite_passes_for_in_memory_and_sqlite_adapters(tmp_path: Path) -> None:
    fake = InMemoryDurableWorkAdapter(FixedClock(NOW), SequentialIdFactory())
    assert_durable_work_port_contract(fake, NOW)

    database = Database(tmp_path / "durable-work.db")
    database.migrate()
    unit_of_work = SQLiteDurableWorkUnitOfWork(
        database, FixedClock(NOW), SequentialIdFactory()
    )
    with unit_of_work.transaction() as transaction:
        assert_durable_work_port_contract(transaction.work, NOW)


def test_domain_state_and_work_intent_share_one_sqlite_transaction(tmp_path: Path) -> None:
    database = Database(tmp_path / "atomic.db")
    database.migrate()
    with database.writer() as connection:
        connection.execute(
            "CREATE TABLE test_domain_records("
            "record_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
        )
    unit_of_work = SQLiteDurableWorkUnitOfWork(
        database, FixedClock(NOW), SequentialIdFactory(100)
    )

    with unit_of_work.transaction() as transaction:
        transaction.connection.execute(
            "INSERT INTO test_domain_records(record_id, revision) VALUES ('committed', 1)"
        )
        committed = transaction.work.enqueue(work_request(NOW, "atomic-commit"))

    with (
        pytest.raises(RuntimeError, match="injected domain failure"),
        unit_of_work.transaction() as transaction,
    ):
        transaction.connection.execute(
            "INSERT INTO test_domain_records(record_id, revision) VALUES ('rolled-back', 1)"
        )
        transaction.work.enqueue(work_request(NOW, "atomic-rollback"))
        raise RuntimeError("injected domain failure")

    with database.reader() as connection:
        domain_ids = {
            str(row[0]) for row in connection.execute("SELECT record_id FROM test_domain_records")
        }
        work_keys = {
            str(row[0]) for row in connection.execute("SELECT idempotency_key FROM work_items")
        }
    assert domain_ids == {"committed"}
    assert work_keys == {"atomic-commit"}
    assert committed.idempotency_key == "atomic-commit"


def test_not_before_and_user_action_gates_fail_before_starting_attempts(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "gates.db")
    database.migrate()
    unit_of_work = SQLiteDurableWorkUnitOfWork(
        database, FixedClock(NOW), SequentialIdFactory(200)
    )
    with unit_of_work.transaction() as transaction:
        future = transaction.work.enqueue(
            work_request(NOW + timedelta(minutes=5), "not-before")
        )
        action = transaction.work.enqueue(
            work_request(NOW, "user-action", requires_user_action=True)
        )
        with pytest.raises(WorkNotReadyError):
            transaction.work.start(future.work_item_id, expected_revision=1)
        with pytest.raises(WorkRequiresUserActionError):
            transaction.work.start(action.work_item_id, expected_revision=1)
        assert transaction.work.list_attempts(future.work_item_id) == ()
        assert transaction.work.list_attempts(action.work_item_id) == ()


def test_work_contract_and_schema_store_references_and_hashes_not_payloads(
    tmp_path: Path,
) -> None:
    assert {field.name for field in fields(EnqueueWorkRequest)} == {
        "project_id",
        "kind",
        "idempotency_key",
        "command",
        "attempt_limit",
        "not_before",
        "requires_user_action",
        "execution_safety",
    }
    assert {field.name for field in fields(WorkCommandReference)} == {
        "contract",
        "contract_version",
        "aggregate_type",
        "aggregate_id",
        "aggregate_version",
        "payload_sha256",
    }
    with pytest.raises(ValueError, match="sensitive"):
        SafeWorkError("PROVIDER_ERROR", "Authorization: Bearer secret")
    with pytest.raises(ValueError, match="opaque identifier"):
        WorkCommandReference(
            contract="quality.run.command",
            contract_version="1.0",
            aggregate_type="panel",
            aggregate_id="完整 Prompt 不允许进入 durable work",
            aggregate_version=1,
            payload_sha256=Sha256.digest(b"safe"),
        )

    database = Database(tmp_path / "schema.db")
    database.migrate()
    with sqlite3.connect(database.path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(work_items)")
        }
    assert "payload_sha256" in columns
    forbidden_columns = {"payload", "payload_json", "prompt", "token", "image_bytes", "body"}
    assert columns.isdisjoint(forbidden_columns)
