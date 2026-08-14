from __future__ import annotations

from pathlib import Path

from ...persistence.migrations import RegisteredMigration

_SOURCE_PATH = "backend/app/platform/recovery/migrations/0021_recovery.sql"
_SQL_PATH = Path(__file__).with_name("0021_recovery.sql")

RECOVERY_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=21,
        owner="recovery",
        name="recovery_reports",
        statements=_SQL_PATH.read_text(encoding="utf-8"),
        source_path=_SOURCE_PATH,
    ),
)

__all__ = ["RECOVERY_MIGRATIONS"]
