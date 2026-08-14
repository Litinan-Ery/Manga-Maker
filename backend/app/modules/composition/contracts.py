from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompositionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class PixelRectV1(CompositionContract):
    x: int = Field(ge=0, lt=4096)
    y: int = Field(ge=0, lt=16_000)
    width: int = Field(ge=64, le=4096)
    height: int = Field(ge=64, le=16_000)


class PanelPlacementV1(CompositionContract):
    panel_id: str = Field(min_length=1, max_length=64)
    asset_version_id: str = Field(min_length=1, max_length=64)
    frame: PixelRectV1
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=4)


class TextLayerV1(CompositionContract):
    layer_id: str = Field(min_length=1, max_length=64)
    panel_id: str | None = Field(default=None, max_length=64)
    kind: Literal["dialogue", "narration", "sfx"]
    text: str = Field(min_length=1, max_length=500)
    speaker: str | None = Field(default=None, max_length=80)
    bounds: PixelRectV1
    font_size: int = Field(default=48, ge=20, le=180)
    align: Literal["left", "center", "right"] = "center"


class PageDocumentSnapshotV1(CompositionContract):
    schema_version: Literal["1.0", "2.0"]
    page_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1, le=10_000)
    width: int = Field(ge=512, le=4096)
    height: int = Field(ge=512, le=16_000)
    reading_direction: Literal["left_to_right", "right_to_left", "top_to_bottom"]
    color_mode: Literal["grayscale", "color"]
    background_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    language: Literal["zh-Hans"]
    template_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    panels: tuple[PanelPlacementV1, ...] = Field(min_length=1, max_length=6)
    text_layers: tuple[TextLayerV1, ...] = Field(default=(), max_length=200)
    show_page_number: bool

    @model_validator(mode="after")
    def valid_links(self) -> PageDocumentSnapshotV1:
        panel_ids = [panel.panel_id for panel in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("page panel ids must be unique")
        layer_ids = [layer.layer_id for layer in self.text_layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("page text layer ids must be unique")
        if any(
            layer.panel_id is not None and layer.panel_id not in set(panel_ids)
            for layer in self.text_layers
        ):
            raise ValueError("text layer references an unknown panel")
        return self


class CreatePageRevisionCommandV1(CompositionContract):
    contract_version: Literal["1.0"] = "1.0"
    project_id: str = Field(min_length=1, max_length=64)
    page_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    document: PageDocumentSnapshotV1


class PageVersionSnapshotV1(CompositionContract):
    contract_version: Literal["1.0"] = "1.0"
    page_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    chapter_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1)
    page_revision: int = Field(ge=1)
    page_version_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    parent_page_version_id: str | None = Field(default=None, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_version: str = Field(min_length=1, max_length=100)
    font_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_current: bool
    created_at: str = Field(min_length=1, max_length=100)
    source_job_id: str | None = Field(default=None, max_length=64)
    document: PageDocumentSnapshotV1
    external_requests_started: Literal[0] = 0

    def legacy_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("contract_version")
        return payload
