# ruff: noqa: RUF001 -- These cases intentionally test fullwidth CJK punctuation.
import pytest
from PIL import Image, ImageDraw

from backend.app.pages.renderer import default_font_path, load_font, wrap_text


@pytest.mark.parametrize("text", ["甲乙丙丁。", "甲乙丙“丁戊”", "甲乙丙（丁戊）", "甲乙丙丁？！"])
def test_cjk_punctuation_stays_with_its_phrase_without_overflow(text: str) -> None:
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    font = load_font(default_font_path(), 42)
    box = draw.textbbox((0, 0), "甲乙丙丁", font=font, stroke_width=1)
    width = box[2] - box[0]
    lines = wrap_text(draw, text, font, width)
    assert "".join(lines) == text
    assert all(line[0] not in "。，！？）”" and line[-1] not in "（“" for line in lines)
    for line in lines:
        bounds = draw.textbbox((0, 0), line, font=font, stroke_width=1)
        assert bounds[2] - bounds[0] <= width


def test_explicit_paragraph_breaks_are_preserved() -> None:
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    assert wrap_text(draw, "第一行。\n第二行。", load_font(default_font_path(), 42), 800) == [
        "第一行。",
        "第二行。",
    ]
