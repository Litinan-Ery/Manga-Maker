from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from ..adaptation.models import StoryboardDocument
from ..adaptation.service import AdaptationService, canonical_json
from ..bibles.models import CharacterBibleDocument, StyleBibleDocument
from ..bibles.service import BibleService
from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..modules.adaptation.contracts import StoryboardVersionRefV1
from ..modules.layout.errors import LayoutError
from ..modules.layout.public import (
    ApprovedChapterLayoutSnapshotV1,
    ApprovedFrameSnapshotV1,
    ApprovedPageLayoutSnapshotV1,
    LayoutFacade,
    LayoutPageRequirementV1,
)
from ..modules.lineage.contracts import (
    ArtifactVersionRefV1,
    RegisterArtifactCommandV1,
    RegisterDependencyCommandV1,
)
from ..modules.lineage.public import ArtifactNotFoundError, LineageFacade
from ..modules.production.adapters.novelai import map_prompt_plan_to_novelai
from ..modules.production.errors import ProviderMappingError
from ..modules.prompting.public import (
    ApprovedCharacterTagSet,
    PromptCompilationError,
    PromptCompilationInput,
    TextModelSource,
    compile_prompt_package,
    require_prompt_package_integrity,
)
from ..modules.prompting.public import (
    CharacterPromptDraft as StructuredCharacterPromptDraft,
)
from ..modules.prompting.public import PanelPromptDraft as StructuredPanelPromptDraft
from ..novelai.contracts import CONTRACT_SHA256, MAPPING_VERSION
from ..shared_kernel import canonical_sha256
from .models import (
    CharacterTagBundleDocument,
    CharacterTagGenerationRequest,
    CharacterTagSetDocument,
    PromptBundleDocument,
    PromptCharacterBlock,
    PromptDraftBundleDocument,
    PromptGenerationRequest,
    PromptLayoutBinding,
    PromptPackageDocument,
)


class PromptingService:
    def __init__(
        self,
        database: Database,
        adaptation: AdaptationService,
        bibles: BibleService,
        layout: LayoutFacade,
        lineage: LineageFacade,
    ) -> None:
        self.database = database
        self.adaptation = adaptation
        self.bibles = bibles
        self.layout = layout
        self.lineage = lineage

    async def generate_character_tags(
        self,
        project_id: str,
        chapter_id: str,
        *,
        confirmed_data_send: bool,
    ) -> dict[str, Any]:
        if not confirmed_data_send:
            self._confirmation_required()
        inputs = self._approved_inputs(project_id, chapter_id)
        provider_model_id = self._provider_model_id(project_id)
        target_ids = self._target_tag_set_ids(project_id, chapter_id, inputs["characters"])
        provider, _, config_revision = self.adaptation.configured_provider(project_id)
        request = CharacterTagGenerationRequest(
            project_id=project_id,
            chapter_id=chapter_id,
            storyboard_version_id=UUID(inputs["storyboard_version_id"]),
            character_bible_version_id=UUID(inputs["character_version_id"]),
            style_bible_version_id=UUID(inputs["style_version_id"]),
            character_bible=inputs["characters"],
            style_bible=inputs["style"],
            target_tag_set_ids=target_ids,
            provider_model_id=provider_model_id,
        )
        try:
            candidate = await provider.generate_character_tags(request)
        except Exception as exc:
            raise self.adaptation.provider_error(exc) from exc
        self.adaptation.require_configuration_revision(project_id, config_revision)
        self._assert_inputs_still_current(project_id, chapter_id, inputs)
        document = self._normalize_tag_document(candidate.document, request)
        version_id = self._persist_tag_document(
            project_id,
            chapter_id,
            document,
            provider_model_id=provider_model_id,
            provenance=self._candidate_provenance(candidate, config_revision),
        )
        return self.get_character_tag_version(project_id, version_id)

    def revise_character_tags(
        self,
        project_id: str,
        version_id: str,
        document: CharacterTagBundleDocument,
    ) -> dict[str, Any]:
        row = self._tag_version_row(project_id, version_id)
        self._require_tag_current_and_fresh(row)
        current_document = CharacterTagBundleDocument.model_validate_json(
            str(row["document_json"])
        )
        inputs = self._approved_inputs(project_id, str(row["chapter_id"]))
        request = CharacterTagGenerationRequest(
            project_id=project_id,
            chapter_id=str(row["chapter_id"]),
            storyboard_version_id=UUID(inputs["storyboard_version_id"]),
            character_bible_version_id=UUID(inputs["character_version_id"]),
            style_bible_version_id=UUID(inputs["style_version_id"]),
            character_bible=inputs["characters"],
            style_bible=inputs["style"],
            target_tag_set_ids={
                str(tag.character_id): tag.tag_set_id
                for tag in current_document.tag_sets
            },
            provider_model_id=str(row["provider_model_id"]),
        )
        normalized = self._normalize_tag_document(document, request)
        next_id = self._persist_tag_document(
            project_id,
            str(row["chapter_id"]),
            normalized,
            provider_model_id=str(row["provider_model_id"]),
            provenance={"change_type": "manual_edit", "parent_version_id": version_id},
        )
        return self.get_character_tag_version(project_id, next_id)

    def approve_character_tags(self, project_id: str, version_id: str) -> dict[str, Any]:
        row = self._tag_version_row(project_id, version_id)
        self._require_tag_current_and_fresh(row)
        approval_hash = self._approval_hash(row)
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO character_tag_bundle_approvals(
                    approval_id, character_tag_bundle_version_id, approval_hash
                ) VALUES (?, ?, ?)
                """,
                (str(uuid7()), version_id, approval_hash),
            )
            self._audit(
                connection,
                project_id,
                "character_tags.approved",
                {"version_id": version_id, "approval_hash": approval_hash},
            )
        return self.get_character_tag_version(project_id, version_id)

    async def generate_prompt_bundle(
        self,
        project_id: str,
        chapter_id: str,
        *,
        confirmed_data_send: bool,
    ) -> dict[str, Any]:
        if not confirmed_data_send:
            self._confirmation_required()
        inputs = self._approved_inputs(project_id, chapter_id)
        layout_snapshot = self._approved_layout_snapshot(project_id, chapter_id, inputs)
        tag_row = self._current_tag_row(project_id, chapter_id)
        if tag_row is None or tag_row["approval_hash"] is None:
            raise ApplicationError(
                "CHARACTER_TAGS_APPROVAL_REQUIRED",
                "请先审批当前角色固定 tags。",
                409,
            )
        self._require_tag_current_and_fresh(tag_row)
        tags = CharacterTagBundleDocument.model_validate_json(str(tag_row["document_json"]))
        provider_model_id = self._provider_model_id(project_id)
        target_ids = self._target_prompt_ids(project_id, chapter_id, inputs["storyboard"])
        prompt_version = self._next_prompt_version(project_id, chapter_id)
        provider, configuration, config_revision = self.adaptation.configured_provider(project_id)
        request = PromptGenerationRequest(
            project_id=project_id,
            chapter_id=chapter_id,
            storyboard=inputs["storyboard"],
            storyboard_version_id=UUID(inputs["storyboard_version_id"]),
            character_bible_version_id=UUID(inputs["character_version_id"]),
            style_bible_version_id=UUID(inputs["style_version_id"]),
            character_bible=inputs["characters"],
            style_bible=inputs["style"],
            character_tag_bundle_version_id=UUID(
                str(tag_row["character_tag_bundle_version_id"])
            ),
            character_tags=tags,
            target_prompt_package_ids=target_ids,
            provider_model_id=provider_model_id,
            layout_snapshot=layout_snapshot.model_dump(mode="json"),
        )
        try:
            candidate = await provider.generate_prompt_bundle(request)
        except Exception as exc:
            raise self.adaptation.provider_error(exc) from exc
        self.adaptation.require_configuration_revision(project_id, config_revision)
        self._assert_inputs_still_current(project_id, chapter_id, inputs)
        self._assert_tag_version_current(
            project_id, str(tag_row["character_tag_bundle_version_id"])
        )
        current_layout_snapshot = self._approved_layout_snapshot(
            project_id,
            chapter_id,
            inputs,
        )
        if current_layout_snapshot.content_sha256 != layout_snapshot.content_sha256:
            raise ApplicationError(
                "PROMPT_LAYOUT_CHANGED",
                "文本模型处理期间版式审批发生变化，结果未写入。",
                409,
            )
        document = self._compile_prompt_bundle(
            candidate.document,
            request,
            text_model_profile_id=project_id,
            text_model_config_revision=config_revision,
            text_model_name=configuration.model,
            prompt_template_version=candidate.prompt_template_version,
            text_stage_run_id=uuid5(
                NAMESPACE_URL,
                f"manga-maker:text-stage-run:{candidate.response_sha256}",
            ),
            prompt_version=prompt_version,
            layout_snapshot=layout_snapshot,
        )
        version_id = self._persist_prompt_document(
            project_id,
            chapter_id,
            document,
            provenance=self._candidate_provenance(candidate, config_revision),
        )
        return self.get_prompt_version(project_id, version_id)

    def revise_prompt_bundle(
        self,
        project_id: str,
        version_id: str,
        draft: PromptDraftBundleDocument,
    ) -> dict[str, Any]:
        row = self._prompt_version_row(project_id, version_id)
        self._require_prompt_current_and_fresh(row)
        current_document = PromptBundleDocument.model_validate_json(
            str(row["document_json"])
        )
        chapter_id = str(row["chapter_id"])
        inputs = self._approved_inputs(project_id, chapter_id)
        layout_snapshot = self._approved_layout_snapshot(project_id, chapter_id, inputs)
        tag_row = self._tag_version_row(
            project_id, str(row["character_tag_bundle_version_id"])
        )
        tags = CharacterTagBundleDocument.model_validate_json(str(tag_row["document_json"]))
        request = PromptGenerationRequest(
            project_id=project_id,
            chapter_id=chapter_id,
            storyboard=inputs["storyboard"],
            storyboard_version_id=UUID(inputs["storyboard_version_id"]),
            character_bible_version_id=UUID(inputs["character_version_id"]),
            style_bible_version_id=UUID(inputs["style_version_id"]),
            character_bible=inputs["characters"],
            style_bible=inputs["style"],
            character_tag_bundle_version_id=UUID(
                str(tag_row["character_tag_bundle_version_id"])
            ),
            character_tags=tags,
            target_prompt_package_ids={
                str(package.panel_id): package.prompt_package_id
                for package in current_document.packages
            },
            provider_model_id=str(row["provider_model_id"]),
            layout_snapshot=layout_snapshot.model_dump(mode="json"),
        )
        prompt_version = self._next_prompt_version(project_id, chapter_id)
        document = self._compile_prompt_bundle(
            draft,
            request,
            text_model_profile_id=str(row["text_model_profile_id"]),
            text_model_config_revision=int(row["text_model_config_revision"]),
            text_model_name=str(row["text_model_name"]),
            prompt_template_version=str(row["prompt_template_version"]),
            text_stage_run_id=uuid5(
                NAMESPACE_URL,
                f"manga-maker:manual-prompt-revision:{version_id}:{prompt_version}",
            ),
            prompt_version=prompt_version,
            layout_snapshot=layout_snapshot,
        )
        next_id = self._persist_prompt_document(
            project_id,
            chapter_id,
            document,
            provenance={"change_type": "manual_edit", "parent_version_id": version_id},
        )
        return self.get_prompt_version(project_id, next_id)

    def approve_prompt_bundle(
        self,
        project_id: str,
        version_id: str,
        *,
        snapshot_sha256: str,
        idempotency_key: str,
        request_sha256: str,
    ) -> dict[str, Any]:
        row = self._prompt_version_row(project_id, version_id)
        self._require_prompt_current_and_fresh(row)
        current_snapshot_sha256 = self._prompt_snapshot_sha256(row)
        if snapshot_sha256 != current_snapshot_sha256:
            raise ApplicationError(
                "PROMPT_APPROVAL_SNAPSHOT_STALE",
                "PromptPlan 预览已经变化，请刷新后重新审批。",
                409,
            )
        approval_hash = self._approval_hash(row)
        with self.database.writer() as connection:
            existing = connection.execute(
                """
                SELECT a.*, v.prompt_bundle_version_id
                FROM prompt_bundle_approvals a
                JOIN prompt_bundle_versions v
                  ON v.prompt_bundle_version_id = a.prompt_bundle_version_id
                JOIN prompt_bundles b ON b.prompt_bundle_id = v.prompt_bundle_id
                WHERE b.project_id = ? AND a.idempotency_key = ?
                """,
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["request_sha256"]) != request_sha256
                    or str(existing["prompt_bundle_version_id"]) != version_id
                ):
                    raise ApplicationError(
                        "PROMPT_APPROVAL_IDEMPOTENCY_CONFLICT",
                        "Idempotency-Key 已绑定到另一份 Prompt 审批请求。",
                        409,
                    )
            else:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO prompt_bundle_approvals(
                        approval_id, prompt_bundle_version_id, approval_hash,
                        snapshot_sha256, idempotency_key, request_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid7()),
                        version_id,
                        approval_hash,
                        snapshot_sha256,
                        idempotency_key,
                        request_sha256,
                    ),
                )
                self._audit(
                    connection,
                    project_id,
                    "prompt_bundle.approved",
                    {"version_id": version_id, "approval_hash": approval_hash},
                )
        return self.get_prompt_version(project_id, version_id)

    def prompt_impact(
        self,
        project_id: str,
        version_id: str,
        *,
        snapshot_sha256: str,
    ) -> dict[str, Any]:
        row = self._prompt_version_row(project_id, version_id)
        self._require_prompt_current_and_fresh(row)
        current_snapshot_sha256 = self._prompt_snapshot_sha256(row)
        if snapshot_sha256 != current_snapshot_sha256:
            raise ApplicationError(
                "PROMPT_IMPACT_SNAPSHOT_STALE",
                "PromptPlan 快照已经变化，请刷新后重试。",
                409,
            )
        document = PromptBundleDocument.model_validate_json(str(row["document_json"]))
        impacts: list[dict[str, Any]] = []
        for package in document.packages:
            structured = package.structured_package
            if structured is None:
                continue
            try:
                discovered = self.lineage.impact_preview(
                    ArtifactVersionRefV1(
                        project_id=project_id,
                        artifact_type="prompt_plan",
                        artifact_id=str(structured.prompt_plan.prompt_plan_id),
                        version=structured.prompt_plan.version,
                        content_sha256=structured.prompt_plan.content_sha256,
                        schema_version=structured.prompt_plan.schema_version,
                    )
                )
            except ArtifactNotFoundError:
                discovered = ()
            impacts.extend(item.model_dump(mode="json") for item in discovered)
        return {
            "contract_version": "1.0",
            "prompt_bundle_version_id": version_id,
            "snapshot_sha256": current_snapshot_sha256,
            "impacts": impacts,
            "requires_reestimate": bool(impacts),
            "external_requests_started": 0,
        }

    def prompt_inspector(
        self,
        project_id: str,
        version_id: str,
        *,
        snapshot_sha256: str,
    ) -> dict[str, Any]:
        row = self._prompt_version_row(project_id, version_id)
        self._require_prompt_current_and_fresh(row)
        current_snapshot_sha256 = self._prompt_snapshot_sha256(row)
        if snapshot_sha256 != current_snapshot_sha256:
            raise ApplicationError(
                "PROMPT_INSPECTOR_SNAPSHOT_STALE",
                "PromptPlan 预览已经变化，请刷新后重试。",
                409,
            )
        document = PromptBundleDocument.model_validate_json(str(row["document_json"]))
        panels: list[dict[str, Any]] = []
        for package in document.packages:
            structured = package.structured_package
            binding = package.layout_binding
            if structured is None or binding is None:
                raise ApplicationError(
                    "PROMPT_INSPECTOR_STRUCTURED_PROMPT_REQUIRED",
                    "旧 PromptPackage 没有结构化 PromptPlan 映射预览。",
                    409,
                )
            try:
                mapped = map_prompt_plan_to_novelai(
                    prompt_plan=structured.prompt_plan,
                    generation_spec_id=uuid5(
                        NAMESPACE_URL,
                        f"manga-maker:prompt-inspector:generation:{structured.content_sha256}",
                    ),
                    provider_execution_spec_id=uuid5(
                        NAMESPACE_URL,
                        f"manga-maker:prompt-inspector:provider:{structured.content_sha256}",
                    ),
                    seed_material=structured.content_sha256,
                    model_id=document.provider_model_id,
                    contract_sha256=CONTRACT_SHA256,
                    capability_snapshot_sha256=binding.capability_snapshot_sha256,
                    page_layout_draft_sha256=binding.layout_content_sha256,
                    width=binding.selected_width,
                    height=binding.selected_height,
                    seed=int(structured.prompt_plan.content_sha256[:8], 16)
                    % 4_294_967_288,
                    steps=28,
                    scale=5.0,
                    sampler="k_euler_ancestral",
                    noise_schedule="karras",
                    mapping_version=MAPPING_VERSION,
                )
            except ProviderMappingError as exc:
                raise ApplicationError(exc.code, exc.message, 409, exc.details) from exc
            payload = mapped.payload.model_dump(mode="json", exclude_none=True)
            panels.append(
                {
                    "panel_id": str(package.panel_id),
                    "prompt_package_id": str(package.prompt_package_id),
                    "prompt_package_sha256": structured.content_sha256,
                    "prompt_plan": structured.prompt_plan.model_dump(mode="json"),
                    "prompt_plan_sha256": structured.prompt_plan.content_sha256,
                    "provider_execution_spec": mapped.execution_spec.model_dump(mode="json"),
                    "provider_execution_spec_sha256": canonical_sha256(
                        mapped.execution_spec.model_dump(mode="json")
                    ),
                    "provider_payload_sha256": mapped.execution_spec.payload_sha256,
                    "provider_payload": self._redacted_prompt_inspector_payload(payload),
                    "mapping_version": mapped.execution_spec.mapping_version,
                    "model_id": mapped.execution_spec.model_id,
                }
            )
        impact = self.prompt_impact(
            project_id,
            version_id,
            snapshot_sha256=snapshot_sha256,
        )
        candidate_counts = [
            int(
                cast(dict[str, Any], panel["provider_payload"])["parameters"].get(
                    "n_samples", 1
                )
            )
            for panel in panels
        ]
        return {
            "contract_version": "1.0",
            "prompt_bundle_version_id": version_id,
            "snapshot_sha256": current_snapshot_sha256,
            "panels": panels,
            "impact": impact,
            "generation_summary": {
                "panel_count": len(panels),
                "candidate_count_per_panel": (
                    candidate_counts[0]
                    if candidate_counts and len(set(candidate_counts)) == 1
                    else None
                ),
                "estimated_calls": sum(candidate_counts),
                "estimated_cost_upper_anlas": None,
                "cost_status": "requires_generation_estimate",
                "cost_notice": (
                    "Prompt 审批不产生费用。保守成本上限在生成预估中按用户确认的"
                    "每格上限计算。"
                ),
            },
            "redaction": {
                "credentials_included": False,
                "headers_included": False,
                "source_chapter_included": False,
                "base64_included": False,
            },
            "external_requests_started": 0,
        }

    @staticmethod
    def _redacted_prompt_inspector_payload(payload: dict[str, Any]) -> dict[str, Any]:
        parameters = cast(dict[str, Any], payload["parameters"])
        allowlisted_parameter_fields = (
            "width",
            "height",
            "steps",
            "scale",
            "sampler",
            "noise_schedule",
            "seed",
            "n_samples",
            "negative_prompt",
            "prompt",
            "qualityToggle",
            "ucPreset",
            "params_version",
            "cfg_rescale",
            "dynamic_thresholding",
            "legacy",
            "legacy_v3_extend",
            "prefer_brownian",
            "deliberate_euler_ancestral_bug",
            "image_format",
            "v4_prompt",
            "v4_negative_prompt",
        )
        return {
            "action": payload["action"],
            "input": payload["input"],
            "model": payload["model"],
            "parameters": {
                key: parameters[key]
                for key in allowlisted_parameter_fields
                if key in parameters
            },
        }

    def get_workflow(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        tag_row = self._current_tag_row(project_id, chapter_id)
        prompt_row = self._current_prompt_row(project_id, chapter_id)
        blockers: list[str] = []
        tag_payload = (
            self._tag_payload(project_id, tag_row) if tag_row is not None else None
        )
        prompt_payload = (
            self._prompt_payload(project_id, prompt_row) if prompt_row is not None else None
        )
        prompt_document = (
            cast(dict[str, Any], prompt_payload["document"])
            if prompt_payload is not None
            else None
        )
        structured_prompt_ready = bool(
            prompt_document is not None
            and prompt_document.get("schema_version") == "1.2"
            and all(
                isinstance(package.get("structured_package"), dict)
                for package in cast(list[dict[str, Any]], prompt_document.get("packages", []))
            )
        )
        if tag_payload is None or tag_payload["approval_status"] != "approved":
            blockers.append("角色固定 tags 尚未批准或已经失效。")
        if prompt_payload is None or prompt_payload["approval_status"] != "approved":
            blockers.append("逐格 PromptPackage 尚未批准或已经失效。")
        elif not structured_prompt_ready:
            blockers.append(
                "旧 legacy_flat_prompt 只能查看历史素材，请重新生成并审批 PromptPlan v2。"
            )
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "character_tags": tag_payload,
            "prompt_bundle": prompt_payload,
            "generation_readiness": {
                "ready": not blockers,
                "blockers": blockers,
                "structured_prompt_ready": structured_prompt_ready,
                "character_tag_bundle_version_id": (
                    tag_payload["version_id"] if tag_payload else None
                ),
                "prompt_bundle_version_id": (
                    prompt_payload["version_id"] if prompt_payload else None
                ),
                "text_model_config_revision": (
                    prompt_payload["document"]["text_model_config_revision"]
                    if prompt_payload
                    else None
                ),
            },
        }

    def get_character_tag_version(self, project_id: str, version_id: str) -> dict[str, Any]:
        return self._tag_payload(project_id, self._tag_version_row(project_id, version_id))

    def get_prompt_version(self, project_id: str, version_id: str) -> dict[str, Any]:
        return self._prompt_payload(project_id, self._prompt_version_row(project_id, version_id))

    def _approved_inputs(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        bundle = self.bibles.get_bundle(project_id, chapter_id)
        readiness = cast(dict[str, Any], bundle["generation_readiness"])
        if not bool(readiness["ready"]):
            raise ApplicationError(
                "BIBLE_APPROVAL_REQUIRED",
                "请先审批当前角色设定表和风格板。",
                409,
                {"blockers": readiness["blockers"]},
            )
        character_payload = cast(dict[str, Any], bundle["character_bible"])
        style_payload = cast(dict[str, Any], bundle["style_bible"])
        characters = CharacterBibleDocument.model_validate(character_payload["document"])
        style = StyleBibleDocument.model_validate(style_payload["document"])
        storyboard_version_id = str(characters.storyboard_version_id)
        if str(style.storyboard_version_id) != storyboard_version_id:
            raise ApplicationError(
                "PROMPT_INPUT_VERSION_MISMATCH",
                "角色设定与风格板没有绑定同一个分镜版本。",
                409,
            )
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sv.document_json FROM storyboard_versions sv
                JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
                JOIN storyboard_approvals a
                  ON a.storyboard_version_id = sv.storyboard_version_id
                WHERE s.project_id = ? AND s.chapter_id = ?
                  AND sv.storyboard_version_id = ? AND sv.is_current = 1
                """,
                (project_id, chapter_id, storyboard_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "PROMPT_STORYBOARD_NOT_READY",
                "当前已审批分镜与设定版本不一致。",
                409,
            )
        return {
            "storyboard": StoryboardDocument.model_validate_json(str(row["document_json"])),
            "storyboard_version_id": storyboard_version_id,
            "characters": characters,
            "style": style,
            "character_version_id": str(readiness["character_bible_version_id"]),
            "style_version_id": str(readiness["style_bible_version_id"]),
        }

    def _normalize_tag_document(
        self,
        source: Any,
        request: CharacterTagGenerationRequest,
    ) -> CharacterTagBundleDocument:
        if str(source.storyboard_version_id) != str(request.storyboard_version_id):
            self._invalid_model_ids("角色 tags 的分镜版本不一致。")
        if str(source.character_bible_version_id) != str(
            request.character_bible_version_id
        ) or str(source.style_bible_version_id) != str(request.style_bible_version_id):
            self._invalid_model_ids("角色 tags 的设定版本不一致。")
        expected = {str(character.character_id) for character in request.character_bible.characters}
        actual = {str(item.character_id) for item in source.tag_sets}
        if actual != expected:
            raise ApplicationError(
                "INVALID_CHARACTER_TAG_COVERAGE",
                "角色 tags 必须与角色设定逐一对应。",
                422,
                {"expected": sorted(expected), "actual": sorted(actual)},
            )
        names = {
            str(character.character_id): character.name
            for character in request.character_bible.characters
        }
        normalized: list[CharacterTagSetDocument] = []
        for item in source.tag_sets:
            character_id = str(item.character_id)
            if item.tag_set_id != request.target_tag_set_ids[character_id]:
                self._invalid_model_ids("角色 tag set 标识与请求不一致。")
            fixed_hash = _tags_hash(item.fixed_tags)
            normalized.append(
                CharacterTagSetDocument(
                    **item.model_dump(exclude={"fixed_tags_sha256", "character_name"}),
                    character_name=names[character_id],
                    fixed_tags_sha256=fixed_hash,
                )
            )
        return CharacterTagBundleDocument(
            schema_version="1.0",
            storyboard_version_id=request.storyboard_version_id,
            character_bible_version_id=request.character_bible_version_id,
            style_bible_version_id=request.style_bible_version_id,
            tag_sets=normalized,
        )

    def _compile_prompt_bundle(
        self,
        draft: PromptDraftBundleDocument,
        request: PromptGenerationRequest,
        *,
        text_model_profile_id: str,
        text_model_config_revision: int,
        text_model_name: str,
        prompt_template_version: str,
        text_stage_run_id: UUID,
        prompt_version: int,
        layout_snapshot: Any,
    ) -> PromptBundleDocument:
        if (
            str(draft.storyboard_version_id) != str(request.storyboard_version_id)
            or str(draft.character_tag_bundle_version_id)
            != str(request.character_tag_bundle_version_id)
        ):
            self._invalid_model_ids("PromptPackage 的输入版本与请求不一致。")
        panels = {
            str(panel.panel_id): panel
            for page in request.storyboard.pages
            for panel in page.panels
        }
        packages = {str(package.panel_id): package for package in draft.packages}
        if set(packages) != set(panels):
            raise ApplicationError(
                "INVALID_PROMPT_PACKAGE_COVERAGE",
                "每个分镜格必须恰好有一个 PromptPackage。",
                422,
            )
        tags_by_character = {
            str(tag.character_id): tag for tag in request.character_tags.tag_sets
        }
        aliases: dict[str, str] = {}
        for character in request.character_bible.characters:
            for name in [character.name, *character.aliases]:
                aliases[name.casefold()] = str(character.character_id)
        layout_by_panel = {
            str(frame.frame.panel_id): (page, frame)
            for page in layout_snapshot.pages
            for frame in page.frames
        }
        if set(layout_by_panel) != set(panels):
            raise ApplicationError(
                "PROMPT_LAYOUT_COVERAGE_INVALID",
                "已批准版式未逐格覆盖当前分镜。",
                409,
            )
        compiled: list[PromptPackageDocument] = []
        text_model_source = TextModelSource(
            text_model_profile_id=UUID(text_model_profile_id),
            profile_version=text_model_config_revision,
            model_name=text_model_name,
            prompt_template_version=prompt_template_version,
            text_stage_run_id=text_stage_run_id,
        )
        for panel_id, panel in panels.items():
            package = packages[panel_id]
            if package.prompt_package_id != request.target_prompt_package_ids[panel_id]:
                self._invalid_model_ids("PromptPackage 标识与请求不一致。")
            unknown_names = [name for name in panel.characters if name.casefold() not in aliases]
            if unknown_names:
                raise ApplicationError(
                    "PROMPT_CHARACTER_NOT_IN_BIBLE",
                    "分镜格包含角色设定中不存在的角色。",
                    422,
                    {"panel_id": panel_id, "characters": unknown_names},
                )
            expected_characters = {aliases[name.casefold()] for name in panel.characters}
            if not expected_characters:
                raise ApplicationError(
                    "PROMPT_CHARACTER_COVERAGE_EMPTY",
                    "v0.3 PromptPlan 至少需要一个目标角色，请先补充分镜角色。",
                    422,
                    {"panel_id": panel_id},
                )
            if len(expected_characters) > 3:
                raise ApplicationError(
                    "PROMPT_CHARACTER_LIMIT_EXCEEDED",
                    "当前 v0.3 PromptPlan 最多支持三个结构化角色。",
                    422,
                    {"panel_id": panel_id, "character_count": len(expected_characters)},
                )
            blocks = {str(block.character_id): block for block in package.character_blocks}
            if set(blocks) != expected_characters:
                raise ApplicationError(
                    "INVALID_PROMPT_CHARACTER_COVERAGE",
                    "PromptPackage 的角色块必须与分镜格角色逐一对应。",
                    422,
                    {
                        "panel_id": panel_id,
                        "expected": sorted(expected_characters),
                        "actual": sorted(blocks),
                    },
                )
            page, approved_frame = layout_by_panel[panel_id]
            frame = approved_frame.frame
            approved_tag_sets: list[ApprovedCharacterTagSet] = []
            structured_blocks: list[StructuredCharacterPromptDraft] = []
            compiled_blocks: list[PromptCharacterBlock] = []
            for source_block in sorted(package.character_blocks, key=lambda item: item.order):
                character_id = str(source_block.character_id)
                tag_set = tags_by_character[character_id]
                if source_block.tag_set_id != tag_set.tag_set_id:
                    self._invalid_model_ids("PromptPackage 的 tag set 标识不一致。")
                try:
                    approved_tag_sets.append(
                        ApprovedCharacterTagSet(
                            character_id=tag_set.character_id,
                            character_tag_set_version_id=tag_set.tag_set_id,
                            fixed_tags=tuple(tag_set.fixed_tags),
                            fixed_tags_sha256=tag_set.fixed_tags_sha256,
                            negative_tags=tuple(tag_set.negative_tags),
                        )
                    )
                except ValueError as exc:
                    raise ApplicationError(
                        "PROMPT_FIXED_TAG_HASH_MISMATCH",
                        "已批准 CharacterTagSet 的有序固定 tags 与哈希不一致。",
                        409,
                        {"character_id": character_id},
                    ) from exc
                structured_blocks.append(
                    StructuredCharacterPromptDraft(
                        character_id=source_block.character_id,
                        character_tag_set_version_id=source_block.tag_set_id,
                        variable_positive_tags=tuple(source_block.variable_tags),
                        negative_tags=tuple(source_block.negative_tags),
                        action=source_block.action,
                        order=source_block.order,
                        center=source_block.center,
                    )
                )
                compiled_blocks.append(
                    PromptCharacterBlock(
                        character_id=source_block.character_id,
                        tag_set_id=source_block.tag_set_id,
                        fixed_tags=tag_set.fixed_tags,
                        fixed_tags_sha256=tag_set.fixed_tags_sha256,
                        variable_tags=source_block.variable_tags,
                    )
                )
            try:
                structured_package = compile_prompt_package(
                    PromptCompilationInput(
                        version=prompt_version,
                        draft=StructuredPanelPromptDraft(
                            prompt_package_id=package.prompt_package_id,
                            panel_id=package.panel_id,
                            base_positive_tags=tuple(package.base_visual_tags),
                            base_negative_tags=tuple(package.negative_tags),
                            relationship_action=package.relationship_action,
                            characters=tuple(structured_blocks),
                            style_tags=tuple(package.style_tags),
                            continuity_tags=tuple(package.continuity_tags),
                        ),
                        approved_tag_sets=tuple(approved_tag_sets),
                        frame=frame,
                        frame_sha256=approved_frame.frame_content_sha256,
                        page_layout_draft_id=page.version.layout.page_layout_draft_id,
                        page_layout_draft_version=page.version.layout.version,
                        text_model_source=text_model_source,
                    )
                )
                require_prompt_package_integrity(structured_package)
            except PromptCompilationError as exc:
                raise ApplicationError(exc.code, exc.message, 422, exc.details) from exc

            prompt = _legacy_flat_positive(structured_package)
            negative = _legacy_flat_negative(structured_package)
            compiled.append(
                PromptPackageDocument(
                    prompt_package_id=package.prompt_package_id,
                    panel_id=package.panel_id,
                    base_visual_tags=package.base_visual_tags,
                    character_blocks=compiled_blocks,
                    style_tags=package.style_tags,
                    negative_tags=package.negative_tags,
                    compiled_prompt=prompt,
                    compiled_negative_prompt=negative,
                    compiled_prompt_sha256=_text_hash(prompt),
                    compiled_negative_prompt_sha256=_text_hash(negative),
                    layout_binding=self._prompt_layout_binding(page, approved_frame),
                    structured_package=structured_package,
                )
            )
        return PromptBundleDocument(
            schema_version="1.2",
            storyboard_version_id=request.storyboard_version_id,
            character_bible_version_id=request.character_bible_version_id,
            style_bible_version_id=request.style_bible_version_id,
            character_tag_bundle_version_id=request.character_tag_bundle_version_id,
            text_model_profile_id=text_model_profile_id,
            text_model_config_revision=text_model_config_revision,
            text_model_name=text_model_name,
            prompt_template_version=prompt_template_version,
            provider_model_id=request.provider_model_id,
            layout_snapshot_sha256=layout_snapshot.content_sha256,
            packages=compiled,
        )

    def _approved_layout_snapshot(
        self,
        project_id: str,
        chapter_id: str,
        inputs: dict[str, Any],
    ) -> ApprovedChapterLayoutSnapshotV1:
        self._require_v03_project(project_id)
        storyboard = cast(StoryboardDocument, inputs["storyboard"])
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sv.version, sv.document_json
                FROM storyboard_versions sv
                JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
                JOIN storyboard_approvals sa
                  ON sa.storyboard_version_id = sv.storyboard_version_id
                WHERE s.project_id = ? AND s.chapter_id = ?
                  AND sv.storyboard_version_id = ? AND sv.is_current = 1
                """,
                (project_id, chapter_id, inputs["storyboard_version_id"]),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "LAYOUT_NOT_READY",
                "当前已审批分镜无法建立版式生成门禁。",
                409,
            )
        storyboard_payload = json.loads(str(row["document_json"]))
        storyboard_ref = StoryboardVersionRefV1(
            storyboard_id=str(storyboard.storyboard_id),
            storyboard_version_id=str(inputs["storyboard_version_id"]),
            version=int(row["version"]),
            content_sha256=canonical_sha256(storyboard_payload),
            approved=True,
        )
        requirements = tuple(
            LayoutPageRequirementV1(
                page_id=page.page_id,
                panel_ids=tuple(panel.panel_id for panel in page.panels),
            )
            for page in storyboard.pages
        )
        try:
            return self.layout.approved_chapter_snapshot(
                UUID(project_id),
                UUID(chapter_id),
                storyboard_ref,
                requirements,
            )
        except LayoutError as exc:
            raise ApplicationError(
                "LAYOUT_NOT_READY",
                "请先为当前已审批分镜完成逐页版式校验与审批。",
                409,
            ) from exc

    def _require_v03_project(self, project_id: str) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT workflow_version FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError("PROJECT_NOT_FOUND", "没有找到该项目。", 404)
        if str(row["workflow_version"]) != "v03":
            raise ApplicationError(
                "LEGACY_PROJECT_MIGRATION_REQUIRED",
                "旧 v0.2 工程可继续只读查看。创建新版 PromptPackage 或生成任务前，"
                "请先完成 v0.3 版式迁移与重新审批。",
                409,
            )

    @staticmethod
    def _prompt_layout_binding(
        page: ApprovedPageLayoutSnapshotV1,
        frame: ApprovedFrameSnapshotV1,
    ) -> PromptLayoutBinding:
        selection = frame.dimension_selection
        return PromptLayoutBinding(
            page_layout_draft_id=page.version.layout.page_layout_draft_id,
            page_layout_draft_version_id=page.version.page_layout_draft_version_id,
            layout_version=page.version.layout.version,
            layout_content_sha256=page.version.layout.content_sha256,
            layout_approval_id=page.approval.approval_id,
            layout_approval_sha256=page.approval.approval_sha256,
            frame_id=frame.frame.frame_id,
            frame_content_sha256=frame.frame_content_sha256,
            dimension_selection_id=selection.dimension_selection_id,
            dimension_selection_sha256=selection.content_sha256,
            selected_width=selection.selected.width,
            selected_height=selection.selected.height,
            expected_crop_ratio=selection.expected_crop_ratio,
            dimension_rule_version=selection.rule_version,
            capability_snapshot_sha256=selection.capability_snapshot_sha256,
        )

    def _register_prompt_lineage(
        self,
        project_id: str,
        version_id: str,
        version: int,
        document: PromptBundleDocument,
    ) -> None:
        if document.schema_version not in {"1.1", "1.2"}:
            return
        for package in document.packages:
            binding = package.layout_binding
            assert binding is not None
            frame = self.lineage.register_artifact(
                RegisterArtifactCommandV1(
                    artifact=ArtifactVersionRefV1(
                        project_id=project_id,
                        artifact_type="frame",
                        artifact_id=str(binding.frame_id),
                        version=binding.layout_version,
                        content_sha256=binding.frame_content_sha256,
                        schema_version="1.0",
                    )
                )
            )
            package_payload = package.model_dump(mode="json")
            prompt = self.lineage.register_artifact(
                RegisterArtifactCommandV1(
                    artifact=ArtifactVersionRefV1(
                        project_id=project_id,
                        artifact_type="prompt_package",
                        artifact_id=str(package.prompt_package_id),
                        version=version,
                        content_sha256=canonical_sha256(package_payload),
                        schema_version=document.schema_version,
                    )
                )
            )
            self.lineage.register_dependency(
                RegisterDependencyCommandV1(
                    upstream=frame,
                    downstream=prompt,
                    edge_type="frame_to_prompt",
                )
            )
            structured_package = package.structured_package
            if structured_package is not None:
                plan = structured_package.prompt_plan
                prompt_plan = self.lineage.register_artifact(
                    RegisterArtifactCommandV1(
                        artifact=ArtifactVersionRefV1(
                            project_id=project_id,
                            artifact_type="prompt_plan",
                            artifact_id=str(plan.prompt_plan_id),
                            version=plan.version,
                            content_sha256=plan.content_sha256,
                            schema_version=plan.schema_version,
                        )
                    )
                )
                self.lineage.register_dependency(
                    RegisterDependencyCommandV1(
                        upstream=frame,
                        downstream=prompt_plan,
                        edge_type="frame_to_prompt",
                    )
                )

    def _persist_tag_document(
        self,
        project_id: str,
        chapter_id: str,
        document: CharacterTagBundleDocument,
        *,
        provider_model_id: str,
        provenance: dict[str, Any],
    ) -> str:
        with self.database.writer() as connection:
            root_id = self._stable_root(
                connection,
                "character_tag",
                project_id,
                chapter_id,
            )
            connection.execute(
                "UPDATE character_tag_bundle_versions SET is_current = 0 "
                "WHERE character_tag_bundle_id = ? AND is_current = 1",
                (root_id,),
            )
            version = self._next_version(connection, "character_tag", root_id)
            version_id = str(uuid7())
            connection.execute(
                """
                INSERT INTO character_tag_bundle_versions(
                    character_tag_bundle_version_id, character_tag_bundle_id, version,
                    storyboard_version_id, character_bible_version_id,
                    style_bible_version_id, provider_model_id, document_json,
                    provenance_json, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    version_id,
                    root_id,
                    version,
                    str(document.storyboard_version_id),
                    str(document.character_bible_version_id),
                    str(document.style_bible_version_id),
                    provider_model_id,
                    canonical_json(document.model_dump(mode="json")),
                    canonical_json(provenance),
                ),
            )
            self._audit(
                connection,
                project_id,
                "character_tags.version_created",
                {"version_id": version_id, "version": version},
            )
        return version_id

    def _persist_prompt_document(
        self,
        project_id: str,
        chapter_id: str,
        document: PromptBundleDocument,
        *,
        provenance: dict[str, Any],
    ) -> str:
        with self.database.writer() as connection:
            root_id = self._stable_root(connection, "prompt", project_id, chapter_id)
            connection.execute(
                "UPDATE prompt_bundle_versions SET is_current = 0 "
                "WHERE prompt_bundle_id = ? AND is_current = 1",
                (root_id,),
            )
            version = self._next_version(connection, "prompt", root_id)
            if any(
                package.structured_package is None
                or package.structured_package.version != version
                or package.structured_package.prompt_plan.version != version
                for package in document.packages
            ):
                raise ApplicationError(
                    "PROMPT_VERSION_CHANGED",
                    "PromptPackage 写入版本已变化，请重新编译后再保存。",
                    409,
                )
            version_id = str(uuid7())
            connection.execute(
                """
                INSERT INTO prompt_bundle_versions(
                    prompt_bundle_version_id, prompt_bundle_id, version,
                    storyboard_version_id, character_bible_version_id,
                    style_bible_version_id, character_tag_bundle_version_id,
                    text_model_profile_id, text_model_config_revision, text_model_name,
                    prompt_template_version, provider_model_id, document_json,
                    provenance_json, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    version_id,
                    root_id,
                    version,
                    str(document.storyboard_version_id),
                    str(document.character_bible_version_id),
                    str(document.style_bible_version_id),
                    str(document.character_tag_bundle_version_id),
                    document.text_model_profile_id,
                    document.text_model_config_revision,
                    document.text_model_name,
                    document.prompt_template_version,
                    document.provider_model_id,
                    canonical_json(document.model_dump(mode="json")),
                    canonical_json(provenance),
                ),
            )
            self._audit(
                connection,
                project_id,
                "prompt_bundle.version_created",
                {"version_id": version_id, "version": version},
            )
        self._register_prompt_lineage(project_id, version_id, version, document)
        return version_id

    def _tag_payload(self, project_id: str, row: sqlite3.Row) -> dict[str, Any]:
        fresh = self._tag_is_fresh(row)
        approved_at = row["approved_at"]
        return {
            "version_id": str(row["character_tag_bundle_version_id"]),
            "version": int(row["version"]),
            "document": CharacterTagBundleDocument.model_validate_json(
                str(row["document_json"])
            ).model_dump(mode="json"),
            "provenance": json.loads(str(row["provenance_json"])),
            "provider_model_id": str(row["provider_model_id"]),
            "approval_status": "stale" if not fresh else "approved" if approved_at else "draft",
            "approval_hash": str(row["approval_hash"]) if row["approval_hash"] else None,
            "approved_at": str(approved_at) if approved_at else None,
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
        }

    def _prompt_payload(self, project_id: str, row: sqlite3.Row) -> dict[str, Any]:
        fresh = self._prompt_is_fresh(row)
        approved_at = row["approved_at"]
        document = PromptBundleDocument.model_validate_json(str(row["document_json"]))
        compatibility = (
            {
                "kind": "prompt_plan_v2",
                "access": "read_write",
                "regeneration_required": False,
                "eligible_for_new_job": True,
            }
            if document.schema_version == "1.2"
            else {
                "kind": "legacy_flat_prompt",
                "access": "read_only",
                "regeneration_required": True,
                "eligible_for_new_job": False,
            }
        )
        return {
            "version_id": str(row["prompt_bundle_version_id"]),
            "version": int(row["version"]),
            "document": document.model_dump(mode="json"),
            "compatibility": compatibility,
            "provenance": json.loads(str(row["provenance_json"])),
            "approval_status": "stale" if not fresh else "approved" if approved_at else "draft",
            "approval_hash": str(row["approval_hash"]) if row["approval_hash"] else None,
            "snapshot_sha256": self._prompt_snapshot_sha256(row),
            "approved_at": str(approved_at) if approved_at else None,
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
        }

    def _current_tag_row(self, project_id: str, chapter_id: str) -> sqlite3.Row | None:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT v.*, b.project_id, b.chapter_id, a.approval_hash,
                       a.created_at AS approved_at
                FROM character_tag_bundles b
                JOIN character_tag_bundle_versions v
                  ON v.character_tag_bundle_id = b.character_tag_bundle_id
                LEFT JOIN character_tag_bundle_approvals a
                  ON a.character_tag_bundle_version_id = v.character_tag_bundle_version_id
                WHERE b.project_id = ? AND b.chapter_id = ? AND v.is_current = 1
                """,
                (project_id, chapter_id),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _current_prompt_row(self, project_id: str, chapter_id: str) -> sqlite3.Row | None:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT v.*, b.project_id, b.chapter_id, a.approval_hash,
                       a.created_at AS approved_at
                FROM prompt_bundles b
                JOIN prompt_bundle_versions v ON v.prompt_bundle_id = b.prompt_bundle_id
                LEFT JOIN prompt_bundle_approvals a
                  ON a.prompt_bundle_version_id = v.prompt_bundle_version_id
                WHERE b.project_id = ? AND b.chapter_id = ? AND v.is_current = 1
                """,
                (project_id, chapter_id),
            ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _tag_version_row(self, project_id: str, version_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT v.*, b.project_id, b.chapter_id, a.approval_hash,
                       a.created_at AS approved_at
                FROM character_tag_bundle_versions v
                JOIN character_tag_bundles b
                  ON b.character_tag_bundle_id = v.character_tag_bundle_id
                LEFT JOIN character_tag_bundle_approvals a
                  ON a.character_tag_bundle_version_id = v.character_tag_bundle_version_id
                WHERE b.project_id = ? AND v.character_tag_bundle_version_id = ?
                """,
                (project_id, version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "CHARACTER_TAG_VERSION_NOT_FOUND", "没有找到角色 tags 版本。", 404
            )
        return cast(sqlite3.Row, row)

    def _prompt_version_row(self, project_id: str, version_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT v.*, b.project_id, b.chapter_id, a.approval_hash,
                       a.created_at AS approved_at
                FROM prompt_bundle_versions v
                JOIN prompt_bundles b ON b.prompt_bundle_id = v.prompt_bundle_id
                LEFT JOIN prompt_bundle_approvals a
                  ON a.prompt_bundle_version_id = v.prompt_bundle_version_id
                WHERE b.project_id = ? AND v.prompt_bundle_version_id = ?
                """,
                (project_id, version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "PROMPT_BUNDLE_VERSION_NOT_FOUND", "没有找到 PromptPackage 版本。", 404
            )
        return cast(sqlite3.Row, row)

    def _tag_is_fresh(self, row: sqlite3.Row) -> bool:
        try:
            inputs = self._approved_inputs(str(row["project_id"]), str(row["chapter_id"]))
        except ApplicationError:
            return False
        return bool(row["is_current"]) and all(
            [
                str(row["storyboard_version_id"]) == inputs["storyboard_version_id"],
                str(row["character_bible_version_id"]) == inputs["character_version_id"],
                str(row["style_bible_version_id"]) == inputs["style_version_id"],
                str(row["provider_model_id"])
                == self._provider_model_id(str(row["project_id"]), required=False),
            ]
        )

    def _prompt_is_fresh(self, row: sqlite3.Row) -> bool:
        if not bool(row["is_current"]):
            return False
        if not self._tag_is_fresh(
            self._tag_version_row(
                str(row["project_id"]), str(row["character_tag_bundle_version_id"])
            )
        ):
            return False
        try:
            inputs = self._approved_inputs(str(row["project_id"]), str(row["chapter_id"]))
        except ApplicationError:
            return False
        try:
            current_layout = self._approved_layout_snapshot(
                str(row["project_id"]),
                str(row["chapter_id"]),
                inputs,
            )
        except ApplicationError:
            return False
        try:
            document = PromptBundleDocument.model_validate_json(str(row["document_json"]))
        except ValueError:
            return False
        if document.schema_version == "1.2":
            try:
                for package in document.packages:
                    if package.structured_package is None:
                        return False
                    require_prompt_package_integrity(package.structured_package)
            except PromptCompilationError:
                return False
        return all(
            [
                str(row["storyboard_version_id"]) == inputs["storyboard_version_id"],
                str(row["character_bible_version_id"]) == inputs["character_version_id"],
                str(row["style_bible_version_id"]) == inputs["style_version_id"],
                str(row["provider_model_id"])
                == self._provider_model_id(str(row["project_id"]), required=False),
                document.layout_snapshot_sha256 == current_layout.content_sha256,
            ]
        )

    def _require_tag_current_and_fresh(self, row: sqlite3.Row) -> None:
        if not self._tag_is_fresh(row):
            raise ApplicationError(
                "CHARACTER_TAGS_STALE",
                "角色 tags 的分镜、设定或 NovelAI 模型已经变化，请重新生成。",
                409,
            )

    def _require_prompt_current_and_fresh(self, row: sqlite3.Row) -> None:
        if not self._prompt_is_fresh(row):
            raise ApplicationError(
                "PROMPT_BUNDLE_STALE",
                "PromptPackage 的上游版本已经变化，请重新生成。",
                409,
            )

    def _assert_inputs_still_current(
        self, project_id: str, chapter_id: str, expected: dict[str, Any]
    ) -> None:
        current = self._approved_inputs(project_id, chapter_id)
        keys = ["storyboard_version_id", "character_version_id", "style_version_id"]
        if any(current[key] != expected[key] for key in keys):
            raise ApplicationError(
                "PROMPT_INPUTS_CHANGED",
                "文本模型处理期间分镜或设定发生变化，结果未写入。",
                409,
            )

    def _assert_tag_version_current(self, project_id: str, version_id: str) -> None:
        row = self._tag_version_row(project_id, version_id)
        if not bool(row["is_current"]) or row["approval_hash"] is None:
            raise ApplicationError(
                "CHARACTER_TAGS_CHANGED",
                "文本模型处理期间角色 tags 发生变化，结果未写入。",
                409,
            )

    def _provider_model_id(self, project_id: str, *, required: bool = True) -> str:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT provider_model_id FROM novelai_configs WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            if not required:
                return ""
            raise ApplicationError(
                "NOVELAI_CONFIGURATION_NOT_FOUND",
                "请先保存 NovelAI 配置，再生成适配该引擎的 tags 和 prompts。",
                409,
            )
        return str(row["provider_model_id"])

    def _target_tag_set_ids(
        self,
        project_id: str,
        chapter_id: str,
        characters: CharacterBibleDocument,
    ) -> dict[str, UUID]:
        current = self._current_tag_row(project_id, chapter_id)
        existing: dict[str, UUID] = {}
        if current is not None:
            document = CharacterTagBundleDocument.model_validate_json(
                str(current["document_json"])
            )
            existing = {str(item.character_id): item.tag_set_id for item in document.tag_sets}
        return {
            str(character.character_id): existing.get(str(character.character_id), uuid7())
            for character in characters.characters
        }

    def _target_prompt_ids(
        self,
        project_id: str,
        chapter_id: str,
        storyboard: StoryboardDocument,
    ) -> dict[str, UUID]:
        current = self._current_prompt_row(project_id, chapter_id)
        existing: dict[str, UUID] = {}
        if current is not None:
            document = PromptBundleDocument.model_validate_json(str(current["document_json"]))
            existing = {str(item.panel_id): item.prompt_package_id for item in document.packages}
        return {
            str(panel.panel_id): existing.get(str(panel.panel_id), uuid7())
            for page in storyboard.pages
            for panel in page.panels
        }

    def _next_prompt_version(self, project_id: str, chapter_id: str) -> int:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(pbv.version), 0) AS version
                FROM prompt_bundles pb
                LEFT JOIN prompt_bundle_versions pbv
                  ON pbv.prompt_bundle_id = pb.prompt_bundle_id
                WHERE pb.project_id = ? AND pb.chapter_id = ?
                """,
                (project_id, chapter_id),
            ).fetchone()
        return int(row["version"]) + 1 if row is not None else 1

    @staticmethod
    def _candidate_provenance(candidate: Any, config_revision: int) -> dict[str, Any]:
        return {
            "change_type": "model_generation",
            "provider": candidate.provider,
            "model": candidate.model,
            "endpoint_host": candidate.endpoint_host,
            "prompt_template_version": candidate.prompt_template_version,
            "response_sha256": candidate.response_sha256,
            "input_tokens": candidate.input_tokens,
            "output_tokens": candidate.output_tokens,
            "duration_ms": candidate.duration_ms,
            "repair_attempts": candidate.repair_attempts,
            "config_revision": config_revision,
        }

    @staticmethod
    def _stable_root(
        connection: sqlite3.Connection,
        kind: str,
        project_id: str,
        chapter_id: str,
    ) -> str:
        table = "character_tag_bundles" if kind == "character_tag" else "prompt_bundles"
        column = "character_tag_bundle_id" if kind == "character_tag" else "prompt_bundle_id"
        row = connection.execute(
            f"SELECT {column} FROM {table} WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id),
        ).fetchone()
        if row is not None:
            return str(row[column])
        root_id = str(uuid7())
        connection.execute(
            f"INSERT INTO {table}({column}, project_id, chapter_id) VALUES (?, ?, ?)",
            (root_id, project_id, chapter_id),
        )
        return root_id

    @staticmethod
    def _next_version(connection: sqlite3.Connection, kind: str, root_id: str) -> int:
        table = (
            "character_tag_bundle_versions" if kind == "character_tag" else "prompt_bundle_versions"
        )
        column = "character_tag_bundle_id" if kind == "character_tag" else "prompt_bundle_id"
        row = connection.execute(
            f"SELECT COALESCE(MAX(version), 0) AS version FROM {table} WHERE {column} = ?",
            (root_id,),
        ).fetchone()
        return int(row["version"]) + 1

    @staticmethod
    def _approval_hash(row: sqlite3.Row) -> str:
        payload = "|".join(
            [
                str(row["document_json"]),
                str(row["storyboard_version_id"]),
                str(row["character_bible_version_id"]),
                str(row["style_bible_version_id"]),
            ]
        )
        return _text_hash(payload)

    @staticmethod
    def _prompt_snapshot_sha256(row: sqlite3.Row) -> str:
        document = PromptBundleDocument.model_validate_json(str(row["document_json"]))
        return canonical_sha256(
            {
                "prompt_bundle_version_id": str(row["prompt_bundle_version_id"]),
                "version": int(row["version"]),
                "storyboard_version_id": str(row["storyboard_version_id"]),
                "character_bible_version_id": str(row["character_bible_version_id"]),
                "style_bible_version_id": str(row["style_bible_version_id"]),
                "character_tag_bundle_version_id": str(
                    row["character_tag_bundle_version_id"]
                ),
                "provider_model_id": str(row["provider_model_id"]),
                "layout_snapshot_sha256": document.layout_snapshot_sha256,
                "prompt_packages": [
                    {
                        "prompt_package_id": str(package.prompt_package_id),
                        "content_sha256": (
                            package.structured_package.content_sha256
                            if package.structured_package is not None
                            else package.compiled_prompt_sha256
                        ),
                        "prompt_plan_sha256": (
                            package.structured_package.prompt_plan_sha256
                            if package.structured_package is not None
                            else None
                        ),
                    }
                    for package in document.packages
                ],
            }
        )

    @staticmethod
    def _invalid_model_ids(message: str) -> None:
        raise ApplicationError("INVALID_MODEL_ARTIFACT_IDS", message, 422)

    @staticmethod
    def _confirmation_required() -> None:
        raise ApplicationError(
            "TEXT_MODEL_DATA_SEND_CONFIRMATION_REQUIRED",
            "请确认将当前已审批数据发送到文本模型后再继续。",
            422,
        )

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


def _tags_hash(tags: list[str]) -> str:
    return canonical_sha256(tags)


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _join_tags(tags: list[str]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalized = " ".join(tag.split())
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(normalized)
    return ", ".join(result)


def _legacy_flat_positive(package: Any) -> str:
    """Compatibility projection only; PromptPlan v2 remains the source of truth."""

    plan = package.prompt_plan
    parts = [*plan.style_tags, *plan.base.positive_tags]
    if plan.base.relationship_action is not None:
        parts.append(plan.base.relationship_action)
    parts.extend(plan.continuity_tags)
    for character in sorted(plan.characters, key=lambda item: item.order):
        parts.extend(character.fixed_tags)
        parts.extend(character.variable_positive_tags)
        parts.append(character.action)
    return _join_tags(parts)


def _legacy_flat_negative(package: Any) -> str:
    """Compatibility projection only; never used to reconstruct character blocks."""

    plan = package.prompt_plan
    return _join_tags(
        [
            *plan.base.negative_tags,
            *(tag for character in plan.characters for tag in character.negative_tags),
        ]
    )
