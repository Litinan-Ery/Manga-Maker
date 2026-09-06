"""Conservative rectangular-grid detection for local lettering of generated pages."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ..pages.models import PageDocument, PixelRect
from ..pages.renderer import (
    default_font_path,
    font_line_height,
    load_font,
    safe_open_rgb,
    wrap_text,
)


def spans(active: list[bool], minimum: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate([*active, False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= minimum:
                ranges.append((start, index))
            start = None
    return ranges


def detected_frames(path: Path, count: int, rtl: bool) -> list[tuple[int, int, int, int]] | None:
    image = safe_open_rgb(path).convert("L")
    width, height = image.size
    pixels = list(image.getdata())
    ink = [[p < 140 for p in pixels[y * width : (y + 1) * width]] for y in range(height)]
    rows = spans([sum(row) >= 3 for row in ink], max(64, height // 10))
    found = []
    for top, bottom in rows:
        columns = spans(
            [sum(row[x] for row in ink[top:bottom]) >= 3 for x in range(width)], max(64, width // 8)
        )
        if rtl:
            columns.reverse()
        for left, right in columns:
            if min(left, top) < 2 or right >= width - 1 or bottom >= height - 1:
                return None
            # Content blobs are not frames: all four sides need a near-continuous dark border.
            vertical = [
                sum(any(row[max(0, edge - 2) : min(width, edge + 3)]) for row in ink[top:bottom])
                / (bottom - top)
                for edge in (left, right - 1)
            ]
            horizontal = [
                sum(
                    any(ink[y][x] for y in range(max(0, edge - 2), min(height, edge + 3)))
                    for x in range(left, right)
                )
                / (right - left)
                for edge in (top, bottom - 1)
            ]
            if min(*vertical, *horizontal) < 0.88:
                return None
            found.append((left, top, right - left, bottom - top))
    return found if len(found) == count else None


def calibrate_lettering(document: PageDocument, path: Path) -> PageDocument:
    if document.page_image is None or document.page_image.text_policy != "local":
        return document
    detected = detected_frames(
        path, len(document.panels), document.reading_direction == "right_to_left"
    )
    old = {p.panel_id: p.frame for p in document.panels}
    actual = old
    if detected is not None:
        with Image.open(path) as image:
            sx, sy = document.width / image.width, document.height / image.height
        rects = [
            PixelRect(x=round(x * sx), y=round(y * sy), width=round(w * sx), height=round(h * sy))
            for x, y, w, h in detected
        ]
        candidate = {p.panel_id: r for p, r in zip(document.panels, rects, strict=True)}
        # Uncertain geometry stays unchanged, but captions can still fit their existing safe zone.
        if all(
            abs((old[key].x + old[key].width / 2) - (r.x + r.width / 2)) <= document.width * 0.2
            and abs((old[key].y + old[key].height / 2) - (r.y + r.height / 2))
            <= document.height * 0.2
            for key, r in candidate.items()
        ):
            actual = candidate
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    layers = []
    for layer in document.text_layers:
        if layer.panel_id is None:
            layers.append(layer)
            continue
        before, after = old[layer.panel_id], actual[layer.panel_id]
        b = layer.bounds
        # Free-standing editorial text is not constrained by a panel's original safe zone.
        if (
            b.x < before.x
            or b.y < before.y
            or b.x + b.width > before.x + before.width
            or b.y + b.height > before.y + before.height
        ):
            layers.append(layer)
            continue
        box = PixelRect(
            x=after.x + round((b.x - before.x) / before.width * after.width),
            y=after.y + round((b.y - before.y) / before.height * after.height),
            width=max(64, round(b.width / before.width * after.width)),
            height=max(64, round(b.height / before.height * after.height)),
        )
        font_size = layer.font_size
        if layer.kind == "narration":
            font = load_font(default_font_path(), font_size)
            padding = max(14, font_size // 3)
            lines = wrap_text(draw, layer.text, font, box.width - 2 * padding)
            height_needed = max(64, font_line_height(draw, font) * len(lines) + 2 * padding + 4)
            if height_needed <= box.height:
                line_width = max(
                    draw.textbbox((0, 0), line, font=font, stroke_width=1)[2] for line in lines
                )
                width_needed = min(box.width, max(64, round(line_width) + 2 * padding + 4))
                box = PixelRect(
                    x=box.x + box.width - width_needed,
                    y=box.y + box.height - height_needed,
                    width=width_needed,
                    height=height_needed,
                )
        layers.append(layer.model_copy(update={"bounds": box}))
    return PageDocument.model_validate(
        {
            **document.model_dump(),
            "panels": [
                p.model_copy(update={"frame": actual[p.panel_id]}).model_dump()
                for p in document.panels
            ],
            "text_layers": [layer.model_dump() for layer in layers],
        }
    )
