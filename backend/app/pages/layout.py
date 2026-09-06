"""Deterministic composition from approved, hierarchical page geometry."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal
from uuid import UUID

from ..adaptation.models import PageCandidate
from ..errors import ApplicationError
from ..ids import uuid7
from ..modules.layout.public import FrameSpec, PageLayoutDraft
from .models import PageDocument, PageLayoutSource, PanelPlacement, PixelRect, TextLayer


def pixel_frames(layout: PageLayoutDraft) -> dict[UUID, PixelRect]:
    by_id = {frame.frame_id: frame for frame in layout.frames}
    absolute: dict[UUID, tuple[float, float, float, float]] = {}

    def resolve(frame_id: UUID) -> tuple[float, float, float, float]:
        if frame_id in absolute:
            return absolute[frame_id]
        frame = by_id[frame_id]
        r = frame.rect
        x, y, width, height = r.x, r.y, r.width, r.height
        if frame.parent_frame_id is not None:
            px, py, pw, ph = resolve(frame.parent_frame_id)
            x, y, width, height = px + x * pw, py + y * ph, width * pw, height * ph
        absolute[frame_id] = (x, y, width, height)
        return absolute[frame_id]

    result = {}
    for frame in layout.frames:
        if frame.panel_id is None:
            continue
        x, y, width, height = resolve(frame.frame_id)
        left, top = round(x * layout.canvas.width), round(y * layout.canvas.height)
        result[frame.frame_id] = PixelRect(
            x=left,
            y=top,
            width=round((x + width) * layout.canvas.width) - left,
            height=round((y + height) * layout.canvas.height) - top,
        )
    return result


def compose_layout_document(
    page: PageCandidate,
    storyboard_version_id: str,
    layout: PageLayoutDraft,
    source: PageLayoutSource,
    assets: dict[str, dict[str, str]],
    previous: PageDocument | None = None,
    *,
    include_text: bool = True,
) -> PageDocument:
    rectangles = pixel_frames(layout)
    leaves = sorted(
        (frame for frame in layout.frames if frame.panel_id is not None),
        key=lambda frame: frame.order or 0,
    )
    panels_by_id = {str(panel.panel_id): panel for panel in page.panels}
    placements: list[PanelPlacement] = []
    text_layers: list[TextLayer] = []
    previous_panels = {panel.panel_id: panel for panel in previous.panels} if previous else {}
    for frame in leaves:
        panel_id = str(frame.panel_id)
        rectangle = rectangles[frame.frame_id]
        prior = previous_panels.get(panel_id)
        placements.append(
            PanelPlacement(
                panel_id=panel_id,
                asset_version_id=(
                    prior.asset_version_id if prior else assets[panel_id]["asset_version_id"]
                ),
                frame=rectangle,
                focal_x=frame.focal_point.x,
                focal_y=frame.focal_point.y,
                zoom=prior.zoom if prior else 1.0,
            )
        )
        if not include_text:
            continue
        if previous is not None:
            layers = [layer for layer in previous.text_layers if layer.panel_id == panel_id]
        else:
            panel = panels_by_id[panel_id]
            contents: tuple[
                tuple[Literal["narration", "dialogue", "sfx"], str, str | None], ...
            ] = (
                ("narration", "\n".join(panel.narration), None),
                *(("dialogue", line.text, line.speaker) for line in panel.dialogue),
                ("sfx", "\n".join(panel.sfx), None),
            )
            layers = [
                TextLayer(
                    layer_id=str(uuid7()),
                    panel_id=panel_id,
                    kind=kind,
                    text=text[offset : offset + 500],
                    speaker=speaker,
                    bounds=rectangle,
                    font_size=64 if kind == "sfx" else 42,
                )
                for kind, text, speaker in contents
                if text.strip()
                for offset in range(0, len(text), 500)
            ]
        text_layers.extend(place_text_layers(layers, frame, rectangle))
    if previous:
        text_layers.extend(layer for layer in previous.text_layers if layer.panel_id is None)
    direction: Literal["left_to_right", "right_to_left", "top_to_bottom"] = {
        "ltr_ttb": "left_to_right",
        "rtl_ttb": "right_to_left",
        "ttb": "top_to_bottom",
    }[layout.reading_direction]  # type: ignore[assignment]
    return PageDocument(
        schema_version="2.0",
        page_id=str(page.page_id),
        page_number=page.page_number,
        width=layout.canvas.width,
        height=layout.canvas.height,
        reading_direction=direction,
        template_id="approved-layout",
        storyboard_version_id=storyboard_version_id,
        layout_source=source,
        panels=placements,
        text_layers=text_layers,
        color_mode=previous.color_mode if previous else "grayscale",
        background_color=previous.background_color if previous else "#ffffff",
        show_page_number=(
            previous.show_page_number if previous else layout.reading_direction != "ttb"
        ),
    )


def place_text_layers(
    layers: list[TextLayer], frame: FrameSpec, bounds: PixelRect
) -> list[TextLayer]:
    if not layers:
        return []
    if not frame.text_safe_zones:
        available = bounds.height - 56 - 16 * (len(layers) - 1)
        heights = [
            min(180 if layer.kind == "sfx" else 240, available // len(layers)) for layer in layers
        ]
        if min(heights) < 64 or bounds.width < 112:
            _text_space_error()
        cursor = bounds.y + 28
        result = []
        for index, layer in enumerate(layers):
            height = heights[index]
            y = (
                bounds.y + bounds.height - 28 - height
                if layer.kind == "sfx" and index == len(layers) - 1
                else cursor
            )
            rect = PixelRect(x=bounds.x + 24, y=y, width=bounds.width - 48, height=height)
            result.append(layer.model_copy(update={"bounds": rect}))
            cursor = y + height + 16
        return result
    groups: dict[int, list[TextLayer]] = defaultdict(list)
    for layer in layers:
        eligible = [i for i, zone in enumerate(frame.text_safe_zones) if zone.kind == layer.kind]
        if not eligible:
            eligible = [i for i, zone in enumerate(frame.text_safe_zones) if zone.kind == "any"]
        if not eligible:
            _text_space_error()
        groups[min(eligible, key=lambda index: len(groups[index]))].append(layer)
    result = []
    for index, grouped in groups.items():
        zone = frame.text_safe_zones[index].rect
        x, y = bounds.x + round(zone.x * bounds.width), bounds.y + round(zone.y * bounds.height)
        width, height = round(zone.width * bounds.width), round(zone.height * bounds.height)
        part_height = (height - 8 * (len(grouped) - 1)) // len(grouped)
        if width < 64 or part_height < 64:
            _text_space_error()
        for offset, layer in enumerate(grouped):
            rectangle = PixelRect(
                x=x, y=y + offset * (part_height + 8), width=width, height=part_height
            )
            if any(_overlap(rectangle, placed.bounds) for placed in result):
                _text_space_error()
            result.append(layer.model_copy(update={"bounds": rectangle}))
    return result


def _overlap(a: PixelRect, b: PixelRect) -> bool:
    return (
        a.x < b.x + b.width
        and b.x < a.x + a.width
        and a.y < b.y + b.height
        and b.y < a.y + a.height
    )


def _text_space_error() -> None:
    raise ApplicationError(
        "PAGE_TEXT_SAFE_ZONE_INSUFFICIENT",
        "批准版式的文字安全区不足或重叠，请调整版式后重新批准。",
        422,
    )
