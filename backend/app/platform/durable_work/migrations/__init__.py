from __future__ import annotations

from pathlib import Path

from ...persistence.migrations import RegisteredMigration

_CORE_SOURCE_PATH = "backend/app/platform/durable_work/migrations/0017_durable_work.sql"
_CORE_SQL_PATH = Path(__file__).with_name("0017_durable_work.sql")
_LEASE_SOURCE_PATH = "backend/app/platform/durable_work/migrations/0018_worker_leases.sql"
_LEASE_SQL_PATH = Path(__file__).with_name("0018_worker_leases.sql")
_OUTBOX_SOURCE_PATH = "backend/app/platform/durable_work/migrations/0019_outbox.sql"
_OUTBOX_SQL_PATH = Path(__file__).with_name("0019_outbox.sql")
_DELIVERY_SOURCE_PATH = (
    "backend/app/platform/durable_work/migrations/0020_outbox_delivery.sql"
)
_DELIVERY_SQL_PATH = Path(__file__).with_name("0020_outbox_delivery.sql")

DURABLE_WORK_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=17,
        owner="durable_work",
        name="durable_work_core",
        statements=_CORE_SQL_PATH.read_text(encoding="utf-8"),
        source_path=_CORE_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=18,
        owner="durable_work",
        name="durable_worker_leases",
        statements=_LEASE_SQL_PATH.read_text(encoding="utf-8"),
        source_path=_LEASE_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=19,
        owner="durable_work",
        name="durable_outbox",
        statements=_OUTBOX_SQL_PATH.read_text(encoding="utf-8"),
        source_path=_OUTBOX_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=20,
        owner="durable_work",
        name="outbox_delivery_attempts",
        statements=_DELIVERY_SQL_PATH.read_text(encoding="utf-8"),
        source_path=_DELIVERY_SOURCE_PATH,
    ),
)

__all__ = ["DURABLE_WORK_MIGRATIONS"]
