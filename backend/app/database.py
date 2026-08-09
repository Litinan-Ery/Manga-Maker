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
    (
        4,
        """
        CREATE TABLE text_model_configs (
            project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
            provider TEXT NOT NULL DEFAULT 'openai-compatible',
            base_url TEXT NOT NULL,
            model TEXT NOT NULL,
            credential_profile_id TEXT NOT NULL,
            timeout_seconds REAL NOT NULL CHECK(timeout_seconds >= 1 AND timeout_seconds <= 180),
            temperature REAL NOT NULL CHECK(temperature >= 0 AND temperature <= 2),
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE storyboards (
            storyboard_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, chapter_id)
        );

        CREATE TABLE storyboard_versions (
            storyboard_version_id TEXT PRIMARY KEY,
            storyboard_id TEXT NOT NULL REFERENCES storyboards(storyboard_id),
            version INTEGER NOT NULL CHECK(version >= 1),
            beat_set_id TEXT NOT NULL REFERENCES story_beat_sets(beat_set_id),
            chapter_version INTEGER NOT NULL CHECK(chapter_version >= 1),
            page_budget INTEGER NOT NULL CHECK(page_budget >= 1 AND page_budget <= 64),
            source_fingerprint TEXT NOT NULL,
            document_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(storyboard_id, version)
        );

        CREATE UNIQUE INDEX one_current_storyboard_version
        ON storyboard_versions(storyboard_id)
        WHERE is_current = 1;

        CREATE TABLE storyboard_approvals (
            approval_id TEXT PRIMARY KEY,
            storyboard_version_id TEXT NOT NULL UNIQUE
                REFERENCES storyboard_versions(storyboard_version_id),
            approval_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX storyboards_by_project
        ON storyboards(project_id, chapter_id);

        CREATE INDEX storyboard_versions_by_storyboard
        ON storyboard_versions(storyboard_id, version);
        """,
    ),
    (
        5,
        """
        CREATE TABLE character_bibles (
            character_bible_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, chapter_id)
        );

        CREATE TABLE character_bible_versions (
            character_bible_version_id TEXT PRIMARY KEY,
            character_bible_id TEXT NOT NULL REFERENCES character_bibles(character_bible_id),
            version INTEGER NOT NULL CHECK(version >= 1),
            storyboard_version_id TEXT NOT NULL
                REFERENCES storyboard_versions(storyboard_version_id),
            document_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(character_bible_id, version)
        );

        CREATE UNIQUE INDEX one_current_character_bible_version
        ON character_bible_versions(character_bible_id)
        WHERE is_current = 1;

        CREATE TABLE character_bible_approvals (
            approval_id TEXT PRIMARY KEY,
            character_bible_version_id TEXT NOT NULL UNIQUE
                REFERENCES character_bible_versions(character_bible_version_id),
            approval_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE style_bibles (
            style_bible_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, chapter_id)
        );

        CREATE TABLE style_bible_versions (
            style_bible_version_id TEXT PRIMARY KEY,
            style_bible_id TEXT NOT NULL REFERENCES style_bibles(style_bible_id),
            version INTEGER NOT NULL CHECK(version >= 1),
            storyboard_version_id TEXT NOT NULL
                REFERENCES storyboard_versions(storyboard_version_id),
            document_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(style_bible_id, version)
        );

        CREATE UNIQUE INDEX one_current_style_bible_version
        ON style_bible_versions(style_bible_id)
        WHERE is_current = 1;

        CREATE TABLE style_bible_approvals (
            approval_id TEXT PRIMARY KEY,
            style_bible_version_id TEXT NOT NULL UNIQUE
                REFERENCES style_bible_versions(style_bible_version_id),
            approval_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE reference_assets (
            reference_asset_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            bible_kind TEXT NOT NULL CHECK(bible_kind IN ('character', 'style')),
            character_id TEXT,
            original_filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK(byte_size > 0),
            width INTEGER NOT NULL CHECK(width > 0),
            height INTEGER NOT NULL CHECK(height > 0),
            sha256 TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            source_note TEXT NOT NULL,
            rights_confirmed INTEGER NOT NULL CHECK(rights_confirmed = 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(
                (bible_kind = 'character' AND character_id IS NOT NULL)
                OR (bible_kind = 'style' AND character_id IS NULL)
            )
        );

        CREATE INDEX character_bible_versions_by_bible
        ON character_bible_versions(character_bible_id, version);

        CREATE INDEX style_bible_versions_by_bible
        ON style_bible_versions(style_bible_id, version);

        CREATE INDEX reference_assets_by_project
        ON reference_assets(project_id, bible_kind, created_at);
        """,
    ),
    (
        6,
        """
        CREATE TABLE novelai_configs (
            project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
            model_label TEXT NOT NULL,
            provider_model_id TEXT NOT NULL,
            inpaint_model_id TEXT NOT NULL,
            credential_profile_id TEXT NOT NULL,
            timeout_seconds REAL NOT NULL CHECK(timeout_seconds >= 1 AND timeout_seconds <= 180),
            contract_sha256 TEXT NOT NULL,
            mapping_version TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
            last_connection_status TEXT CHECK(
                last_connection_status IS NULL
                OR last_connection_status IN ('ok', 'failed')
            ),
            last_connection_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        7,
        """
        CREATE TABLE generation_jobs (
            job_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            storyboard_version_id TEXT NOT NULL
                REFERENCES storyboard_versions(storyboard_version_id),
            character_bible_version_id TEXT NOT NULL
                REFERENCES character_bible_versions(character_bible_version_id),
            style_bible_version_id TEXT NOT NULL
                REFERENCES style_bible_versions(style_bible_version_id),
            novelai_config_revision INTEGER NOT NULL CHECK(novelai_config_revision >= 1),
            provider_model_id TEXT NOT NULL,
            mapping_version TEXT NOT NULL,
            contract_sha256 TEXT NOT NULL,
            plan_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'draft', 'awaiting_approval', 'queued', 'running', 'paused',
                'needs_review', 'failed', 'completed', 'canceled'
            )),
            user_action_id TEXT NOT NULL,
            page_count INTEGER NOT NULL CHECK(page_count >= 1),
            panel_count INTEGER NOT NULL CHECK(panel_count >= 1),
            max_calls INTEGER NOT NULL CHECK(max_calls >= 1),
            max_cost_anlas INTEGER NOT NULL CHECK(max_cost_anlas >= 0),
            estimated_cost_upper_anlas INTEGER NOT NULL
                CHECK(estimated_cost_upper_anlas >= 0),
            cost_basis TEXT NOT NULL,
            calls_started INTEGER NOT NULL DEFAULT 0 CHECK(calls_started >= 0),
            calls_completed INTEGER NOT NULL DEFAULT 0 CHECK(calls_completed >= 0),
            recorded_cost_anlas INTEGER NOT NULL DEFAULT 0 CHECK(recorded_cost_anlas >= 0),
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            paused_at TEXT,
            completed_at TEXT
        );

        CREATE TABLE generation_job_items (
            item_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES generation_jobs(job_id),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
            page_id TEXT NOT NULL,
            page_number INTEGER NOT NULL CHECK(page_number >= 1),
            panel_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'queued', 'running', 'needs_review', 'failed', 'completed', 'canceled'
            )),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            cost_ceiling_anlas INTEGER NOT NULL CHECK(cost_ceiling_anlas >= 0),
            recorded_cost_anlas INTEGER NOT NULL DEFAULT 0 CHECK(recorded_cost_anlas >= 0),
            active_attempt_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_id, ordinal),
            UNIQUE(job_id, panel_id)
        );

        CREATE TABLE generation_attempts (
            attempt_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL REFERENCES generation_job_items(item_id),
            attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
            status TEXT NOT NULL CHECK(status IN (
                'running', 'needs_review', 'failed', 'completed', 'canceled'
            )),
            cost_ceiling_anlas INTEGER NOT NULL CHECK(cost_ceiling_anlas >= 0),
            recorded_cost_anlas INTEGER CHECK(
                recorded_cost_anlas IS NULL OR recorded_cost_anlas >= 0
            ),
            error_code TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            UNIQUE(item_id, attempt_number)
        );

        CREATE UNIQUE INDEX one_global_running_generation_attempt
        ON generation_attempts(status)
        WHERE status = 'running';

        CREATE UNIQUE INDEX one_active_generation_job_per_project
        ON generation_jobs(project_id)
        WHERE status IN ('queued', 'running', 'paused', 'needs_review');

        CREATE INDEX generation_jobs_by_project
        ON generation_jobs(project_id, created_at);

        CREATE INDEX generation_job_items_by_job
        ON generation_job_items(job_id, ordinal);
        """,
    ),
    (
        8,
        """
        ALTER TABLE generation_jobs
        ADD COLUMN credential_profile_id TEXT NOT NULL DEFAULT '';

        ALTER TABLE generation_jobs
        ADD COLUMN timeout_seconds REAL NOT NULL DEFAULT 30
            CHECK(timeout_seconds >= 1 AND timeout_seconds <= 180);

        ALTER TABLE generation_jobs
        ADD COLUMN allocated_cost_anlas INTEGER NOT NULL DEFAULT 0
            CHECK(allocated_cost_anlas >= 0);

        ALTER TABLE generation_jobs
        ADD COLUMN unverified_cost_calls INTEGER NOT NULL DEFAULT 0
            CHECK(unverified_cost_calls >= 0);

        ALTER TABLE generation_jobs
        ADD COLUMN items_claimed INTEGER NOT NULL DEFAULT 0
            CHECK(items_claimed >= 0);

        ALTER TABLE generation_attempts
        ADD COLUMN provider_request_started INTEGER NOT NULL DEFAULT 0
            CHECK(provider_request_started IN (0, 1));

        ALTER TABLE generation_attempts
        ADD COLUMN provider_started_at TEXT;

        UPDATE generation_jobs
        SET credential_profile_id = COALESCE((
                SELECT credential_profile_id FROM novelai_configs nc
                WHERE nc.project_id = generation_jobs.project_id
                  AND nc.revision = generation_jobs.novelai_config_revision
            ), ''),
            timeout_seconds = COALESCE((
                SELECT timeout_seconds FROM novelai_configs nc
                WHERE nc.project_id = generation_jobs.project_id
                  AND nc.revision = generation_jobs.novelai_config_revision
            ), 30),
            items_claimed = calls_started,
            calls_started = 0;

        CREATE TABLE generation_specs (
            spec_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL UNIQUE REFERENCES generation_attempts(attempt_id),
            item_id TEXT NOT NULL REFERENCES generation_job_items(item_id),
            schema_version TEXT NOT NULL,
            document_json TEXT NOT NULL,
            spec_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE asset_versions (
            asset_version_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            panel_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version >= 1),
            parent_asset_version_id TEXT REFERENCES asset_versions(asset_version_id),
            job_id TEXT NOT NULL REFERENCES generation_jobs(job_id),
            item_id TEXT NOT NULL REFERENCES generation_job_items(item_id),
            attempt_id TEXT NOT NULL UNIQUE REFERENCES generation_attempts(attempt_id),
            spec_id TEXT NOT NULL UNIQUE REFERENCES generation_specs(spec_id),
            status TEXT NOT NULL CHECK(status IN ('ready', 'failed')),
            original_relative_path TEXT NOT NULL UNIQUE,
            provenance_relative_path TEXT NOT NULL UNIQUE,
            image_sha256 TEXT NOT NULL,
            width INTEGER NOT NULL CHECK(width > 0),
            height INTEGER NOT NULL CHECK(height > 0),
            seed INTEGER NOT NULL CHECK(seed >= 0),
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, panel_id, version)
        );

        ALTER TABLE generation_job_items
        ADD COLUMN asset_version_id TEXT REFERENCES asset_versions(asset_version_id);

        CREATE UNIQUE INDEX one_current_asset_version_per_panel
        ON asset_versions(project_id, panel_id)
        WHERE is_current = 1 AND status = 'ready';

        CREATE INDEX asset_versions_by_panel
        ON asset_versions(project_id, panel_id, version);
        """,
    ),
    (
        9,
        """
        CREATE TABLE comic_pages (
            page_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
            page_number INTEGER NOT NULL CHECK(page_number >= 1),
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, chapter_id, page_number)
        );

        CREATE TABLE page_versions (
            page_version_id TEXT PRIMARY KEY,
            page_id TEXT NOT NULL REFERENCES comic_pages(page_id),
            version INTEGER NOT NULL CHECK(version >= 1),
            parent_page_version_id TEXT REFERENCES page_versions(page_version_id),
            storyboard_version_id TEXT NOT NULL
                REFERENCES storyboard_versions(storyboard_version_id),
            schema_version TEXT NOT NULL,
            document_json TEXT NOT NULL,
            document_sha256 TEXT NOT NULL,
            rendered_relative_path TEXT NOT NULL UNIQUE,
            render_sha256 TEXT NOT NULL,
            renderer_version TEXT NOT NULL,
            font_sha256 TEXT NOT NULL,
            is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(page_id, version)
        );

        CREATE UNIQUE INDEX one_current_page_version
        ON page_versions(page_id)
        WHERE is_current = 1;

        CREATE INDEX comic_pages_by_chapter
        ON comic_pages(project_id, chapter_id, page_number);

        CREATE INDEX page_versions_by_page
        ON page_versions(page_id, version);
        """,
    ),
    (
        10,
        """
        CREATE TABLE mask_assets (
            mask_asset_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            panel_id TEXT NOT NULL,
            parent_asset_version_id TEXT NOT NULL
                REFERENCES asset_versions(asset_version_id),
            relative_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            width INTEGER NOT NULL CHECK(width > 0),
            height INTEGER NOT NULL CHECK(height > 0),
            selected_pixel_count INTEGER NOT NULL CHECK(selected_pixel_count > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, parent_asset_version_id, sha256)
        );

        ALTER TABLE generation_jobs
        ADD COLUMN operation_kind TEXT NOT NULL DEFAULT 'chapter_generate'
            CHECK(operation_kind IN (
                'chapter_generate', 'panel_reroll', 'page_reroll', 'inpaint'
            ));

        ALTER TABLE generation_jobs
        ADD COLUMN target_page_id TEXT;

        ALTER TABLE generation_jobs
        ADD COLUMN target_page_version_id TEXT REFERENCES page_versions(page_version_id);

        ALTER TABLE generation_jobs
        ADD COLUMN result_page_version_id TEXT REFERENCES page_versions(page_version_id);

        ALTER TABLE generation_job_items
        ADD COLUMN operation_kind TEXT NOT NULL DEFAULT 'chapter_generate'
            CHECK(operation_kind IN (
                'chapter_generate', 'panel_reroll', 'page_reroll', 'inpaint'
            ));

        ALTER TABLE generation_job_items
        ADD COLUMN parent_asset_version_id TEXT REFERENCES asset_versions(asset_version_id);

        ALTER TABLE generation_job_items
        ADD COLUMN mask_asset_id TEXT REFERENCES mask_assets(mask_asset_id);

        ALTER TABLE generation_job_items
        ADD COLUMN edit_prompt TEXT;

        ALTER TABLE generation_job_items
        ADD COLUMN inpaint_strength REAL
            CHECK(inpaint_strength IS NULL OR (
                inpaint_strength >= 0.1 AND inpaint_strength <= 1
            ));

        ALTER TABLE page_versions
        ADD COLUMN source_job_id TEXT REFERENCES generation_jobs(job_id);

        CREATE UNIQUE INDEX one_page_version_per_source_job
        ON page_versions(source_job_id)
        WHERE source_job_id IS NOT NULL;

        CREATE INDEX mask_assets_by_parent
        ON mask_assets(project_id, parent_asset_version_id, created_at);

        CREATE INDEX revision_jobs_by_page
        ON generation_jobs(project_id, target_page_id, created_at)
        WHERE target_page_id IS NOT NULL;
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
