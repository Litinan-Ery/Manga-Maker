from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from backend.app.pages.models import PageDocument, PanelPlacement, PixelRect, TextLayer
from backend.app.pages.renderer import PageRenderer
from backend.app.pages.templates import all_templates, templates


def test_one_to_six_panel_templates_are_bounded_and_non_overlapping() -> None:
    for expected_count, page_template in enumerate(templates(), start=1):
        assert page_template.panel_count == expected_count
        assert len(page_template.frames) == expected_count
        for frame in page_template.frames:
            assert frame.x >= 0
            assert frame.y >= 0
            assert frame.x + frame.width <= 2048
            assert frame.y + frame.height <= 3072
        for index, first in enumerate(page_template.frames):
            for second in page_template.frames[index + 1 :]:
                assert not rectangles_overlap(first, second)


def test_canonical_page_render_is_deterministic_and_contains_chinese_text(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "asset.png"
    Image.new("RGB", (640, 960), color=(210, 160, 110)).save(asset, format="PNG")
    page_template = templates()[0]
    document = PageDocument(
        page_id="page-1",
        page_number=1,
        template_id=page_template.template_id,
        storyboard_version_id="storyboard-v1",
        panels=[
            PanelPlacement(
                panel_id="panel-1",
                asset_version_id="asset-1",
                frame=page_template.frames[0],
                focal_x=0.3,
                focal_y=0.7,
                zoom=1.2,
            )
        ],
        text_layers=[
            TextLayer(
                layer_id="layer-1",
                panel_id="panel-1",
                kind="dialogue",
                text="林夏: 门后有人吗?",
                bounds={"x": 300, "y": 260, "width": 720, "height": 300},
                font_size=54,
            ),
            TextLayer(
                layer_id="layer-2",
                panel_id="panel-1",
                kind="narration",
                text="雨夜，旧宅没有回答。",
                bounds={"x": 300, "y": 620, "width": 760, "height": 190},
                font_size=46,
                align="left",
            ),
            TextLayer(
                layer_id="layer-3",
                panel_id="panel-1",
                kind="sfx",
                text="吱呀",
                bounds={"x": 1300, "y": 2200, "width": 420, "height": 180},
                font_size=76,
            ),
        ],
    )
    renderer = PageRenderer()
    first = renderer.render(document, {"asset-1": asset})
    second = renderer.render(document, {"asset-1": asset})

    assert first.png_bytes == second.png_bytes
    assert first.sha256 == second.sha256
    assert len(first.font_sha256) == 64
    with Image.open(BytesIO(first.png_bytes)) as image:
        assert image.size == (2048, 3072)
        assert image.format == "PNG"
        assert image.getpixel((100, 100))[0] == image.getpixel((100, 100))[1]
        assert image.getbbox() is not None


def test_color_vertical_strip_and_rtl_profiles_render_without_forced_grayscale(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "color.png"
    Image.new("RGB", (640, 960), color=(210, 80, 30)).save(asset, format="PNG")
    strip = next(item for item in all_templates() if item.template_id == "strip-1")
    document = PageDocument(
        schema_version="2.0",
        page_id="page-color",
        page_number=1,
        width=strip.width,
        height=strip.height,
        reading_direction="top_to_bottom",
        color_mode="color",
        background_color="#fff4df",
        template_id=strip.template_id,
        storyboard_version_id="storyboard-v1",
        panels=[
            PanelPlacement(
                panel_id="panel-1",
                asset_version_id="asset-1",
                frame=strip.frames[0],
            )
        ],
        show_page_number=False,
    )
    rendered = PageRenderer().render(document, {"asset-1": asset})
    with Image.open(BytesIO(rendered.png_bytes)) as image:
        assert image.size == (1440, 1804)
        assert image.getpixel((720, 800)) == (210, 80, 30)
        assert image.getpixel((20, 20)) == (255, 244, 223)

    rtl = document.model_copy(
        update={
            "page_id": "page-rtl",
            "width": 2048,
            "height": 3072,
            "reading_direction": "right_to_left",
            "template_id": "grid-1",
            "panels": [
                document.panels[0].model_copy(
                    update={"frame": templates()[0].frames[0]}
                )
            ],
        }
    )
    assert PageDocument.model_validate(rtl.model_dump()).reading_direction == "right_to_left"


def rectangles_overlap(first: PixelRect, second: PixelRect) -> bool:
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )
