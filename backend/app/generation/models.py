from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class GenerationSpecDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0", "1.1"] = "1.0"
    spec_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    chapter_id: str = Field(min_length=1, max_length=64)
    job_id: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=64)
    attempt_id: str = Field(min_length=1, max_length=64)
    correlation_id: str = Field(min_length=1, max_length=100)
    panel_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    character_bible_version_id: str = Field(min_length=1, max_length=64)
    style_bible_version_id: str = Field(min_length=1, max_length=64)
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
    ] = (
        "approved_storyboard_and_bibles"
    )

    @model_validator(mode="after")
    def validate_revision_inputs(self) -> GenerationSpecDocument:
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
    provider_request: NovelAIImageRequest
