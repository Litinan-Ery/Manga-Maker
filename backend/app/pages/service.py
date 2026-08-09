from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError

from ..adaptation.models import PageCandidate, StoryboardDocument
from ..database import Database
from ..errors import ApplicationError
from ..generation.assets import canonical_json, fsync_directory, write_synced
from ..ids import uuid7
from .models import PageDocument, PanelPlacement, PixelRect, TextLayer
from .renderer import PageRenderer, PageRenderError, RenderedPage
from .templates import PageTemplate, template_for_count, templates


class PageService:
    def __init__(self, database: Database, renderer: PageRenderer | None = None) -> None:
        self.database = database
        self.renderer = renderer or PageRenderer()

    def template_payloads(self) -> list[dict[str, object]]:
        return [page_template.payload() for page_template in templates()]

    def require_project(self, project_id: str) -> None:
        self._require_project(project_id)

    def draft_pages(self, project_id: str, chapter_id: str) -> list[dict[str, Any]]:
        storyboard_version_id, storyboard = self._approved_storyboard(
            project_id, chapter_id
        )
        results: list[dict[str, Any]] = []
        for page in storyboard.pages:
            existing = self._optional_current(project_id, str(page.page_id))
            if existing is not None:
                results.append(existing)
                continue
            page_template = template_for_count(len(page.panels))
            assets = self._current_assets_for_page(project_id, page)
            document = PageDocument(
                page_id=str(page.page_id),
                page_number=page.page_number,
                template_id=page_template.template_id,
                storyboard_version_id=storyboard_version_id,
                panels=[
                    PanelPlacement(
                        panel_id=str(panel.panel_id),
                        asset_version_id=assets[str(panel.panel_id)]["asset_version_id"],
                        frame=page_template.frames[index],
                    )
                    for index, panel in enumerate(page.panels)
                ],
                text_layers=default_text_layers(page, page_template),
            )
            self._insert_page_root(project_id, chapter_id, page)
            results.append(
                self._create_version(
                    project_id,
                    str(page.page_id),
                    document,
                    expected_revision=1,
                    initial=True,
                )
            )
        return results

    def list_pages(self, project_id: str, chapter_id: str) -> list[dict[str, Any]]:
        self._require_project(project_id)
        with self.database.reader() as connection:
            rows = connection.execute(
                """
                SELECT page_id FROM comic_pages
                WHERE project_id = ? AND chapter_id = ?
                ORDER BY page_number
                """,
                (project_id, chapter_id),
            ).fetchall()
        return [self.get_current(project_id, str(row["page_id"])) for row in rows]

    def get_current(self, project_id: str, page_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT pv.*, cp.project_id, cp.chapter_id, cp.page_number,
                       cp.revision AS page_revision
                FROM page_versions pv
                JOIN comic_pages cp ON cp.page_id = pv.page_id
                WHERE cp.project_id = ? AND cp.page_id = ? AND pv.is_current = 1
                """,
                (project_id, page_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("PAGE_VERSION_NOT_FOUND", "没有找到当前页面版本。", 404)
        return self._payload(row)

    def get_version(
        self, project_id: str, page_id: str, page_version_id: str
    ) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT pv.*, cp.project_id, cp.chapter_id, cp.page_number,
                       cp.revision AS page_revision
                FROM page_versions pv
                JOIN comic_pages cp ON cp.page_id = pv.page_id
                WHERE cp.project_id = ? AND cp.page_id = ?
                  AND pv.page_version_id = ?
                """,
                (project_id, page_id, page_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("PAGE_VERSION_NOT_FOUND", "没有找到该页面版本。", 404)
        return self._payload(row)

    def create_revision(
        self,
        project_id: str,
        page_id: str,
        document: PageDocument,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        return self._create_version(
            project_id,
            page_id,
            document,
            expected_revision=expected_revision,
            initial=False,
        )

    def content_path(
        self, project_id: str, page_id: str, page_version_id: str
    ) -> Path:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT pv.rendered_relative_path, p.workspace_path
                FROM page_versions pv
                JOIN comic_pages cp ON cp.page_id = pv.page_id
                JOIN projects p ON p.project_id = cp.project_id
                WHERE cp.project_id = ? AND cp.page_id = ?
                  AND pv.page_version_id = ?
                """,
                (project_id, page_id, page_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("PAGE_VERSION_NOT_FOUND", "没有找到该页面版本。", 404)
        workspace = Path(str(row["workspace_path"])).resolve()
        path = (workspace / str(row["rendered_relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError("PAGE_RENDER_FILE_MISSING", "页面渲染文件缺失或路径无效。", 409)
        return path

    def _create_version(
        self,
        project_id: str,
        page_id: str,
        document: PageDocument,
        *,
        expected_revision: int,
        initial: bool,
    ) -> dict[str, Any]:
        root, current = self._version_context(project_id, page_id)
        if int(root["revision"]) != expected_revision:
            raise ApplicationError(
                "PAGE_REVISION_CONFLICT", "页面已被修改，请刷新后重试。", 409
            )
        if document.page_id != page_id or document.page_number != int(root["page_number"]):
            raise ApplicationError(
                "PAGE_DOCUMENT_ID_MISMATCH", "页面文档与目标页不匹配。", 422
            )
        if initial and current is not None:
            return self.get_current(project_id, page_id)
        if not initial and current is None:
            raise ApplicationError("PAGE_VERSION_NOT_FOUND", "请先建立初始页面。", 409)
        if current is not None and document.storyboard_version_id != str(
            current["storyboard_version_id"]
        ):
            raise ApplicationError(
                "PAGE_STORYBOARD_VERSION_MISMATCH",
                "页面不能静默切换到另一个分镜版本。",
                409,
            )
        if current is not None:
            current_document = PageDocument.model_validate_json(
                str(current["document_json"])
            )
            if [panel.panel_id for panel in document.panels] != [
                panel.panel_id for panel in current_document.panels
            ]:
                raise ApplicationError(
                    "PAGE_PANEL_SET_MISMATCH",
                    "布局编辑不能删除、新增或重排分镜面板。",
                    422,
                )
        asset_paths = self._validate_assets(project_id, document)
        serialized = canonical_json(document.model_dump(mode="json"))
        document_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if current is not None and document_sha256 == str(current["document_sha256"]):
            return self.get_current(project_id, page_id)
        try:
            rendered = self.renderer.render(document, asset_paths)
        except PageRenderError as exc:
            raise ApplicationError(
                "PAGE_RENDER_INVALID", f"页面无法确定性渲染: {exc}", 422
            ) from exc
        version = int(current["version"]) + 1 if current is not None else 1
        page_version_id = str(uuid7())
        relative_path = self._persist_render_file(
            Path(str(root["workspace_path"])),
            page_id,
            page_version_id,
            version,
            rendered,
        )
        parent_id = str(current["page_version_id"]) if current is not None else None
        try:
            with self.database.writer() as connection:
                live = connection.execute(
                    "SELECT revision FROM comic_pages WHERE page_id = ?", (page_id,)
                ).fetchone()
                if live is None or int(live["revision"]) != expected_revision:
                    raise ApplicationError(
                        "PAGE_REVISION_CONFLICT", "页面已被修改，请刷新后重试。", 409
                    )
                connection.execute(
                    "UPDATE page_versions SET is_current = 0 WHERE page_id = ?",
                    (page_id,),
                )
                connection.execute(
                    """
                    INSERT INTO page_versions(
                        page_version_id, page_id, version, parent_page_version_id,
                        storyboard_version_id, schema_version, document_json,
                        document_sha256, rendered_relative_path, render_sha256,
                        renderer_version, font_sha256, is_current
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        page_version_id,
                        page_id,
                        version,
                        parent_id,
                        document.storyboard_version_id,
                        document.schema_version,
                        serialized,
                        document_sha256,
                        relative_path,
                        rendered.sha256,
                        rendered.renderer_version,
                        rendered.font_sha256,
                    ),
                )
                connection.execute(
                    """
                    UPDATE comic_pages
                    SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE page_id = ?
                    """,
                    (page_id,),
                )
                connection.execute(
                    """
                    INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
                    VALUES (?, ?, 'page.version_created', ?)
                    """,
                    (
                        str(uuid7()),
                        project_id,
                        canonical_json(
                            {
                                "page_id": page_id,
                                "page_version_id": page_version_id,
                                "version": version,
                                "parent_page_version_id": parent_id,
                                "document_sha256": document_sha256,
                                "render_sha256": rendered.sha256,
                                "external_requests_started": 0,
                            }
                        ),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ApplicationError(
                "PAGE_VERSION_CONFLICT", "页面版本登记冲突，请刷新后重试。", 409
            ) from exc
        return self.get_current(project_id, page_id)

    def _insert_page_root(
        self, project_id: str, chapter_id: str, page: PageCandidate
    ) -> None:
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO comic_pages(
                    page_id, project_id, chapter_id, page_number
                ) VALUES (?, ?, ?, ?)
                """,
                (str(page.page_id), project_id, chapter_id, page.page_number),
            )

    def _version_context(
        self, project_id: str, page_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row | None]:
        with self.database.reader() as connection:
            root = connection.execute(
                """
                SELECT cp.*, p.workspace_path FROM comic_pages cp
                JOIN projects p ON p.project_id = cp.project_id
                WHERE cp.project_id = ? AND cp.page_id = ?
                """,
                (project_id, page_id),
            ).fetchone()
            current = connection.execute(
                "SELECT * FROM page_versions WHERE page_id = ? AND is_current = 1",
                (page_id,),
            ).fetchone()
        if root is None:
            raise ApplicationError("PAGE_NOT_FOUND", "没有找到该页面。", 404)
        return cast(sqlite3.Row, root), cast(sqlite3.Row | None, current)

    def _validate_assets(self, project_id: str, document: PageDocument) -> dict[str, Path]:
        result: dict[str, Path] = {}
        with self.database.reader() as connection:
            for placement in document.panels:
                row = connection.execute(
                    """
                    SELECT av.original_relative_path, p.workspace_path
                    FROM asset_versions av
                    JOIN projects p ON p.project_id = av.project_id
                    WHERE av.project_id = ? AND av.asset_version_id = ?
                      AND av.panel_id = ? AND av.status = 'ready'
                    """,
                    (project_id, placement.asset_version_id, placement.panel_id),
                ).fetchone()
                if row is None:
                    raise ApplicationError(
                        "PAGE_ASSET_VERSION_INVALID",
                        "页面引用了不存在或不匹配的面板素材。",
                        422,
                    )
                workspace = Path(str(row["workspace_path"])).resolve()
                path = (workspace / str(row["original_relative_path"])).resolve()
                if not path.is_relative_to(workspace) or not path.is_file():
                    raise ApplicationError(
                        "PAGE_ASSET_FILE_MISSING", "页面引用的面板文件缺失。", 409
                    )
                result[placement.asset_version_id] = path
        return result

    def _persist_render_file(
        self,
        workspace: Path,
        page_id: str,
        page_version_id: str,
        version: int,
        rendered: RenderedPage,
    ) -> str:
        workspace = workspace.resolve()
        relative_directory = (
            Path("pages")
            / page_id
            / "versions"
            / f"{version:04d}-{page_version_id}"
        )
        final_directory = (workspace / relative_directory).resolve()
        staging_directory = (workspace / "pages" / ".staging" / page_version_id).resolve()
        if not final_directory.is_relative_to(workspace) or not staging_directory.is_relative_to(
            workspace
        ):
            raise ApplicationError("PAGE_RENDER_PATH_INVALID", "页面渲染路径不安全。", 500)
        staging_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_synced(staging_directory / "page.png", rendered.png_bytes)
        final_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(staging_directory, final_directory)
        fsync_directory(final_directory.parent)
        return str(relative_directory / "page.png")

    def _approved_storyboard(
        self, project_id: str, chapter_id: str
    ) -> tuple[str, StoryboardDocument]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sv.storyboard_version_id, sv.document_json
                FROM storyboards s
                JOIN storyboard_versions sv ON sv.storyboard_id = s.storyboard_id
                JOIN storyboard_approvals sa
                  ON sa.storyboard_version_id = sv.storyboard_version_id
                WHERE s.project_id = ? AND s.chapter_id = ? AND sv.is_current = 1
                """,
                (project_id, chapter_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "PAGE_STORYBOARD_NOT_APPROVED", "请先审批当前分镜。", 409
            )
        return str(row["storyboard_version_id"]), StoryboardDocument.model_validate_json(
            str(row["document_json"])
        )

    def _current_assets_for_page(
        self, project_id: str, page: PageCandidate
    ) -> dict[str, dict[str, str]]:
        panel_ids = [str(panel.panel_id) for panel in page.panels]
        placeholders = ",".join("?" for _ in panel_ids)
        with self.database.reader() as connection:
            rows = connection.execute(
                f"""
                SELECT panel_id, asset_version_id FROM asset_versions
                WHERE project_id = ? AND is_current = 1 AND status = 'ready'
                  AND panel_id IN ({placeholders})
                """,
                (project_id, *panel_ids),
            ).fetchall()
        assets = {
            str(row["panel_id"]): {"asset_version_id": str(row["asset_version_id"])}
            for row in rows
        }
        missing = [panel_id for panel_id in panel_ids if panel_id not in assets]
        if missing:
            raise ApplicationError(
                "PAGE_ASSETS_INCOMPLETE",
                "当前页还有面板没有可用素材，无法建立正式页面。",
                409,
                {"missing_panel_ids": missing},
            )
        return assets

    def _optional_current(self, project_id: str, page_id: str) -> dict[str, Any] | None:
        try:
            return self.get_current(project_id, page_id)
        except ApplicationError as exc:
            if exc.code == "PAGE_VERSION_NOT_FOUND":
                return None
            raise

    def _require_project(self, project_id: str) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError("PROJECT_NOT_FOUND", "没有找到该项目。", 404)

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        try:
            document = PageDocument.model_validate_json(str(row["document_json"]))
        except ValidationError as exc:
            raise ApplicationError(
                "PAGE_DOCUMENT_CORRUPT", "已保存的页面文档无法校验。", 500
            ) from exc
        return {
            "page_id": str(row["page_id"]),
            "project_id": str(row["project_id"]),
            "chapter_id": str(row["chapter_id"]),
            "page_number": int(row["page_number"]),
            "page_revision": int(row["page_revision"]),
            "page_version_id": str(row["page_version_id"]),
            "version": int(row["version"]),
            "parent_page_version_id": row["parent_page_version_id"],
            "storyboard_version_id": str(row["storyboard_version_id"]),
            "document_sha256": str(row["document_sha256"]),
            "render_sha256": str(row["render_sha256"]),
            "renderer_version": str(row["renderer_version"]),
            "font_sha256": str(row["font_sha256"]),
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
            "document": document.model_dump(mode="json"),
            "external_requests_started": 0,
        }


def default_text_layers(page: PageCandidate, page_template: PageTemplate) -> list[TextLayer]:
    layers: list[TextLayer] = []
    for index, panel in enumerate(page.panels):
        frame = page_template.frames[index]
        panel_id = str(panel.panel_id)
        narration = "\n".join(panel.narration)
        dialogue = "\n".join(f"{line.speaker}: {line.text}" for line in panel.dialogue)
        sfx = "\n".join(panel.sfx)
        cursor = frame.y + 28
        if narration:
            bounds = layer_bounds(frame, cursor, preferred_width=frame.width - 56, height=190)
            layers.extend(
                chunked_layers("narration", narration, panel_id, bounds, font_size=42)
            )
            cursor += 210
        if dialogue:
            bounds = layer_bounds(
                frame,
                cursor,
                preferred_width=min(680, frame.width - 72),
                height=280,
            )
            layers.extend(
                chunked_layers("dialogue", dialogue, panel_id, bounds, font_size=42)
            )
        if sfx:
            bounds = layer_bounds(
                frame,
                frame.y + frame.height - 220,
                preferred_width=min(520, frame.width - 72),
                height=180,
                align_right=True,
            )
            layers.extend(chunked_layers("sfx", sfx, panel_id, bounds, font_size=64))
    if len(layers) > 200:
        raise ApplicationError(
            "PAGE_TEXT_LAYER_LIMIT",
            "分镜文字过多，请先精简对白、旁白或音效。",
            422,
        )
    return layers


def layer_bounds(
    frame: PixelRect,
    y: int,
    *,
    preferred_width: int,
    height: int,
    align_right: bool = False,
) -> PixelRect:
    width = max(160, min(preferred_width, frame.width - 32))
    height = max(96, min(height, frame.height - 32))
    x = frame.x + frame.width - width - 24 if align_right else frame.x + 24
    y = max(frame.y + 16, min(y, frame.y + frame.height - height - 16))
    return PixelRect(x=x, y=y, width=width, height=height)


def chunked_layers(
    kind: Literal["dialogue", "narration", "sfx"],
    text: str,
    panel_id: str,
    bounds: PixelRect,
    *,
    font_size: int,
) -> list[TextLayer]:
    chunks = text_chunks(text, 70 if kind != "sfx" else 24)
    return [
        TextLayer(
            layer_id=str(uuid7()),
            panel_id=panel_id,
            kind=kind,
            text=chunk,
            bounds=PixelRect(
                x=bounds.x,
                y=min(
                    bounds.y + offset * (bounds.height + 16),
                    3072 - bounds.height,
                ),
                width=bounds.width,
                height=bounds.height,
            ),
            font_size=font_size,
        )
        for offset, chunk in enumerate(chunks)
    ]


def text_chunks(text: str, maximum: int) -> list[str]:
    normalized = text.strip()
    return [normalized[index : index + maximum] for index in range(0, len(normalized), maximum)]
