from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..modules.production.adapters.novelai import NovelAIV4Payload
from ..modules.production.contracts import ProviderExecutionSpec
from ..novelai.client import NovelAIImageRequest


class ReferenceUse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_asset_id: str = Field(min_length=1, max_length=64)
    original_sha256: str = Field(min_length=64, max_length=64)
    prepared_sha256: str = Field(min_length=64, max_length=64)
    description: Literal["character", "style", "character&style"]
    strength: float = Field(ge=0, le=1)
    fidelity: float = Field(ge=0, le=1)
    prepared_width: int = Field(gt=0)
    prepared_height: int = Field(gt=0)


class CharacterTagSetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: str = Field(min_length=1, max_length=64)
    character_tag_set_version_id: str = Field(min_length=1, max_length=64)
    fixed_tags_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationSpecDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4"] = "1.0"
    spec_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    chapter_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=64)
    attempt_id: str = Field(min_length=1, max_length=64)
    correlation_id: str = Field(pattern=r"^[A-Za-z0-9]{6}$")
    panel_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    character_bible_version_id: str = Field(min_length=1, max_length=64)
    style_bible_version_id: str = Field(min_length=1, max_length=64)
    character_tag_bundle_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    prompt_bundle_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    prompt_package_id: str | None = Field(default=None, min_length=1, max_length=64)
    text_model_config_revision: int | None = Field(default=None, ge=1)
    compiled_prompt_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    compiled_negative_prompt_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    generation_approval_id: str | None = Field(default=None, min_length=1, max_length=64)
    generation_approval_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_approval_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_plan_id: str | None = Field(default=None, min_length=1, max_length=64)
    prompt_plan_version: int | None = Field(default=None, ge=1)
    prompt_plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_package_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    character_tag_set_refs: list[CharacterTagSetRef] = Field(default_factory=list, max_length=3)
    approved_provider_execution_spec_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    provider_payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_count: int | None = Field(default=None, ge=1, le=16)
    quality_rule_version: str | None = Field(default=None, min_length=1, max_length=100)
    layout_snapshot_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    page_layout_draft_id: str | None = Field(default=None, min_length=1, max_length=64)
    page_layout_draft_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    layout_version: int | None = Field(default=None, ge=1)
    layout_content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    layout_approval_id: str | None = Field(default=None, min_length=1, max_length=64)
    layout_approval_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    frame_id: str | None = Field(default=None, min_length=1, max_length=64)
    frame_content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    dimension_selection_id: str | None = Field(default=None, min_length=1, max_length=64)
    dimension_selection_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    expected_crop_ratio: float | None = Field(default=None, ge=0, le=1)
    dimension_rule_version: str | None = Field(default=None, min_length=1, max_length=64)
    capability_snapshot_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    provider: Literal["novelai"] = "novelai"
    provider_model_id: str = Field(min_length=1, max_length=100)
    mapping_version: str = Field(min_length=1, max_length=100)
    contract_sha256: str = Field(min_length=64, max_length=64)
    action: Literal["generate", "reroll", "inpaint"] = "generate"
    prompt: str = Field(min_length=1, max_length=12_000)
    negative_prompt: str = Field(min_length=1, max_length=12_000)
    width: int = Field(ge=64, le=2048)
    height: int = Field(ge=64, le=2048)
    steps: int = Field(ge=1, le=50)
    scale: float = Field(ge=0, le=10)
    sampler: str = Field(min_length=1, max_length=100)
    noise_schedule: str = Field(min_length=1, max_length=100)
    seed: int = Field(ge=0, le=4_294_967_287)
    references: list[ReferenceUse] = Field(default_factory=list, max_length=1)
    parent_asset_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    parent_image_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    mask_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    mask_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    edit_prompt: str | None = Field(default=None, min_length=1, max_length=2_000)
    inpaint_strength: float | None = Field(default=None, ge=0.1, le=1)
    prompt_source: Literal[
        "approved_storyboard_and_bibles",
        "approved_storyboard_and_bibles_plus_user_edit",
        "approved_prompt_package",
        "approved_prompt_package_plus_user_edit",
    ] = "approved_storyboard_and_bibles"

    @model_validator(mode="after")
    def validate_revision_inputs(self) -> GenerationSpecDocument:
        prompt_package_fields = (
            self.character_tag_bundle_version_id,
            self.prompt_bundle_version_id,
            self.prompt_package_id,
            self.text_model_config_revision,
            self.compiled_prompt_sha256,
            self.compiled_negative_prompt_sha256,
        )
        if self.schema_version in {"1.2", "1.3", "1.4"} and not all(
            value is not None for value in prompt_package_fields
        ):
            raise ValueError("schema 1.2 requires frozen PromptPackage provenance")
        layout_fields = (
            self.layout_snapshot_sha256,
            self.page_layout_draft_id,
            self.page_layout_draft_version_id,
            self.layout_version,
            self.layout_content_sha256,
            self.layout_approval_id,
            self.layout_approval_sha256,
            self.frame_id,
            self.frame_content_sha256,
            self.dimension_selection_id,
            self.dimension_selection_sha256,
            self.expected_crop_ratio,
            self.dimension_rule_version,
            self.capability_snapshot_sha256,
        )
        if self.schema_version in {"1.3", "1.4"} and any(
            value is None for value in layout_fields
        ):
            raise ValueError("schema 1.3 requires frozen approved layout provenance")
        approval_fields = (
            self.generation_approval_id,
            self.generation_approval_sha256,
            self.prompt_approval_hash,
            self.prompt_snapshot_sha256,
            self.prompt_plan_id,
            self.prompt_plan_version,
            self.prompt_plan_sha256,
            self.prompt_package_sha256,
            self.approved_provider_execution_spec_sha256,
            self.provider_payload_sha256,
            self.candidate_count,
            self.quality_rule_version,
        )
        if self.schema_version == "1.4" and (
            any(value is None for value in approval_fields)
            or not self.character_tag_set_refs
        ):
            raise ValueError("schema 1.4 requires the complete GenerationApproval freeze")
        parent_fields = (self.parent_asset_version_id, self.parent_image_sha256)
        if self.action == "generate":
            if any(value is not None for value in parent_fields):
                raise ValueError("initial generation cannot include a parent asset")
        elif not all(value is not None for value in parent_fields):
            raise ValueError("revision generation requires a frozen parent asset")
        inpaint_fields = (
            self.mask_asset_id,
            self.mask_sha256,
            self.edit_prompt,
            self.inpaint_strength,
        )
        if self.action == "inpaint":
            if not all(value is not None for value in inpaint_fields):
                raise ValueError("inpaint requires mask, edit prompt, and strength")
            if self.references:
                raise ValueError("P0 inpaint does not combine precise references")
        elif any(value is not None for value in inpaint_fields):
            raise ValueError("non-inpaint generation cannot include mask inputs")
        return self


@dataclass(frozen=True, slots=True)
class CompiledGenerationSpec:
    document: GenerationSpecDocument
    provider_execution_spec: ProviderExecutionSpec
    provider_payload: NovelAIV4Payload
    provider_request: NovelAIImageRequest
