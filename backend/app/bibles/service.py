from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from ..adaptation.models import StoryboardDocument
from ..adaptation.service import canonical_json
from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..projects import ProjectService
from .models import (
    CharacterBibleDocument,
    CharacterProfile,
    StyleBibleDocument,
    character_approval_issues,
    style_approval_issues,
)

BibleKind = Literal["character", "style"]
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_DIMENSION = 8192
MAX_REFERENCE_PIXELS = 25_000_000
IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}
@dataclass(frozen=True, slots=True)
class ReferenceImageMetadata:
    media_type: str
    extension: str
    width: int
    height: int
    sha256: str
    byte_size: int


class BibleService:
    def __init__(self, database: Database, projects: ProjectService) -> None:
        self.database = database
        self.projects = projects

    def generate_bundle(self, project_id: str, storyboard_version_id: str) -> dict[str, Any]:
        storyboard_row, storyboard = self._require_storyboard_ready(
            project_id, storyboard_version_id
        )
        chapter_id = str(storyboard_row["chapter_id"])
        character_names: list[str] = []
        seen_names: set[str] = set()
        for page in storyboard.pages:
            for panel in page.panels:
                for name in panel.characters:
                    normalized = " ".join(name.split())
                    if normalized and normalized.casefold() not in seen_names:
                        seen_names.add(normalized.casefold())
                        character_names.append(normalized)

        with self.database.writer() as connection:
            character_bible_id = self._stable_bible_id(
                connection, "character", project_id, chapter_id
            )
            style_bible_id = self._stable_bible_id(connection, "style", project_id, chapter_id)
            character_document = CharacterBibleDocument(
                schema_version="1.0",
                character_bible_id=UUID(character_bible_id),
                storyboard_version_id=UUID(storyboard_version_id),
                characters=[self._draft_character(name) for name in character_names],
                notes="这是根据已审批分镜在本机确定性草拟的角色清单，请补全后再审批。",
            )
            style_document = StyleBibleDocument(
                schema_version="1.0",
                style_bible_id=UUID(style_bible_id),
                storyboard_version_id=UUID(storyboard_version_id),
                summary="黑白分页漫画, 简体中文横排文字由本地排版, 不进入画面生成。",
                line_art="清晰稳定的黑色墨线，主体轮廓略粗，内部细节线更轻。",
                screentone="使用克制的灰阶网点区分材质与景深，避免大面积脏灰。",
                lighting="高对比黑白光影，暗部保持可读轮廓。",
                background_density="关键建立镜头使用完整背景，近景和情绪格适度留白。",
                whitespace="对白与旁白区域预留干净负空间，但图像内不生成气泡或文字。",
                camera_language="以中景建立动作关系，关键情绪使用近景，转折处使用明确构图重心。",
                positive_prompt_fragment=(
                    "black and white manga, crisp ink line art, controlled screentone, "
                    "high contrast lighting, readable composition"
                ),
                negative_prompt_fragment=(
                    "color, text, letters, speech bubble, caption, page number, watermark, logo"
                ),
                prohibited_elements=[
                    "可读文字",
                    "对白气泡",
                    "页码",
                    "水印",
                    "随机标识",
                    "彩色画面",
                ],
            )
            character_version_id = self._insert_character_version(
                connection,
                character_bible_id,
                storyboard_version_id,
                character_document,
                {"change_type": "local_storyboard_draft"},
            )
            style_version_id = self._insert_style_version(
                connection,
                style_bible_id,
                storyboard_version_id,
                style_document,
                {"change_type": "local_default_draft"},
            )
            self._audit(
                connection,
                project_id,
                "bibles.bundle_generated",
                {
                    "storyboard_version_id": storyboard_version_id,
                    "character_bible_version_id": character_version_id,
                    "style_bible_version_id": style_version_id,
                    "character_count": len(character_names),
                    "external_model_called": False,
                },
            )
        return self.get_bundle(project_id, chapter_id)

    def get_bundle(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        character_row = self._current_bible_row(project_id, chapter_id, "character")
        style_row = self._current_bible_row(project_id, chapter_id, "style")
        if character_row is None or style_row is None:
            raise ApplicationError(
                code="BIBLE_BUNDLE_NOT_FOUND",
                message="该章节尚未生成角色设定表和风格板。",
                status_code=404,
            )
        character = self._version_payload(project_id, "character", character_row)
        style = self._version_payload(project_id, "style", style_row)
        blockers: list[str] = []
        if character["approval_status"] != "approved":
            blockers.append("角色设定表尚未批准或已经失效。")
        if style["approval_status"] != "approved":
            blockers.append("风格板尚未批准或已经失效。")
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "character_bible": character,
            "style_bible": style,
            "generation_readiness": {
                "ready": not blockers,
                "blockers": blockers,
                "character_bible_version_id": character["version_id"],
                "style_bible_version_id": style["version_id"],
            },
        }

    def revise_character_bible(
        self,
        project_id: str,
        version_id: str,
        document: CharacterBibleDocument,
    ) -> dict[str, Any]:
        parent = self._version_row(project_id, "character", version_id)
        self._require_current_and_fresh(parent)
        normalized = document.model_copy(
            update={
                "character_bible_id": UUID(str(parent["bible_id"])),
                "storyboard_version_id": UUID(str(parent["storyboard_version_id"])),
            }
        )
        self._validate_reference_ids(project_id, "character", normalized)
        previous = CharacterBibleDocument.model_validate_json(str(parent["document_json"]))
        affected = self._affected_character_panels(parent, previous, normalized)
        with self.database.writer() as connection:
            self._assert_current(connection, "character", version_id)
            next_id = self._insert_character_version(
                connection,
                str(parent["bible_id"]),
                str(parent["storyboard_version_id"]),
                normalized,
                {
                    "change_type": "manual_edit",
                    "parent_version_id": version_id,
                    "affected_panel_ids": affected,
                },
            )
            self._audit(
                connection,
                project_id,
                "character_bible.version_created",
                {"version_id": next_id, "affected_panel_ids": affected},
            )
        return self._version_payload(
            project_id, "character", self._version_row(project_id, "character", next_id)
        )

    def revise_style_bible(
        self,
        project_id: str,
        version_id: str,
        document: StyleBibleDocument,
    ) -> dict[str, Any]:
        parent = self._version_row(project_id, "style", version_id)
        self._require_current_and_fresh(parent)
        normalized = document.model_copy(
            update={
                "style_bible_id": UUID(str(parent["bible_id"])),
                "storyboard_version_id": UUID(str(parent["storyboard_version_id"])),
            }
        )
        self._validate_reference_ids(project_id, "style", normalized)
        affected = self._all_panel_ids(parent)
        with self.database.writer() as connection:
            self._assert_current(connection, "style", version_id)
            next_id = self._insert_style_version(
                connection,
                str(parent["bible_id"]),
                str(parent["storyboard_version_id"]),
                normalized,
                {
                    "change_type": "manual_edit",
                    "parent_version_id": version_id,
                    "affected_panel_ids": affected,
                },
            )
            self._audit(
                connection,
                project_id,
                "style_bible.version_created",
                {"version_id": next_id, "affected_panel_ids": affected},
            )
        return self._version_payload(
            project_id, "style", self._version_row(project_id, "style", next_id)
        )

    def approve(self, project_id: str, kind: BibleKind, version_id: str) -> dict[str, Any]:
        row = self._version_row(project_id, kind, version_id)
        self._require_current_and_fresh(row)
        if kind == "character":
            character_document = CharacterBibleDocument.model_validate_json(
                str(row["document_json"])
            )
            document: CharacterBibleDocument | StyleBibleDocument = character_document
            issues = character_approval_issues(character_document)
        else:
            document = StyleBibleDocument.model_validate_json(str(row["document_json"]))
            issues = style_approval_issues(document)
        if issues:
            raise ApplicationError(
                code="BIBLE_NOT_APPROVABLE",
                message="当前设定尚未满足审批条件。",
                status_code=422,
                details={"issues": issues},
            )
        approval_hash = hashlib.sha256(
            (
                canonical_json(document.model_dump(mode="json")) + str(row["storyboard_version_id"])
            ).encode("utf-8")
        ).hexdigest()
        approval_table = f"{kind}_bible_approvals"
        version_column = f"{kind}_bible_version_id"
        with self.database.writer() as connection:
            self._assert_current(connection, kind, version_id)
            existing = connection.execute(
                f"SELECT approval_id FROM {approval_table} WHERE {version_column} = ?",
                (version_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    f"""
                    INSERT INTO {approval_table}(approval_id, {version_column}, approval_hash)
                    VALUES (?, ?, ?)
                    """,
                    (str(uuid7()), version_id, approval_hash),
                )
                self._audit(
                    connection,
                    project_id,
                    f"{kind}_bible.approved",
                    {"version_id": version_id, "approval_hash": approval_hash},
                )
        return self._version_payload(
            project_id, kind, self._version_row(project_id, kind, version_id)
        )

    def attach_reference(
        self,
        project_id: str,
        kind: BibleKind,
        version_id: str,
        *,
        character_id: str | None,
        original_filename: str,
        data: bytes,
        source_note: str,
        rights_confirmed: bool,
    ) -> dict[str, Any]:
        if not rights_confirmed:
            raise ApplicationError(
                code="REFERENCE_RIGHTS_NOT_CONFIRMED",
                message="必须确认拥有或获准使用该参考图。",
                status_code=422,
            )
        normalized_note = " ".join(source_note.split())
        if not normalized_note or len(normalized_note) > 500:
            raise ApplicationError(
                code="INVALID_REFERENCE_SOURCE_NOTE",
                message="请填写 1-500 字的参考图来源说明。",
                status_code=422,
            )
        parent = self._version_row(project_id, kind, version_id)
        self._require_current_and_fresh(parent)
        metadata = inspect_reference_image(data)
        reference_asset_id = str(uuid7())
        safe_filename = safe_original_filename(original_filename)

        if kind == "character":
            if character_id is None:
                raise ApplicationError(
                    code="CHARACTER_REFERENCE_TARGET_REQUIRED",
                    message="角色参考图必须指定目标角色。",
                    status_code=422,
                )
            document = CharacterBibleDocument.model_validate_json(str(parent["document_json"]))
            target = next(
                (
                    character
                    for character in document.characters
                    if str(character.character_id) == character_id
                ),
                None,
            )
            if target is None:
                raise ApplicationError(
                    code="CHARACTER_NOT_FOUND",
                    message="角色设定表中没有找到目标角色。",
                    status_code=404,
                )
            characters = [
                character.model_copy(
                    update={
                        "reference_asset_ids": [
                            *character.reference_asset_ids,
                            UUID(reference_asset_id),
                        ]
                    }
                )
                if character.character_id == target.character_id
                else character
                for character in document.characters
            ]
            updated_document: CharacterBibleDocument | StyleBibleDocument = document.model_copy(
                update={"characters": characters}
            )
            affected = self._panels_for_character_names(parent, {target.name})
        else:
            if character_id is not None:
                raise ApplicationError(
                    code="INVALID_STYLE_REFERENCE_TARGET",
                    message="风格参考图不能指定角色。",
                    status_code=422,
                )
            style_document = StyleBibleDocument.model_validate_json(str(parent["document_json"]))
            updated_document = style_document.model_copy(
                update={
                    "reference_asset_ids": [
                        *style_document.reference_asset_ids,
                        UUID(reference_asset_id),
                    ]
                }
            )
            affected = self._all_panel_ids(parent)

        relative_path = self._write_reference_file(project_id, metadata, data)

        with self.database.writer() as connection:
            self._assert_current(connection, kind, version_id)
            connection.execute(
                """
                INSERT INTO reference_assets(
                    reference_asset_id, project_id, bible_kind, character_id,
                    original_filename, media_type, byte_size, width, height,
                    sha256, relative_path, source_note, rights_confirmed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    reference_asset_id,
                    project_id,
                    kind,
                    character_id,
                    safe_filename,
                    metadata.media_type,
                    metadata.byte_size,
                    metadata.width,
                    metadata.height,
                    metadata.sha256,
                    relative_path,
                    normalized_note,
                ),
            )
            provenance = {
                "change_type": "reference_attached",
                "parent_version_id": version_id,
                "reference_asset_id": reference_asset_id,
                "affected_panel_ids": affected,
            }
            if kind == "character":
                next_id = self._insert_character_version(
                    connection,
                    str(parent["bible_id"]),
                    str(parent["storyboard_version_id"]),
                    cast(CharacterBibleDocument, updated_document),
                    provenance,
                )
            else:
                next_id = self._insert_style_version(
                    connection,
                    str(parent["bible_id"]),
                    str(parent["storyboard_version_id"]),
                    cast(StyleBibleDocument, updated_document),
                    provenance,
                )
            self._audit(
                connection,
                project_id,
                "reference_asset.attached",
                {
                    "reference_asset_id": reference_asset_id,
                    "bible_kind": kind,
                    "version_id": next_id,
                    "sha256": metadata.sha256,
                    "width": metadata.width,
                    "height": metadata.height,
                    "rights_confirmed": True,
                },
            )
        return {
            "bible": self._version_payload(
                project_id, kind, self._version_row(project_id, kind, next_id)
            ),
            "reference_asset": self.reference_asset(project_id, reference_asset_id),
        }

    def reference_asset(self, project_id: str, reference_asset_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM reference_assets
                WHERE project_id = ? AND reference_asset_id = ?
                """,
                (project_id, reference_asset_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="REFERENCE_ASSET_NOT_FOUND",
                message="没有找到该参考图。",
                status_code=404,
            )
        return reference_payload(row)

    def reference_content(self, project_id: str, reference_asset_id: str) -> tuple[Path, str, str]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT relative_path, media_type, original_filename
                FROM reference_assets WHERE project_id = ? AND reference_asset_id = ?
                """,
                (project_id, reference_asset_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="REFERENCE_ASSET_NOT_FOUND",
                message="没有找到该参考图。",
                status_code=404,
            )
        workspace = self.projects.workspace_path(project_id)
        allowed_root = (workspace / "assets" / "references").resolve()
        candidate = (workspace / str(row["relative_path"])).resolve()
        if not candidate.is_relative_to(allowed_root) or not candidate.is_file():
            raise ApplicationError(
                code="REFERENCE_ASSET_MISSING",
                message="参考图文件缺失或路径无效。",
                status_code=409,
            )
        return candidate, str(row["media_type"]), str(row["original_filename"])

    @staticmethod
    def _draft_character(name: str) -> CharacterProfile:
        placeholder = "待补充"
        return CharacterProfile(
            character_id=uuid7(),
            name=name,
            aliases=[],
            narrative_role=f"{placeholder}: 主要、次要或一次性角色",
            age_range=f"{placeholder}: 年龄段",
            face_shape=f"{placeholder}: 脸型与五官",
            hair=f"{placeholder}: 发型、长度与颜色明度",
            body_type=f"{placeholder}: 体型与身高关系",
            outfit=[f"{placeholder}: 常用服装"],
            signature_features=[f"{placeholder}: 稳定标志物"],
            variable_features=[],
            forbidden_changes=[f"{placeholder}: 不得漂移的外观特征"],
            props=[],
            relationships=[],
            expression_range=[f"{placeholder}: 可用表情范围"],
            positive_prompt_fragment=f"consistent character design, {name}",
            negative_prompt_fragment="inconsistent face, inconsistent hair, inconsistent outfit",
            reference_asset_ids=[],
        )

    def _stable_bible_id(
        self,
        connection: sqlite3.Connection,
        kind: BibleKind,
        project_id: str,
        chapter_id: str,
    ) -> str:
        table = f"{kind}_bibles"
        id_column = f"{kind}_bible_id"
        row = connection.execute(
            f"SELECT {id_column} FROM {table} WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id),
        ).fetchone()
        if row is not None:
            return str(row[id_column])
        bible_id = str(uuid7())
        connection.execute(
            f"INSERT INTO {table}({id_column}, project_id, chapter_id) VALUES (?, ?, ?)",
            (bible_id, project_id, chapter_id),
        )
        return bible_id

    def _insert_character_version(
        self,
        connection: sqlite3.Connection,
        bible_id: str,
        storyboard_version_id: str,
        document: CharacterBibleDocument,
        provenance: dict[str, Any],
    ) -> str:
        version_id = str(uuid7())
        version = self._next_version(connection, "character", bible_id)
        normalized = document.model_copy(
            update={
                "character_bible_id": UUID(bible_id),
                "storyboard_version_id": UUID(storyboard_version_id),
            }
        )
        connection.execute(
            "UPDATE character_bible_versions SET is_current = 0 "
            "WHERE character_bible_id = ? AND is_current = 1",
            (bible_id,),
        )
        connection.execute(
            """
            INSERT INTO character_bible_versions(
                character_bible_version_id, character_bible_id, version,
                storyboard_version_id, document_json, provenance_json, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                version_id,
                bible_id,
                version,
                storyboard_version_id,
                canonical_json(normalized.model_dump(mode="json")),
                canonical_json(provenance),
            ),
        )
        return version_id

    def _insert_style_version(
        self,
        connection: sqlite3.Connection,
        bible_id: str,
        storyboard_version_id: str,
        document: StyleBibleDocument,
        provenance: dict[str, Any],
    ) -> str:
        version_id = str(uuid7())
        version = self._next_version(connection, "style", bible_id)
        normalized = document.model_copy(
            update={
                "style_bible_id": UUID(bible_id),
                "storyboard_version_id": UUID(storyboard_version_id),
            }
        )
        connection.execute(
            "UPDATE style_bible_versions SET is_current = 0 "
            "WHERE style_bible_id = ? AND is_current = 1",
            (bible_id,),
        )
        connection.execute(
            """
            INSERT INTO style_bible_versions(
                style_bible_version_id, style_bible_id, version,
                storyboard_version_id, document_json, provenance_json, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                version_id,
                bible_id,
                version,
                storyboard_version_id,
                canonical_json(normalized.model_dump(mode="json")),
                canonical_json(provenance),
            ),
        )
        return version_id

    @staticmethod
    def _next_version(connection: sqlite3.Connection, kind: BibleKind, bible_id: str) -> int:
        row = connection.execute(
            f"""
            SELECT COALESCE(MAX(version), 0) AS version
            FROM {kind}_bible_versions WHERE {kind}_bible_id = ?
            """,
            (bible_id,),
        ).fetchone()
        return int(row["version"]) + 1

    def _current_bible_row(
        self, project_id: str, chapter_id: str, kind: BibleKind
    ) -> sqlite3.Row | None:
        with self.database.reader() as connection:
            row = connection.execute(
                f"""
                SELECT bv.*, b.{kind}_bible_id AS bible_id, b.project_id, b.chapter_id,
                       a.approval_hash, a.created_at AS approved_at
                FROM {kind}_bibles b
                JOIN {kind}_bible_versions bv
                  ON bv.{kind}_bible_id = b.{kind}_bible_id
                LEFT JOIN {kind}_bible_approvals a
                  ON a.{kind}_bible_version_id = bv.{kind}_bible_version_id
                WHERE b.project_id = ? AND b.chapter_id = ? AND bv.is_current = 1
                """,
                (project_id, chapter_id),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _version_row(self, project_id: str, kind: BibleKind, version_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                f"""
                SELECT bv.*, b.{kind}_bible_id AS bible_id, b.project_id, b.chapter_id,
                       a.approval_hash, a.created_at AS approved_at
                FROM {kind}_bible_versions bv
                JOIN {kind}_bibles b ON b.{kind}_bible_id = bv.{kind}_bible_id
                LEFT JOIN {kind}_bible_approvals a
                  ON a.{kind}_bible_version_id = bv.{kind}_bible_version_id
                WHERE b.project_id = ? AND bv.{kind}_bible_version_id = ?
                """,
                (project_id, version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="BIBLE_VERSION_NOT_FOUND",
                message="没有找到该设定版本。",
                status_code=404,
            )
        return cast(sqlite3.Row, row)

    def _version_payload(
        self, project_id: str, kind: BibleKind, row: sqlite3.Row
    ) -> dict[str, Any]:
        if kind == "character":
            character_document = CharacterBibleDocument.model_validate_json(
                str(row["document_json"])
            )
            document: CharacterBibleDocument | StyleBibleDocument = character_document
            issues = character_approval_issues(character_document)
            version_id = str(row["character_bible_version_id"])
        else:
            document = StyleBibleDocument.model_validate_json(str(row["document_json"]))
            issues = style_approval_issues(document)
            version_id = str(row["style_bible_version_id"])
        fresh = self._storyboard_version_is_ready(project_id, str(row["storyboard_version_id"]))
        approved_at = str(row["approved_at"]) if row["approved_at"] is not None else None
        approval_status = "stale" if not fresh else "approved" if approved_at else "draft"
        asset_ids = document_reference_ids(document)
        references = self._reference_payloads(project_id, asset_ids)
        return {
            "kind": kind,
            "bible_id": str(row["bible_id"]),
            "version_id": version_id,
            "version": int(row["version"]),
            "storyboard_version_id": str(row["storyboard_version_id"]),
            "document": document.model_dump(mode="json"),
            "provenance": json.loads(str(row["provenance_json"])),
            "approval_status": approval_status,
            "approval_hash": str(row["approval_hash"]) if row["approval_hash"] else None,
            "approved_at": approved_at,
            "approval_issues": issues,
            "reference_assets": references,
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
        }

    def _reference_payloads(self, project_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
        if not asset_ids:
            return []
        placeholders = ",".join("?" for _ in asset_ids)
        with self.database.reader() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM reference_assets
                WHERE project_id = ? AND reference_asset_id IN ({placeholders})
                ORDER BY created_at, reference_asset_id
                """,
                (project_id, *asset_ids),
            ).fetchall()
        return [reference_payload(row) for row in rows]

    def _validate_reference_ids(
        self,
        project_id: str,
        kind: BibleKind,
        document: CharacterBibleDocument | StyleBibleDocument,
    ) -> None:
        asset_ids = document_reference_ids(document)
        if not asset_ids:
            return
        placeholders = ",".join("?" for _ in asset_ids)
        with self.database.reader() as connection:
            rows = connection.execute(
                f"""
                SELECT reference_asset_id, bible_kind, character_id
                FROM reference_assets
                WHERE project_id = ? AND reference_asset_id IN ({placeholders})
                """,
                (project_id, *asset_ids),
            ).fetchall()
        by_id = {str(row["reference_asset_id"]): row for row in rows}
        if set(by_id) != set(asset_ids):
            raise ApplicationError(
                code="INVALID_REFERENCE_ASSET",
                message="设定引用了不存在或属于其他项目的参考图。",
                status_code=422,
            )
        if kind == "style":
            if any(row["bible_kind"] != "style" for row in rows):
                raise ApplicationError(
                    code="INVALID_REFERENCE_ASSET_KIND",
                    message="风格板只能引用风格参考图。",
                    status_code=422,
                )
            return
        character_document = cast(CharacterBibleDocument, document)
        for character in character_document.characters:
            for asset_id in character.reference_asset_ids:
                row = by_id[str(asset_id)]
                if row["bible_kind"] != "character" or str(row["character_id"]) != str(
                    character.character_id
                ):
                    raise ApplicationError(
                        code="INVALID_CHARACTER_REFERENCE",
                        message="角色参考图不能绑定到其他角色。",
                        status_code=422,
                    )

    def _require_storyboard_ready(
        self, project_id: str, storyboard_version_id: str
    ) -> tuple[sqlite3.Row, StoryboardDocument]:
        row = self._storyboard_row(project_id, storyboard_version_id)
        if (
            not bool(row["is_current"])
            or row["approval_hash"] is None
            or not bool(row["source_current"])
        ):
            raise ApplicationError(
                code="STORYBOARD_APPROVAL_REQUIRED",
                message="请先审批当前且来源未变化的结构化分镜。",
                status_code=409,
            )
        return row, StoryboardDocument.model_validate_json(str(row["document_json"]))

    def _storyboard_row(self, project_id: str, storyboard_version_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sv.*, s.project_id, s.chapter_id, sa.approval_hash,
                       EXISTS(
                           SELECT 1
                           FROM source_chapters c
                           JOIN source_chapter_sets cs
                             ON cs.chapter_set_id = c.chapter_set_id
                           JOIN story_beat_sets sbs ON sbs.chapter_id = c.chapter_id
                           WHERE c.chapter_id = s.chapter_id AND cs.is_current = 1
                             AND sbs.beat_set_id = sv.beat_set_id AND sbs.is_current = 1
                       ) AS source_current
                FROM storyboard_versions sv
                JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
                LEFT JOIN storyboard_approvals sa
                  ON sa.storyboard_version_id = sv.storyboard_version_id
                WHERE s.project_id = ? AND sv.storyboard_version_id = ?
                """,
                (project_id, storyboard_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="STORYBOARD_VERSION_NOT_FOUND",
                message="没有找到该分镜版本。",
                status_code=404,
            )
        return cast(sqlite3.Row, row)

    def _storyboard_version_is_ready(self, project_id: str, storyboard_version_id: str) -> bool:
        row = self._storyboard_row(project_id, storyboard_version_id)
        return bool(row["is_current"] and row["approval_hash"] and row["source_current"])

    def _require_current_and_fresh(self, row: sqlite3.Row) -> None:
        if not bool(row["is_current"]):
            raise ApplicationError(
                code="BIBLE_VERSION_NOT_CURRENT",
                message="只能从当前设定版本继续编辑或审批。",
                status_code=409,
            )
        if not self._storyboard_version_is_ready(
            str(row["project_id"]), str(row["storyboard_version_id"])
        ):
            raise ApplicationError(
                code="BIBLE_INPUT_STALE",
                message="分镜已经变化或失效，请重新生成角色设定表和风格板。",
                status_code=409,
            )

    @staticmethod
    def _assert_current(connection: sqlite3.Connection, kind: BibleKind, version_id: str) -> None:
        row = connection.execute(
            f"""
            SELECT is_current FROM {kind}_bible_versions
            WHERE {kind}_bible_version_id = ?
            """,
            (version_id,),
        ).fetchone()
        if row is None or not bool(row["is_current"]):
            raise ApplicationError(
                code="BIBLE_VERSION_NOT_CURRENT",
                message="设定版本已变化，请刷新后重试。",
                status_code=409,
            )

    def _affected_character_panels(
        self,
        row: sqlite3.Row,
        previous: CharacterBibleDocument,
        current: CharacterBibleDocument,
    ) -> list[str]:
        previous_by_id = {
            str(character.character_id): character.model_dump(mode="json")
            for character in previous.characters
        }
        current_by_id = {
            str(character.character_id): character.model_dump(mode="json")
            for character in current.characters
        }
        changed_ids = {
            character_id
            for character_id in set(previous_by_id) | set(current_by_id)
            if previous_by_id.get(character_id) != current_by_id.get(character_id)
        }
        names = {
            character.name
            for character in [*previous.characters, *current.characters]
            if str(character.character_id) in changed_ids
        }
        return self._panels_for_character_names(row, names)

    def _panels_for_character_names(self, row: sqlite3.Row, names: set[str]) -> list[str]:
        storyboard = StoryboardDocument.model_validate_json(
            str(
                self._storyboard_row(str(row["project_id"]), str(row["storyboard_version_id"]))[
                    "document_json"
                ]
            )
        )
        folded = {name.casefold() for name in names}
        return [
            str(panel.panel_id)
            for page in storyboard.pages
            for panel in page.panels
            if folded.intersection(name.casefold() for name in panel.characters)
        ]

    def _all_panel_ids(self, row: sqlite3.Row) -> list[str]:
        storyboard = StoryboardDocument.model_validate_json(
            str(
                self._storyboard_row(str(row["project_id"]), str(row["storyboard_version_id"]))[
                    "document_json"
                ]
            )
        )
        return [str(panel.panel_id) for page in storyboard.pages for panel in page.panels]

    def _write_reference_file(
        self, project_id: str, metadata: ReferenceImageMetadata, data: bytes
    ) -> str:
        workspace = self.projects.workspace_path(project_id)
        root = (workspace / "assets" / "references").resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = root / f"{metadata.sha256}.{metadata.extension}"
        if target.exists():
            existing = target.read_bytes()
            if len(existing) != metadata.byte_size or hashlib.sha256(existing).hexdigest() != (
                metadata.sha256
            ):
                raise ApplicationError(
                    code="REFERENCE_STORE_CONFLICT",
                    message="本地参考图内容寻址文件与预期哈希不一致。",
                    status_code=409,
                )
        else:
            staging = root / f".staging-{uuid7()}"
            descriptor = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(staging, target)
            except Exception:
                if staging.exists():
                    os.replace(staging, root / f".orphan-{uuid7()}")
                raise
        return target.relative_to(workspace).as_posix()

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (str(uuid7()), project_id, event_type, canonical_json(payload)),
        )


def inspect_reference_image(data: bytes) -> ReferenceImageMetadata:
    if not data or len(data) > MAX_REFERENCE_BYTES:
        raise ApplicationError(
            code="INVALID_REFERENCE_IMAGE_SIZE",
            message="参考图必须大于 0 字节且不超过 10 MB。",
            status_code=413 if len(data) > MAX_REFERENCE_BYTES else 422,
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                if (
                    width > MAX_REFERENCE_DIMENSION
                    or height > MAX_REFERENCE_DIMENSION
                    or width * height > MAX_REFERENCE_PIXELS
                ):
                    raise ApplicationError(
                        code="REFERENCE_IMAGE_TOO_LARGE",
                        message="参考图尺寸不得超过 8192 像素或 2500 万像素。",
                        status_code=422,
                    )
                image.verify()
            with Image.open(BytesIO(data)) as decoded:
                decoded.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ApplicationError(
            code="INVALID_REFERENCE_IMAGE",
            message="参考图无法安全解码。",
            status_code=422,
        ) from exc
    except Image.DecompressionBombWarning as exc:
        raise ApplicationError(
            code="REFERENCE_IMAGE_TOO_LARGE",
            message="参考图像素总量过大。",
            status_code=422,
        ) from exc
    if image_format not in IMAGE_FORMATS:
        raise ApplicationError(
            code="UNSUPPORTED_REFERENCE_IMAGE",
            message="P0 只支持 PNG、JPEG 和 WebP 参考图。",
            status_code=415,
        )
    media_type, extension = IMAGE_FORMATS[image_format]
    return ReferenceImageMetadata(
        media_type=media_type,
        extension=extension,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
    )


def safe_original_filename(filename: str) -> str:
    normalized = Path(filename.replace("\x00", "")).name.strip()
    return normalized[:255] or "reference-image"


def document_reference_ids(
    document: CharacterBibleDocument | StyleBibleDocument,
) -> list[str]:
    if isinstance(document, CharacterBibleDocument):
        return [
            str(asset_id)
            for character in document.characters
            for asset_id in character.reference_asset_ids
        ]
    return [str(asset_id) for asset_id in document.reference_asset_ids]


def reference_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "reference_asset_id": str(row["reference_asset_id"]),
        "bible_kind": str(row["bible_kind"]),
        "character_id": str(row["character_id"]) if row["character_id"] else None,
        "original_filename": str(row["original_filename"]),
        "media_type": str(row["media_type"]),
        "byte_size": int(row["byte_size"]),
        "width": int(row["width"]),
        "height": int(row["height"]),
        "sha256": str(row["sha256"]),
        "source_note": str(row["source_note"]),
        "rights_confirmed": bool(row["rights_confirmed"]),
        "created_at": str(row["created_at"]),
    }
