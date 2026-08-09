from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from ..database import Database
from ..errors import ApplicationError
from ..generation.assets import canonical_json
from ..ids import uuid7

LibraryKind = Literal["character", "prop", "location", "panel"]


class AssetLibraryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_items(
        self, project_id: str, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        clause = "" if include_archived else "AND library.status = 'active'"
        with self.database.reader() as connection:
            project = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            rows = connection.execute(
                f"""SELECT library.*, asset.panel_id, asset.image_sha256,
                           asset.width, asset.height
                    FROM asset_library_items library
                    JOIN asset_versions asset
                      ON asset.asset_version_id = library.source_asset_version_id
                    WHERE library.project_id = ? {clause}
                    ORDER BY library.kind, library.name, library.created_at""",
                (project_id,),
            ).fetchall()
        if project is None:
            raise ApplicationError("PROJECT_NOT_FOUND", "没有找到该项目。", 404)
        return [self._payload(row) for row in rows]

    def create_item(
        self,
        project_id: str,
        *,
        source_asset_version_id: str,
        kind: LibraryKind,
        name: str,
        tags: list[str],
        notes: str,
    ) -> dict[str, Any]:
        normalized_tags = self._normalize_tags(tags)
        library_item_id = str(uuid7())
        try:
            with self.database.writer() as connection:
                asset = connection.execute(
                    """SELECT 1 FROM asset_versions
                       WHERE project_id = ? AND asset_version_id = ? AND status = 'ready'""",
                    (project_id, source_asset_version_id),
                ).fetchone()
                if asset is None:
                    raise ApplicationError(
                        "LIBRARY_ASSET_INVALID",
                        "只能收藏当前项目中已完成的面板素材。",
                        422,
                    )
                connection.execute(
                    """INSERT INTO asset_library_items(
                           library_item_id, project_id, source_asset_version_id,
                           kind, name, tags_json, notes
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        library_item_id,
                        project_id,
                        source_asset_version_id,
                        kind,
                        name.strip(),
                        canonical_json(normalized_tags),
                        notes.strip(),
                    ),
                )
                self._audit(
                    connection,
                    project_id,
                    "asset_library.item_created",
                    {
                        "library_item_id": library_item_id,
                        "source_asset_version_id": source_asset_version_id,
                    },
                )
        except sqlite3.IntegrityError as exc:
            raise ApplicationError(
                "LIBRARY_ITEM_ALREADY_EXISTS", "该素材已在项目素材库中。", 409
            ) from exc
        return self.get_item(project_id, library_item_id)

    def update_item(
        self,
        project_id: str,
        library_item_id: str,
        *,
        kind: LibraryKind,
        name: str,
        tags: list[str],
        notes: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_tags = self._normalize_tags(tags)
        with self.database.writer() as connection:
            updated = connection.execute(
                """UPDATE asset_library_items
                   SET kind = ?, name = ?, tags_json = ?, notes = ?,
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE project_id = ? AND library_item_id = ? AND revision = ?""",
                (
                    kind,
                    name.strip(),
                    canonical_json(normalized_tags),
                    notes.strip(),
                    project_id,
                    library_item_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                self._raise_missing_or_conflict(
                    connection, project_id, library_item_id
                )
            self._audit(
                connection,
                project_id,
                "asset_library.item_updated",
                {"library_item_id": library_item_id},
            )
        return self.get_item(project_id, library_item_id)

    def set_status(
        self,
        project_id: str,
        library_item_id: str,
        *,
        status: Literal["active", "archived"],
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.database.writer() as connection:
            updated = connection.execute(
                """UPDATE asset_library_items
                   SET status = ?, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE project_id = ? AND library_item_id = ? AND revision = ?""",
                (status, project_id, library_item_id, expected_revision),
            ).rowcount
            if updated != 1:
                self._raise_missing_or_conflict(
                    connection, project_id, library_item_id
                )
            self._audit(
                connection,
                project_id,
                f"asset_library.item_{status}",
                {"library_item_id": library_item_id},
            )
        return self.get_item(project_id, library_item_id)

    def get_item(self, project_id: str, library_item_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT library.*, asset.panel_id, asset.image_sha256,
                          asset.width, asset.height
                   FROM asset_library_items library
                   JOIN asset_versions asset
                     ON asset.asset_version_id = library.source_asset_version_id
                   WHERE library.project_id = ? AND library.library_item_id = ?""",
                (project_id, library_item_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "LIBRARY_ITEM_NOT_FOUND", "没有找到该素材库项目。", 404
            )
        return self._payload(row)

    def content_path(self, project_id: str, library_item_id: str) -> Path:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT asset.original_relative_path, project.workspace_path
                   FROM asset_library_items library
                   JOIN asset_versions asset
                     ON asset.asset_version_id = library.source_asset_version_id
                   JOIN projects project ON project.project_id = library.project_id
                   WHERE library.project_id = ? AND library.library_item_id = ?""",
                (project_id, library_item_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "LIBRARY_ITEM_NOT_FOUND", "没有找到该素材库项目。", 404
            )
        workspace = Path(str(row["workspace_path"])).resolve()
        path = (workspace / str(row["original_relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError(
                "LIBRARY_ASSET_FILE_MISSING", "素材库引用的原始文件缺失。", 409
            )
        return path

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if len(normalized) > 20 or any(len(tag) > 40 for tag in normalized):
            raise ApplicationError(
                "LIBRARY_TAGS_INVALID", "素材标签最多 20 个，每个不超过 40 字。", 422
            )
        return normalized

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        try:
            tags = json.loads(str(row["tags_json"]))
        except json.JSONDecodeError as exc:
            raise ApplicationError(
                "LIBRARY_ITEM_CORRUPT", "素材库标签数据已损坏。", 500
            ) from exc
        return {
            "library_item_id": str(row["library_item_id"]),
            "project_id": str(row["project_id"]),
            "source_asset_version_id": str(row["source_asset_version_id"]),
            "source_panel_id": str(row["panel_id"]),
            "kind": str(row["kind"]),
            "name": str(row["name"]),
            "tags": tags,
            "notes": str(row["notes"]),
            "status": str(row["status"]),
            "revision": int(row["revision"]),
            "image_sha256": str(row["image_sha256"]),
            "width": int(row["width"]),
            "height": int(row["height"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "external_requests_started": 0,
        }

    @staticmethod
    def _raise_missing_or_conflict(
        connection: sqlite3.Connection, project_id: str, library_item_id: str
    ) -> None:
        exists = connection.execute(
            """SELECT 1 FROM asset_library_items
               WHERE project_id = ? AND library_item_id = ?""",
            (project_id, library_item_id),
        ).fetchone()
        if exists is None:
            raise ApplicationError(
                "LIBRARY_ITEM_NOT_FOUND", "没有找到该素材库项目。", 404
            )
        raise ApplicationError(
            "LIBRARY_ITEM_REVISION_CONFLICT", "素材库条目已变化，请刷新后重试。", 409
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
               VALUES (?, ?, ?, ?)""",
            (
                str(uuid7()),
                project_id,
                event_type,
                canonical_json({**payload, "external_requests_started": 0}),
            ),
        )
