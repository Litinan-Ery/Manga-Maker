"""Registered text-execution migrations only."""

from __future__ import annotations

from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

_TEXT_MODEL_REMARK_SOURCE_PATH = (
    "backend/app/modules/text_execution/migrations/0032_text_model_remark_name.sql"
)

TEXT_EXECUTION_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=32,
        owner="text_execution",
        name="text_model_remark_name",
        statements=Path(__file__)
        .with_name("0032_text_model_remark_name.sql")
        .read_text(encoding="utf-8"),
        source_path=_TEXT_MODEL_REMARK_SOURCE_PATH,
    ),
)

__all__ = ["TEXT_EXECUTION_MIGRATIONS"]
