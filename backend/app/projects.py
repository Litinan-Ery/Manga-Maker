from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import Database
from .errors import ApplicationError
from .ids import uuid7

PROJECT_DIRECTORIES = (
    "source/preflight",
    "source/chapters",
    "storyboard/versions",
    "layouts/versions",
    "bibles/characters",
    "bibles/styles",
    "assets/references",
    "assets/panels",
    "assets/masks",
    "assets/staging",
    "pages",
    "exports",
    "audit",
)


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    title: str
    status: str
    revision: int
    workflow_version: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> ProjectRecord:
        return cls(
            project_id=str(row["project_id"]),
            title=str(row["title"]),
            status=str(row["status"]),
            revision=int(row["revision"]),
            workflow_version=str(row["workflow_version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class ProjectService:
    def __init__(self, database: Database, projects_dir: Path) -> None:
        self.database = database
        self.projects_dir = projects_dir

    def create(self, title: str) -> ProjectRecord:
        normalized_title = " ".join(title.split())
        if not normalized_title:
            raise ApplicationError(
                code="INVALID_PROJECT_TITLE",
                message="项目名称不能为空。",
                status_code=422,
            )

        project_id = str(uuid7())
        final_path = self.projects_dir / project_id
        staging_path = self.projects_dir / f".staging-{project_id}"
        orphan_path = self.projects_dir / f".orphan-{project_id}"
        self.projects_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging_path.mkdir(mode=0o700)
        try:
            for relative_directory in PROJECT_DIRECTORIES:
                (staging_path / relative_directory).mkdir(mode=0o700, parents=True)
            manifest = {
                "schema_version": "1.0",
                "project_id": project_id,
                "title": normalized_title,
                "status": "draft",
                "workflow_version": "v03",
            }
            self._write_json(staging_path / "manifest.json", manifest)
            os.replace(staging_path, final_path)

            try:
                with self.database.writer() as connection:
                    connection.execute(
                        """
                        INSERT INTO projects(
                            project_id, title, workspace_path, workflow_version
                        ) VALUES (?, ?, ?, 'v03')
                        """,
                        (project_id, normalized_title, str(final_path)),
                    )
                    connection.execute(
                        """
                        INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
                        VALUES (?, ?, 'project.created', ?)
                        """,
                        (
                            str(uuid7()),
                            project_id,
                            json.dumps({"title": normalized_title}, ensure_ascii=False),
                        ),
                    )
            except Exception:
                os.replace(final_path, orphan_path)
                raise
        except Exception:
            if staging_path.exists():
                os.replace(staging_path, orphan_path)
            raise
        return self.get(project_id)

    def get(self, project_id: str) -> ProjectRecord:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT project_id, title, status, revision, workflow_version,
                       created_at, updated_at
                FROM projects WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="PROJECT_NOT_FOUND",
                message="没有找到该项目。",
                status_code=404,
            )
        return ProjectRecord.from_row(row)

    def list(self) -> list[ProjectRecord]:
        with self.database.reader() as connection:
            rows = connection.execute(
                """
                SELECT project_id, title, status, revision, workflow_version,
                       created_at, updated_at
                FROM projects ORDER BY updated_at DESC, project_id DESC
                """
            ).fetchall()
        return [ProjectRecord.from_row(row) for row in rows]

    def workspace_path(self, project_id: str) -> Path:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT workspace_path FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="PROJECT_NOT_FOUND",
                message="没有找到该项目。",
                status_code=404,
            )
        candidate = Path(str(row["workspace_path"])).resolve()
        allowed_root = self.projects_dir.resolve()
        if not candidate.is_relative_to(allowed_root):
            raise ApplicationError(
                code="INVALID_PROJECT_WORKSPACE",
                message="项目工作区路径无效。",
                status_code=500,
            )
        return candidate

    def require_writable(self, project_id: str) -> None:
        if self.get(project_id).workflow_version != "v03":
            raise ApplicationError(
                code="LEGACY_PROJECT_READ_ONLY",
                message="v0.2 历史工程在完成迁移前仅允许查看。",
                status_code=409,
            )

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
