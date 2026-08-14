from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.app.database import DATABASE_MIGRATION_REGISTRY, Database
from backend.app.platform.persistence.migrations import (
    MigrationRegistry,
    MigrationRegistryError,
    ModuleMigrationRunner,
    RegisteredMigration,
    UnknownAppliedMigrationError,
)

ROOT = Path(__file__).resolve().parents[2]
V02_DATABASE = ROOT / "tests" / "fixtures" / "v0.2" / "schema16.db.fixture"


def _versions(path: Path) -> list[int]:
    with sqlite3.connect(path) as connection:
        return [
            int(row[0])
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]


def test_empty_database_migrates_once_and_repeated_execution_is_idempotent(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "empty.db")
    database.migrate()
    first_versions = _versions(database.path)
    database.migrate()

    assert first_versions == list(range(1, DATABASE_MIGRATION_REGISTRY.latest_version + 1))
    assert _versions(database.path) == first_versions
    assert database.schema_version() == DATABASE_MIGRATION_REGISTRY.latest_version
    assert database.check()


def test_v02_schema16_fixture_forward_migration_is_safe_and_repeatable(
    tmp_path: Path,
) -> None:
    target = tmp_path / "schema16.db"
    shutil.copy2(V02_DATABASE, target)
    database = Database(target)
    with sqlite3.connect(target) as connection:
        before_counts = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("projects", "asset_versions", "page_versions", "export_revisions")
        )

    database.migrate()
    database.migrate()

    assert _versions(target) == list(range(1, DATABASE_MIGRATION_REGISTRY.latest_version + 1))
    assert database.schema_version() == DATABASE_MIGRATION_REGISTRY.latest_version
    with database.reader() as connection:
        assert (
            connection.execute("SELECT workflow_version FROM projects").fetchone()[0]
            == "legacy_v02"
        )
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        after_counts = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("projects", "asset_versions", "page_versions", "export_revisions")
        )
    assert after_counts == before_counts


def test_forward_migration_creates_a_verified_pre_migration_backup(tmp_path: Path) -> None:
    target = tmp_path / "schema16.db"
    shutil.copy2(V02_DATABASE, target)
    before_hash = Database._logical_database_hash(target)

    Database(target).migrate()

    backup = target.with_name("schema16.db.pre-migration-v16.bak")
    assert backup.is_file()
    assert Database._logical_database_hash(backup) == before_hash
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16


def test_failed_migration_restores_the_original_database(tmp_path: Path) -> None:
    target = tmp_path / "schema16.db"
    shutil.copy2(V02_DATABASE, target)
    before_hash = Database._logical_database_hash(target)
    database = Database(target)
    original = DATABASE_MIGRATION_REGISTRY.migrations[16]
    failing = RegisteredMigration(
        original.version,
        original.owner,
        original.name,
        "CREATE TABLE injected_partial_write(value TEXT); SELECT * FROM missing_table;",
        source_path=original.source_path,
    )
    registry = MigrationRegistry(
        (
            *DATABASE_MIGRATION_REGISTRY.migrations[:16],
            failing,
            *DATABASE_MIGRATION_REGISTRY.migrations[17:],
        )
    )

    with pytest.raises(sqlite3.Error):
        from backend.app import database as database_module

        previous = database_module.DATABASE_MIGRATION_REGISTRY
        database_module.DATABASE_MIGRATION_REGISTRY = registry
        try:
            database.migrate()
        finally:
            database_module.DATABASE_MIGRATION_REGISTRY = previous

    backup = target.with_name("schema16.db.pre-migration-v16.bak")
    assert backup.is_file()
    assert Database._logical_database_hash(backup) == before_hash
    assert Database._logical_database_hash(target) == before_hash
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'injected_partial_write'"
            ).fetchone()[0]
            == 0
        )


def test_schema29_database_rebuilds_prompt_idempotency_index_at_schema30(
    tmp_path: Path,
) -> None:
    target = tmp_path / "schema29.db"
    schema29 = MigrationRegistry(DATABASE_MIGRATION_REGISTRY.migrations[:29])
    with sqlite3.connect(target) as connection:
        ModuleMigrationRunner(schema29).migrate(connection)
        before = [
            str(row[2])
            for row in connection.execute("PRAGMA index_info(prompt_approval_idempotency)")
        ]
    assert before == ["idempotency_key"]

    Database(target).migrate()

    assert target.with_name("schema29.db.pre-migration-v29.bak").is_file()
    with sqlite3.connect(target) as connection:
        after = [
            str(row[2])
            for row in connection.execute("PRAGMA index_info(prompt_approval_idempotency)")
        ]
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 30
    assert after == ["prompt_bundle_version_id", "idempotency_key"]


def test_legacy_project_remains_readable_but_generation_requires_migration(
    tmp_path: Path,
) -> None:
    target = tmp_path / "legacy-read-only.db"
    shutil.copy2(V02_DATABASE, target)
    database = Database(target)
    database.migrate()
    with database.reader() as connection:
        project_id = str(
            connection.execute("SELECT project_id FROM projects LIMIT 1").fetchone()[0]
        )

    from backend.app.bibles.service import BibleService
    from backend.app.generation.queue import GenerationQueueService
    from backend.app.projects import ProjectService

    class UnusedPrompting:
        pass

    projects = ProjectService(database, tmp_path / "projects")
    assert projects.get(project_id).workflow_version == "legacy_v02"
    with pytest.raises(Exception) as write_blocked:
        projects.require_writable(project_id)
    assert getattr(write_blocked.value, "code", None) == "LEGACY_PROJECT_READ_ONLY"
    queue = GenerationQueueService(
        database,
        BibleService(database, projects, object()),  # type: ignore[arg-type]
        UnusedPrompting(),  # type: ignore[arg-type]
    )
    with pytest.raises(Exception) as raised:
        queue.estimate(project_id, "legacy-chapter", per_panel_cost_ceiling_anlas=10)
    assert getattr(raised.value, "code", None) == "LEGACY_PROJECT_MIGRATION_REQUIRED"


def test_unknown_higher_schema_version_fails_closed_before_running_migrations(
    tmp_path: Path,
) -> None:
    target = tmp_path / "future.db"
    shutil.copy2(V02_DATABASE, target)
    with sqlite3.connect(target) as connection:
        connection.execute("INSERT INTO schema_migrations(version) VALUES (999)")

    with pytest.raises(
        UnknownAppliedMigrationError,
        match=rf"999.*through version {DATABASE_MIGRATION_REGISTRY.latest_version}",
    ) as raised:
        Database(target).migrate()
    assert raised.value.versions == frozenset({999})
    assert _versions(target)[-1] == 999


def test_migration_registry_rejects_duplicates_gaps_and_out_of_order_versions() -> None:
    first = RegisteredMigration(1, "test", "first", "SELECT 1")
    duplicate = RegisteredMigration(1, "test", "duplicate", "SELECT 1")
    third = RegisteredMigration(3, "test", "third", "SELECT 1")

    with pytest.raises(MigrationRegistryError, match="globally unique"):
        MigrationRegistry((first, duplicate))
    with pytest.raises(MigrationRegistryError, match="contiguous"):
        MigrationRegistry((first, third))
    with pytest.raises(MigrationRegistryError, match="ascending"):
        MigrationRegistry((third, first))
