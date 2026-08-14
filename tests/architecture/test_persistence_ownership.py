from __future__ import annotations

import ast
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pytest

from backend.app.database import DATABASE_MIGRATION_REGISTRY, MIGRATIONS
from backend.app.platform.persistence.ownership import (
    TABLE_OWNER_ENTRIES,
    TABLE_OWNERS,
    TABLE_SCHEMA_VERSIONS,
    OwnershipRegistryError,
    TableOwner,
    build_table_owner_registry,
)

ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = ROOT / "backend" / "app" / "modules"
DURABLE_WORK_ROOT = ROOT / "backend" / "app" / "platform" / "durable_work"
RECOVERY_ROOT = ROOT / "backend" / "app" / "platform" / "recovery"
V02_DATABASE = ROOT / "tests" / "fixtures" / "v0.2" / "schema16.db.fixture"

WRITE_TARGET = re.compile(
    r"\b(?:"
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"ALTER\s+TABLE|"
    r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"INSERT(?:\s+OR\s+\w+)?\s+INTO|"
    r"REPLACE\s+INTO|"
    r"UPDATE|"
    r"DELETE\s+FROM"
    r")\s+[\"`\[]?([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
REFERENCE_TARGET = re.compile(
    r"\bREFERENCES\s+[\"`\[]?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE
)
SQL_MARKER = re.compile(
    r"\b(?:SELECT|CREATE|ALTER|DROP|INSERT|REPLACE|UPDATE|DELETE)\b", re.IGNORECASE
)


def _module_sql() -> Iterable[tuple[str, Path, int, str]]:
    roots = (
        (MODULES_ROOT, None),
        (DURABLE_WORK_ROOT, "durable_work"),
        (RECOVERY_ROOT, "recovery"),
    )
    for root, fixed_owner in roots:
        for path in sorted(root.rglob("*.sql")):
            owner = fixed_owner or path.relative_to(root).parts[0]
            yield owner, path, 1, path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            owner = fixed_owner or path.relative_to(root).parts[0]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and SQL_MARKER.search(node.value)
                ):
                    yield owner, path, node.lineno, node.value


def test_v02_fixture_tables_have_one_exact_registered_owner() -> None:
    with sqlite3.connect(V02_DATABASE) as connection:
        actual = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert len(TABLE_OWNER_ENTRIES) == len(TABLE_OWNERS)
    expected = {
        table for table, introduced_in in TABLE_SCHEMA_VERSIONS.items() if introduced_in <= 16
    }
    assert actual == expected, (
        f"unregistered={sorted(actual - expected)}; "
        f"orphaned={sorted(expected - actual)}"
    )


def test_duplicate_table_owners_fail_closed_with_both_owners() -> None:
    with pytest.raises(OwnershipRegistryError, match=r"alpha.*beta"):
        build_table_owner_registry(
            (TableOwner("duplicate_table", "alpha"), TableOwner("duplicate_table", "beta"))
        )


def test_database_migrations_are_globally_registered_and_module_files_are_not_orphaned() -> None:
    compatibility = [
        (migration.version, migration.statements)
        for migration in DATABASE_MIGRATION_REGISTRY.migrations
        if migration.compatibility
    ]
    assert compatibility == list(MIGRATIONS)
    names = [migration.name for migration in DATABASE_MIGRATION_REGISTRY.migrations]
    assert len(names) == len(set(names)), "migration names must be globally unique"

    disk_paths = {
        path.relative_to(ROOT).as_posix()
        for migration_root in (MODULES_ROOT, DURABLE_WORK_ROOT, RECOVERY_ROOT)
        for path in migration_root.rglob("migrations/*.sql")
    }
    registered_paths = {
        migration.source_path
        for migration in DATABASE_MIGRATION_REGISTRY.migrations
        if migration.source_path is not None
    }
    assert disk_paths == registered_paths, (
        f"unregistered migration files={sorted(disk_paths - registered_paths)}; "
        f"missing files={sorted(registered_paths - disk_paths)}"
    )
    for migration in DATABASE_MIGRATION_REGISTRY.migrations:
        if migration.compatibility:
            assert migration.owner == "legacy_v02" and migration.source_path is None
            continue
        assert migration.source_path is not None
        allowed_prefixes = (
            f"backend/app/modules/{migration.owner}/migrations/",
            f"backend/app/platform/{migration.owner}/migrations/",
        )
        assert migration.source_path.startswith(allowed_prefixes)


def test_module_sql_writes_only_owner_tables_and_has_no_cross_module_cascade() -> None:
    violations: list[str] = []
    for source_owner, path, line, sql in _module_sql():
        location = f"{path.relative_to(ROOT)}:{line}"
        for match in WRITE_TARGET.finditer(sql):
            table = match.group(1)
            table_owner = TABLE_OWNERS.get(table)
            if table_owner is None:
                violations.append(f"{location}: unregistered write target {table}")
            elif table_owner != source_owner:
                violations.append(
                    f"{location}: cross-module SQL write {source_owner} -> {table_owner}.{table}"
                )
        if re.search(r"\bON\s+(?:DELETE|UPDATE)\s+CASCADE\b", sql, re.IGNORECASE):
            for match in REFERENCE_TARGET.finditer(sql):
                table = match.group(1)
                target_owner = TABLE_OWNERS.get(table)
                if target_owner is None:
                    violations.append(f"{location}: cascade references unregistered table {table}")
                elif target_owner != source_owner:
                    violations.append(
                        f"{location}: cross-module cascade {source_owner} -> "
                        f"{target_owner}.{table}"
                    )
    assert not violations, "\n".join(violations)
