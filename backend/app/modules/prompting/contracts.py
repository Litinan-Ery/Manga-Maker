from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..layout.contracts import NormalizedPoint, NormalizedRect

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PromptingContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _validate_tags(values: list[str], field_name: str) -> None:
    if len(values) != len({value.casefold() for value in values}):
        raise ValueError(f"{field_name} cannot contain duplicate tags")
    if any(not value or "," in value or "\n" in value for value in values):
        raise ValueError(f"{field_name} must contain individual non-empty tags")


class PromptBase(PromptingContract):
    positive_tags: list[str] = Field(min_length=1, max_length=100)
    negative_tags: list[str] = Field(default_factory=list, max_length=100)
    relationship_action: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid_tags(self) -> PromptBase:
        _validate_tags(self.positive_tags, "base positive tags")
        _validate_tags(self.negative_tags, "base negative tags")
        return self


class PromptCharacter(PromptingContract):
    character_id: UUID
    character_tag_set_version_id: UUID
    fixed_tags: list[str] = Field(min_length=1, max_length=60)
    fixed_tags_sha256: Sha256
    variable_positive_tags: list[str] = Field(default_factory=list, max_length=60)
    negative_tags: list[str] = Field(default_factory=list, max_length=60)
    action: str = Field(min_length=1, max_length=500)
    order: int = Field(ge=0, le=19)
    center: NormalizedPoint

    @model_validator(mode="after")
    def valid_tags(self) -> PromptCharacter:
        _validate_tags(self.fixed_tags, "fixed tags")
        _validate_tags(self.variable_positive_tags, "variable positive tags")
        _validate_tags(self.negative_tags, "character negative tags")
        return self


class LayoutConstraints(PromptingContract):
    page_layout_draft_id: UUID
    page_layout_draft_version: int = Field(ge=1)
    frame_id: UUID
    frame_sha256: Sha256
    aspect_ratio: float = Field(gt=0, le=10)
    focal_point: NormalizedPoint
    reserved_text_zones: list[NormalizedRect] = Field(default_factory=list, max_length=20)
    crop_safe_rect: NormalizedRect


class PromptPlan(PromptingContract):
    schema_version: Literal["2.0"] = "2.0"
    prompt_plan_id: UUID
    version: int = Field(ge=1)
    panel_id: UUID
    base: PromptBase
    characters: list[PromptCharacter] = Field(min_length=1, max_length=3)
    style_tags: list[str] = Field(min_length=1, max_length=100)
    continuity_tags: list[str] = Field(default_factory=list, max_length=100)
    layout_constraints: LayoutConstraints
    content_sha256: Sha256

    @model_validator(mode="after")
    def valid_character_structure(self) -> PromptPlan:
        _validate_tags(self.style_tags, "style tags")
        _validate_tags(self.continuity_tags, "continuity tags")
        character_ids = [character.character_id for character in self.characters]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("each prompt character must appear exactly once")
        orders = sorted(character.order for character in self.characters)
        if orders != list(range(len(self.characters))):
            raise ValueError("prompt character order must be contiguous and start at zero")
        if len(self.characters) > 1 and self.base.relationship_action is None:
            raise ValueError("multi-character prompts require a relationship action")
        return self


class TextModelSource(PromptingContract):
    text_model_profile_id: UUID
    profile_version: int = Field(ge=1)
    model_name: str = Field(min_length=1, max_length=200)
    prompt_template_version: str = Field(min_length=1, max_length=100)
    text_stage_run_id: UUID


class PromptPackage(PromptingContract):
    schema_version: Literal["2.0"] = "2.0"
    prompt_package_id: UUID
    version: int = Field(ge=1)
    panel_id: UUID
    text_model_source: TextModelSource
    prompt_plan: PromptPlan
    prompt_plan_sha256: Sha256
    content_sha256: Sha256
    approved_content_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def references_same_panel(self) -> PromptPackage:
        if self.panel_id != self.prompt_plan.panel_id:
            raise ValueError("prompt package and prompt plan must reference the same panel")
        if self.prompt_plan_sha256 != self.prompt_plan.content_sha256:
            raise ValueError("prompt_plan_sha256 must match the embedded prompt plan")
        return self
