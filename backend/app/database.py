from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            revision INTEGER NOT NULL DEFAULT 1,
            workspace_path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            project_id TEXT REFERENCES projects(project_id),
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE source_preflights (
            preflight_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            original_filename TEXT NOT NULL,
            staging_path TEXT NOT NULL UNIQUE,
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
            sha256 TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE source_files (
            source_file_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            preflight_id TEXT NOT NULL UNIQUE REFERENCES source_preflights(preflight_id),
            original_filename TEXT NOT NULL,
            original_path TEXT NOT NULL UNIQUE,
            normalized_path TEXT NOT NULL UNIQUE,
            encoding TEXT NOT NULL,
            byte_sha256 TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            character_count INTEGER NOT NULL CHECK(character_count >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE source_chapter_sets (
            chapter_set_id TEXT PRIMARY KEY,
            source_file_id TEXT NOT NULL REFERENCES source_files(source_file_id),
            version INTEGER NOT NULL CHECK(version >= 1),
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_file_id, version)
        );

        CREATE UNIQUE INDEX one_current_chapter_set_per_source
        ON source_chapter_sets(source_file_id)
        WHERE is_current = 1;

        CREATE TABLE source_chapters (
            chapter_id TEXT PRIMARY KEY,
            chapter_set_id TEXT NOT NULL REFERENCES source_chapter_sets(chapter_set_id),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
            title TEXT NOT NULL,
            start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
            end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
            text_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chapter_set_id, ordinal)
        );

        CREATE TABLE source_anchors (
            anchor_id TEXT PRIMARY KEY,
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            chapter_version INTEGER NOT NULL CHECK(chapter_version >= 1),
            start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
            end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
            excerpt_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX source_files_by_project ON source_files(project_id);
        CREATE INDEX source_preflights_by_project ON source_preflights(project_id);
        CREATE INDEX source_chapters_by_set ON source_chapters(chapter_set_id, ordinal);
        """,
    ),
    (
        3,
        """
        CREATE TABLE story_beat_sets (
            beat_set_id TEXT PRIMARY KEY,
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            version INTEGER NOT NULL CHECK(version >= 1),
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chapter_id, version)
        );

        CREATE UNIQUE INDEX one_current_beat_set_per_chapter
        ON story_beat_sets(chapter_id)
        WHERE is_current = 1;

        CREATE TABLE story_beats (
            beat_id TEXT PRIMARY KEY,
            beat_set_id TEXT NOT NULL REFERENCES story_beat_sets(beat_set_id),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
            anchor_id TEXT NOT NULL REFERENCES source_anchors(anchor_id),
            source_summary TEXT NOT NULL,
            resolution_status TEXT NOT NULL DEFAULT 'unresolved'
                CHECK(resolution_status IN (
                    'represented', 'condensed', 'omitted', 'unresolved'
                )),
            omission_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(beat_set_id, ordinal)
        );

        CREATE INDEX story_beat_sets_by_chapter
        ON story_beat_sets(chapter_id, version);

        CREATE INDEX story_beats_by_set
        ON story_beats(beat_set_id, ordinal);
        """,
    ),
)


class Database:
    """SQLite access with a process-local single-writer boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                applied = {
                    row["version"]
                    for row in connection.execute("SELECT version FROM schema_migrations")
                }
                for version, statements in MIGRATIONS:
                    if version in applied:
                        continue
                    connection.executescript(
                        f"""
                        BEGIN IMMEDIATE;
                        {statements}
                        INSERT INTO schema_migrations(version) VALUES ({version});
                        COMMIT;
                        """
                    )
            finally:
                connection.close()

    @contextlib.contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def writer(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
            finally:
                connection.close()

    def schema_version(self) -> int:
        with self.reader() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
        return int(row["version"] if row is not None else 0)

    def check(self) -> bool:
        try:
            with self.reader() as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
            return row is not None and row[0] == "ok"
        except sqlite3.Error:
            return False
