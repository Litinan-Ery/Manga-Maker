from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceBeatInput(ContractModel):
    beat_id: str = Field(min_length=1, max_length=64)
    anchor_id: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(min_length=1, max_length=4000)


class StoryboardRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    chapter_id: str = Field(min_length=1, max_length=64)
    chapter_version: int = Field(ge=1)
    chapter_text: str = Field(min_length=1, max_length=2_000_000)
    story_beats: list[SourceBeatInput] = Field(min_length=1, max_length=2000)
    page_budget: int = Field(ge=1, le=64)
    reading_direction: Literal["left_to_right", "right_to_left"] = "left_to_right"
    language: Literal["zh-Hans"] = "zh-Hans"
    adaptation_preferences: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_source_ids(self) -> StoryboardRequest:
        beat_ids = [beat.beat_id for beat in self.story_beats]
        anchor_ids = [beat.anchor_id for beat in self.story_beats]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError("story beat ids must be unique")
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("source anchor ids must be unique")
        return self


class DialogueLine(ContractModel):
    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=200)


class PanelCandidate(ContractModel):
    panel_id: UUID
    order: int = Field(ge=1, le=12)
    purpose: str = Field(min_length=1, max_length=500)
    shot: str = Field(min_length=1, max_length=120)
    characters: list[str] = Field(default_factory=list, max_length=20)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=20)
    narration: list[str] = Field(default_factory=list, max_length=20)
    sfx: list[str] = Field(default_factory=list, max_length=20)
    visual_prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(min_length=1, max_length=2000)
    source_anchor_ids: list[str] = Field(min_length=1, max_length=50)


class PageCandidate(ContractModel):
    page_id: UUID
    page_number: int = Field(ge=1, le=64)
    turning_point: str = Field(min_length=1, max_length=500)
    panels: list[PanelCandidate] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def ordered_panels(self) -> PageCandidate:
        orders = [panel.order for panel in self.panels]
        if orders != list(range(1, len(self.panels) + 1)):
            raise ValueError("panel order must be contiguous and start at 1")
        return self


class BeatResolution(ContractModel):
    beat_id: str = Field(min_length=1, max_length=64)
    status: Literal["represented", "condensed", "omitted", "unresolved"]
    reason: str | None = Field(default=None, max_length=500)
    page_numbers: list[int] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def omission_has_reason(self) -> BeatResolution:
        if self.status == "omitted" and not self.reason:
            raise ValueError("omitted story beats require a reason")
        return self


class StoryboardDocument(ContractModel):
    schema_version: Literal["1.0"]
    storyboard_id: UUID
    chapter_version: int = Field(ge=1)
    beat_resolutions: list[BeatResolution] = Field(min_length=1, max_length=2000)
    pages: list[PageCandidate] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def ordered_pages(self) -> StoryboardDocument:
        page_numbers = [page.page_number for page in self.pages]
        if page_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("page numbers must be contiguous and start at 1")
        return self


def validate_storyboard_semantics(document: StoryboardDocument, request: StoryboardRequest) -> None:
    if document.chapter_version != request.chapter_version:
        raise ValueError("storyboard chapter version does not match the request")
    if len(document.pages) > request.page_budget:
        raise ValueError("storyboard exceeds the approved page budget")

    expected_beats = {beat.beat_id for beat in request.story_beats}
    resolutions = [resolution.beat_id for resolution in document.beat_resolutions]
    if len(resolutions) != len(set(resolutions)):
        raise ValueError("story beat resolutions contain duplicates")
    if set(resolutions) != expected_beats:
        raise ValueError("story beat resolutions must cover every requested beat exactly once")

    allowed_anchors = {beat.anchor_id for beat in request.story_beats}
    used_anchors = {
        anchor_id
        for page in document.pages
        for panel in page.panels
        for anchor_id in panel.source_anchor_ids
    }
    unknown_anchors = used_anchors - allowed_anchors
    if unknown_anchors:
        raise ValueError("storyboard contains source anchors that were not supplied")

    page_numbers = {page.page_number for page in document.pages}
    for resolution in document.beat_resolutions:
        if not set(resolution.page_numbers).issubset(page_numbers):
            raise ValueError("story beat resolution references an unknown page")
