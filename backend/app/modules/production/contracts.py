from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..layout.contracts import NormalizedPoint

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ProductionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _validate_tags(values: list[str], field_name: str) -> None:
    if len(values) != len({value.casefold() for value in values}):
        raise ValueError(f"{field_name} cannot contain duplicate tags")
    if any(not value or "," in value or "\n" in value for value in values):
        raise ValueError(f"{field_name} must contain individual non-empty tags")


class ProviderCharacterCaption(ProductionContract):
    character_id: UUID
    order: int = Field(ge=0, le=19)
    center: NormalizedPoint
    positive_tags: list[str] = Field(min_length=1, max_length=120)
    negative_tags: list[str] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def valid_tags(self) -> ProviderCharacterCaption:
        _validate_tags(self.positive_tags, "positive character tags")
        _validate_tags(self.negative_tags, "negative character tags")
        return self


class ProviderExecutionSpec(ProductionContract):
    schema_version: Literal["1.0"] = "1.0"
    provider_execution_spec_id: UUID
    version: int = Field(ge=1)
    generation_spec_id: UUID
    provider: Literal["novelai"] = "novelai"
    action: Literal["generate", "infill"] = "generate"
    mapping_version: str = Field(min_length=1, max_length=100)
    contract_sha256: Sha256
    capability_snapshot_sha256: Sha256
    model_id: str = Field(min_length=1, max_length=100)
    prompt_plan_id: UUID
    prompt_plan_version: int = Field(ge=1)
    prompt_plan_sha256: Sha256
    page_layout_draft_id: UUID
    page_layout_draft_version: int = Field(ge=1)
    page_layout_draft_sha256: Sha256
    width: int = Field(ge=64, le=4096)
    height: int = Field(ge=64, le=4096)
    seed: int = Field(ge=0, le=4_294_967_295)
    base_positive_tags: list[str] = Field(min_length=1, max_length=200)
    base_negative_tags: list[str] = Field(min_length=1, max_length=200)
    character_captions: list[ProviderCharacterCaption] = Field(min_length=1, max_length=3)
    payload_sha256: Sha256

    @model_validator(mode="after")
    def valid_character_captions(self) -> ProviderExecutionSpec:
        _validate_tags(self.base_positive_tags, "base positive tags")
        _validate_tags(self.base_negative_tags, "base negative tags")
        character_ids = [caption.character_id for caption in self.character_captions]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("provider character captions must reference unique characters")
        orders = sorted(caption.order for caption in self.character_captions)
        if orders != list(range(len(self.character_captions))):
            raise ValueError("provider character caption order must be contiguous")
        if self.width * self.height > 16_777_216:
            raise ValueError("provider image dimensions exceed the contract pixel limit")
        return self
