from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from .book.service import BookProductionService
from .database import Database
from .exports.service import ExportService
from .generation.assets import canonical_json
from .generation.queue import GenerationQueueService
from .ids import uuid7
from .projects import ProjectService
from .safety import redact_sensitive
from .vault import CredentialVault

RecoveryTrigger = Literal["startup", "manual"]


class RecoveryService:
    """Persisted, no-provider startup reconciliation and workspace integrity audit."""

    def __init__(
        self,
        database: Database,
        projects: ProjectService,
        queue: GenerationQueueService,
        exports: ExportService,
        vault: CredentialVault,
        book_production: BookProductionService,
    ) -> None:
        self.database = database
        self.projects = projects
        self.queue = queue
        self.exports = exports
        self.vault = vault
        self.book_production = book_production

    def reconcile_startup(self) -> dict[str, Any]:
        export_recovery = self.exports.reconcile_startup()
        queue_recovery = self.queue.reconcile_startup()
        book_recovery = self.book_production.reconcile_startup()
        project_recovery = self._preserve_interrupted_project_directories()
        return self._record_run(
            "startup",
            queue_recovery=queue_recovery,
            export_recovery=export_recovery,
            project_recovery=project_recovery,
            book_recovery=book_recovery,
        )

    def run_manual_check(self) -> dict[str, Any]:
        return self._record_run(
            "manual",
            queue_recovery={"needs_review": 0, "paused": 0},
            export_recovery={
                "interrupted_exports_failed_closed": 0,
                "partial_directories_preserved": 0,
            },
            project_recovery={"interrupted_workspaces_preserved": 0},
            book_recovery={"book_plans_paused": 0, "book_plans_needs_review": 0},
        )

    def latest(self) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT * FROM recovery_runs
                   ORDER BY created_at DESC, recovery_run_id DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            return {
                "status": "needs_attention",
                "message": "尚未完成本地恢复检查。",
                "external_requests_started": 0,
            }
        summary = json.loads(str(row["summary_json"]))
        return {
            "recovery_run_id": str(row["recovery_run_id"]),
            "trigger": str(row["trigger"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            **summary,
            "external_requests_started": 0,
        }

    def _record_run(
        self,
        trigger: RecoveryTrigger,
        *,
        queue_recovery: dict[str, int],
        export_recovery: dict[str, int],
        project_recovery: dict[str, int],
        book_recovery: dict[str, int],
    ) -> dict[str, Any]:
        integrity = self._integrity_summary()
        attention_count = (
            queue_recovery["needs_review"]
            + export_recovery["interrupted_exports_failed_closed"]
            + project_recovery["interrupted_workspaces_preserved"]
            + book_recovery["book_plans_paused"]
            + book_recovery["book_plans_needs_review"]
            + integrity["critical_findings"]
            + integrity["staging_items"]
            + integrity["unregistered_version_files"]
        )
        status = "healthy" if attention_count == 0 else "needs_attention"
        summary = {
            "queue_recovery": queue_recovery,
            "export_recovery": export_recovery,
            "project_recovery": project_recovery,
            "book_recovery": book_recovery,
            "integrity": integrity,
            "provider_requests_started": 0,
        }
        recovery_run_id = str(uuid7())
        with self.database.writer() as connection:
            connection.execute(
                """INSERT INTO recovery_runs(
                       recovery_run_id, trigger, status, summary_json
                   ) VALUES (?, ?, ?, ?)""",
                (recovery_run_id, trigger, status, canonical_json(summary)),
            )
        return {
            "recovery_run_id": recovery_run_id,
            "trigger": trigger,
            "status": status,
            **summary,
            "external_requests_started": 0,
        }

    def _integrity_summary(self) -> dict[str, int | bool]:
        missing_files = 0
        hash_mismatches = 0
        staging_items = 0
        unregistered_version_files = 0
        forbidden_project_files = 0
        invalid_audit_payloads = 0

        with self.database.reader() as connection:
            foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            project_rows = connection.execute(
                "SELECT project_id, workspace_path FROM projects"
            ).fetchall()
            file_rows = self._registered_files(connection)
            audit_rows = connection.execute("SELECT payload_json FROM audit_events").fetchall()

        registered_paths: set[Path] = set()
        for path, expected_sha in file_rows:
            resolved = path.resolve()
            registered_paths.add(resolved)
            if not resolved.is_file():
                missing_files += 1
            elif expected_sha is not None and _sha256_file(resolved) != expected_sha:
                hash_mismatches += 1

        for row in project_rows:
            workspace = Path(str(row["workspace_path"])).resolve()
            if (
                not workspace.is_relative_to(self.projects.projects_dir.resolve())
                or not workspace.is_dir()
            ):
                missing_files += 1
                continue
            staging_items += sum(
                1
                for pattern in (
                    "assets/.staging/*",
                    "pages/.staging/*",
                    "exports/.staging-*",
                    "source/preflight/.orphan-*",
                )
                for _item in workspace.glob(pattern)
            )
            for pattern in (
                "assets/panels/**/original.png",
                "assets/masks/**/mask.png",
                "pages/**/page.png",
            ):
                unregistered_version_files += sum(
                    1 for path in workspace.glob(pattern) if path.resolve() not in registered_paths
                )
            forbidden_project_files += self._forbidden_file_count(workspace)

        for row in audit_rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                invalid_audit_payloads += 1
                continue
            if redact_sensitive(payload) != payload:
                invalid_audit_payloads += 1

        vault_outside_projects = not self.vault.path.resolve().is_relative_to(
            self.projects.projects_dir.resolve()
        )
        database_ok = self.database.check()
        critical = (
            foreign_key_violations
            + missing_files
            + hash_mismatches
            + forbidden_project_files
            + invalid_audit_payloads
            + int(not database_ok)
        )
        return {
            "database_ok": database_ok,
            "foreign_key_violations": foreign_key_violations,
            "missing_files": missing_files,
            "hash_mismatches": hash_mismatches,
            "staging_items": staging_items,
            "unregistered_version_files": unregistered_version_files,
            "forbidden_project_files": forbidden_project_files,
            "invalid_audit_payloads": invalid_audit_payloads,
            "vault_outside_projects": vault_outside_projects,
            "critical_findings": critical + int(not vault_outside_projects),
        }

    def _registered_files(self, connection: Any) -> list[tuple[Path, str | None]]:
        result: list[tuple[Path, str | None]] = []
        for row in connection.execute("SELECT staging_path, sha256 FROM source_preflights"):
            result.append((Path(str(row["staging_path"])), str(row["sha256"])))
        for row in connection.execute(
            """SELECT original_path, normalized_path, byte_sha256, text_sha256
               FROM source_files"""
        ):
            result.extend(
                (
                    (Path(str(row["original_path"])), str(row["byte_sha256"])),
                    (Path(str(row["normalized_path"])), str(row["text_sha256"])),
                )
            )
        relative_queries = (
            (
                """SELECT p.workspace_path, r.relative_path, r.sha256
                   FROM reference_assets r JOIN projects p ON p.project_id = r.project_id""",
                "relative_path",
                "sha256",
            ),
            (
                """SELECT p.workspace_path, a.original_relative_path AS relative_path,
                          a.image_sha256 AS sha256
                   FROM asset_versions a JOIN projects p ON p.project_id = a.project_id""",
                "relative_path",
                "sha256",
            ),
            (
                """SELECT p.workspace_path, a.provenance_relative_path AS relative_path
                   FROM asset_versions a JOIN projects p ON p.project_id = a.project_id""",
                "relative_path",
                None,
            ),
            (
                """SELECT p.workspace_path, m.relative_path, m.sha256
                   FROM mask_assets m JOIN projects p ON p.project_id = m.project_id""",
                "relative_path",
                "sha256",
            ),
            (
                """SELECT p.workspace_path, v.rendered_relative_path AS relative_path,
                          v.render_sha256 AS sha256
                   FROM page_versions v JOIN comic_pages c ON c.page_id = v.page_id
                   JOIN projects p ON p.project_id = c.project_id""",
                "relative_path",
                "sha256",
            ),
            (
                """SELECT p.workspace_path,
                          'exports/' || f.relative_path AS relative_path, f.sha256
                   FROM export_files f JOIN export_revisions e
                     ON e.export_revision_id = f.export_revision_id
                   JOIN projects p ON p.project_id = e.project_id""",
                "relative_path",
                "sha256",
            ),
        )
        for query, path_column, hash_column in relative_queries:
            for row in connection.execute(query):
                expected = str(row[hash_column]) if hash_column is not None else None
                result.append(
                    (
                        Path(str(row["workspace_path"])) / str(row[path_column]),
                        expected,
                    )
                )
        return result

    def _preserve_interrupted_project_directories(self) -> dict[str, int]:
        preserved = 0
        for pattern in (".staging-*", ".restore-*"):
            for source in list(self.projects.projects_dir.glob(pattern)):
                target = self.projects.projects_dir / f".orphan-recovery-{uuid7()}"
                os.replace(source, target)
                preserved += 1
        return {"interrupted_workspaces_preserved": preserved}

    @staticmethod
    def _forbidden_file_count(workspace: Path) -> int:
        count = 0
        forbidden_names = {".env", "credentials.vault", "id_rsa", "id_ed25519"}
        forbidden_suffixes = {".key", ".pem", ".p12", ".vault"}
        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace)
            if (
                path.name.casefold() in forbidden_names
                or path.suffix.casefold() in forbidden_suffixes
                or "secrets" in {part.casefold() for part in relative.parts}
            ):
                count += 1
        return count


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
