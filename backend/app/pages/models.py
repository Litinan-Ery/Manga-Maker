from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PAGE_WIDTH = 2048
PAGE_HEIGHT = 3072
STRIP_WIDTH = 1440
MAX_PAGE_WIDTH = 4096
MAX_PAGE_HEIGHT = 16_000
MAX_PAGE_PIXELS = 32_000_000


class PageContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PixelRect(PageContractModel):
    x: int = Field(ge=0, lt=MAX_PAGE_WIDTH)
    y: int = Field(ge=0, lt=MAX_PAGE_HEIGHT)
    width: int = Field(ge=64, le=MAX_PAGE_WIDTH)
    height: int = Field(ge=64, le=MAX_PAGE_HEIGHT)


class PanelPlacement(PageContractModel):
    panel_id: str = Field(min_length=1, max_length=64)
    asset_version_id: str = Field(min_length=1, max_length=64)
    frame: PixelRect
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1.0, ge=1, le=4)


class TextLayer(PageContractModel):
    layer_id: str = Field(min_length=1, max_length=64)
    panel_id: str | None = Field(default=None, max_length=64)
    kind: Literal["dialogue", "narration", "sfx"]
    text: str = Field(min_length=1, max_length=500)
    speaker: str | None = Field(default=None, max_length=80)
    bounds: PixelRect
    font_size: int = Field(default=48, ge=20, le=180)
    align: Literal["left", "center", "right"] = "center"

    @model_validator(mode="after")
    def safe_text(self) -> TextLayer:
        if any(ord(character) < 32 and character not in "\n\t" for character in self.text):
            raise ValueError("text layers cannot contain control characters")
        return self


class PageDocument(PageContractModel):
    schema_version: Literal["1.0", "2.0"] = "1.0"
    page_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1, le=10_000)
    width: int = Field(default=PAGE_WIDTH, ge=512, le=MAX_PAGE_WIDTH)
    height: int = Field(default=PAGE_HEIGHT, ge=512, le=MAX_PAGE_HEIGHT)
    reading_direction: Literal[
        "left_to_right", "right_to_left", "top_to_bottom"
    ] = "left_to_right"
    color_mode: Literal["grayscale", "color"] = "grayscale"
    background_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    language: Literal["zh-Hans"] = "zh-Hans"
    template_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    panels: list[PanelPlacement] = Field(min_length=1, max_length=6)
    text_layers: list[TextLayer] = Field(default_factory=list, max_length=200)
    show_page_number: bool = True

    @model_validator(mode="after")
    def unique_ids_and_valid_links(self) -> PageDocument:
        if self.width * self.height > MAX_PAGE_PIXELS:
            raise ValueError("page canvas exceeds the local rendering pixel limit")
        if self.schema_version == "1.0" and (
            self.width != PAGE_WIDTH
            or self.height != PAGE_HEIGHT
            or self.reading_direction != "left_to_right"
            or self.color_mode != "grayscale"
            or self.background_color.lower() != "#ffffff"
        ):
            raise ValueError("page schema 1.0 only supports the canonical monochrome profile")
        panel_ids = [panel.panel_id for panel in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("page panel ids must be unique")
        layer_ids = [layer.layer_id for layer in self.text_layers]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("page text layer ids must be unique")
        unknown_panel_ids = {
            layer.panel_id
            for layer in self.text_layers
            if layer.panel_id is not None and layer.panel_id not in set(panel_ids)
        }
        if unknown_panel_ids:
            raise ValueError("text layer references an unknown panel")
        for rectangle in [
            *(panel.frame for panel in self.panels),
            *(layer.bounds for layer in self.text_layers),
        ]:
            if (
                rectangle.x + rectangle.width > self.width
                or rectangle.y + rectangle.height > self.height
            ):
                raise ValueError("page rectangle must remain within the selected canvas")
        return self
