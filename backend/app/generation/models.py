from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

    schema_version: Literal["1.0"] = "1.0"
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
    action: Literal["generate"] = "generate"
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
    prompt_source: Literal["approved_storyboard_and_bibles"] = (
        "approved_storyboard_and_bibles"
    )


@dataclass(frozen=True, slots=True)
class CompiledGenerationSpec:
    document: GenerationSpecDocument
    provider_request: NovelAIImageRequest
