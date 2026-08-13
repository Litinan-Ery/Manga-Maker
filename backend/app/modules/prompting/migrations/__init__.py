"""Registered prompting migrations only."""

from __future__ import annotations

from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

_APPROVAL_SOURCE_PATH = (
    "backend/app/modules/prompting/migrations/0028_prompt_approval_idempotency.sql"
)

PROMPTING_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=28,
        owner="prompting",
        name="prompt_approval_idempotency",
        statements=Path(__file__).with_name(
            "0028_prompt_approval_idempotency.sql"
        ).read_text(encoding="utf-8"),
        source_path=_APPROVAL_SOURCE_PATH,
    ),
)

__all__ = ["PROMPTING_MIGRATIONS"]
