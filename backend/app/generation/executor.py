from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from ..adaptation.models import StoryboardDocument
from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..modules.layout.contracts import FrameSpec
from ..modules.layout.domain import frame_content_sha256
from ..modules.production.adapters.novelai import (
    NovelAIV4Payload,
    require_frozen_novelai_payload,
)
from ..modules.production.contracts import ProviderExecutionSpec
from ..modules.production.errors import ProviderMappingError
from ..modules.prompting.public import (
    PromptCompilationError,
    PromptPlan,
    prompt_plan_sha256,
    require_prompt_package_integrity,
)
from ..novelai.client import (
    NovelAIAuthenticationError,
    NovelAIClient,
    NovelAIConfiguration,
    NovelAIConfigurationError,
    NovelAIImageRequest,
    NovelAIInsufficientBalanceError,
    NovelAIInvalidRequestError,
    NovelAIPermissionError,
    NovelAIProvider,
    NovelAIRateLimitError,
    NovelAIResponseFormatError,
    NovelAITemporaryError,
    NovelAIUnknownOutcomeError,
    PreciseReferenceInput,
    SecretReader,
    novelai_correlation_id,
)
from ..prompting.models import CharacterTagBundleDocument, PromptBundleDocument
from ..shared_kernel import canonical_sha256
from ..vault import CredentialVault, VaultLockedError
from .assets import AssetStore
from .models import CompiledGenerationSpec, GenerationSpecDocument, ReferenceUse
from .queue import GenerationQueueService
from .references import ReferencePreparationError

MAX_PROVIDER_ATTEMPTS_PER_ITEM = 3


class ImageProviderFactory(Protocol):
    def __call__(
        self, configuration: NovelAIConfiguration, secret_reader: SecretReader
    ) -> NovelAIProvider: ...


class RevisionFinalizer(Protocol):
    def finalize_if_completed(self, job_id: str) -> dict[str, Any] | None: ...


def default_image_provider_factory(
    configuration: NovelAIConfiguration, secret_reader: SecretReader
) -> NovelAIProvider:
    return NovelAIClient(configuration, secret_reader)


class GenerationSpecCompiler:
    def __init__(self, database: Database) -> None:
        self.database = database

    def compile(self, claim: dict[str, Any]) -> CompiledGenerationSpec:
        context = self._context(str(claim["attempt_id"]))
        self._require_current_layout_freeze(context)
        self._require_current_generation_freeze(context)
        operation = str(context["operation_kind"])
        storyboard = StoryboardDocument.model_validate_json(str(context["storyboard_json"]))
        tags = CharacterTagBundleDocument.model_validate_json(str(context["tag_bundle_json"]))
        prompt_bundle = PromptBundleDocument.model_validate_json(str(context["prompt_bundle_json"]))
        panel = next(
            (
                panel
                for page in storyboard.pages
                for panel in page.panels
                if str(panel.panel_id) == str(context["panel_id"])
            ),
            None,
        )
        if panel is None:
            raise ApplicationError(
                "GENERATION_PANEL_NOT_FOUND",
                "固定分镜版本中没有找到目标面板。",
                409,
            )
        package = next(
            (
                item
                for item in prompt_bundle.packages
                if str(item.panel_id) == str(context["panel_id"])
            ),
            None,
        )
        if package is None:
            raise ApplicationError(
                "GENERATION_PROMPT_PACKAGE_NOT_FOUND",
                "冻结 PromptPackage 中没有找到目标面板。",
                409,
            )
        self._verify_prompt_package(package, tags)
        prompt = package.compiled_prompt
        if operation == "inpaint":
            prompt = joined_prompt([prompt, str(context["edit_prompt"] or "")])
        negative_prompt = package.compiled_negative_prompt
        if len(prompt) > 12_000 or len(negative_prompt) > 12_000:
            raise ApplicationError(
                "GENERATION_PROMPT_TOO_LONG",
                "合并后的面板提示词超过本地安全上限，请先精简设定。",
                422,
            )

        reference_use: ReferenceUse | None = None
        provider_reference: PreciseReferenceInput | None = None
        frozen_reference = (
            json.loads(str(context["reference_use_json"]))
            if context["reference_use_json"] is not None
            else None
        )
        if frozen_reference is not None:
            if operation == "inpaint":
                raise ApplicationError(
                    "NOVELAI_INPAINT_REFERENCE_CONFLICT",
                    "局部重绘不能沿用 Precise Reference。",
                    409,
                )
            reference_use = ReferenceUse.model_validate(frozen_reference)
            reference_images = NovelAIV4Payload.model_validate_json(
                str(context["provider_payload_json"])
            ).parameters.director_reference_images
            if not reference_images or len(reference_images) != 1:
                raise ApplicationError(
                    "GENERATION_REFERENCE_FREEZE_MISSING",
                    "冻结载荷缺少已批准 Precise Reference。",
                    409,
                )
            provider_reference = PreciseReferenceInput(
                png_base64=reference_images[0],
                description=reference_use.description,
                strength=reference_use.strength,
                fidelity=reference_use.fidelity,
            )

        seed = int(context["provider_seed"])
        spec_id = str(uuid7())
        correlation_id = novelai_correlation_id()
        revision_inputs = self._revision_inputs(context)
        spec_action = {
            "chapter_generate": "generate",
            "panel_reroll": "reroll",
            "page_reroll": "reroll",
            "inpaint": "inpaint",
        }.get(operation)
        if spec_action is None:
            raise ApplicationError("GENERATION_OPERATION_INVALID", "生成任务操作类型无效。", 409)
        document = GenerationSpecDocument(
            schema_version="1.4",
            spec_id=spec_id,
            project_id=str(context["project_id"]),
            chapter_id=str(context["chapter_id"]),
            job_id=str(context["job_id"]),
            item_id=str(context["item_id"]),
            attempt_id=str(context["attempt_id"]),
            correlation_id=correlation_id,
            panel_id=str(context["panel_id"]),
            storyboard_version_id=str(context["storyboard_version_id"]),
            character_bible_version_id=str(context["character_bible_version_id"]),
            style_bible_version_id=str(context["style_bible_version_id"]),
            character_tag_bundle_version_id=str(context["character_tag_bundle_version_id"]),
            prompt_bundle_version_id=str(context["prompt_bundle_version_id"]),
            prompt_package_id=str(package.prompt_package_id),
            text_model_config_revision=int(context["text_model_config_revision"]),
            compiled_prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            compiled_negative_prompt_sha256=package.compiled_negative_prompt_sha256,
            generation_approval_id=str(context["generation_approval_id"]),
            generation_approval_sha256=str(context["generation_approval_sha256"]),
            prompt_approval_hash=str(context["prompt_approval_hash"]),
            prompt_snapshot_sha256=str(context["prompt_snapshot_sha256"]),
            prompt_plan_id=str(context["prompt_plan_id"]),
            prompt_plan_version=int(context["prompt_plan_version"]),
            prompt_plan_sha256=str(context["prompt_plan_sha256"]),
            prompt_package_sha256=str(context["prompt_package_sha256"]),
            character_tag_set_refs=json.loads(str(context["character_tag_set_refs_json"])),
            approved_provider_execution_spec_sha256=str(
                context["provider_execution_spec_sha256"]
            ),
            provider_payload_sha256=str(context["provider_payload_sha256"]),
            candidate_count=int(context["candidate_count"]),
            quality_rule_version=str(context["quality_rule_version"]),
            layout_snapshot_sha256=str(context["layout_snapshot_sha256"]),
            page_layout_draft_id=str(context["page_layout_draft_id"]),
            page_layout_draft_version_id=str(context["page_layout_draft_version_id"]),
            layout_version=int(context["layout_version"]),
            layout_content_sha256=str(context["layout_content_sha256"]),
            layout_approval_id=str(context["layout_approval_id"]),
            layout_approval_sha256=str(context["layout_approval_sha256"]),
            frame_id=str(context["frame_id"]),
            frame_content_sha256=str(context["frame_content_sha256"]),
            dimension_selection_id=str(context["dimension_selection_id"]),
            dimension_selection_sha256=str(context["dimension_selection_sha256"]),
            expected_crop_ratio=float(context["expected_crop_ratio"]),
            dimension_rule_version=str(context["dimension_rule_version"]),
            capability_snapshot_sha256=str(context["capability_snapshot_sha256"]),
            provider_model_id=str(context["provider_model_id"]),
            mapping_version=str(context["mapping_version"]),
            contract_sha256=str(context["contract_sha256"]),
            action=spec_action,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=int(context["selected_width"]),
            height=int(context["selected_height"]),
            steps=28,
            scale=5.0,
            sampler="k_euler_ancestral",
            noise_schedule="karras",
            seed=seed,
            references=[reference_use] if reference_use is not None else [],
            parent_asset_version_id=revision_inputs["parent_asset_version_id"],
            parent_image_sha256=revision_inputs["parent_image_sha256"],
            mask_asset_id=revision_inputs["mask_asset_id"],
            mask_sha256=revision_inputs["mask_sha256"],
            edit_prompt=(
                str(context["edit_prompt"]) if context["edit_prompt"] is not None else None
            ),
            inpaint_strength=(
                float(context["inpaint_strength"])
                if context["inpaint_strength"] is not None
                else None
            ),
            prompt_source=(
                "approved_prompt_package_plus_user_edit"
                if operation == "inpaint"
                else "approved_prompt_package"
            ),
        )
        approved_execution_spec = ProviderExecutionSpec.model_validate_json(
            str(context["provider_execution_spec_json"])
        )
        execution_spec = approved_execution_spec.model_copy(
            update={
                "provider_execution_spec_id": uuid5(
                    NAMESPACE_URL,
                    f"manga-maker:provider-execution:{document.spec_id}",
                ),
                "generation_spec_id": UUID(document.spec_id),
            }
        )
        frozen_payload = require_frozen_novelai_payload(
            execution_spec,
            NovelAIV4Payload.model_validate_json(str(context["provider_payload_json"])),
        )
        return CompiledGenerationSpec(
            document=document,
            provider_execution_spec=execution_spec,
            provider_payload=frozen_payload,
            provider_request=self._provider_request(
                document,
                provider_reference,
                execution_spec=execution_spec,
                frozen_payload=frozen_payload,
                source_image_base64=revision_inputs["source_image_base64"],
                mask_base64=revision_inputs["mask_base64"],
            ),
        )

    def configuration_for_attempt(self, attempt_id: str) -> NovelAIConfiguration:
        context = self._context(attempt_id)
        credential_profile_id = str(context["credential_profile_id"])
        if not credential_profile_id:
            raise ApplicationError(
                "GENERATION_CREDENTIAL_SNAPSHOT_MISSING",
                "该旧队列没有冻结 NovelAI 凭证引用，请取消并重新创建。",
                409,
            )
        return NovelAIConfiguration(
            provider_model_id=str(context["provider_model_id"]),
            credential_profile_id=credential_profile_id,
            timeout_seconds=float(context["timeout_seconds"]),
        )

    @staticmethod
    def _verify_prompt_package(package: Any, tags: CharacterTagBundleDocument) -> None:
        structured = package.structured_package
        if structured is None:
            raise ApplicationError(
                "GENERATION_STRUCTURED_PROMPT_REQUIRED",
                "旧 legacy_flat_prompt 不能执行新生成任务。",
                409,
            )
        try:
            require_prompt_package_integrity(structured)
        except PromptCompilationError as exc:
            raise ApplicationError(exc.code, exc.message, 409, exc.details) from exc
        tags_by_id = {str(item.tag_set_id): item for item in tags.tag_sets}
        structured_by_id = {
            str(item.character_tag_set_version_id): item
            for item in structured.prompt_plan.characters
        }
        for block in package.character_blocks:
            tag_set = tags_by_id.get(str(block.tag_set_id))
            structured_character = structured_by_id.get(str(block.tag_set_id))
            if (
                tag_set is None
                or structured_character is None
                or block.fixed_tags_sha256 != tag_set.fixed_tags_sha256
                or structured_character.fixed_tags_sha256 != tag_set.fixed_tags_sha256
            ):
                raise ApplicationError(
                    "GENERATION_FIXED_TAGS_MISMATCH",
                    "PromptPackage 中的固定角色 tags 与冻结 TagSet 不一致。",
                    409,
                )
            if (
                block.fixed_tags != tag_set.fixed_tags
                or structured_character.fixed_tags != tag_set.fixed_tags
            ):
                raise ApplicationError(
                    "GENERATION_FIXED_TAGS_MISMATCH",
                    "PromptPackage 中的固定角色 tags 已被修改。",
                    409,
                )
        if hashlib.sha256(package.compiled_prompt.encode("utf-8")).hexdigest() != (
            package.compiled_prompt_sha256
        ) or hashlib.sha256(package.compiled_negative_prompt.encode("utf-8")).hexdigest() != (
            package.compiled_negative_prompt_sha256
        ):
            raise ApplicationError(
                "GENERATION_PROMPT_HASH_MISMATCH",
                "冻结 PromptPackage 的提示词哈希不一致。",
                409,
            )

    def _context(self, attempt_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT ga.attempt_id, gi.item_id, gi.panel_id,
                       gi.operation_kind, gi.parent_asset_version_id,
                       gi.mask_asset_id, gi.edit_prompt, gi.inpaint_strength,
                       gi.page_layout_draft_id, gi.page_layout_draft_version_id,
                       gi.layout_version, gi.layout_content_sha256,
                       gi.layout_approval_id, gi.layout_approval_sha256,
                       gi.frame_id, gi.frame_content_sha256,
                       gi.dimension_selection_id, gi.dimension_selection_sha256,
                       gi.selected_width, gi.selected_height, gi.expected_crop_ratio,
                       gi.dimension_rule_version, gi.capability_snapshot_sha256,
                       gi.prompt_plan_id, gi.prompt_plan_version,
                       gi.prompt_plan_sha256, gi.prompt_plan_json,
                       gi.prompt_package_sha256, gi.character_tag_set_refs_json,
                       gi.provider_execution_spec_id,
                       gi.provider_execution_spec_json,
                       gi.provider_execution_spec_sha256,
                       gi.provider_payload_sha256, gi.provider_payload_json,
                       gi.provider_seed, gi.candidate_count, gi.reference_use_json,
                       gj.job_id,
                       gj.project_id, gj.chapter_id, gj.storyboard_version_id,
                       gj.character_bible_version_id, gj.style_bible_version_id,
                       gj.character_tag_bundle_version_id, gj.prompt_bundle_version_id,
                       gj.text_model_config_revision,
                       gj.novelai_config_revision,
                       gj.provider_model_id, gj.mapping_version, gj.contract_sha256,
                       gj.credential_profile_id, gj.timeout_seconds,
                       gj.layout_snapshot_sha256, gj.plan_fingerprint,
                       gj.generation_approval_id, gj.generation_approval_sha256,
                       gj.prompt_approval_hash, gj.prompt_snapshot_sha256,
                       gj.candidate_count_per_panel, gj.quality_rule_version,
                       gap.approval_sha256 AS stored_generation_approval_sha256,
                       gap.snapshot_json AS generation_approval_snapshot_json,
                       gap.plan_fingerprint AS generation_approval_plan_fingerprint,
                       gap.state AS generation_approval_state,
                       pba.approval_hash AS current_prompt_approval_hash,
                       pba.snapshot_sha256 AS current_prompt_snapshot_sha256,
                       sv.is_current AS storyboard_is_current,
                       cbv.is_current AS character_bible_is_current,
                       sbv.is_current AS style_bible_is_current,
                       ctv.is_current AS tag_bundle_is_current,
                       pbv.is_current AS prompt_bundle_is_current,
                       tmc.revision AS current_text_model_config_revision,
                       nc.revision AS current_novelai_config_revision,
                       nc.provider_model_id AS current_provider_model_id,
                       nc.inpaint_model_id AS current_inpaint_model_id,
                       nc.mapping_version AS current_mapping_version,
                       nc.contract_sha256 AS current_contract_sha256,
                       nc.credential_profile_id AS current_credential_profile_id,
                       sv.document_json AS storyboard_json,
                       cbv.document_json AS character_json,
                       sbv.document_json AS style_json,
                       ctv.document_json AS tag_bundle_json,
                       pbv.document_json AS prompt_bundle_json,
                       p.workspace_path
                FROM generation_attempts ga
                JOIN generation_job_items gi ON gi.item_id = ga.item_id
                JOIN generation_jobs gj ON gj.job_id = gi.job_id
                JOIN storyboard_versions sv
                  ON sv.storyboard_version_id = gj.storyboard_version_id
                JOIN character_bible_versions cbv
                  ON cbv.character_bible_version_id = gj.character_bible_version_id
                JOIN style_bible_versions sbv
                  ON sbv.style_bible_version_id = gj.style_bible_version_id
                JOIN character_tag_bundle_versions ctv
                  ON ctv.character_tag_bundle_version_id = gj.character_tag_bundle_version_id
                JOIN prompt_bundle_versions pbv
                  ON pbv.prompt_bundle_version_id = gj.prompt_bundle_version_id
                JOIN generation_approvals gap
                  ON gap.generation_approval_id = gj.generation_approval_id
                JOIN prompt_bundle_approvals pba
                  ON pba.prompt_bundle_version_id = gj.prompt_bundle_version_id
                JOIN text_model_configs tmc ON tmc.project_id = gj.project_id
                JOIN novelai_configs nc ON nc.project_id = gj.project_id
                JOIN projects p ON p.project_id = gj.project_id
                WHERE ga.attempt_id = ? AND ga.status = 'running'
                """,
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "GENERATION_ATTEMPT_NOT_RUNNING", "生成尝试不存在或已经结束。", 409
            )
        return cast(sqlite3.Row, row)

    def _require_current_layout_freeze(self, context: sqlite3.Row) -> None:
        """Revalidate the exact approved frame before a spec or secret can be used."""

        required = (
            "layout_snapshot_sha256",
            "page_layout_draft_id",
            "page_layout_draft_version_id",
            "layout_content_sha256",
            "layout_approval_id",
            "layout_approval_sha256",
            "frame_id",
            "frame_content_sha256",
            "dimension_selection_id",
            "dimension_selection_sha256",
            "dimension_rule_version",
            "capability_snapshot_sha256",
        )
        if any(not str(context[field]) for field in required):
            raise ApplicationError(
                "GENERATION_LAYOUT_SNAPSHOT_MISSING",
                "生成任务没有冻结有效版式，请取消并重新创建。",
                409,
            )
        prompt_bundle = PromptBundleDocument.model_validate_json(str(context["prompt_bundle_json"]))
        package = next(
            (
                item
                for item in prompt_bundle.packages
                if str(item.panel_id) == str(context["panel_id"])
            ),
            None,
        )
        binding = package.layout_binding if package is not None else None
        expected = (
            str(context["layout_snapshot_sha256"]),
            str(context["page_layout_draft_id"]),
            str(context["page_layout_draft_version_id"]),
            int(context["layout_version"]),
            str(context["layout_content_sha256"]),
            str(context["layout_approval_id"]),
            str(context["layout_approval_sha256"]),
            str(context["frame_id"]),
            str(context["frame_content_sha256"]),
            str(context["dimension_selection_id"]),
            str(context["dimension_selection_sha256"]),
            int(context["selected_width"]),
            int(context["selected_height"]),
            float(context["expected_crop_ratio"]),
            str(context["dimension_rule_version"]),
            str(context["capability_snapshot_sha256"]),
        )
        actual = (
            prompt_bundle.layout_snapshot_sha256,
            str(binding.page_layout_draft_id) if binding else None,
            str(binding.page_layout_draft_version_id) if binding else None,
            binding.layout_version if binding else None,
            binding.layout_content_sha256 if binding else None,
            str(binding.layout_approval_id) if binding else None,
            binding.layout_approval_sha256 if binding else None,
            str(binding.frame_id) if binding else None,
            binding.frame_content_sha256 if binding else None,
            str(binding.dimension_selection_id) if binding else None,
            binding.dimension_selection_sha256 if binding else None,
            binding.selected_width if binding else None,
            binding.selected_height if binding else None,
            binding.expected_crop_ratio if binding else None,
            binding.dimension_rule_version if binding else None,
            binding.capability_snapshot_sha256 if binding else None,
        )
        if actual != expected:
            raise ApplicationError(
                "GENERATION_LAYOUT_SNAPSHOT_MISMATCH",
                "生成任务与已审批 PromptPackage 的版式冻结信息不一致。",
                409,
            )

        with self.database.reader() as connection:
            current = connection.execute(
                """
                SELECT pld.page_layout_draft_version_id, pld.version,
                       pld.content_sha256, pld.document_json,
                       la.approval_id, la.approval_sha256,
                       ds.dimension_selection_id, ds.content_sha256 AS selection_sha256,
                       ds.rule_version, ds.capability_snapshot_sha256,
                       ds.selected_width, ds.selected_height, ds.expected_crop_ratio
                FROM page_layout_drafts pld
                JOIN layout_approvals la
                  ON la.page_layout_draft_version_id = pld.page_layout_draft_version_id
                JOIN dimension_selections ds
                  ON ds.page_layout_draft_version_id = pld.page_layout_draft_version_id
                 AND ds.frame_id = ?
                JOIN layout_approval_dimension_selections lads
                  ON lads.approval_id = la.approval_id
                 AND lads.dimension_selection_id = ds.dimension_selection_id
                WHERE pld.project_id = ? AND pld.page_layout_draft_id = ?
                  AND pld.page_layout_draft_version_id = ? AND pld.is_current = 1
                """,
                (
                    str(context["frame_id"]),
                    str(context["project_id"]),
                    str(context["page_layout_draft_id"]),
                    str(context["page_layout_draft_version_id"]),
                ),
            ).fetchone()
        if current is None:
            raise ApplicationError(
                "GENERATION_LAYOUT_STALE",
                "冻结版式不再是当前有效审批，请重新建立生成计划。",
                409,
            )
        layout_document = json.loads(str(current["document_json"]))
        frame = next(
            (
                item
                for item in layout_document.get("frames", [])
                if str(item.get("frame_id")) == str(context["frame_id"])
            ),
            None,
        )
        if frame is None:
            raise ApplicationError("GENERATION_LAYOUT_STALE", "冻结版式格已不存在。", 409)
        checks = (
            str(current["page_layout_draft_version_id"])
            == str(context["page_layout_draft_version_id"]),
            int(current["version"]) == int(context["layout_version"]),
            str(current["content_sha256"]) == str(context["layout_content_sha256"]),
            str(current["approval_id"]) == str(context["layout_approval_id"]),
            str(current["approval_sha256"]) == str(context["layout_approval_sha256"]),
            frame_content_sha256(FrameSpec.model_validate(frame))
            == str(context["frame_content_sha256"]),
            str(current["dimension_selection_id"]) == str(context["dimension_selection_id"]),
            str(current["selection_sha256"]) == str(context["dimension_selection_sha256"]),
            int(current["selected_width"]) == int(context["selected_width"]),
            int(current["selected_height"]) == int(context["selected_height"]),
            float(current["expected_crop_ratio"]) == float(context["expected_crop_ratio"]),
            str(current["rule_version"]) == str(context["dimension_rule_version"]),
            str(current["capability_snapshot_sha256"])
            == str(context["capability_snapshot_sha256"]),
        )
        if not all(checks):
            raise ApplicationError(
                "GENERATION_LAYOUT_STALE",
                "冻结版式、审批、格或尺寸选择已经变化。",
                409,
            )

    def _require_current_generation_freeze(self, context: sqlite3.Row) -> None:
        """Verify every GenerationApproval input before a credential can be read."""

        required = (
            "generation_approval_id",
            "generation_approval_sha256",
            "prompt_approval_hash",
            "prompt_snapshot_sha256",
            "prompt_plan_id",
            "prompt_plan_sha256",
            "prompt_plan_json",
            "prompt_package_sha256",
            "character_tag_set_refs_json",
            "provider_execution_spec_json",
            "provider_execution_spec_sha256",
            "provider_payload_sha256",
            "provider_payload_json",
            "quality_rule_version",
        )
        if any(context[field] is None or not str(context[field]) for field in required):
            raise ApplicationError(
                "GENERATION_APPROVAL_FREEZE_MISSING",
                "生成任务缺少完整冻结批准，请重新估算并创建 Job。",
                409,
            )
        current_model_id = (
            context["current_inpaint_model_id"]
            if str(context["operation_kind"]) == "inpaint"
            else context["current_provider_model_id"]
        )
        if (
            str(context["generation_approval_state"]) != "active"
            or not all(
                bool(context[field])
                for field in (
                    "storyboard_is_current",
                    "character_bible_is_current",
                    "style_bible_is_current",
                    "tag_bundle_is_current",
                    "prompt_bundle_is_current",
                )
            )
            or int(context["text_model_config_revision"])
            != int(context["current_text_model_config_revision"])
            or int(context["current_novelai_config_revision"])
            != int(context["novelai_config_revision"])
            or str(current_model_id) != str(context["provider_model_id"])
            or str(context["current_mapping_version"]) != str(context["mapping_version"])
            or str(context["current_contract_sha256"]) != str(context["contract_sha256"])
            or str(context["current_credential_profile_id"])
            != str(context["credential_profile_id"])
            or str(context["generation_approval_sha256"])
            != str(context["stored_generation_approval_sha256"])
            or str(context["plan_fingerprint"])
            != str(context["generation_approval_plan_fingerprint"])
            or str(context["prompt_approval_hash"])
            != str(context["current_prompt_approval_hash"])
            or str(context["prompt_snapshot_sha256"])
            != str(context["current_prompt_snapshot_sha256"])
            or int(context["candidate_count"])
            != int(context["candidate_count_per_panel"])
        ):
            raise ApplicationError(
                "GENERATION_APPROVAL_STALE",
                "生成批准或 Prompt 审批已变化，请重新估算。",
                409,
            )

        try:
            approval_snapshot = json.loads(str(context["generation_approval_snapshot_json"]))
            if canonical_sha256(approval_snapshot) != str(
                context["generation_approval_sha256"]
            ):
                raise ValueError("approval hash mismatch")
            prompt_plan = PromptPlan.model_validate_json(str(context["prompt_plan_json"]))
            execution_spec = ProviderExecutionSpec.model_validate_json(
                str(context["provider_execution_spec_json"])
            )
            payload = NovelAIV4Payload.model_validate_json(
                str(context["provider_payload_json"])
            )
            references = json.loads(str(context["character_tag_set_refs_json"]))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ApplicationError(
                "GENERATION_APPROVAL_FREEZE_INVALID",
                "生成批准冻结数据无法通过本地契约校验。",
                409,
            ) from exc

        if (
            prompt_plan_sha256(prompt_plan) != str(context["prompt_plan_sha256"])
            or prompt_plan.content_sha256 != str(context["prompt_plan_sha256"])
            or str(prompt_plan.prompt_plan_id) != str(context["prompt_plan_id"])
            or prompt_plan.version != int(context["prompt_plan_version"])
            or canonical_sha256(execution_spec.model_dump(mode="json"))
            != str(context["provider_execution_spec_sha256"])
            or str(execution_spec.provider_execution_spec_id)
            != str(context["provider_execution_spec_id"])
            or execution_spec.prompt_plan_sha256 != str(context["prompt_plan_sha256"])
            or execution_spec.payload_sha256 != str(context["provider_payload_sha256"])
            or execution_spec.model_id != str(context["provider_model_id"])
            or execution_spec.mapping_version != str(context["mapping_version"])
            or execution_spec.contract_sha256 != str(context["contract_sha256"])
            or execution_spec.capability_snapshot_sha256
            != str(context["capability_snapshot_sha256"])
            or execution_spec.page_layout_draft_sha256
            != str(context["layout_content_sha256"])
            or execution_spec.seed != int(context["provider_seed"])
            or canonical_sha256(payload.model_dump(mode="json", exclude_none=True))
            != str(context["provider_payload_sha256"])
            or references
            != [
                {
                    "character_id": str(character.character_id),
                    "character_tag_set_version_id": str(
                        character.character_tag_set_version_id
                    ),
                    "fixed_tags_sha256": character.fixed_tags_sha256,
                }
                for character in sorted(prompt_plan.characters, key=lambda item: item.order)
            ]
        ):
            raise ApplicationError(
                "GENERATION_APPROVAL_STALE",
                "PromptPlan、TagSet、模型或供应商冻结载荷已变化，请重新估算。",
                409,
            )
        try:
            require_frozen_novelai_payload(execution_spec, payload)
        except ProviderMappingError as exc:
            raise ApplicationError(exc.code, exc.message, 409, exc.details) from exc

        bundle = PromptBundleDocument.model_validate_json(str(context["prompt_bundle_json"]))
        package = next(
            (
                item
                for item in bundle.packages
                if str(item.panel_id) == str(context["panel_id"])
            ),
            None,
        )
        structured = package.structured_package if package is not None else None
        if (
            structured is None
            or structured.content_sha256 != str(context["prompt_package_sha256"])
            or structured.prompt_plan.content_sha256 != str(context["prompt_plan_sha256"])
        ):
            raise ApplicationError(
                "GENERATION_APPROVAL_STALE",
                "当前 PromptPackage 不再匹配冻结批准，请重新估算。",
                409,
            )

    def _revision_inputs(self, context: sqlite3.Row) -> dict[str, str | None]:
        parent_asset_version_id = context["parent_asset_version_id"]
        if parent_asset_version_id is None:
            return {
                "parent_asset_version_id": None,
                "parent_image_sha256": None,
                "mask_asset_id": None,
                "mask_sha256": None,
                "source_image_base64": None,
                "mask_base64": None,
            }
        with self.database.reader() as connection:
            parent = connection.execute(
                """
                SELECT av.image_sha256, av.original_relative_path, p.workspace_path
                FROM asset_versions av
                JOIN projects p ON p.project_id = av.project_id
                WHERE av.asset_version_id = ? AND av.project_id = ?
                  AND av.panel_id = ? AND av.status = 'ready'
                """,
                (
                    str(parent_asset_version_id),
                    str(context["project_id"]),
                    str(context["panel_id"]),
                ),
            ).fetchone()
            mask = None
            if context["mask_asset_id"] is not None:
                mask = connection.execute(
                    """
                    SELECT sha256, relative_path FROM mask_assets
                    WHERE mask_asset_id = ? AND project_id = ?
                      AND panel_id = ? AND parent_asset_version_id = ?
                    """,
                    (
                        str(context["mask_asset_id"]),
                        str(context["project_id"]),
                        str(context["panel_id"]),
                        str(parent_asset_version_id),
                    ),
                ).fetchone()
        if parent is None:
            raise ApplicationError(
                "REVISION_PARENT_ASSET_INVALID", "冻结的父素材不存在或已损坏。", 409
            )
        workspace = Path(str(parent["workspace_path"])).resolve()
        parent_path = (workspace / str(parent["original_relative_path"])).resolve()
        if not parent_path.is_relative_to(workspace) or not parent_path.is_file():
            raise ApplicationError(
                "REVISION_PARENT_ASSET_FILE_MISSING", "冻结的父素材文件缺失。", 409
            )
        parent_raw = parent_path.read_bytes()
        if hashlib.sha256(parent_raw).hexdigest() != str(parent["image_sha256"]):
            raise ApplicationError(
                "REVISION_PARENT_ASSET_HASH_MISMATCH", "冻结的父素材哈希不一致。", 409
            )
        result: dict[str, str | None] = {
            "parent_asset_version_id": str(parent_asset_version_id),
            "parent_image_sha256": str(parent["image_sha256"]),
            "mask_asset_id": None,
            "mask_sha256": None,
            "source_image_base64": None,
            "mask_base64": None,
        }
        if str(context["operation_kind"]) != "inpaint":
            return result
        if mask is None:
            raise ApplicationError("MASK_NOT_FOUND", "冻结的局部重绘蒙版不存在。", 409)
        mask_path = (workspace / str(mask["relative_path"])).resolve()
        if not mask_path.is_relative_to(workspace) or not mask_path.is_file():
            raise ApplicationError("MASK_FILE_MISSING", "冻结的蒙版文件缺失。", 409)
        mask_raw = mask_path.read_bytes()
        if hashlib.sha256(mask_raw).hexdigest() != str(mask["sha256"]):
            raise ApplicationError("MASK_HASH_MISMATCH", "冻结的蒙版哈希不一致。", 409)
        result.update(
            {
                "mask_asset_id": str(context["mask_asset_id"]),
                "mask_sha256": str(mask["sha256"]),
                "source_image_base64": base64.b64encode(parent_raw).decode("ascii"),
                "mask_base64": base64.b64encode(mask_raw).decode("ascii"),
            }
        )
        return result

    @staticmethod
    def _provider_request(
        document: GenerationSpecDocument,
        reference: PreciseReferenceInput | None,
        *,
        execution_spec: ProviderExecutionSpec,
        frozen_payload: NovelAIV4Payload,
        source_image_base64: str | None,
        mask_base64: str | None,
    ) -> NovelAIImageRequest:
        return NovelAIImageRequest(
            correlation_id=document.correlation_id,
            provider_model_id=document.provider_model_id,
            prompt=document.prompt,
            negative_prompt=document.negative_prompt,
            width=document.width,
            height=document.height,
            steps=document.steps,
            scale=document.scale,
            seed=document.seed,
            sampler=document.sampler,
            noise_schedule=document.noise_schedule,
            precise_reference=reference,
            action="infill" if document.action == "inpaint" else "generate",
            source_image_base64=source_image_base64,
            mask_base64=mask_base64,
            inpaint_strength=document.inpaint_strength,
            provider_execution_spec=execution_spec,
            frozen_payload=frozen_payload,
        )


class GenerationExecutor:
    def __init__(
        self,
        database: Database,
        queue: GenerationQueueService,
        vault: CredentialVault,
        assets: AssetStore,
        *,
        provider_factory: ImageProviderFactory = default_image_provider_factory,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        revision_finalizer: RevisionFinalizer | None = None,
    ) -> None:
        self.database = database
        self.queue = queue
        self.vault = vault
        self.assets = assets
        self.compiler = GenerationSpecCompiler(database)
        self.provider_factory = provider_factory
        self.retry_sleep = retry_sleep
        self.revision_finalizer = revision_finalizer
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, job_id: str) -> bool:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return False
        task = asyncio.create_task(self.run_until_blocked(job_id))
        self._tasks[job_id] = task

        def on_done(completed: asyncio.Task[None]) -> None:
            self._task_done(job_id, completed)

        task.add_done_callback(on_done)
        return True

    async def run_until_blocked(self, job_id: str) -> None:
        job = self._job_by_id(job_id)
        maximum_iterations = int(job["max_calls"]) + int(job["panel_count"]) + 1
        for _ in range(maximum_iterations):
            outcome = await self.execute_next(job_id)
            if outcome in {"completed", "retry"}:
                continue
            return

    async def execute_next(self, job_id: str) -> str:
        claim = self.queue.claim_next(job_id)
        if claim is None:
            return "blocked"
        attempt_id = str(claim["attempt_id"])
        try:
            compiled = self.compiler.compile(claim)
            spec_sha256 = self.assets.persist_spec(
                compiled.document,
                compiled.provider_execution_spec,
                compiled.provider_payload,
            )
            configuration = self.compiler.configuration_for_attempt(attempt_id)
        except ApplicationError as exc:
            self.queue.fail_attempt(
                attempt_id, error_code=exc.code, outcome_unknown=False
            )
            return "failed"
        except (
            NovelAIConfigurationError,
            ProviderMappingError,
            ReferencePreparationError,
        ):
            self.queue.fail_attempt(
                attempt_id, error_code="SPEC_COMPILATION_FAILED", outcome_unknown=False
            )
            return "failed"

        try:
            secret_value: str | None = self.vault.get_secret(configuration.credential_profile_id)

            def frozen_secret_reader(profile_id: str) -> str:
                if secret_value is None or profile_id != configuration.credential_profile_id:
                    raise KeyError(profile_id)
                return secret_value

            provider = self.provider_factory(configuration, frozen_secret_reader)
        except (NovelAIConfigurationError, VaultLockedError, KeyError) as exc:
            self.queue.fail_attempt(
                attempt_id,
                error_code=provider_error_code(exc),
                outcome_unknown=False,
            )
            return "failed"
        except Exception:
            self.queue.fail_attempt(
                attempt_id,
                error_code="PROVIDER_CONFIGURATION_INVALID",
                outcome_unknown=False,
            )
            return "failed"

        if not self.queue.mark_provider_request_started(attempt_id):
            secret_value = None
            return "blocked"
        try:
            generated = await provider.generate_image(compiled.provider_request)
        except NovelAITemporaryError:
            if int(claim["attempt_number"]) < MAX_PROVIDER_ATTEMPTS_PER_ITEM:
                self.queue.requeue_attempt(attempt_id, error_code="PROVIDER_TEMPORARY")
                await self.retry_sleep(0.5 * (2 ** (int(claim["attempt_number"]) - 1)))
                return "retry"
            self.queue.fail_attempt(
                attempt_id, error_code="PROVIDER_TEMPORARY_EXHAUSTED", outcome_unknown=False
            )
            return "failed"
        except (NovelAIUnknownOutcomeError, NovelAIResponseFormatError):
            self.queue.fail_attempt(
                attempt_id, error_code="UNKNOWN_PROVIDER_OUTCOME", outcome_unknown=True
            )
            return "needs_review"
        except (
            NovelAIAuthenticationError,
            NovelAIPermissionError,
            NovelAIInsufficientBalanceError,
            NovelAIRateLimitError,
            NovelAIInvalidRequestError,
            NovelAIConfigurationError,
            VaultLockedError,
            KeyError,
        ) as exc:
            self.queue.fail_attempt(
                attempt_id,
                error_code=provider_error_code(exc),
                outcome_unknown=False,
            )
            return "failed"
        except Exception:
            self.queue.fail_attempt(
                attempt_id, error_code="UNKNOWN_PROVIDER_OUTCOME", outcome_unknown=True
            )
            return "needs_review"
        finally:
            secret_value = None

        try:
            self.assets.register_generated_image(
                compiled.document,
                generated,
                spec_sha256=spec_sha256,
                recorded_cost_anlas=None,
            )
        except Exception as exc:
            self.queue.fail_attempt(
                attempt_id,
                error_code=local_asset_error_code(exc),
                outcome_unknown=True,
            )
            return "needs_review"
        if self.revision_finalizer is not None:
            try:
                self.revision_finalizer.finalize_if_completed(job_id)
            except Exception:
                self.queue.mark_job_needs_review(job_id, "PAGE_REVISION_FINALIZATION_FAILED")
                return "needs_review"
        return "completed"

    async def shutdown(self) -> None:
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    def _job_by_id(self, job_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError("GENERATION_JOB_NOT_FOUND", "没有找到该生成队列。", 404)
        return cast(sqlite3.Row, row)

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(job_id, None)
        if not task.cancelled():
            task.exception()


def joined_prompt(parts: list[str]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = " ".join(part.split()).strip(" ,")
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return ", ".join(result)


def provider_error_code(exc: Exception) -> str:
    if isinstance(exc, NovelAIAuthenticationError):
        return "PROVIDER_UNAUTHORIZED"
    if isinstance(exc, NovelAIPermissionError):
        return "PROVIDER_FORBIDDEN"
    if isinstance(exc, NovelAIInsufficientBalanceError):
        return "PROVIDER_QUOTA"
    if isinstance(exc, NovelAIRateLimitError):
        return "PROVIDER_RATE_LIMITED"
    if isinstance(exc, NovelAIInvalidRequestError):
        return "PROVIDER_REJECTED"
    if isinstance(exc, VaultLockedError):
        return "VAULT_LOCKED"
    if isinstance(exc, KeyError):
        return "CREDENTIAL_PROFILE_NOT_FOUND"
    return "PROVIDER_CONFIGURATION_INVALID"


def local_asset_error_code(exc: Exception) -> str:
    if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
        return "LOCAL_STORAGE_FULL"
    return "LOCAL_ASSET_COMMIT_FAILED"
