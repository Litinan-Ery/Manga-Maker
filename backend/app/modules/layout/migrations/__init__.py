"""Registered layout migrations only."""
from __future__ import annotations

from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

_V23_SOURCE_PATH = "backend/app/modules/layout/migrations/0023_layout_versions.sql"
_V24_SOURCE_PATH = "backend/app/modules/layout/migrations/0024_layout_commands.sql"

LAYOUT_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=23,
        owner="layout",
        name="layout_versions_and_approvals",
        statements=Path(__file__).with_name("0023_layout_versions.sql").read_text(
            encoding="utf-8"
        ),
        source_path=_V23_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=24,
        owner="layout",
        name="layout_command_idempotency_and_dimension_bindings",
        statements=Path(__file__).with_name("0024_layout_commands.sql").read_text(
            encoding="utf-8"
        ),
        source_path=_V24_SOURCE_PATH,
    ),
)

__all__ = ["LAYOUT_MIGRATIONS"]
