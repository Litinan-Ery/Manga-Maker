from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PAGE_WIDTH = 2048
PAGE_HEIGHT = 3072


class PageContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PixelRect(PageContractModel):
    x: int = Field(ge=0, lt=PAGE_WIDTH)
    y: int = Field(ge=0, lt=PAGE_HEIGHT)
    width: int = Field(ge=64, le=PAGE_WIDTH)
    height: int = Field(ge=64, le=PAGE_HEIGHT)

    @model_validator(mode="after")
    def within_page(self) -> PixelRect:
        if self.x + self.width > PAGE_WIDTH or self.y + self.height > PAGE_HEIGHT:
            raise ValueError("rectangle must remain within the canonical page")
        return self


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
    schema_version: Literal["1.0"] = "1.0"
    page_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1, le=10_000)
    width: Literal[2048] = 2048
    height: Literal[3072] = 3072
    reading_direction: Literal["left_to_right"] = "left_to_right"
    language: Literal["zh-Hans"] = "zh-Hans"
    template_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    panels: list[PanelPlacement] = Field(min_length=1, max_length=6)
    text_layers: list[TextLayer] = Field(default_factory=list, max_length=200)
    show_page_number: bool = True

    @model_validator(mode="after")
    def unique_ids_and_valid_links(self) -> PageDocument:
        panel_ids = [panel.panel_id for panel in self.panels]
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("page panel ids must be unique")
        asset_ids = [panel.asset_version_id for panel in self.panels]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("page asset version ids must be unique")
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
        return self
