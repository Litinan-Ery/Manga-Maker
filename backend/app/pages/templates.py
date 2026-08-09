from __future__ import annotations

from dataclasses import dataclass

from .models import PAGE_HEIGHT, PAGE_WIDTH, PixelRect

PAGE_MARGIN = 96
PAGE_NUMBER_SPACE = 112
GUTTER = 36
CONTENT_BOTTOM = PAGE_HEIGHT - PAGE_MARGIN - PAGE_NUMBER_SPACE


@dataclass(frozen=True, slots=True)
class PageTemplate:
    template_id: str
    label: str
    frames: tuple[PixelRect, ...]

    @property
    def panel_count(self) -> int:
        return len(self.frames)

    def payload(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "label": self.label,
            "panel_count": self.panel_count,
            "frames": [frame.model_dump(mode="json") for frame in self.frames],
        }


def templates() -> tuple[PageTemplate, ...]:
    left = PAGE_MARGIN
    top = PAGE_MARGIN
    width = PAGE_WIDTH - PAGE_MARGIN * 2
    height = CONTENT_BOTTOM - top
    half_width = (width - GUTTER) // 2
    half_height = (height - GUTTER) // 2
    third_height = (height - GUTTER * 2) // 3
    return (
        PageTemplate("grid-1", "1 格·全页", (rect(left, top, width, height),)),
        PageTemplate(
            "grid-2",
            "2 格·上下",
            (
                rect(left, top, width, half_height),
                rect(left, top + half_height + GUTTER, width, half_height),
            ),
        ),
        PageTemplate(
            "grid-3",
            "3 格·上宽下双",
            (
                rect(left, top, width, half_height),
                rect(left, top + half_height + GUTTER, half_width, half_height),
                rect(
                    left + half_width + GUTTER,
                    top + half_height + GUTTER,
                    half_width,
                    half_height,
                ),
            ),
        ),
        PageTemplate(
            "grid-4",
            "4 格·两行两列",
            (
                rect(left, top, half_width, half_height),
                rect(left + half_width + GUTTER, top, half_width, half_height),
                rect(left, top + half_height + GUTTER, half_width, half_height),
                rect(
                    left + half_width + GUTTER,
                    top + half_height + GUTTER,
                    half_width,
                    half_height,
                ),
            ),
        ),
        PageTemplate(
            "grid-5",
            "5 格·上宽下四",
            (
                rect(left, top, width, third_height),
                rect(left, top + third_height + GUTTER, half_width, third_height),
                rect(
                    left + half_width + GUTTER,
                    top + third_height + GUTTER,
                    half_width,
                    third_height,
                ),
                rect(left, top + (third_height + GUTTER) * 2, half_width, third_height),
                rect(
                    left + half_width + GUTTER,
                    top + (third_height + GUTTER) * 2,
                    half_width,
                    third_height,
                ),
            ),
        ),
        PageTemplate(
            "grid-6",
            "6 格·三行两列",
            tuple(
                rect(
                    left + column * (half_width + GUTTER),
                    top + row * (third_height + GUTTER),
                    half_width,
                    third_height,
                )
                for row in range(3)
                for column in range(2)
            ),
        ),
    )


def template_for_count(panel_count: int) -> PageTemplate:
    try:
        return templates()[panel_count - 1]
    except IndexError as exc:
        raise ValueError("page templates support one to six panels") from exc


def get_template(template_id: str) -> PageTemplate:
    for page_template in templates():
        if page_template.template_id == template_id:
            return page_template
    raise ValueError("unknown page template")


def rect(x: int, y: int, width: int, height: int) -> PixelRect:
    return PixelRect(x=x, y=y, width=width, height=height)
