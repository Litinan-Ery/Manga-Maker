from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

from PIL import Image

from ..database import Database
from ..errors import ApplicationError
from ..generation.assets import canonical_json, fsync_directory, write_synced
from ..ids import uuid7
from ..projects import PROJECT_DIRECTORIES, ProjectService

EXPORT_SCHEMA_VERSION = "1.0"
PACKAGE_SCHEMA_VERSION = "1.0"
MAX_PACKAGE_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_FILES = 20_000
MAX_PACKAGE_FILE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
CREDENTIAL_REENTRY_PROFILE_ID = "restore-required"

# Tables are listed in dependency order. Configuration profile references are retained only
# as empty strings; the encrypted vault itself is outside every project and is never read.
PROJECT_TABLE_QUERIES: tuple[tuple[str, str], ...] = (
    ("projects", "SELECT * FROM projects WHERE project_id = ?"),
    ("audit_events", "SELECT * FROM audit_events WHERE project_id = ? ORDER BY created_at"),
    ("source_preflights", "SELECT * FROM source_preflights WHERE project_id = ?"),
    ("source_files", "SELECT * FROM source_files WHERE project_id = ?"),
    (
        "source_chapter_sets",
        """SELECT cs.* FROM source_chapter_sets cs JOIN source_files sf
           ON sf.source_file_id = cs.source_file_id WHERE sf.project_id = ?""",
    ),
    (
        "source_chapters",
        """SELECT c.* FROM source_chapters c JOIN source_chapter_sets cs
           ON cs.chapter_set_id = c.chapter_set_id JOIN source_files sf
           ON sf.source_file_id = cs.source_file_id WHERE sf.project_id = ?""",
    ),
    (
        "source_anchors",
        """SELECT a.* FROM source_anchors a JOIN source_chapters c
           ON c.chapter_id = a.chapter_id JOIN source_chapter_sets cs
           ON cs.chapter_set_id = c.chapter_set_id JOIN source_files sf
           ON sf.source_file_id = cs.source_file_id WHERE sf.project_id = ?""",
    ),
    (
        "story_beat_sets",
        """SELECT bs.* FROM story_beat_sets bs JOIN source_chapters c
           ON c.chapter_id = bs.chapter_id JOIN source_chapter_sets cs
           ON cs.chapter_set_id = c.chapter_set_id JOIN source_files sf
           ON sf.source_file_id = cs.source_file_id WHERE sf.project_id = ?""",
    ),
    (
        "story_beats",
        """SELECT b.* FROM story_beats b JOIN story_beat_sets bs
           ON bs.beat_set_id = b.beat_set_id JOIN source_chapters c
           ON c.chapter_id = bs.chapter_id JOIN source_chapter_sets cs
           ON cs.chapter_set_id = c.chapter_set_id JOIN source_files sf
           ON sf.source_file_id = cs.source_file_id WHERE sf.project_id = ?""",
    ),
    ("text_model_configs", "SELECT * FROM text_model_configs WHERE project_id = ?"),
    ("storyboards", "SELECT * FROM storyboards WHERE project_id = ?"),
    (
        "storyboard_versions",
        """SELECT v.* FROM storyboard_versions v JOIN storyboards s
           ON s.storyboard_id = v.storyboard_id WHERE s.project_id = ?""",
    ),
    (
        "storyboard_approvals",
        """SELECT a.* FROM storyboard_approvals a JOIN storyboard_versions v
           ON v.storyboard_version_id = a.storyboard_version_id JOIN storyboards s
           ON s.storyboard_id = v.storyboard_id WHERE s.project_id = ?""",
    ),
    ("character_bibles", "SELECT * FROM character_bibles WHERE project_id = ?"),
    (
        "character_bible_versions",
        """SELECT v.* FROM character_bible_versions v JOIN character_bibles b
           ON b.character_bible_id = v.character_bible_id WHERE b.project_id = ?""",
    ),
    (
        "character_bible_approvals",
        """SELECT a.* FROM character_bible_approvals a JOIN character_bible_versions v
           ON v.character_bible_version_id = a.character_bible_version_id
           JOIN character_bibles b ON b.character_bible_id = v.character_bible_id
           WHERE b.project_id = ?""",
    ),
    ("style_bibles", "SELECT * FROM style_bibles WHERE project_id = ?"),
    (
        "style_bible_versions",
        """SELECT v.* FROM style_bible_versions v JOIN style_bibles b
           ON b.style_bible_id = v.style_bible_id WHERE b.project_id = ?""",
    ),
    (
        "style_bible_approvals",
        """SELECT a.* FROM style_bible_approvals a JOIN style_bible_versions v
           ON v.style_bible_version_id = a.style_bible_version_id JOIN style_bibles b
           ON b.style_bible_id = v.style_bible_id WHERE b.project_id = ?""",
    ),
    ("reference_assets", "SELECT * FROM reference_assets WHERE project_id = ?"),
    ("novelai_configs", "SELECT * FROM novelai_configs WHERE project_id = ?"),
    ("generation_jobs", "SELECT * FROM generation_jobs WHERE project_id = ?"),
    (
        "generation_job_items",
        """SELECT i.* FROM generation_job_items i JOIN generation_jobs j
           ON j.job_id = i.job_id WHERE j.project_id = ?""",
    ),
    (
        "generation_attempts",
        """SELECT a.* FROM generation_attempts a JOIN generation_job_items i
           ON i.item_id = a.item_id JOIN generation_jobs j ON j.job_id = i.job_id
           WHERE j.project_id = ?""",
    ),
    (
        "generation_specs",
        """SELECT s.* FROM generation_specs s JOIN generation_job_items i
           ON i.item_id = s.item_id JOIN generation_jobs j ON j.job_id = i.job_id
           WHERE j.project_id = ?""",
    ),
    ("asset_versions", "SELECT * FROM asset_versions WHERE project_id = ?"),
    ("comic_pages", "SELECT * FROM comic_pages WHERE project_id = ?"),
    (
        "page_versions",
        """SELECT v.* FROM page_versions v JOIN comic_pages p ON p.page_id = v.page_id
           WHERE p.project_id = ?""",
    ),
    ("mask_assets", "SELECT * FROM mask_assets WHERE project_id = ?"),
)


class ExportService:
    def __init__(self, database: Database, projects: ProjectService) -> None:
        self.database = database
        self.projects = projects
        self.imports_dir = projects.projects_dir.parent / "imports"
        self.imports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def preflight_export(
        self,
        project_id: str,
        chapter_id: str,
        page_version_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        pages, project, chapter = self._selected_pages(project_id, chapter_id, page_version_ids)
        selection = [self._selection_entry(row, index) for index, row in enumerate(pages, 1)]
        fingerprint = self._selection_fingerprint(project_id, chapter_id, selection)
        return {
            "project_id": project_id,
            "project_title": str(project["title"]),
            "chapter_id": chapter_id,
            "chapter_title": str(chapter["title"]),
            "schema_version": EXPORT_SCHEMA_VERSION,
            "page_count": len(selection),
            "pages": selection,
            "blockers": [],
            "warnings": [],
            "plan_fingerprint": fingerprint,
            "formats": ["engineering_package", "png", "pdf", "cbz"],
            "external_requests_started": 0,
        }

    def create_export(
        self,
        project_id: str,
        chapter_id: str,
        page_version_ids: list[str],
        plan_fingerprint: str,
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ApplicationError(
                "EXPORT_CONFIRMATION_REQUIRED", "导出前必须确认固定页面版本和顺序。", 422
            )
        plan = self.preflight_export(project_id, chapter_id, page_version_ids)
        if plan["plan_fingerprint"] != plan_fingerprint:
            raise ApplicationError(
                "EXPORT_PLAN_STALE", "页面版本已经变化，请重新预检后再导出。", 409
            )

        export_revision_id = str(uuid7())
        workspace = self.projects.workspace_path(project_id)
        export_root = workspace / "exports"
        staging = export_root / f".staging-{export_revision_id}"
        failed = export_root / f".failed-{export_revision_id}"
        final = export_root / export_revision_id
        staging.mkdir(mode=0o700, parents=False, exist_ok=False)
        selection_json = canonical_json(plan["pages"])
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT INTO export_revisions(
                    export_revision_id, project_id, chapter_id, status,
                    schema_version, page_selection_json, selection_sha256
                ) VALUES (?, ?, ?, 'staging', ?, ?, ?)
                """,
                (
                    export_revision_id,
                    project_id,
                    chapter_id,
                    EXPORT_SCHEMA_VERSION,
                    selection_json,
                    plan_fingerprint,
                ),
            )
        try:
            files = self._build_export(staging, workspace, export_revision_id, plan)
            self._validate_published_export(staging, plan, files)
            os.replace(staging, final)
            fsync_directory(export_root)
            with self.database.writer() as connection:
                connection.execute(
                    """
                    UPDATE export_revisions SET status = 'completed',
                        export_directory_relative_path = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE export_revision_id = ? AND status = 'staging'
                    """,
                    (str(final.relative_to(workspace)), export_revision_id),
                )
                for item in files:
                    connection.execute(
                        """
                        INSERT INTO export_files(
                            export_file_id, export_revision_id, kind, ordinal,
                            filename, relative_path, sha256, byte_size
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid7()),
                            export_revision_id,
                            item["kind"],
                            item.get("ordinal"),
                            item["filename"],
                            str(Path(final.name) / item["path"]),
                            item["sha256"],
                            item["byte_size"],
                        ),
                    )
                connection.execute(
                    """INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
                       VALUES (?, ?, 'export.completed', ?)""",
                    (
                        str(uuid7()),
                        project_id,
                        canonical_json(
                            {
                                "export_revision_id": export_revision_id,
                                "chapter_id": chapter_id,
                                "page_count": plan["page_count"],
                                "selection_sha256": plan_fingerprint,
                            }
                        ),
                    ),
                )
        except Exception as exc:
            if staging.exists():
                os.replace(staging, failed)
            elif final.exists():
                os.replace(final, failed)
            with self.database.writer() as connection:
                connection.execute(
                    """UPDATE export_revisions SET status = 'failed', failure_code = ?
                       WHERE export_revision_id = ? AND status = 'staging'""",
                    (self._safe_failure_code(exc), export_revision_id),
                )
            if isinstance(exc, ApplicationError):
                raise
            raise ApplicationError(
                "EXPORT_BUILD_FAILED", "导出未完成, 之前的成功导出没有受到影响。", 500
            ) from exc
        return self.get_export(project_id, export_revision_id)

    def list_exports(self, project_id: str) -> list[dict[str, Any]]:
        self.projects.get(project_id)
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT export_revision_id FROM export_revisions
                   WHERE project_id = ? ORDER BY created_at DESC, export_revision_id DESC""",
                (project_id,),
            ).fetchall()
        return [self.get_export(project_id, str(row["export_revision_id"])) for row in rows]

    def get_export(self, project_id: str, export_revision_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT er.*, sc.title AS chapter_title FROM export_revisions er
                   JOIN source_chapters sc ON sc.chapter_id = er.chapter_id
                   WHERE er.project_id = ? AND er.export_revision_id = ?""",
                (project_id, export_revision_id),
            ).fetchone()
            files = connection.execute(
                """SELECT * FROM export_files WHERE export_revision_id = ?
                   ORDER BY CASE kind WHEN 'engineering_package' THEN 1 WHEN 'png' THEN 2
                   WHEN 'pdf' THEN 3 ELSE 4 END, ordinal, filename""",
                (export_revision_id,),
            ).fetchall()
        if row is None:
            raise ApplicationError("EXPORT_NOT_FOUND", "没有找到该导出版本。", 404)
        return {
            "export_revision_id": str(row["export_revision_id"]),
            "project_id": str(row["project_id"]),
            "chapter_id": str(row["chapter_id"]),
            "chapter_title": str(row["chapter_title"]),
            "status": str(row["status"]),
            "schema_version": str(row["schema_version"]),
            "pages": json.loads(str(row["page_selection_json"])),
            "selection_sha256": str(row["selection_sha256"]),
            "failure_code": row["failure_code"],
            "created_at": str(row["created_at"]),
            "completed_at": row["completed_at"],
            "files": [
                {
                    "export_file_id": str(item["export_file_id"]),
                    "kind": str(item["kind"]),
                    "ordinal": item["ordinal"],
                    "filename": str(item["filename"]),
                    "sha256": str(item["sha256"]),
                    "byte_size": int(item["byte_size"]),
                }
                for item in files
            ],
            "external_requests_started": 0,
        }

    def export_file_path(
        self, project_id: str, export_revision_id: str, export_file_id: str
    ) -> tuple[Path, str, str]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT ef.*, er.status, p.workspace_path FROM export_files ef
                   JOIN export_revisions er
                     ON er.export_revision_id = ef.export_revision_id
                   JOIN projects p ON p.project_id = er.project_id
                   WHERE er.project_id = ? AND er.export_revision_id = ?
                     AND ef.export_file_id = ?""",
                (project_id, export_revision_id, export_file_id),
            ).fetchone()
        if row is None or row["status"] != "completed":
            raise ApplicationError("EXPORT_FILE_NOT_FOUND", "没有找到该导出文件。", 404)
        workspace = Path(str(row["workspace_path"])).resolve()
        candidate = (workspace / "exports" / str(row["relative_path"])).resolve()
        if (
            not candidate.is_relative_to((workspace / "exports").resolve())
            or not candidate.is_file()
        ):
            raise ApplicationError("EXPORT_FILE_DAMAGED", "导出文件缺失或路径无效。", 409)
        if _sha256_file(candidate) != row["sha256"]:
            raise ApplicationError("EXPORT_FILE_DAMAGED", "导出文件哈希不匹配。", 409)
        return candidate, str(row["filename"]), self._media_type(str(row["kind"]))

    def preflight_package(self, filename: str, content: bytes) -> dict[str, Any]:
        if not content:
            raise ApplicationError("EMPTY_PROJECT_PACKAGE", "工程包为空。", 422)
        if len(content) > MAX_PACKAGE_COMPRESSED_BYTES:
            raise ApplicationError("PROJECT_PACKAGE_TOO_LARGE", "工程包超过安全上限。", 413)
        package_sha = hashlib.sha256(content).hexdigest()
        import_preflight_id = str(uuid7())
        package_path = self.imports_dir / f"{import_preflight_id}.manga-maker.zip"
        write_synced(package_path, content)
        try:
            validated = self._validate_package(package_path)
        except Exception:
            rejected = self.imports_dir / f".rejected-{import_preflight_id}.zip"
            os.replace(package_path, rejected)
            raise
        manifest = validated["manifest"]
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT INTO package_import_preflights(
                    import_preflight_id, package_path, package_sha256,
                    source_project_id, source_title, manifest_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'ready')
                """,
                (
                    import_preflight_id,
                    str(package_path),
                    package_sha,
                    manifest["source_project_id"],
                    manifest["source_title"],
                    canonical_json(manifest),
                ),
            )
        return {
            "import_preflight_id": import_preflight_id,
            "filename": Path(filename).name,
            "package_sha256": package_sha,
            "source_project_id": manifest["source_project_id"],
            "source_title": manifest["source_title"],
            "schema_version": manifest["schema_version"],
            "file_count": validated["file_count"],
            "expanded_bytes": validated["expanded_bytes"],
            "record_counts": manifest["record_counts"],
            "page_count": len(manifest["selected_pages"]),
            "requires_confirmation": True,
            "writes_performed": 0,
        }

    def restore_package(self, import_preflight_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ApplicationError(
                "RESTORE_CONFIRMATION_REQUIRED", "恢复工程包前必须明确确认。", 422
            )
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT * FROM package_import_preflights
                   WHERE import_preflight_id = ?""",
                (import_preflight_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError("IMPORT_PREFLIGHT_NOT_FOUND", "没有找到工程包预检。", 404)
        if row["status"] != "ready":
            raise ApplicationError("IMPORT_PREFLIGHT_ALREADY_USED", "该工程包预检已经使用。", 409)
        package_path = Path(str(row["package_path"])).resolve()
        if (
            not package_path.is_relative_to(self.imports_dir.resolve())
            or not package_path.is_file()
        ):
            raise ApplicationError("PROJECT_PACKAGE_DAMAGED", "工程包暂存文件缺失。", 409)
        if _sha256_file(package_path) != row["package_sha256"]:
            raise ApplicationError("PROJECT_PACKAGE_DAMAGED", "工程包暂存哈希已变化。", 409)
        validated = self._validate_package(package_path)
        records = validated["records"]
        source_project = records["tables"]["projects"][0]
        source_project_id = str(source_project["project_id"])
        conflict = self._id_exists("projects", "project_id", source_project_id)
        restored_project_id = str(uuid7()) if conflict else source_project_id
        staging = self.projects.projects_dir / f".restore-{import_preflight_id}"
        final = self.projects.projects_dir / restored_project_id
        orphan = self.projects.projects_dir / f".orphan-restore-{import_preflight_id}"
        if staging.exists() or final.exists():
            raise ApplicationError("RESTORE_WORKSPACE_CONFLICT", "恢复工作区已经存在。", 409)
        staging.mkdir(mode=0o700)
        try:
            for relative in PROJECT_DIRECTORIES:
                (staging / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
            self._extract_project_files(package_path, validated, staging)
            local_title = str(source_project["title"])
            if conflict:
                local_title = f"{local_title} (恢复)"
            local_manifest = {
                "schema_version": "1.0",
                "project_id": restored_project_id,
                "source_project_id": source_project_id,
                "title": local_title,
                "status": str(source_project["status"]),
            }
            manifest_path = staging / "manifest.json"
            if manifest_path.exists():
                manifest_path.unlink()
            write_synced(
                manifest_path,
                (
                    json.dumps(local_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                ).encode(),
            )
            os.replace(staging, final)
            fsync_directory(self.projects.projects_dir)
            try:
                self._restore_records(
                    records,
                    import_preflight_id=import_preflight_id,
                    old_workspace=Path(str(source_project["workspace_path"])),
                    new_workspace=final,
                    restored_project_id=restored_project_id,
                    source_project_id=source_project_id,
                    local_title=local_title,
                    remap_all=conflict,
                )
            except Exception:
                os.replace(final, orphan)
                raise
        except Exception:
            if staging.exists():
                os.replace(staging, orphan)
            raise
        return {
            "import_preflight_id": import_preflight_id,
            "project_id": restored_project_id,
            "source_project_id": source_project_id,
            "title": local_title,
            "id_conflict_remapped": conflict,
            "record_counts": validated["manifest"]["record_counts"],
            "file_count": validated["file_count"],
            "external_requests_started": 0,
        }

    def _selected_pages(
        self, project_id: str, chapter_id: str, page_version_ids: list[str] | None
    ) -> tuple[list[sqlite3.Row], sqlite3.Row, sqlite3.Row]:
        self.projects.get(project_id)
        with self.database.reader() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            chapter = connection.execute(
                """SELECT sc.* FROM source_chapters sc
                   JOIN source_chapter_sets cs ON cs.chapter_set_id = sc.chapter_set_id
                   JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                   WHERE sf.project_id = ? AND sc.chapter_id = ?""",
                (project_id, chapter_id),
            ).fetchone()
            roots = connection.execute(
                """SELECT page_id, page_number FROM comic_pages
                   WHERE project_id = ? AND chapter_id = ? ORDER BY page_number""",
                (project_id, chapter_id),
            ).fetchall()
            if page_version_ids is None:
                pages = connection.execute(
                    """SELECT pv.*, cp.page_number, cp.chapter_id, cp.project_id
                       FROM page_versions pv JOIN comic_pages cp ON cp.page_id = pv.page_id
                       WHERE cp.project_id = ? AND cp.chapter_id = ? AND pv.is_current = 1
                       ORDER BY cp.page_number""",
                    (project_id, chapter_id),
                ).fetchall()
            else:
                if not page_version_ids or len(page_version_ids) > 64:
                    raise ApplicationError(
                        "INVALID_EXPORT_SELECTION", "请选择 1-64 个页面版本。", 422
                    )
                if len(set(page_version_ids)) != len(page_version_ids):
                    raise ApplicationError("INVALID_EXPORT_SELECTION", "页面版本不能重复。", 422)
                placeholders = ",".join("?" for _ in page_version_ids)
                found = connection.execute(
                    f"""SELECT pv.*, cp.page_number, cp.chapter_id, cp.project_id
                        FROM page_versions pv JOIN comic_pages cp ON cp.page_id = pv.page_id
                        WHERE cp.project_id = ? AND cp.chapter_id = ?
                          AND pv.page_version_id IN ({placeholders})""",
                    (project_id, chapter_id, *page_version_ids),
                ).fetchall()
                indexed = {str(item["page_version_id"]): item for item in found}
                pages = [indexed[item] for item in page_version_ids if item in indexed]
        if project is None or chapter is None:
            raise ApplicationError("SOURCE_CHAPTER_NOT_FOUND", "没有找到该项目章节。", 404)
        if not roots:
            raise ApplicationError("EXPORT_PAGES_MISSING", "该章节还没有可导出的漫画页。", 409)
        if len(pages) != len(roots):
            raise ApplicationError(
                "EXPORT_PAGES_INCOMPLETE", "必须为章节中的每一页选择一个确定版本。", 422
            )
        if [str(row["page_id"]) for row in pages] != [str(row["page_id"]) for row in roots]:
            raise ApplicationError(
                "EXPORT_PAGE_ORDER_INVALID", "页面顺序必须与章节阅读顺序一致。", 422
            )
        workspace = self.projects.workspace_path(project_id)
        for row in pages:
            candidate = (workspace / str(row["rendered_relative_path"])).resolve()
            if not candidate.is_relative_to(workspace) or not candidate.is_file():
                raise ApplicationError("EXPORT_PAGE_DAMAGED", "页面渲染文件缺失。", 409)
            if _sha256_file(candidate) != row["render_sha256"]:
                raise ApplicationError("EXPORT_PAGE_DAMAGED", "页面渲染哈希不匹配。", 409)
            try:
                with Image.open(candidate) as image:
                    image.verify()
                with Image.open(candidate) as image:
                    if image.size != (2048, 3072) or image.format != "PNG":
                        raise ValueError
            except (OSError, ValueError) as exc:
                raise ApplicationError(
                    "EXPORT_PAGE_INVALID", "页面必须是 2048 x 3072 的有效 PNG。", 409
                ) from exc
        return list(pages), project, chapter

    @staticmethod
    def _selection_entry(row: sqlite3.Row, ordinal: int) -> dict[str, Any]:
        return {
            "ordinal": ordinal,
            "page_id": str(row["page_id"]),
            "page_number": int(row["page_number"]),
            "page_version_id": str(row["page_version_id"]),
            "version": int(row["version"]),
            "render_sha256": str(row["render_sha256"]),
            "width": 2048,
            "height": 3072,
        }

    @staticmethod
    def _selection_fingerprint(
        project_id: str, chapter_id: str, selection: list[dict[str, Any]]
    ) -> str:
        payload = canonical_json(
            {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "project_id": project_id,
                "chapter_id": chapter_id,
                "pages": selection,
            }
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def _build_export(
        self,
        staging: Path,
        workspace: Path,
        export_revision_id: str,
        plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        png_dir = staging / "png"
        png_dir.mkdir(mode=0o700)
        png_paths: list[Path] = []
        files: list[dict[str, Any]] = []
        with self.database.reader() as connection:
            selected_rows = {
                str(row["page_version_id"]): row
                for row in connection.execute(
                    "SELECT * FROM page_versions WHERE page_version_id IN ({})".format(
                        ",".join("?" for _ in plan["pages"])
                    ),
                    tuple(item["page_version_id"] for item in plan["pages"]),
                ).fetchall()
            }
        for page in plan["pages"]:
            source = workspace / str(
                selected_rows[page["page_version_id"]]["rendered_relative_path"]
            )
            target = png_dir / f"{page['ordinal']:03d}.png"
            write_synced(target, source.read_bytes())
            png_paths.append(target)
            files.append(self._file_record("png", target, staging, page["ordinal"]))

        pdf_path = staging / "manga.pdf"
        self._write_pdf(pdf_path, png_paths, plan)
        files.append(self._file_record("pdf", pdf_path, staging))

        cbz_path = staging / "manga.cbz"
        self._write_cbz(cbz_path, png_paths, plan)
        files.append(self._file_record("cbz", cbz_path, staging))

        package_path = staging / "project.manga-maker.zip"
        self._write_project_package(package_path, workspace, export_revision_id, plan)
        files.insert(0, self._file_record("engineering_package", package_path, staging))

        export_manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "export_revision_id": export_revision_id,
            "project_id": plan["project_id"],
            "chapter_id": plan["chapter_id"],
            "selection_sha256": plan["plan_fingerprint"],
            "pages": plan["pages"],
            "files": [
                {key: value for key, value in item.items() if key != "path"} for item in files
            ],
            "credentials_included": False,
            "external_requests_started": 0,
        }
        write_synced(
            staging / "export-manifest.json",
            (canonical_json(export_manifest) + "\n").encode(),
        )
        return files

    @staticmethod
    def _write_pdf(path: Path, png_paths: list[Path], plan: dict[str, Any]) -> None:
        images: list[Image.Image] = []
        try:
            for item in png_paths:
                with Image.open(item) as source:
                    images.append(source.convert("RGB"))
            output = BytesIO()
            images[0].save(
                output,
                format="PDF",
                save_all=True,
                append_images=images[1:],
                resolution=144,
                title=plan["project_title"],
                subject=plan["chapter_title"],
                creator="Manga Maker",
            )
            write_synced(path, output.getvalue())
        finally:
            for image in images:
                image.close()

    @staticmethod
    def _write_cbz(path: Path, png_paths: list[Path], plan: dict[str, Any]) -> None:
        comic_info = ElementTree.Element("ComicInfo")
        ElementTree.SubElement(comic_info, "Title").text = str(plan["chapter_title"])
        ElementTree.SubElement(comic_info, "Series").text = str(plan["project_title"])
        ElementTree.SubElement(comic_info, "PageCount").text = str(len(png_paths))
        ElementTree.SubElement(comic_info, "LanguageISO").text = "zh-CN"
        ElementTree.SubElement(comic_info, "Manga").text = "No"
        pages = ElementTree.SubElement(comic_info, "Pages")
        for index in range(len(png_paths)):
            ElementTree.SubElement(pages, "Page", Image=str(index), Type="Story")
        xml = ElementTree.tostring(comic_info, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(
            path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            _zip_write_bytes(archive, "ComicInfo.xml", xml)
            for index, png in enumerate(png_paths, 1):
                _zip_write_bytes(archive, f"{index:03d}.png", png.read_bytes())
        os.chmod(path, 0o600)

    def _write_project_package(
        self, path: Path, workspace: Path, export_revision_id: str, plan: dict[str, Any]
    ) -> None:
        records = self._snapshot_records(plan["project_id"])
        records_payload = (canonical_json(records) + "\n").encode()
        package_files: list[tuple[str, bytes]] = [("records.json", records_payload)]
        for source in self._portable_project_files(workspace):
            package_files.append(
                (f"project/{source.relative_to(workspace).as_posix()}", source.read_bytes())
            )
        manifest_files = [
            {"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "byte_size": len(payload)}
            for name, payload in package_files
        ]
        manifest = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "minimum_database_schema": 11,
            "source_project_id": plan["project_id"],
            "source_title": plan["project_title"],
            "export_revision_id": export_revision_id,
            "record_counts": {table: len(rows) for table, rows in records["tables"].items()},
            "selected_pages": plan["pages"],
            "files": manifest_files,
            "credentials_included": False,
        }
        with zipfile.ZipFile(
            path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            _zip_write_bytes(archive, "manifest.json", (canonical_json(manifest) + "\n").encode())
            for name, payload in package_files:
                _zip_write_bytes(archive, name, payload)
        os.chmod(path, 0o600)

    def _snapshot_records(self, project_id: str) -> dict[str, Any]:
        tables: dict[str, list[dict[str, Any]]] = {}
        workspace = self.projects.workspace_path(project_id)
        portable_workspace = Path("/MANGA_MAKER_PROJECT")
        with self.database.reader() as connection:
            for table, query in PROJECT_TABLE_QUERIES:
                rows = [dict(row) for row in connection.execute(query, (project_id,)).fetchall()]
                for row in rows:
                    if "credential_profile_id" in row:
                        row["credential_profile_id"] = CREDENTIAL_REENTRY_PROFILE_ID
                    if table == "novelai_configs":
                        row["last_connection_status"] = None
                        row["last_connection_at"] = None
                    if table == "projects":
                        row["workspace_path"] = str(portable_workspace)
                    if table in ("source_preflights", "source_files"):
                        for column in ("staging_path", "original_path", "normalized_path"):
                            if row.get(column):
                                try:
                                    relative = Path(str(row[column])).relative_to(workspace)
                                except ValueError as exc:
                                    raise ApplicationError(
                                        "PROJECT_PATH_INVALID",
                                        "项目来源文件位于工作区之外, 无法安全打包。",
                                        409,
                                    ) from exc
                                row[column] = str(portable_workspace / relative)
                    for column, value in tuple(row.items()):
                        if column.endswith("_json") and isinstance(value, str):
                            row[column] = self._sanitize_package_json(value)
                tables[table] = rows
        return {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "database_schema": self.database.schema_version(),
            "tables": tables,
            "credentials_included": False,
        }

    @staticmethod
    def _portable_project_files(workspace: Path) -> list[Path]:
        result: list[Path] = []
        allowed_roots = {
            "manifest.json",
            "source",
            "storyboard",
            "bibles",
            "assets",
            "pages",
            "audit",
        }
        for path in workspace.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(workspace)
            if relative.parts[0] not in allowed_roots:
                continue
            if relative.parts[:2] == ("assets", "staging"):
                continue
            result.append(path)
        return sorted(result, key=lambda item: item.relative_to(workspace).as_posix())

    def _validate_published_export(
        self, staging: Path, plan: dict[str, Any], files: list[dict[str, Any]]
    ) -> None:
        pngs = sorted(staging.joinpath("png").glob("*.png"))
        if len(pngs) != plan["page_count"]:
            raise ApplicationError("EXPORT_VALIDATION_FAILED", "PNG 页数不匹配。", 500)
        for index, png in enumerate(pngs, 1):
            if png.name != f"{index:03d}.png":
                raise ApplicationError("EXPORT_VALIDATION_FAILED", "PNG 页序无效。", 500)
            with Image.open(png) as image:
                image.verify()
            with Image.open(png) as image:
                if image.size != (2048, 3072):
                    raise ApplicationError("EXPORT_VALIDATION_FAILED", "PNG 尺寸无效。", 500)
        pdf_payload = (staging / "manga.pdf").read_bytes()
        if (
            not pdf_payload.startswith(b"%PDF-")
            or pdf_payload.count(b"/Type /Page\n") != plan["page_count"]
        ):
            raise ApplicationError("EXPORT_VALIDATION_FAILED", "PDF 页数不匹配。", 500)
        with zipfile.ZipFile(staging / "manga.cbz") as archive:
            expected = [
                "ComicInfo.xml",
                *[f"{i:03d}.png" for i in range(1, plan["page_count"] + 1)],
            ]
            if archive.namelist() != expected:
                raise ApplicationError("EXPORT_VALIDATION_FAILED", "CBZ 页序无效。", 500)
            ElementTree.fromstring(archive.read("ComicInfo.xml"))
        self._validate_package(staging / "project.manga-maker.zip")
        for item in files:
            target = staging / item["path"]
            if _sha256_file(target) != item["sha256"]:
                raise ApplicationError("EXPORT_VALIDATION_FAILED", "导出文件哈希不匹配。", 500)

    def _validate_package(self, package_path: Path) -> dict[str, Any]:
        try:
            archive = zipfile.ZipFile(package_path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ApplicationError("INVALID_PROJECT_PACKAGE", "工程包不是有效 ZIP。", 422) from exc
        with archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_PACKAGE_FILES:
                raise ApplicationError("PROJECT_PACKAGE_LIMIT_EXCEEDED", "工程包文件数无效。", 422)
            seen: set[str] = set()
            total = 0
            for info in infos:
                self._validate_zip_member(info, seen)
                if info.is_dir():
                    continue
                total += info.file_size
                if info.file_size > MAX_PACKAGE_FILE_BYTES or total > MAX_PACKAGE_EXPANDED_BYTES:
                    raise ApplicationError(
                        "PROJECT_PACKAGE_LIMIT_EXCEEDED", "工程包解压大小超限。", 422
                    )
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ApplicationError(
                        "PROJECT_PACKAGE_COMPRESSION_BOMB", "工程包压缩比超限。", 422
                    )
            if "manifest.json" not in seen or "records.json" not in seen:
                raise ApplicationError("PROJECT_PACKAGE_MANIFEST_MISSING", "工程包缺少清单。", 422)
            try:
                manifest = json.loads(archive.read("manifest.json"))
                records = json.loads(archive.read("records.json"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApplicationError(
                    "PROJECT_PACKAGE_MANIFEST_INVALID", "工程包清单无效。", 422
                ) from exc
            self._validate_package_documents(manifest, records)
            listed = {item["path"]: item for item in manifest["files"]}
            actual = {name for name in seen if name != "manifest.json" and not name.endswith("/")}
            if set(listed) != actual:
                raise ApplicationError(
                    "PROJECT_PACKAGE_FILE_LIST_MISMATCH", "工程包文件清单不一致。", 422
                )
            for name in actual:
                self._validate_package_file_scope(name)
            for name, item in listed.items():
                payload = archive.read(name)
                if (
                    len(payload) != item["byte_size"]
                    or hashlib.sha256(payload).hexdigest() != item["sha256"]
                ):
                    raise ApplicationError(
                        "PROJECT_PACKAGE_HASH_MISMATCH", "工程包文件哈希不匹配。", 422
                    )
            required = sum(int(item["byte_size"]) for item in manifest["files"])
            if shutil.disk_usage(self.projects.projects_dir).free < required * 2 + 64 * 1024 * 1024:
                raise ApplicationError(
                    "RESTORE_DISK_SPACE_INSUFFICIENT", "磁盘空间不足以恢复工程包。", 409
                )
        return {
            "manifest": manifest,
            "records": records,
            "file_count": len([item for item in infos if not item.is_dir()]),
            "expanded_bytes": total,
        }

    @staticmethod
    def _validate_zip_member(info: zipfile.ZipInfo, seen: set[str]) -> None:
        name = info.filename
        if not name or "\\" in name or "\x00" in name:
            raise ApplicationError("PROJECT_PACKAGE_PATH_INVALID", "工程包包含无效路径。", 422)
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ApplicationError("PROJECT_PACKAGE_PATH_INVALID", "工程包包含越界路径。", 422)
        if name in seen:
            raise ApplicationError("PROJECT_PACKAGE_DUPLICATE_PATH", "工程包包含重复路径。", 422)
        seen.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise ApplicationError(
                "PROJECT_PACKAGE_SYMLINK_REJECTED", "工程包不能包含符号链接。", 422
            )

    @staticmethod
    def _validate_package_documents(manifest: Any, records: Any) -> None:
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION
        ):
            raise ApplicationError(
                "PROJECT_PACKAGE_SCHEMA_UNSUPPORTED", "工程包版本不受支持。", 422
            )
        required_manifest = {
            "source_project_id",
            "source_title",
            "record_counts",
            "selected_pages",
            "files",
        }
        if (
            not required_manifest.issubset(manifest)
            or manifest.get("credentials_included") is not False
        ):
            raise ApplicationError("PROJECT_PACKAGE_MANIFEST_INVALID", "工程包清单字段无效。", 422)
        if not isinstance(records, dict) or records.get("schema_version") != PACKAGE_SCHEMA_VERSION:
            raise ApplicationError(
                "PROJECT_PACKAGE_SCHEMA_UNSUPPORTED", "工程记录版本不受支持。", 422
            )
        tables = records.get("tables")
        expected_tables = [table for table, _query in PROJECT_TABLE_QUERIES]
        if not isinstance(tables, dict) or set(tables) != set(expected_tables):
            raise ApplicationError("PROJECT_PACKAGE_RECORDS_INVALID", "工程记录表清单无效。", 422)
        if len(tables["projects"]) != 1:
            raise ApplicationError(
                "PROJECT_PACKAGE_RECORDS_INVALID", "工程包必须包含一个项目。", 422
            )
        if str(tables["projects"][0].get("project_id")) != str(manifest["source_project_id"]):
            raise ApplicationError("PROJECT_PACKAGE_RECORDS_INVALID", "工程项目 ID 不一致。", 422)
        if any(
            len(tables[table]) != int(manifest["record_counts"].get(table, -1))
            for table in expected_tables
        ):
            raise ApplicationError("PROJECT_PACKAGE_RECORDS_INVALID", "工程对象计数不一致。", 422)
        for table, rows in tables.items():
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ApplicationError(
                    "PROJECT_PACKAGE_RECORDS_INVALID", f"{table} 记录无效。", 422
                )
            for row in rows:
                if row.get("credential_profile_id") not in (
                    None,
                    "",
                    CREDENTIAL_REENTRY_PROFILE_ID,
                ):
                    raise ApplicationError(
                        "PROJECT_PACKAGE_CONTAINS_CREDENTIAL_REFERENCE", "工程包包含凭证引用。", 422
                    )
                for column, value in row.items():
                    if column.endswith("_json") and isinstance(value, str):
                        ExportService._validate_package_json_safety(value)

    @staticmethod
    def _extract_project_files(package_path: Path, validated: dict[str, Any], target: Path) -> None:
        file_manifest = {item["path"]: item for item in validated["manifest"]["files"]}
        with zipfile.ZipFile(package_path) as archive:
            for name, expected in file_manifest.items():
                if not name.startswith("project/"):
                    continue
                relative = PurePosixPath(name).relative_to("project")
                if relative.as_posix() == "manifest.json":
                    continue
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                payload = archive.read(name)
                if hashlib.sha256(payload).hexdigest() != expected["sha256"]:
                    raise ApplicationError(
                        "PROJECT_PACKAGE_HASH_MISMATCH", "恢复时文件哈希不匹配。", 422
                    )
                write_synced(destination, payload)

    def _restore_records(
        self,
        records: dict[str, Any],
        *,
        import_preflight_id: str,
        old_workspace: Path,
        new_workspace: Path,
        restored_project_id: str,
        source_project_id: str,
        local_title: str,
        remap_all: bool,
    ) -> None:
        tables: dict[str, list[dict[str, Any]]] = records["tables"]
        with self.database.writer() as connection:
            connection.execute("PRAGMA defer_foreign_keys = ON")
            primary_keys: dict[str, str] = {}
            mappings: dict[str, dict[Any, Any]] = {}
            for table, _query in PROJECT_TABLE_QUERIES:
                columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
                pk_columns = [str(col["name"]) for col in columns if int(col["pk"]) > 0]
                if len(pk_columns) != 1:
                    raise ApplicationError(
                        "RESTORE_SCHEMA_MISMATCH", f"{table} 主键结构不受支持。", 409
                    )
                pk = pk_columns[0]
                primary_keys[table] = pk
                mappings[table] = {}
                for row in tables[table]:
                    old = row[pk]
                    if table in ("projects", "text_model_configs", "novelai_configs"):
                        new = restored_project_id
                    elif remap_all or self._id_exists_in_connection(connection, table, pk, old):
                        new = str(uuid7())
                    else:
                        new = old
                    mappings[table][old] = new

            foreign_keys: dict[str, list[sqlite3.Row]] = {
                table: connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
                for table, _query in PROJECT_TABLE_QUERIES
            }
            value_mapping = {
                old: new
                for table_mapping in mappings.values()
                for old, new in table_mapping.items()
                if old != new
            }
            for table, _query in PROJECT_TABLE_QUERIES:
                pk = primary_keys[table]
                valid_columns = {
                    str(col["name"])
                    for col in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for original in tables[table]:
                    row = dict(original)
                    row[pk] = mappings[table][original[pk]]
                    for foreign in foreign_keys[table]:
                        column = str(foreign["from"])
                        target_table = str(foreign["table"])
                        value = row.get(column)
                        if value is not None and target_table in mappings:
                            row[column] = mappings[target_table].get(value, value)
                    if table == "projects":
                        row["workspace_path"] = str(new_workspace)
                        row["title"] = local_title
                        row["source_project_id"] = source_project_id
                    if table in ("source_preflights", "source_files"):
                        for column in ("staging_path", "original_path", "normalized_path"):
                            if row.get(column):
                                row[column] = self._rewrite_workspace_path(
                                    str(row[column]), old_workspace, new_workspace
                                )
                    if remap_all:
                        relative_columns = {
                            "asset_versions": (
                                "original_relative_path",
                                "provenance_relative_path",
                            ),
                            "page_versions": ("rendered_relative_path",),
                            "mask_assets": ("relative_path",),
                        }.get(table, ())
                        for column in relative_columns:
                            old_relative = str(row[column])
                            restored_root = "pages" if table == "page_versions" else "assets"
                            new_relative = str(
                                Path(restored_root)
                                / "restored"
                                / restored_project_id
                                / old_relative
                            )
                            self._relocate_relative_file(new_workspace, old_relative, new_relative)
                            row[column] = new_relative
                    if "credential_profile_id" in row:
                        row["credential_profile_id"] = CREDENTIAL_REENTRY_PROFILE_ID
                    for column, value in tuple(row.items()):
                        if column.endswith("_json") and isinstance(value, str):
                            row[column] = self._remap_json(value, value_mapping)
                    if table == "generation_specs":
                        row["spec_sha256"] = hashlib.sha256(
                            str(row["document_json"]).encode()
                        ).hexdigest()
                    if table == "page_versions":
                        row["document_sha256"] = hashlib.sha256(
                            str(row["document_json"]).encode()
                        ).hexdigest()
                    unknown = set(row) - valid_columns
                    if unknown:
                        raise ApplicationError(
                            "RESTORE_SCHEMA_MISMATCH", f"{table} 包含未知字段。", 409
                        )
                    columns = list(row)
                    placeholders = ",".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                        tuple(row[column] for column in columns),
                    )
            connection.execute(
                """INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
                   VALUES (?, ?, 'project.restored', ?)""",
                (
                    str(uuid7()),
                    restored_project_id,
                    canonical_json(
                        {
                            "source_project_id": source_project_id,
                            "package_schema_version": PACKAGE_SCHEMA_VERSION,
                        }
                    ),
                ),
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ApplicationError(
                    "RESTORE_FOREIGN_KEY_MISMATCH",
                    "工程包对象关系无法完整恢复。",
                    422,
                    details={
                        "violations": [
                            {
                                "table": str(item[0]),
                                "rowid": int(item[1]),
                                "parent": str(item[2]),
                                "foreign_key": int(item[3]),
                            }
                            for item in violations[:20]
                        ]
                    },
                )
            connection.execute(
                """UPDATE package_import_preflights
                   SET status = 'restored', restored_project_id = ?,
                       restored_at = CURRENT_TIMESTAMP
                   WHERE import_preflight_id = ? AND status = 'ready'""",
                (restored_project_id, import_preflight_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ApplicationError(
                    "IMPORT_PREFLIGHT_ALREADY_USED", "该工程包预检已经使用。", 409
                )

    @staticmethod
    def _rewrite_workspace_path(value: str, old_workspace: Path, new_workspace: Path) -> str:
        try:
            relative = Path(value).relative_to(old_workspace)
        except ValueError as exc:
            raise ApplicationError(
                "PROJECT_PACKAGE_PATH_INVALID", "工程记录包含工作区外路径。", 422
            ) from exc
        candidate = (new_workspace / relative).resolve()
        if not candidate.is_relative_to(new_workspace.resolve()):
            raise ApplicationError("PROJECT_PACKAGE_PATH_INVALID", "工程记录路径越界。", 422)
        return str(candidate)

    @staticmethod
    def _remap_json(payload: str, mapping: dict[Any, Any]) -> str:
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ApplicationError(
                "PROJECT_PACKAGE_RECORDS_INVALID", "工程记录包含无效 JSON。", 422
            ) from exc

        def visit(value: Any) -> Any:
            if isinstance(value, str):
                return mapping.get(value, value)
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {key: visit(item) for key, item in value.items()}
            return value

        return canonical_json(visit(document))

    @staticmethod
    def _sanitize_package_json(payload: str) -> str:
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ApplicationError(
                "PROJECT_RECORD_JSON_INVALID", "项目记录包含无效 JSON, 无法安全打包。", 409
            ) from exc

        def visit(value: Any) -> Any:
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {
                    key: (
                        CREDENTIAL_REENTRY_PROFILE_ID
                        if key == "credential_profile_id"
                        else None
                        if key == "credential_fingerprint"
                        else visit(item)
                    )
                    for key, item in value.items()
                }
            return value

        return canonical_json(visit(document))

    @staticmethod
    def _validate_package_json_safety(payload: str) -> None:
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ApplicationError(
                "PROJECT_PACKAGE_RECORDS_INVALID", "工程包记录包含无效 JSON。", 422
            ) from exc
        forbidden_keys = {
            "api_key",
            "api_token",
            "authorization",
            "bearer_token",
            "password",
            "secret",
            "token",
        }

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key.casefold() in forbidden_keys:
                        raise ApplicationError(
                            "PROJECT_PACKAGE_CONTAINS_CREDENTIAL",
                            "工程包记录包含禁止的凭证字段。",
                            422,
                        )
                    if key == "credential_profile_id" and item not in (
                        None,
                        "",
                        CREDENTIAL_REENTRY_PROFILE_ID,
                    ):
                        raise ApplicationError(
                            "PROJECT_PACKAGE_CONTAINS_CREDENTIAL_REFERENCE",
                            "工程包包含原凭证引用。",
                            422,
                        )
                    visit(item)

        visit(document)

    @staticmethod
    def _validate_package_file_scope(name: str) -> None:
        if name == "records.json":
            return
        path = PurePosixPath(name)
        if len(path.parts) < 2 or path.parts[0] != "project":
            raise ApplicationError(
                "PROJECT_PACKAGE_FILE_SCOPE_INVALID", "工程包包含范围外文件。", 422
            )
        allowed_roots = {
            "manifest.json",
            "source",
            "storyboard",
            "bibles",
            "assets",
            "pages",
            "audit",
        }
        if path.parts[1] not in allowed_roots or path.parts[1:3] == (
            "assets",
            "staging",
        ):
            raise ApplicationError(
                "PROJECT_PACKAGE_FILE_SCOPE_INVALID", "工程包包含范围外项目文件。", 422
            )

    @staticmethod
    def _relocate_relative_file(workspace: Path, old: str, new: str) -> None:
        source = (workspace / old).resolve()
        target = (workspace / new).resolve()
        if (
            not source.is_relative_to(workspace.resolve())
            or not target.is_relative_to(workspace.resolve())
            or not source.is_file()
        ):
            raise ApplicationError(
                "PROJECT_PACKAGE_FILE_MISSING", "恢复记录引用的工程文件缺失。", 422
            )
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(source, target)

    def _id_exists(self, table: str, column: str, value: Any) -> bool:
        with self.database.reader() as connection:
            return self._id_exists_in_connection(connection, table, column, value)

    @staticmethod
    def _id_exists_in_connection(
        connection: sqlite3.Connection, table: str, column: str, value: Any
    ) -> bool:
        return (
            connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (value,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _file_record(
        kind: str, path: Path, root: Path, ordinal: int | None = None
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "ordinal": ordinal,
            "filename": path.name,
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "byte_size": path.stat().st_size,
        }

    @staticmethod
    def _safe_failure_code(exc: Exception) -> str:
        return exc.code if isinstance(exc, ApplicationError) else type(exc).__name__[:64]

    @staticmethod
    def _media_type(kind: str) -> str:
        return {
            "engineering_package": "application/zip",
            "png": "image/png",
            "pdf": "application/pdf",
            "cbz": "application/vnd.comicbook+zip",
        }[kind]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_write_bytes(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    archive.writestr(info, payload)
