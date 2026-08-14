from __future__ import annotations

import sqlite3
from dataclasses import dataclass


class MigrationRegistryError(RuntimeError):
    pass


class UnknownAppliedMigrationError(MigrationRegistryError):
    def __init__(self, versions: set[int], known_latest: int) -> None:
        ordered = ", ".join(str(version) for version in sorted(versions))
        super().__init__(
            f"database contains unregistered migration version(s) {ordered}; "
            f"this build knows through version {known_latest}"
        )
        self.versions = frozenset(versions)
        self.known_latest = known_latest


@dataclass(frozen=True, slots=True)
class RegisteredMigration:
    version: int
    owner: str
    name: str
    statements: str
    compatibility: bool = False
    source_path: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise MigrationRegistryError("migration version must be positive")
        if not self.owner or not self.name or not self.statements.strip():
            raise MigrationRegistryError("migration owner, name, and statements are required")
        if self.compatibility and self.owner != "legacy_v02":
            raise MigrationRegistryError("only legacy_v02 migrations may use compatibility mode")
        if self.source_path is not None and not self.source_path.strip():
            raise MigrationRegistryError("migration source_path cannot be blank")


@dataclass(frozen=True, slots=True)
class MigrationRegistry:
    migrations: tuple[RegisteredMigration, ...]

    def __post_init__(self) -> None:
        versions = [migration.version for migration in self.migrations]
        if versions != sorted(versions):
            raise MigrationRegistryError("migrations must be registered in ascending order")
        if len(versions) != len(set(versions)):
            raise MigrationRegistryError("migration versions must be globally unique")
        if versions and versions != list(range(1, versions[-1] + 1)):
            raise MigrationRegistryError("global migration versions must be contiguous")

    @property
    def latest_version(self) -> int:
        return self.migrations[-1].version if self.migrations else 0

    @property
    def known_versions(self) -> frozenset[int]:
        return frozenset(migration.version for migration in self.migrations)


class ModuleMigrationRunner:
    def __init__(self, registry: MigrationRegistry) -> None:
        self.registry = registry

    def migrate(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
        }
        unknown = applied - self.registry.known_versions
        if unknown:
            raise UnknownAppliedMigrationError(unknown, self.registry.latest_version)

        for migration in self.registry.migrations:
            if migration.version in applied:
                continue
            try:
                connection.executescript(
                    f"""
                    BEGIN IMMEDIATE;
                    {migration.statements}
                    INSERT INTO schema_migrations(version) VALUES ({migration.version});
                    COMMIT;
                    """
                )
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
