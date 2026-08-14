from __future__ import annotations

from pathlib import Path

from ....platform.persistence.migrations import RegisteredMigration

_SOURCE_PATH = "backend/app/modules/lineage/migrations/0022_artifact_graph.sql"
_SQL_PATH = Path(__file__).with_name("0022_artifact_graph.sql")

LINEAGE_MIGRATIONS: tuple[RegisteredMigration, ...] = (
    RegisteredMigration(
        version=22,
        owner="lineage",
        name="artifact_dependency_graph",
        statements=_SQL_PATH.read_text(encoding="utf-8"),
        source_path=_SOURCE_PATH,
    ),
)

__all__ = ["LINEAGE_MIGRATIONS"]
