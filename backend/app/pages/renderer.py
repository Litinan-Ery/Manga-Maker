from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .models import PAGE_HEIGHT, PAGE_WIDTH, PageDocument, PixelRect, TextLayer

DEFAULT_CJK_FONT_PATHS = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/STHeiti Light.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
)
RENDERER_VERSION = "pillow-page-renderer-1"


class PageRenderError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RenderedPage:
    png_bytes: bytes
    sha256: str
    width: int
    height: int
    renderer_version: str
    font_sha256: str


class PageRenderer:
    def __init__(self, font_path: Path | None = None) -> None:
        self.font_path = (font_path or default_font_path()).resolve()
        if not self.font_path.is_file():
            raise PageRenderError("a local CJK font is required for page rendering")

    def render(self, document: PageDocument, asset_paths: dict[str, Path]) -> RenderedPage:
        canvas = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), color="white")
        draw = ImageDraw.Draw(canvas)
        for placement in document.panels:
            path = asset_paths.get(placement.asset_version_id)
            if path is None:
                raise PageRenderError("page references an unavailable asset version")
            panel = safe_open_rgb(path)
            fitted = cover_crop(
                panel,
                placement.frame,
                focal_x=placement.focal_x,
                focal_y=placement.focal_y,
                zoom=placement.zoom,
            ).convert("L").convert("RGB")
            canvas.paste(fitted, (placement.frame.x, placement.frame.y))
            draw.rectangle(rectangle(placement.frame), outline="black", width=10)

        for layer in document.text_layers:
            self._draw_text_layer(draw, layer)
        if document.show_page_number:
            self._draw_page_number(draw, document.page_number)

        output = BytesIO()
        canvas.save(output, format="PNG", optimize=False, compress_level=9)
        payload = output.getvalue()
        return RenderedPage(
            png_bytes=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            renderer_version=RENDERER_VERSION,
            font_sha256=file_sha256(self.font_path),
        )

    def _draw_text_layer(self, draw: ImageDraw.ImageDraw, layer: TextLayer) -> None:
        bounds = layer.bounds
        font = load_font(self.font_path, layer.font_size)
        padding = max(14, layer.font_size // 3)
        text_width = bounds.width - padding * 2
        text_height = bounds.height - padding * 2
        if text_width <= 0 or text_height <= 0:
            raise PageRenderError(f"text layer {layer.layer_id} has no usable text area")
        lines = wrap_text(draw, layer.text, font, text_width)
        line_height = font_line_height(draw, font)
        total_height = line_height * len(lines)
        if total_height > text_height:
            raise PageRenderError(f"text layer {layer.layer_id} overflows its bounds")

        box = rectangle(bounds)
        if layer.kind == "dialogue":
            draw.ellipse(box, fill="white", outline="black", width=7)
        elif layer.kind == "narration":
            draw.rounded_rectangle(box, radius=18, fill="white", outline="black", width=7)

        y = bounds.y + padding + (text_height - total_height) // 2
        for line in lines:
            line_box = draw.textbbox((0, 0), line, font=font, stroke_width=1)
            line_width = round(line_box[2] - line_box[0])
            if layer.align == "left":
                x = bounds.x + padding
            elif layer.align == "right":
                x = bounds.x + bounds.width - padding - line_width
            else:
                x = bounds.x + (bounds.width - line_width) // 2
            if layer.kind == "sfx":
                draw.text(
                    (x, y),
                    line,
                    font=font,
                    fill="white",
                    stroke_width=max(2, layer.font_size // 14),
                    stroke_fill="black",
                )
            else:
                draw.text((x, y), line, font=font, fill="black", stroke_width=1)
            y += line_height

    def _draw_page_number(self, draw: ImageDraw.ImageDraw, page_number: int) -> None:
        font = load_font(self.font_path, 38)
        text = str(page_number)
        box = draw.textbbox((0, 0), text, font=font)
        width = box[2] - box[0]
        draw.text(
            ((PAGE_WIDTH - width) // 2, PAGE_HEIGHT - 82),
            text,
            font=font,
            fill="black",
        )


def default_font_path() -> Path:
    for candidate in DEFAULT_CJK_FONT_PATHS:
        if candidate.is_file():
            return candidate
    raise PageRenderError("no supported local CJK font was found")


def safe_open_rgb(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            source.load()
            if source.width <= 0 or source.height <= 0 or source.width * source.height > 25_000_000:
                raise PageRenderError("panel asset dimensions are invalid")
            return cast(Image.Image, source.convert("RGB"))
    except (UnidentifiedImageError, OSError) as exc:
        raise PageRenderError("panel asset failed safe image decoding") from exc


def cover_crop(
    source: Image.Image,
    frame: PixelRect,
    *,
    focal_x: float,
    focal_y: float,
    zoom: float,
) -> Image.Image:
    base_scale = max(frame.width / source.width, frame.height / source.height)
    scale = base_scale * zoom
    resized_width = max(frame.width, round(source.width * scale))
    resized_height = max(frame.height, round(source.height * scale))
    resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
    max_left = resized_width - frame.width
    max_top = resized_height - frame.height
    left = round(max_left * focal_x)
    top = round(max_top * focal_y)
    return resized.crop((left, top, left + frame.width, top + frame.height))


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    maximum_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\t", "  ").splitlines() or [""]:
        current = ""
        for character in paragraph:
            candidate = current + character
            box = draw.textbbox((0, 0), candidate, font=font, stroke_width=1)
            if current and box[2] - box[0] > maximum_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        lines.append(current or " ")
    return lines


def font_line_height(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), "国Ag", font=font, stroke_width=1)
    return int(max(1, box[3] - box[1] + max(6, font.size // 6)))


def rectangle(bounds: PixelRect) -> tuple[int, int, int, int]:
    return (
        bounds.x,
        bounds.y,
        bounds.x + bounds.width - 1,
        bounds.y + bounds.height - 1,
    )


@lru_cache(maxsize=128)
def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError as exc:
        raise PageRenderError("local CJK font could not be loaded") from exc


@lru_cache(maxsize=8)
def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
