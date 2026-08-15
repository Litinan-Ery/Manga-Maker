from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from PIL import Image, UnidentifiedImageError

from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..modules.production.adapters.novelai import map_prompt_plan_to_novelai
from ..modules.production.errors import ProviderMappingError
from ..modules.prompting.public import PromptPlan
from ..pages.models import PageDocument
from ..pages.service import PageService
from ..shared_kernel import canonical_sha256
from .assets import canonical_json, fsync_directory, write_synced
from .queue import (
    STANDARD_COST_BASIS,
    GenerationPlan,
    GenerationQueueService,
    _redacted_provider_payload,
)

RevisionOperation = Literal["panel_reroll", "page_reroll", "inpaint"]
MAX_MASK_BYTES = 8 * 1024 * 1024
MAX_MASK_PIXELS = 4_000_000


def _base64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class RevisionTarget:
    ordinal: int
    page_id: str
    page_number: int
    panel_id: str
    panel_order: int
    parent_asset_version_id: str
    mask_asset_id: str | None
    edit_prompt: str | None
    inpaint_strength: float | None
    cost_ceiling_anlas: int
    layout_panel: dict[str, Any]
    prompt_package_id: str
    prompt_package_sha256: str
    prompt_plan_id: str
    prompt_plan_version: int
    prompt_plan_sha256: str
    prompt_plan: dict[str, Any]
    character_tag_set_refs: tuple[dict[str, Any], ...]
    provider_execution_spec_id: str
    provider_execution_spec: dict[str, Any]
    provider_execution_spec_sha256: str
    provider_payload_sha256: str
    provider_payload: dict[str, Any]
    provider_seed: int
    candidate_count: int
    reference_use: dict[str, Any] | None

    def payload(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "page_id": self.page_id,
            "page_number": self.page_number,
            "panel_id": self.panel_id,
            "panel_order": self.panel_order,
            "parent_asset_version_id": self.parent_asset_version_id,
            "mask_asset_id": self.mask_asset_id,
            "edit_prompt": self.edit_prompt,
            "inpaint_strength": self.inpaint_strength,
            "cost_ceiling_anlas": self.cost_ceiling_anlas,
            "layout_panel": self.layout_panel,
            "prompt_package_id": self.prompt_package_id,
            "prompt_package_sha256": self.prompt_package_sha256,
            "prompt_plan_id": self.prompt_plan_id,
            "prompt_plan_version": self.prompt_plan_version,
            "prompt_plan_sha256": self.prompt_plan_sha256,
            "prompt_plan": self.prompt_plan,
            "character_tag_set_refs": list(self.character_tag_set_refs),
            "provider_execution_spec_id": self.provider_execution_spec_id,
            "provider_execution_spec": self.provider_execution_spec,
            "provider_execution_spec_sha256": self.provider_execution_spec_sha256,
            "provider_payload_sha256": self.provider_payload_sha256,
            "provider_payload": _redacted_provider_payload(self.provider_payload),
            "provider_seed": self.provider_seed,
            "candidate_count": self.candidate_count,
            "reference_use": self.reference_use,
        }

    def freeze_payload(self) -> dict[str, Any]:
        payload = self.payload()
        payload["provider_payload"] = self.provider_payload
        return payload


@dataclass(frozen=True, slots=True)
class RevisionPlan:
    operation: RevisionOperation
    base: GenerationPlan
    page_id: str
    page_version_id: str
    page_number: int
    provider_model_id: str
    targets: tuple[RevisionTarget, ...]
    fingerprint: str

    @property
    def estimated_cost_upper_anlas(self) -> int:
        return sum(target.cost_ceiling_anlas for target in self.targets)

    def payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "project_id": self.base.project_id,
            "chapter_id": self.base.chapter_id,
            "page_id": self.page_id,
            "page_version_id": self.page_version_id,
            "page_number": self.page_number,
            "storyboard_version_id": self.base.storyboard_version_id,
            "character_bible_version_id": self.base.character_bible_version_id,
            "style_bible_version_id": self.base.style_bible_version_id,
            "provider_model_id": self.provider_model_id,
            "mapping_version": self.base.mapping_version,
            "contract_sha256": self.base.contract_sha256,
            "panel_count": len(self.targets),
            "estimated_calls": len(self.targets),
            "estimated_cost_upper_anlas": self.estimated_cost_upper_anlas,
            "cost_basis": "user_confirmed_per_panel_ceiling",
            "cost_notice": "这是用户确认的保守预留，不是供应商实际扣费。",
            "plan_fingerprint": self.fingerprint,
            "targets": [target.payload() for target in self.targets],
            "external_request_created": False,
        }


class RevisionService:
    def __init__(
        self,
        database: Database,
        queue: GenerationQueueService,
        pages: PageService,
    ) -> None:
        self.database = database
        self.queue = queue
        self.pages = pages

    def create_mask(
        self,
        project_id: str,
        panel_id: str,
        parent_asset_version_id: str,
        raw: bytes,
    ) -> dict[str, Any]:
        if not raw or len(raw) > MAX_MASK_BYTES:
            raise ApplicationError(
                "MASK_FILE_SIZE_INVALID", "PNG 蒙版必须大于 0 字节且不超过 8 MB。", 422
            )
        parent = self._asset_row(project_id, panel_id, parent_asset_version_id)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as source:
                    if source.format != "PNG":
                        raise ApplicationError(
                            "MASK_FORMAT_INVALID", "P0 蒙版必须是 PNG 文件。", 415
                        )
                    width, height = source.size
                    if width <= 0 or height <= 0 or width * height > MAX_MASK_PIXELS:
                        raise ApplicationError(
                            "MASK_DIMENSIONS_INVALID", "蒙版像素总量超出本地安全上限。", 422
                        )
                    if (width, height) != (int(parent["width"]), int(parent["height"])):
                        raise ApplicationError(
                            "MASK_DIMENSIONS_MISMATCH", "蒙版尺寸必须与父素材完全一致。", 422
                        )
                    source.load()
                    normalized = source.convert("L").point(
                        lambda value: 255 if value >= 128 else 0
                    )
        except ApplicationError:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as exc:
            raise ApplicationError("MASK_DECODE_FAILED", "蒙版无法安全解码。", 422) from exc

        histogram = normalized.histogram()
        selected_pixels = int(histogram[255])
        total_pixels = width * height
        if selected_pixels == 0:
            raise ApplicationError("MASK_EMPTY", "蒙版没有选中任何需要重绘的区域。", 422)
        if selected_pixels == total_pixels:
            raise ApplicationError(
                "MASK_SELECTS_ENTIRE_IMAGE", "局部重绘蒙版不能选择整张图片。", 422
            )
        output = BytesIO()
        normalized.save(output, format="PNG", optimize=False, compress_level=9)
        payload = output.getvalue()
        sha256 = hashlib.sha256(payload).hexdigest()
        existing = self._optional_mask_by_hash(
            project_id, parent_asset_version_id, sha256
        )
        if existing is not None:
            return self._mask_payload(existing)

        mask_asset_id = str(uuid7())
        workspace = Path(str(parent["workspace_path"])).resolve()
        relative_directory = Path("assets") / "masks" / mask_asset_id
        final_directory = (workspace / relative_directory).resolve()
        staging_directory = (workspace / "assets" / ".staging" / mask_asset_id).resolve()
        if not final_directory.is_relative_to(workspace) or not staging_directory.is_relative_to(
            workspace
        ):
            raise ApplicationError("MASK_PATH_INVALID", "蒙版目标路径不安全。", 500)
        staging_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_synced(staging_directory / "mask.png", payload)
        final_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(staging_directory, final_directory)
        fsync_directory(final_directory.parent)
        relative_path = str(relative_directory / "mask.png")
        try:
            with self.database.writer() as connection:
                connection.execute(
                    """
                    INSERT INTO mask_assets(
                        mask_asset_id, project_id, panel_id,
                        parent_asset_version_id, relative_path, sha256,
                        width, height, selected_pixel_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mask_asset_id,
                        project_id,
                        panel_id,
                        parent_asset_version_id,
                        relative_path,
                        sha256,
                        width,
                        height,
                        selected_pixels,
                    ),
                )
                self._audit(
                    connection,
                    project_id,
                    "generation.mask_created",
                    {
                        "mask_asset_id": mask_asset_id,
                        "panel_id": panel_id,
                        "parent_asset_version_id": parent_asset_version_id,
                        "sha256": sha256,
                        "selected_pixel_count": selected_pixels,
                        "external_requests_started": 0,
                    },
                )
        except sqlite3.IntegrityError as exc:
            raise ApplicationError("MASK_VERSION_CONFLICT", "蒙版登记冲突。", 409) from exc
        return self.get_mask(project_id, mask_asset_id)

    def get_mask(self, project_id: str, mask_asset_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM mask_assets WHERE project_id = ? AND mask_asset_id = ?",
                (project_id, mask_asset_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("MASK_NOT_FOUND", "没有找到该蒙版。", 404)
        return self._mask_payload(row)

    def mask_content_path(self, project_id: str, mask_asset_id: str) -> Path:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT ma.relative_path, p.workspace_path
                FROM mask_assets ma
                JOIN projects p ON p.project_id = ma.project_id
                WHERE ma.project_id = ? AND ma.mask_asset_id = ?
                """,
                (project_id, mask_asset_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("MASK_NOT_FOUND", "没有找到该蒙版。", 404)
        workspace = Path(str(row["workspace_path"])).resolve()
        path = (workspace / str(row["relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError("MASK_FILE_MISSING", "蒙版文件缺失或路径无效。", 409)
        return path

    def estimate(
        self,
        project_id: str,
        operation: RevisionOperation,
        page_id: str,
        *,
        panel_id: str | None,
        mask_asset_id: str | None,
        edit_prompt: str | None,
        inpaint_strength: float | None,
        per_panel_cost_ceiling_anlas: int,
    ) -> dict[str, Any]:
        return self._build_plan(
            project_id,
            operation,
            page_id,
            panel_id=panel_id,
            mask_asset_id=mask_asset_id,
            edit_prompt=edit_prompt,
            inpaint_strength=inpaint_strength,
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        ).payload()

    def create_job(
        self,
        project_id: str,
        operation: RevisionOperation,
        page_id: str,
        *,
        panel_id: str | None,
        mask_asset_id: str | None,
        edit_prompt: str | None,
        inpaint_strength: float | None,
        per_panel_cost_ceiling_anlas: int,
        plan_fingerprint: str,
        max_calls: int,
        max_cost_anlas: int,
        confirmed: bool,
        idempotency_key: str,
        request_sha256: str,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ApplicationError(
                "GENERATION_APPROVAL_REQUIRED", "请确认目标、调用上限和成本预留。", 422
            )
        plan = self._build_plan(
            project_id,
            operation,
            page_id,
            panel_id=panel_id,
            mask_asset_id=mask_asset_id,
            edit_prompt=edit_prompt,
            inpaint_strength=inpaint_strength,
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        )
        if plan.fingerprint != plan_fingerprint:
            raise ApplicationError(
                "GENERATION_PLAN_STALE", "页面、素材、蒙版或配置已变化，请重新预检。", 409
            )
        panel_count = len(plan.targets)
        if max_calls < panel_count or max_calls > panel_count * 3:
            raise ApplicationError(
                "GENERATION_CALL_LIMIT_INVALID", "调用上限必须覆盖目标且不超过三倍。", 422
            )
        if max_cost_anlas < plan.estimated_cost_upper_anlas or max_cost_anlas > 10_000_000:
            raise ApplicationError(
                "GENERATION_COST_LIMIT_INVALID", "成本上限未覆盖预留或超出安全范围。", 422
            )
        with self.database.reader() as connection:
            existing = connection.execute(
                """
                SELECT ga.request_sha256, ga.plan_fingerprint, gj.job_id
                FROM generation_approvals ga
                LEFT JOIN generation_jobs gj
                  ON gj.generation_approval_id = ga.generation_approval_id
                WHERE ga.project_id = ? AND ga.idempotency_key = ?
                """,
                (project_id, idempotency_key),
            ).fetchone()
        if existing is not None:
            if (
                str(existing["request_sha256"]) != request_sha256
                or str(existing["plan_fingerprint"]) != plan.fingerprint
            ):
                raise ApplicationError(
                    "GENERATION_APPROVAL_IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key 已绑定到另一份修订批准。",
                    409,
                )
            if existing["job_id"] is None:
                raise ApplicationError(
                    "GENERATION_APPROVAL_INCOMPLETE",
                    "修订批准已存在但 Job 尚未完整创建，请进行恢复。",
                    409,
                )
            return self.queue.get_job(project_id, str(existing["job_id"]))
        job_id = str(uuid7())
        user_action_id = str(uuid7())
        generation_approval_id = str(uuid7())
        approval_snapshot = {
            "schema_version": "1.0",
            "project_id": project_id,
            "chapter_id": plan.base.chapter_id,
            "operation": operation,
            "page_id": page_id,
            "page_version_id": plan.page_version_id,
            "plan_fingerprint": plan.fingerprint,
            "prompt_approval_hash": plan.base.prompt_approval_hash,
            "prompt_snapshot_sha256": plan.base.prompt_snapshot_sha256,
            "layout_snapshot_sha256": plan.base.layout_snapshot_sha256,
            "character_tag_bundle_version_id": plan.base.character_tag_bundle_version_id,
            "provider_model_id": plan.provider_model_id,
            "mapping_version": plan.base.mapping_version,
            "contract_sha256": plan.base.contract_sha256,
            "candidate_count_per_panel": 1,
            "quality_rule_version": plan.base.quality_rule_version,
            "billing_mode": "standard",
            "cost_basis": STANDARD_COST_BASIS,
            "max_calls": max_calls,
            "max_cost_anlas": max_cost_anlas,
            "targets": [target.freeze_payload() for target in plan.targets],
        }
        approval_sha256 = canonical_sha256(approval_snapshot)
        try:
            with self.database.writer() as connection:
                connection.execute(
                    """
                    INSERT INTO generation_approvals(
                        generation_approval_id, project_id, chapter_id,
                        plan_fingerprint, approval_sha256, snapshot_json,
                        idempotency_key, request_sha256, candidate_count_per_panel,
                        quality_rule_version, user_action_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        generation_approval_id,
                        project_id,
                        plan.base.chapter_id,
                        plan.fingerprint,
                        approval_sha256,
                        canonical_json(approval_snapshot),
                        idempotency_key,
                        request_sha256,
                        plan.base.quality_rule_version,
                        user_action_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO generation_jobs(
                        job_id, project_id, chapter_id, storyboard_version_id,
                        character_bible_version_id, style_bible_version_id,
                        character_tag_bundle_version_id, prompt_bundle_version_id,
                        text_model_config_revision,
                        novelai_config_revision, provider_model_id, mapping_version,
                        contract_sha256, credential_profile_id, timeout_seconds,
                        layout_snapshot_sha256, plan_fingerprint, status,
                        user_action_id, page_count,
                        panel_count, max_calls, max_cost_anlas,
                        estimated_cost_upper_anlas, cost_basis, operation_kind,
                        target_page_id, target_page_version_id,
                        generation_approval_id, generation_approval_sha256,
                        prompt_approval_hash, prompt_snapshot_sha256,
                        candidate_count_per_panel, quality_rule_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, 1,
                              ?, ?, ?, ?, 'user_confirmed_per_panel_ceiling', ?, ?, ?,
                              ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        job_id,
                        project_id,
                        plan.base.chapter_id,
                        plan.base.storyboard_version_id,
                        plan.base.character_bible_version_id,
                        plan.base.style_bible_version_id,
                        plan.base.character_tag_bundle_version_id,
                        plan.base.prompt_bundle_version_id,
                        plan.base.text_model_config_revision,
                        plan.base.novelai_config_revision,
                        plan.provider_model_id,
                        plan.base.mapping_version,
                        plan.base.contract_sha256,
                        plan.base.credential_profile_id,
                        plan.base.timeout_seconds,
                        plan.base.layout_snapshot_sha256,
                        plan.fingerprint,
                        user_action_id,
                        panel_count,
                        max_calls,
                        max_cost_anlas,
                        plan.estimated_cost_upper_anlas,
                        operation,
                        page_id,
                        plan.page_version_id,
                        generation_approval_id,
                        approval_sha256,
                        plan.base.prompt_approval_hash,
                        plan.base.prompt_snapshot_sha256,
                        plan.base.quality_rule_version,
                    ),
                )
                for target in plan.targets:
                    connection.execute(
                        """
                        INSERT INTO generation_job_items(
                            item_id, job_id, ordinal, page_id, page_number,
                            panel_id, status, cost_ceiling_anlas, operation_kind,
                            parent_asset_version_id, mask_asset_id, edit_prompt,
                            inpaint_strength, page_layout_draft_id,
                            page_layout_draft_version_id, layout_version,
                            layout_content_sha256, layout_approval_id,
                            layout_approval_sha256, frame_id, frame_content_sha256,
                            dimension_selection_id, dimension_selection_sha256,
                            selected_width, selected_height, expected_crop_ratio,
                            dimension_rule_version, capability_snapshot_sha256,
                            prompt_plan_id, prompt_plan_version, prompt_plan_sha256,
                            prompt_plan_json, prompt_package_sha256,
                            character_tag_set_refs_json,
                            provider_execution_spec_id, provider_execution_spec_json,
                            provider_execution_spec_sha256,
                            provider_payload_sha256, provider_payload_json,
                            provider_seed, candidate_count, reference_use_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'queued',
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            str(uuid7()),
                            job_id,
                            target.ordinal,
                            target.page_id,
                            target.page_number,
                            target.panel_id,
                            target.cost_ceiling_anlas,
                            operation,
                            target.parent_asset_version_id,
                            target.mask_asset_id,
                            target.edit_prompt,
                            target.inpaint_strength,
                            target.layout_panel["page_layout_draft_id"],
                            target.layout_panel["page_layout_draft_version_id"],
                            target.layout_panel["layout_version"],
                            target.layout_panel["layout_content_sha256"],
                            target.layout_panel["layout_approval_id"],
                            target.layout_panel["layout_approval_sha256"],
                            target.layout_panel["frame_id"],
                            target.layout_panel["frame_content_sha256"],
                            target.layout_panel["dimension_selection_id"],
                            target.layout_panel["dimension_selection_sha256"],
                            target.layout_panel["selected_width"],
                            target.layout_panel["selected_height"],
                            target.layout_panel["expected_crop_ratio"],
                            target.layout_panel["dimension_rule_version"],
                            target.layout_panel["capability_snapshot_sha256"],
                            target.prompt_plan_id,
                            target.prompt_plan_version,
                            target.prompt_plan_sha256,
                            canonical_json(target.prompt_plan),
                            target.prompt_package_sha256,
                            canonical_json(list(target.character_tag_set_refs)),
                            target.provider_execution_spec_id,
                            canonical_json(target.provider_execution_spec),
                            target.provider_execution_spec_sha256,
                            target.provider_payload_sha256,
                            canonical_json(target.provider_payload),
                            target.provider_seed,
                            target.candidate_count,
                            (
                                canonical_json(target.reference_use)
                                if target.reference_use is not None
                                else None
                            ),
                        ),
                    )
                self._audit(
                    connection,
                    project_id,
                    "generation.revision_job_created",
                    {
                        "job_id": job_id,
                        "operation": operation,
                        "page_id": page_id,
                        "page_version_id": plan.page_version_id,
                        "panel_count": panel_count,
                        "max_calls": max_calls,
                        "max_cost_anlas": max_cost_anlas,
                        "external_request_created": False,
                    },
                )
        except sqlite3.IntegrityError as exc:
            raise ApplicationError(
                "GENERATION_JOB_ALREADY_ACTIVE",
                "该项目已有未结束的生成队列，请先完成、取消或审阅。",
                409,
            ) from exc
        return self.queue.get_job(project_id, job_id)

    def finalize_if_completed(self, job_id: str) -> dict[str, Any] | None:
        with self.database.reader() as connection:
            job = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise ApplicationError("GENERATION_JOB_NOT_FOUND", "没有找到该生成队列。", 404)
            if str(job["operation_kind"]) == "chapter_generate":
                return None
            if job["result_page_version_id"] is not None:
                return self.pages.get_version(
                    str(job["project_id"]),
                    str(job["target_page_id"]),
                    str(job["result_page_version_id"]),
                )
            if str(job["status"]) != "completed":
                return None
            items = connection.execute(
                """
                SELECT panel_id, asset_version_id FROM generation_job_items
                WHERE job_id = ? ORDER BY ordinal
                """,
                (job_id,),
            ).fetchall()
        if not items or any(item["asset_version_id"] is None for item in items):
            raise ApplicationError(
                "REVISION_RESULTS_INCOMPLETE", "生成队列已结束但素材结果不完整。", 409
            )
        replacements = {
            str(item["panel_id"]): str(item["asset_version_id"]) for item in items
        }
        page = self.pages.create_generated_revision(
            str(job["project_id"]),
            str(job["target_page_id"]),
            str(job["target_page_version_id"]),
            replacements,
            source_job_id=job_id,
        )
        with self.database.writer() as connection:
            connection.execute(
                """
                UPDATE generation_jobs SET result_page_version_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND result_page_version_id IS NULL
                """,
                (page["page_version_id"], job_id),
            )
            self._audit(
                connection,
                str(job["project_id"]),
                "generation.revision_page_finalized",
                {
                    "job_id": job_id,
                    "page_version_id": page["page_version_id"],
                    "external_requests_started": int(job["calls_started"]),
                },
            )
        return page

    def _build_plan(
        self,
        project_id: str,
        operation: RevisionOperation,
        page_id: str,
        *,
        panel_id: str | None,
        mask_asset_id: str | None,
        edit_prompt: str | None,
        inpaint_strength: float | None,
        per_panel_cost_ceiling_anlas: int,
    ) -> RevisionPlan:
        page = self.pages.get_current(project_id, page_id)
        document = PageDocument.model_validate(page["document"])
        base = self.queue.build_plan(
            project_id,
            str(page["chapter_id"]),
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        )
        if document.storyboard_version_id != base.storyboard_version_id:
            raise ApplicationError(
                "REVISION_PAGE_STALE", "页面未绑定当前已审批分镜，请先确认版本。", 409
            )
        base_panels = {panel.panel_id: panel for panel in base.panels}
        placements = list(document.panels)
        if operation != "page_reroll":
            if not panel_id:
                raise ApplicationError("REVISION_PANEL_REQUIRED", "请选择目标面板。", 422)
            placements = [placement for placement in placements if placement.panel_id == panel_id]
            if not placements:
                raise ApplicationError("REVISION_PANEL_NOT_FOUND", "当前页没有该面板。", 404)
        if operation == "inpaint":
            normalized_edit = " ".join((edit_prompt or "").split()).strip()
            if not normalized_edit or len(normalized_edit) > 2_000:
                raise ApplicationError(
                    "INPAINT_PROMPT_INVALID", "局部修改说明必须为 1 到 2000 个字符。", 422
                )
            if inpaint_strength is None or not 0.1 <= inpaint_strength <= 1:
                raise ApplicationError(
                    "INPAINT_STRENGTH_INVALID", "局部重绘强度必须在 0.1 到 1 之间。", 422
                )
        else:
            normalized_edit = None
            if mask_asset_id is not None or edit_prompt is not None or inpaint_strength is not None:
                raise ApplicationError(
                    "REVISION_INPUTS_INVALID", "reroll 不接受蒙版或局部修改参数。", 422
                )
        targets: list[RevisionTarget] = []
        for ordinal, placement in enumerate(placements, start=1):
            base_panel = base_panels.get(placement.panel_id)
            if base_panel is None:
                raise ApplicationError(
                    "REVISION_PANEL_STALE", "目标面板不在当前生成计划中。", 409
                )
            parent_asset = self._asset_row(
                project_id, placement.panel_id, placement.asset_version_id
            )
            selected_mask: str | None = None
            if operation == "inpaint":
                if not mask_asset_id:
                    raise ApplicationError("MASK_REQUIRED", "局部重绘需要不可变蒙版。", 422)
                mask = self._mask_row(project_id, mask_asset_id)
                if (
                    str(mask["panel_id"]) != placement.panel_id
                    or str(mask["parent_asset_version_id"]) != placement.asset_version_id
                ):
                    raise ApplicationError(
                        "MASK_PARENT_MISMATCH", "蒙版没有绑定当前页所用的父素材。", 409
                    )
                selected_mask = mask_asset_id
            prompt_plan = PromptPlan.model_validate(base_panel.prompt_plan)
            provider_seed = self._revision_seed(
                base_panel.provider_seed,
                operation,
                placement.asset_version_id,
                selected_mask,
                normalized_edit,
                inpaint_strength,
            )
            provider_spec_id = uuid5(
                NAMESPACE_URL,
                "manga-maker:revision-provider-preview:"
                f"{operation}:{page['page_version_id']}:{placement.panel_id}:"
                f"{provider_seed}:{selected_mask or ''}:{normalized_edit or ''}",
            )
            generation_preview_id = uuid5(
                NAMESPACE_URL,
                "manga-maker:revision-generation-preview:"
                f"{operation}:{page['page_version_id']}:{placement.panel_id}:"
                f"{provider_seed}:{selected_mask or ''}:{normalized_edit or ''}",
            )
            source_image_base64: str | None = None
            mask_base64: str | None = None
            if operation == "inpaint":
                assert selected_mask is not None
                parent_raw = self._verified_asset_bytes(parent_asset)
                mask_row = self._mask_row(project_id, selected_mask)
                mask_raw = self._verified_mask_bytes(parent_asset, mask_row)
                source_image_base64 = _base64(parent_raw)
                mask_base64 = _base64(mask_raw)
            try:
                mapped = map_prompt_plan_to_novelai(
                    prompt_plan=prompt_plan,
                    generation_spec_id=generation_preview_id,
                    provider_execution_spec_id=provider_spec_id,
                    seed_material=str(provider_spec_id),
                    model_id=(
                        base.inpaint_model_id
                        if operation == "inpaint"
                        else base.provider_model_id
                    ),
                    contract_sha256=base.contract_sha256,
                    capability_snapshot_sha256=base_panel.capability_snapshot_sha256,
                    page_layout_draft_sha256=base_panel.layout_content_sha256,
                    width=base_panel.selected_width,
                    height=base_panel.selected_height,
                    seed=provider_seed,
                    steps=28,
                    scale=5.0,
                    sampler="k_euler_ancestral",
                    noise_schedule="karras",
                    mapping_version=base.mapping_version,
                    action="infill" if operation == "inpaint" else "generate",
                    reference=(
                        self._reference_payload(base_panel.provider_payload)
                        if operation != "inpaint"
                        else None
                    ),
                    source_image_base64=source_image_base64,
                    mask_base64=mask_base64,
                    inpaint_strength=(
                        inpaint_strength if operation == "inpaint" else None
                    ),
                    edit_prompt=normalized_edit,
                )
            except ProviderMappingError as exc:
                raise ApplicationError(exc.code, exc.message, 409, exc.details) from exc
            targets.append(
                RevisionTarget(
                    ordinal=ordinal,
                    page_id=page_id,
                    page_number=int(page["page_number"]),
                    panel_id=placement.panel_id,
                    panel_order=base_panel.panel_order,
                    parent_asset_version_id=placement.asset_version_id,
                    mask_asset_id=selected_mask,
                    edit_prompt=normalized_edit,
                    inpaint_strength=inpaint_strength if operation == "inpaint" else None,
                    cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
                    layout_panel={
                        key: value
                        for key, value in base_panel.payload().items()
                        if key
                        in {
                            "page_layout_draft_id",
                            "page_layout_draft_version_id",
                            "layout_version",
                            "layout_content_sha256",
                            "layout_approval_id",
                            "layout_approval_sha256",
                            "frame_id",
                            "frame_content_sha256",
                            "dimension_selection_id",
                            "dimension_selection_sha256",
                            "selected_width",
                            "selected_height",
                            "expected_crop_ratio",
                            "dimension_rule_version",
                            "capability_snapshot_sha256",
                        }
                    },
                    prompt_package_id=base_panel.prompt_package_id,
                    prompt_package_sha256=base_panel.prompt_package_sha256,
                    prompt_plan_id=base_panel.prompt_plan_id,
                    prompt_plan_version=base_panel.prompt_plan_version,
                    prompt_plan_sha256=base_panel.prompt_plan_sha256,
                    prompt_plan=base_panel.prompt_plan,
                    character_tag_set_refs=base_panel.character_tag_set_refs,
                    provider_execution_spec_id=str(
                        mapped.execution_spec.provider_execution_spec_id
                    ),
                    provider_execution_spec=mapped.execution_spec.model_dump(mode="json"),
                    provider_execution_spec_sha256=hashlib.sha256(
                        canonical_json(
                            mapped.execution_spec.model_dump(mode="json")
                        ).encode()
                    ).hexdigest(),
                    provider_payload_sha256=mapped.execution_spec.payload_sha256,
                    provider_payload=mapped.payload.model_dump(
                        mode="json", exclude_none=True
                    ),
                    provider_seed=provider_seed,
                    candidate_count=1,
                    reference_use=(
                        base_panel.reference_use if operation != "inpaint" else None
                    ),
                )
            )
        provider_model_id = (
            base.inpaint_model_id if operation == "inpaint" else base.provider_model_id
        )
        stable = {
            "operation": operation,
            "base_plan_fingerprint": base.fingerprint,
            "page_id": page_id,
            "page_version_id": page["page_version_id"],
            "provider_model_id": provider_model_id,
            "targets": [target.payload() for target in targets],
        }
        fingerprint = hashlib.sha256(canonical_json(stable).encode("utf-8")).hexdigest()
        return RevisionPlan(
            operation=operation,
            base=base,
            page_id=page_id,
            page_version_id=str(page["page_version_id"]),
            page_number=int(page["page_number"]),
            provider_model_id=provider_model_id,
            targets=tuple(targets),
            fingerprint=fingerprint,
        )

    def _asset_row(
        self, project_id: str, panel_id: str, asset_version_id: str
    ) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT av.*, p.workspace_path FROM asset_versions av
                JOIN projects p ON p.project_id = av.project_id
                WHERE av.project_id = ? AND av.panel_id = ?
                  AND av.asset_version_id = ? AND av.status = 'ready'
                """,
                (project_id, panel_id, asset_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "REVISION_PARENT_ASSET_INVALID", "父素材不存在或与目标面板不匹配。", 409
            )
        return cast(sqlite3.Row, row)

    @staticmethod
    def _revision_seed(
        base_seed: int,
        operation: RevisionOperation,
        parent_asset_version_id: str,
        mask_asset_id: str | None,
        edit_prompt: str | None,
        inpaint_strength: float | None,
    ) -> int:
        material = "|".join(
            (
                str(base_seed),
                operation,
                parent_asset_version_id,
                mask_asset_id or "",
                edit_prompt or "",
                str(inpaint_strength or ""),
            )
        )
        return int(hashlib.sha256(material.encode()).hexdigest()[:8], 16) % 4_294_967_288

    @staticmethod
    def _reference_payload(provider_payload: dict[str, Any]) -> dict[str, Any] | None:
        parameters = cast(dict[str, Any], provider_payload.get("parameters", {}))
        images = parameters.get("director_reference_images")
        descriptions = parameters.get("director_reference_descriptions")
        strengths = parameters.get("director_reference_strength_values")
        fidelities = parameters.get("director_reference_secondary_strength_values")
        if not images:
            return None
        try:
            if (
                not isinstance(descriptions, list)
                or not isinstance(strengths, list)
                or not isinstance(fidelities, list)
            ):
                raise TypeError("reference arrays missing")
            return {
                "png_base64": images[0],
                "description": descriptions[0]["caption"]["base_caption"],
                "strength": strengths[0],
                "fidelity": fidelities[0],
            }
        except (IndexError, KeyError, TypeError) as exc:
            raise ApplicationError(
                "GENERATION_REFERENCE_FREEZE_INVALID",
                "冻结 Precise Reference 载荷不完整。",
                409,
            ) from exc

    @staticmethod
    def _verified_asset_bytes(row: sqlite3.Row) -> bytes:
        workspace = Path(str(row["workspace_path"])).resolve()
        path = (workspace / str(row["original_relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError(
                "REVISION_PARENT_ASSET_FILE_MISSING", "父素材文件缺失。", 409
            )
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(row["image_sha256"]):
            raise ApplicationError(
                "REVISION_PARENT_ASSET_HASH_MISMATCH", "父素材哈希不一致。", 409
            )
        return raw

    @staticmethod
    def _verified_mask_bytes(parent: sqlite3.Row, mask: sqlite3.Row) -> bytes:
        workspace = Path(str(parent["workspace_path"])).resolve()
        path = (workspace / str(mask["relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError("MASK_FILE_MISSING", "蒙版文件缺失。", 409)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(mask["sha256"]):
            raise ApplicationError("MASK_HASH_MISMATCH", "蒙版哈希不一致。", 409)
        return raw

    def _mask_row(self, project_id: str, mask_asset_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM mask_assets WHERE project_id = ? AND mask_asset_id = ?",
                (project_id, mask_asset_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("MASK_NOT_FOUND", "没有找到该蒙版。", 404)
        return cast(sqlite3.Row, row)

    def _optional_mask_by_hash(
        self, project_id: str, parent_asset_version_id: str, sha256: str
    ) -> sqlite3.Row | None:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM mask_assets WHERE project_id = ?
                  AND parent_asset_version_id = ? AND sha256 = ?
                """,
                (project_id, parent_asset_version_id, sha256),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    @staticmethod
    def _mask_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "mask_asset_id": str(row["mask_asset_id"]),
            "project_id": str(row["project_id"]),
            "panel_id": str(row["panel_id"]),
            "parent_asset_version_id": str(row["parent_asset_version_id"]),
            "sha256": str(row["sha256"]),
            "width": int(row["width"]),
            "height": int(row["height"]),
            "selected_pixel_count": int(row["selected_pixel_count"]),
            "created_at": str(row["created_at"]),
            "external_requests_started": 0,
        }

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
