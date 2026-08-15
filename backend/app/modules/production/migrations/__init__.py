"""Registered production migrations only."""

from __future__ import annotations

from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

_LAYOUT_SOURCE_PATH = "backend/app/modules/production/migrations/0026_generation_layout_freeze.sql"
_PROVIDER_SPEC_SOURCE_PATH = (
    "backend/app/modules/production/migrations/0027_provider_execution_specs.sql"
)
_APPROVAL_FREEZE_SOURCE_PATH = (
    "backend/app/modules/production/migrations/0029_generation_approval_freeze.sql"
)
_VERIFICATION_CALL_AUDIT_SOURCE_PATH = (
    "backend/app/modules/production/migrations/0031_generation_verification_call_audit.sql"
)

PRODUCTION_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=26,
        owner="production",
        name="generation_layout_freeze",
        statements=Path(__file__)
        .with_name("0026_generation_layout_freeze.sql")
        .read_text(encoding="utf-8"),
        source_path=_LAYOUT_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=27,
        owner="production",
        name="provider_execution_specs",
        statements=Path(__file__)
        .with_name("0027_provider_execution_specs.sql")
        .read_text(encoding="utf-8"),
        source_path=_PROVIDER_SPEC_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=29,
        owner="production",
        name="generation_approval_freeze",
        statements=Path(__file__).with_name(
            "0029_generation_approval_freeze.sql"
        ).read_text(encoding="utf-8"),
        source_path=_APPROVAL_FREEZE_SOURCE_PATH,
    ),
    RegisteredMigration(
        version=31,
        owner="production",
        name="generation_verification_call_audit",
        statements=Path(__file__).with_name(
            "0031_generation_verification_call_audit.sql"
        ).read_text(encoding="utf-8"),
        source_path=_VERIFICATION_CALL_AUDIT_SOURCE_PATH,
    ),
)

__all__ = ["PRODUCTION_MIGRATIONS"]
