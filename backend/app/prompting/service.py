from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, cast
from uuid import UUID

from ..adaptation.models import StoryboardDocument
from ..adaptation.service import AdaptationService, canonical_json
from ..bibles.models import CharacterBibleDocument, StyleBibleDocument
from ..bibles.service import BibleService
from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from .models import (
    CharacterTagBundleDocument,
    CharacterTagGenerationRequest,
    CharacterTagSetDocument,
    PromptBundleDocument,
    PromptCharacterBlock,
    PromptDraftBundleDocument,
    PromptGenerationRequest,
    PromptPackageDocument,
)

UNIVERSAL_NEGATIVE_TAGS = [
    "color",
    "text",
    "letters",
    "speech bubble",
    "caption",
    "page number",
    "watermark",
    "logo",
]
UNIVERSAL_POSITIVE_TAGS = ["no text", "no letters", "no speech bubbles", "no watermark"]


class PromptingService:
    def __init__(
        self,
        database: Database,
        adaptation: AdaptationService,
        bibles: BibleService,
    ) -> None:
        self.database = database
        self.adaptation = adaptation
        self.bibles = bibles

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
        document = self._compile_prompt_bundle(
            candidate.document,
            request,
            text_model_profile_id=project_id,
            text_model_config_revision=config_revision,
            text_model_name=configuration.model,
            prompt_template_version=candidate.prompt_template_version,
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
        )
        document = self._compile_prompt_bundle(
            draft,
            request,
            text_model_profile_id=str(row["text_model_profile_id"]),
            text_model_config_revision=int(row["text_model_config_revision"]),
            text_model_name=str(row["text_model_name"]),
            prompt_template_version=str(row["prompt_template_version"]),
        )
        next_id = self._persist_prompt_document(
            project_id,
            chapter_id,
            document,
            provenance={"change_type": "manual_edit", "parent_version_id": version_id},
        )
        return self.get_prompt_version(project_id, next_id)

    def approve_prompt_bundle(self, project_id: str, version_id: str) -> dict[str, Any]:
        row = self._prompt_version_row(project_id, version_id)
        self._require_prompt_current_and_fresh(row)
        approval_hash = self._approval_hash(row)
        with self.database.writer() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO prompt_bundle_approvals(
                    approval_id, prompt_bundle_version_id, approval_hash
                ) VALUES (?, ?, ?)
                """,
                (str(uuid7()), version_id, approval_hash),
            )
            self._audit(
                connection,
                project_id,
                "prompt_bundle.approved",
                {"version_id": version_id, "approval_hash": approval_hash},
            )
        return self.get_prompt_version(project_id, version_id)

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
        if tag_payload is None or tag_payload["approval_status"] != "approved":
            blockers.append("角色固定 tags 尚未批准或已经失效。")
        if prompt_payload is None or prompt_payload["approval_status"] != "approved":
            blockers.append("逐格 PromptPackage 尚未批准或已经失效。")
        return {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "character_tags": tag_payload,
            "prompt_bundle": prompt_payload,
            "generation_readiness": {
                "ready": not blockers,
                "blockers": blockers,
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
        compiled: list[PromptPackageDocument] = []
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
            expected_characters = {
                aliases[name.casefold()] for name in panel.characters
            }
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
            compiled_blocks: list[PromptCharacterBlock] = []
            negative_parts = list(package.negative_tags)
            for character_id in sorted(expected_characters):
                source_block = blocks[character_id]
                tag_set = tags_by_character[character_id]
                if source_block.tag_set_id != tag_set.tag_set_id:
                    self._invalid_model_ids("PromptPackage 的 tag set 标识不一致。")
                fixed = {tag.casefold() for tag in tag_set.fixed_tags}
                variable = {tag.casefold() for tag in source_block.variable_tags}
                if fixed & variable:
                    raise ApplicationError(
                        "PROMPT_FIXED_VARIABLE_TAG_CONFLICT",
                        "逐格可变 tags 不能重复或覆盖角色固定 tags。",
                        422,
                        {
                            "panel_id": panel_id,
                            "character_id": character_id,
                            "conflicts": sorted(fixed & variable),
                        },
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
                negative_parts.extend(tag_set.negative_tags)
            prompt_parts = list(package.base_visual_tags)
            for block in compiled_blocks:
                prompt_parts.extend(block.fixed_tags)
                prompt_parts.extend(block.variable_tags)
            prompt_parts.extend(package.style_tags)
            prompt_parts.extend(UNIVERSAL_POSITIVE_TAGS)
            prompt = _join_tags(prompt_parts)
            negative = _join_tags([*negative_parts, *UNIVERSAL_NEGATIVE_TAGS])
            compiled.append(
                PromptPackageDocument(
                    **package.model_dump(exclude={"character_blocks"}),
                    character_blocks=compiled_blocks,
                    compiled_prompt=prompt,
                    compiled_negative_prompt=negative,
                    compiled_prompt_sha256=_text_hash(prompt),
                    compiled_negative_prompt_sha256=_text_hash(negative),
                )
            )
        return PromptBundleDocument(
            schema_version="1.0",
            storyboard_version_id=request.storyboard_version_id,
            character_bible_version_id=request.character_bible_version_id,
            style_bible_version_id=request.style_bible_version_id,
            character_tag_bundle_version_id=request.character_tag_bundle_version_id,
            text_model_profile_id=text_model_profile_id,
            text_model_config_revision=text_model_config_revision,
            text_model_name=text_model_name,
            prompt_template_version=prompt_template_version,
            provider_model_id=request.provider_model_id,
            packages=compiled,
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
        return {
            "version_id": str(row["prompt_bundle_version_id"]),
            "version": int(row["version"]),
            "document": PromptBundleDocument.model_validate_json(
                str(row["document_json"])
            ).model_dump(mode="json"),
            "provenance": json.loads(str(row["provenance_json"])),
            "approval_status": "stale" if not fresh else "approved" if approved_at else "draft",
            "approval_hash": str(row["approval_hash"]) if row["approval_hash"] else None,
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
        return all(
            [
                str(row["storyboard_version_id"]) == inputs["storyboard_version_id"],
                str(row["character_bible_version_id"]) == inputs["character_version_id"],
                str(row["style_bible_version_id"]) == inputs["style_version_id"],
                str(row["provider_model_id"])
                == self._provider_model_id(str(row["project_id"]), required=False),
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
    return _text_hash(canonical_json(tags))


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
