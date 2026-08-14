"""Registered project-source migrations only."""

from __future__ import annotations

from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

_SOURCE_PATH = (
    "backend/app/modules/project_source/migrations/0025_project_workflow_version.sql"
)

PROJECT_SOURCE_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=25,
        owner="project_source",
        name="project_workflow_version",
        statements=Path(__file__).with_name("0025_project_workflow_version.sql").read_text(
            encoding="utf-8"
        ),
        source_path=_SOURCE_PATH,
    ),
)

__all__ = ["PROJECT_SOURCE_MIGRATIONS"]
