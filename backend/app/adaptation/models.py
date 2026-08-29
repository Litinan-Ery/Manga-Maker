from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PageType = Literal["standard", "cover", "splash", "special"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceBeatInput(ContractModel):
    beat_id: str = Field(min_length=1, max_length=64)
    anchor_id: str = Field(min_length=1, max_length=64)
    excerpt: str = Field(min_length=1, max_length=4000)


class StoryboardRequest(ContractModel):
    schema_version: Literal["1.1"] = "1.1"
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


class SceneCandidate(ContractModel):
    scene_id: UUID
    order: int = Field(ge=1, le=200)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    time_of_day: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=1000)
    beat_ids: list[str] = Field(min_length=1, max_length=2000)


class PageCandidate(ContractModel):
    page_id: UUID
    page_number: int = Field(ge=1, le=64)
    page_type: PageType | None = None
    turning_point: str = Field(min_length=1, max_length=500)
    scene_ids: list[UUID] = Field(min_length=1, max_length=20)
    panels: list[PanelCandidate] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def ordered_panels(self) -> PageCandidate:
        if len(self.scene_ids) != len(set(self.scene_ids)):
            raise ValueError("page scene ids must be unique")
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
    schema_version: Literal["1.0", "1.1"]
    storyboard_id: UUID
    chapter_version: int = Field(ge=1)
    beat_resolutions: list[BeatResolution] = Field(min_length=1, max_length=2000)
    scenes: list[SceneCandidate] = Field(min_length=1, max_length=200)
    pages: list[PageCandidate] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def ordered_pages(self) -> StoryboardDocument:
        scene_orders = [scene.order for scene in self.scenes]
        if scene_orders != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene order must be contiguous and start at 1")
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene ids must be unique")
        page_numbers = [page.page_number for page in self.pages]
        if page_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("page numbers must be contiguous and start at 1")
        page_ids = [page.page_id for page in self.pages]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("page ids must be unique")
        panel_ids = [panel.panel_id for page in self.pages for panel in page.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("panel ids must be unique")
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

    resolution_by_beat = {
        resolution.beat_id: resolution.status for resolution in document.beat_resolutions
    }
    scene_beats = [beat_id for scene in document.scenes for beat_id in scene.beat_ids]
    if len(scene_beats) != len(set(scene_beats)):
        raise ValueError("story beats must not be assigned to multiple scenes")
    if set(scene_beats) - expected_beats:
        raise ValueError("scenes contain story beats that were not supplied")
    required_scene_beats = {
        beat_id for beat_id, status in resolution_by_beat.items() if status != "omitted"
    }
    if set(scene_beats) != required_scene_beats:
        raise ValueError("every non-omitted story beat must be assigned to exactly one scene")

    scene_ids = {scene.scene_id for scene in document.scenes}
    referenced_scene_ids = {scene_id for page in document.pages for scene_id in page.scene_ids}
    if referenced_scene_ids != scene_ids:
        raise ValueError(
            "every scene must be referenced by a page and unknown scenes are forbidden"
        )

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
        if len(resolution.page_numbers) != len(set(resolution.page_numbers)):
            raise ValueError("story beat resolution page numbers must be unique")
        if not set(resolution.page_numbers).issubset(page_numbers):
            raise ValueError("story beat resolution references an unknown page")
    for page in document.pages:
        for panel in page.panels:
            if len(panel.source_anchor_ids) != len(set(panel.source_anchor_ids)):
                raise ValueError("panel source anchor ids must be unique")
