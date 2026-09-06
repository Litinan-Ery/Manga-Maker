from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Literal, cast

from ..database import Database
from ..errors import ApplicationError
from ..generation.assets import canonical_json, write_synced
from ..generation.executor import provider_error_code
from ..ids import uuid7
from ..modules.production.adapters.novelai import require_frozen_novelai_payload
from ..novelai.client import (
    NovelAIConfiguration,
    NovelAIError,
    NovelAIImageRequest,
    NovelAIResponseFormatError,
    NovelAIUnknownOutcomeError,
    novelai_correlation_id,
    require_opus_zero_anlas_available,
    require_opus_zero_anlas_payload,
    validate_generated_png,
)
from ..novelai.service import NovelAIService, ProviderFactory, default_provider_factory
from ..pages.service import PageService
from ..prompting.service import PromptingService
from ..shared_kernel import canonical_sha256
from ..vault import CredentialVault, VaultLockedError
from .compiler import compile_full_page, input_fingerprint
from .lettering import calibrate_lettering
from .models import FullPageOptions, FullPagePlan


class FullPageService:
    def __init__(
        self,
        database: Database,
        prompting: PromptingService,
        novelai: NovelAIService,
        vault: CredentialVault,
        pages: PageService,
        *,
        provider_factory: ProviderFactory = default_provider_factory,
    ) -> None:
        self.database, self.prompting, self.novelai = database, prompting, novelai
        self.vault, self.pages, self.provider_factory = vault, pages, provider_factory

    def sources(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        inputs = self.prompting.approved_comic_inputs(project_id, chapter_id)
        return {
            "pages": [
                {
                    "page_id": str(page.page_id),
                    "page_number": page.page_number,
                    "panels": [
                        {
                            "panel_id": str(panel.panel_id),
                            "visual_description": panel.visual_prompt,
                            "characters": panel.characters,
                            "texts": [
                                *panel.narration,
                                *(line.text for line in panel.dialogue),
                                *panel.sfx,
                            ],
                        }
                        for panel in page.panels
                    ],
                }
                for page in inputs["storyboard"].pages
            ],
            "external_requests_started": 0,
        }

    def preview(self, project_id: str, options: FullPageOptions) -> dict[str, Any]:
        configuration = self.novelai.get_configuration(project_id)
        if configuration["provider_model_id"] != "nai-diffusion-5-full":
            raise ApplicationError(
                "FULL_PAGE_MODEL_REQUIRED", "整页模式请先选择 NovelAI V5 Full。", 409
            )
        inputs = self.prompting.approved_comic_inputs(project_id, options.chapter_id)
        generation_id = str(uuid7())
        plan = compile_full_page(
            project_id, generation_id, options, inputs, configuration["revision"]
        )
        serialized = plan.model_dump(mode="json")
        with self.database.writer() as connection:
            connection.execute(
                """INSERT INTO full_page_generations(
                    generation_id, project_id, chapter_id, page_id, plan_json, plan_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    generation_id,
                    project_id,
                    options.chapter_id,
                    plan.page_document.page_id,
                    canonical_json(serialized),
                    canonical_sha256(serialized),
                ),
            )
        return self.get(project_id, generation_id)

    def list_generations(self, project_id: str, chapter_id: str) -> list[dict[str, Any]]:
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT * FROM full_page_generations WHERE project_id = ? AND chapter_id = ?
                ORDER BY created_at DESC, generation_id DESC LIMIT 100""",
                (project_id, chapter_id),
            ).fetchall()
        return [self._payload(row) for row in rows]

    def get(self, project_id: str, generation_id: str) -> dict[str, Any]:
        return self._payload(self._row(project_id, generation_id))

    def list_current_generations(self, project_id: str, chapter_id: str) -> list[dict[str, Any]]:
        """One latest frozen attempt per page, without the history UI's 100-row limit."""
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY page_id ORDER BY created_at DESC, rowid DESC
                    ) AS attempt_rank FROM full_page_generations
                    WHERE project_id = ? AND chapter_id = ?
                ) WHERE attempt_rank = 1""",
                (project_id, chapter_id),
            ).fetchall()
        return [self._payload(row) for row in rows]

    async def generate(
        self,
        project_id: str,
        generation_id: str,
        *,
        plan_sha256: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        row = self._row(project_id, generation_id)
        plan = self._plan(row)
        if not confirmed or plan_sha256 != str(row["plan_sha256"]):
            raise ApplicationError(
                "FULL_PAGE_APPROVAL_REQUIRED", "请确认当前预览及成本后生成。", 409
            )
        if row["status"] != "draft":
            return self._payload(row)
        configuration = self.novelai.get_configuration(project_id)
        self._require_fresh(plan, configuration)
        payload = require_frozen_novelai_payload(
            plan.provider_execution_spec, plan.provider_payload
        )
        billing_mode: Literal["opus_zero_anlas", "standard"] = (
            "opus_zero_anlas" if plan.options.cost_ceiling_anlas == 0 else "standard"
        )
        if billing_mode == "opus_zero_anlas":
            require_opus_zero_anlas_payload(payload)
        try:
            with self.vault.credential_transaction():
                profile_id = str(configuration["credential_profile_id"])
                secret = self.vault.get_secret(profile_id)
        except (VaultLockedError, KeyError) as exc:
            raise ApplicationError(
                "FULL_PAGE_CREDENTIAL_UNAVAILABLE", "请解锁并检查 NovelAI 凭证。", 409
            ) from exc

        def read_secret(profile_id: str) -> str:
            if profile_id != str(configuration["credential_profile_id"]):
                raise KeyError(profile_id)
            return secret

        provider = self.provider_factory(
            NovelAIConfiguration(
                provider_model_id=payload.model,
                credential_profile_id=profile_id,
                timeout_seconds=float(configuration["timeout_seconds"]),
            ),
            read_secret,
        )
        request = NovelAIImageRequest(
            correlation_id=novelai_correlation_id(),
            provider_model_id=payload.model,
            prompt=payload.input,
            negative_prompt=payload.parameters.negative_prompt,
            width=payload.parameters.width,
            height=payload.parameters.height,
            steps=payload.parameters.steps,
            scale=payload.parameters.scale,
            seed=payload.parameters.seed,
            billing_mode=billing_mode,
            provider_execution_spec=plan.provider_execution_spec,
            frozen_payload=payload,
        )
        try:
            with self.database.writer() as connection:
                active = connection.execute(
                    "SELECT 1 FROM generation_attempts WHERE status = 'running' "
                    "UNION ALL SELECT 1 FROM full_page_generations WHERE status = 'running'"
                ).fetchone()
                if active is not None:
                    raise ApplicationError(
                        "FULL_PAGE_BUSY", "已有图像生成正在执行，请等待完成。", 409
                    )
                updated = connection.execute(
                    """UPDATE full_page_generations SET status = 'running', approval_sha256 = ?,
                    started_at = CURRENT_TIMESTAMP WHERE generation_id = ? AND status = 'draft'""",
                    (plan_sha256, generation_id),
                ).rowcount
                if not updated:
                    return self.get(project_id, generation_id)
        except sqlite3.IntegrityError as exc:
            raise ApplicationError(
                "FULL_PAGE_BUSY", "已有整页生成正在执行，请等待完成。", 409
            ) from exc
        sent = False
        try:
            verification = None
            if billing_mode == "opus_zero_anlas":
                self._request_started(generation_id)
                subscription = await provider.get_subscription()
                require_opus_zero_anlas_available(subscription, payload.model)
                verification = subscription.zero_anlas_verification()
            # Source edits during subscription lookup must stop image generation.
            self._require_fresh(plan, self.novelai.get_configuration(project_id))
            self._request_started(generation_id)
            sent = True
            generated = await provider.generate_image(request)
            validate_generated_png(generated.png_bytes, request)
            relative = Path("assets") / "full-pages" / generation_id / "original.png"
            path = self._workspace(project_id) / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            write_synced(path, generated.png_bytes)
            image_sha256 = hashlib.sha256(generated.png_bytes).hexdigest()
            result = {
                "width": generated.width,
                "height": generated.height,
                "seed": generated.seed,
                "seed_source": generated.seed_source,
                "correlation_id": request.correlation_id,
                "zero_anlas_verification": verification,
                "cost_status": "opus_eligibility_verified" if verification else "not_reported",
                "provider_payload_sha256": plan.provider_execution_spec.payload_sha256,
            }
            write_synced(
                path.with_name("provenance.json"),
                (
                    canonical_json(
                        {
                            "plan_sha256": plan_sha256,
                            "image_sha256": image_sha256,
                            "result": result,
                            "credentials_included": False,
                        }
                    )
                    + "\n"
                ).encode(),
            )
            with self.database.writer() as connection:
                connection.execute(
                    """UPDATE full_page_generations SET status = 'ready', image_relative_path = ?,
                    image_sha256 = ?, result_json = ?
                    WHERE generation_id = ? AND status = 'running'""",
                    (str(relative), image_sha256, canonical_json(result), generation_id),
                )
        except asyncio.CancelledError:
            self._failed(generation_id, "FULL_PAGE_INTERRUPTED", unknown=sent)
            raise
        except (NovelAIError, ApplicationError) as exc:
            code = exc.code if isinstance(exc, ApplicationError) else provider_error_code(exc)
            if isinstance(exc, NovelAIUnknownOutcomeError):
                code = "FULL_PAGE_PROVIDER_OUTCOME_UNKNOWN"
            self._failed(
                generation_id,
                code,
                unknown=sent
                and isinstance(exc, (NovelAIUnknownOutcomeError, NovelAIResponseFormatError)),
            )
        except Exception:
            self._failed(generation_id, "FULL_PAGE_RESULT_UNCERTAIN", unknown=sent)
        finally:
            secret = ""
        return self.get(project_id, generation_id)

    def adopt(
        self,
        project_id: str,
        generation_id: str,
        *,
        expected_revision: int,
        confirmed: bool,
    ) -> dict[str, Any]:
        row = self._row(project_id, generation_id)
        plan = self._plan(row)
        if row["status"] != "ready" or not confirmed:
            raise ApplicationError("FULL_PAGE_REVIEW_REQUIRED", "请检查成图后再采用为页面。", 409)
        self._require_fresh(plan, self.novelai.get_configuration(project_id))
        return self.pages.adopt_full_page(
            project_id,
            plan.options.chapter_id,
            calibrate_lettering(plan.page_document, self.content_path(project_id, generation_id)),
            expected_revision=expected_revision,
        )

    def content_path(self, project_id: str, generation_id: str) -> Path:
        row = self._row(project_id, generation_id)
        if row["status"] != "ready" or not row["image_relative_path"]:
            raise ApplicationError("FULL_PAGE_IMAGE_NOT_READY", "整页图像尚未就绪。", 409)
        workspace = self._workspace(project_id)
        path = (workspace / str(row["image_relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError("FULL_PAGE_IMAGE_MISSING", "整页图像文件缺失。", 409)
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["image_sha256"]:
            raise ApplicationError("FULL_PAGE_IMAGE_CHANGED", "整页图像校验失败。", 409)
        return path

    def verify_current_plan(self, project_id: str, generation_id: str) -> None:
        """Read-only freshness check for workflow recovery; creates no approval or request."""
        plan = self._plan(self._row(project_id, generation_id))
        self._require_fresh(plan, self.novelai.get_configuration(project_id))

    def reconcile_startup(self) -> None:
        with self.database.writer() as connection:
            connection.execute(
                """UPDATE full_page_generations SET status = 'needs_review',
                error_code = 'FULL_PAGE_INTERRUPTED' WHERE status = 'running'"""
            )

    def _require_fresh(self, plan: FullPagePlan, configuration: dict[str, Any]) -> None:
        inputs = self.prompting.approved_comic_inputs(plan.project_id, plan.options.chapter_id)
        if (
            configuration["revision"] != plan.configuration_revision
            or configuration["provider_model_id"] != plan.provider_payload.model
            or input_fingerprint(inputs) != plan.inputs_sha256
        ):
            raise ApplicationError(
                "FULL_PAGE_PREVIEW_STALE", "分镜、版式、角色或配置已变化，请重新预览。", 409
            )

    def _row(self, project_id: str, generation_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM full_page_generations WHERE project_id = ? AND generation_id = ?",
                (project_id, generation_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("FULL_PAGE_GENERATION_NOT_FOUND", "没有找到整页生成记录。", 404)
        return cast(sqlite3.Row, row)

    @staticmethod
    def _plan(row: sqlite3.Row) -> FullPagePlan:
        raw = json.loads(str(row["plan_json"]))
        if canonical_sha256(raw) != row["plan_sha256"]:
            raise ApplicationError("FULL_PAGE_PLAN_CHANGED", "冻结的整页预览校验失败。", 409)
        return FullPagePlan.model_validate(raw)

    def _payload(self, row: sqlite3.Row) -> dict[str, Any]:
        plan = self._plan(row)
        return {
            "generation_id": str(row["generation_id"]),
            "status": str(row["status"]),
            "plan_sha256": str(row["plan_sha256"]),
            "page_id": str(row["page_id"]),
            "page_number": plan.options.page_number,
            "panel_count": len(plan.page_document.panels),
            "options": plan.options.model_dump(mode="json"),
            "provider_payload": plan.provider_payload.model_dump(mode="json", exclude_none=True),
            "provider_payload_sha256": plan.provider_execution_spec.payload_sha256,
            "generation_calls": 1,
            "verification_calls": int(plan.options.cost_ceiling_anlas == 0),
            "cost_ceiling_anlas": plan.options.cost_ceiling_anlas,
            "external_requests_started": int(row["external_requests_started"]),
            "error_code": row["error_code"],
            "created_at": str(row["created_at"]),
            "removed_conflicting_tags": plan.removed_conflicting_tags,
            "image_sha256": row["image_sha256"],
            "result": json.loads(str(row["result_json"])) if row["result_json"] else None,
        }

    def _workspace(self, project_id: str) -> Path:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT workspace_path FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError("PROJECT_NOT_FOUND", "没有找到该项目。", 404)
        return Path(str(row["workspace_path"])).resolve()

    def _request_started(self, generation_id: str) -> None:
        with self.database.writer() as connection:
            connection.execute(
                "UPDATE full_page_generations "
                "SET external_requests_started = external_requests_started + 1 "
                "WHERE generation_id = ?",
                (generation_id,),
            )

    def _failed(self, generation_id: str, code: str, *, unknown: bool) -> None:
        with self.database.writer() as connection:
            connection.execute(
                "UPDATE full_page_generations SET status = ?, error_code = ? "
                "WHERE generation_id = ?",
                ("needs_review" if unknown else "failed", code, generation_id),
            )
