from pathlib import Path

from PIL import Image, ImageDraw

from backend.app.fullpages.lettering import calibrate_lettering, detected_frames
from backend.app.pages.models import (
    PageDocument,
    PageImageSource,
    PanelPlacement,
    PixelRect,
    TextLayer,
)


def test_actual_unequal_frames_keep_captions_inside_their_own_panel(tmp_path: Path) -> None:
    image = Image.new("RGB", (832, 1216), "white")
    draw = ImageDraw.Draw(image)
    for rect in [(20, 24, 350, 600), (360, 24, 810, 600), (20, 640, 810, 1190)]:
        draw.rectangle(rect, outline="black", width=4)
    draw.ellipse((50, 80, 270, 290), fill="black")
    path = tmp_path / "actual.png"
    image.save(path)
    original_rects = [(1065, 92, 922, 1352), (61, 92, 922, 1352), (61, 1567, 1925, 1413)]
    panels, layers = [], []
    for i, (x, y, width, height) in enumerate(original_rects):
        panels.append(
            PanelPlacement(
                panel_id=f"p{i}",
                asset_version_id="image",
                frame=PixelRect(x=x, y=y, width=width, height=height),
            )
        )
        layers.append(
            TextLayer(
                layer_id=f"t{i}",
                panel_id=f"p{i}",
                kind="narration",
                text="等待被唤醒的书。",
                bounds=PixelRect(x=x + 40, y=y + 1000, width=width - 80, height=260),
            )
        )
    document = PageDocument(
        schema_version="2.0",
        page_id="page",
        page_number=1,
        reading_direction="right_to_left",
        template_id="approved",
        storyboard_version_id="storyboard",
        page_image=PageImageSource(generation_id="image", text_policy="local"),
        panels=panels,
        text_layers=layers,
    )
    calibrated = calibrate_lettering(document, path)
    assert calibrated != document
    assert calibrated.panels[1].frame.width < document.panels[1].frame.width
    for layer, panel, original in zip(
        calibrated.text_layers, calibrated.panels, document.text_layers, strict=True
    ):
        frame, box = panel.frame, layer.bounds
        assert frame.x <= box.x < box.x + box.width <= frame.x + frame.width
        assert frame.y <= box.y < box.y + box.height <= frame.y + frame.height
        assert box.width < original.bounds.width
        assert box.height < 150
    assert calibrate_lettering(calibrated, path) == calibrated
    assert document.text_layers[0].bounds.height == 260
    assert detected_frames(path, 4, True) is None


def test_unframed_art_does_not_claim_panel_geometry(tmp_path: Path) -> None:
    image = Image.new("RGB", (832, 1216), "white")
    ImageDraw.Draw(image).ellipse((20, 50, 810, 1190), fill="black")
    path = tmp_path / "unframed.png"
    image.save(path)
    assert detected_frames(path, 1, True) is None


def test_unrecognized_frame_still_fits_caption_without_moving_geometry(tmp_path: Path) -> None:
    path = tmp_path / "bleed.png"
    Image.new("RGB", (832, 1216), "black").save(path)
    document = PageDocument(
        schema_version="2.0",
        page_id="page",
        page_number=1,
        reading_direction="right_to_left",
        template_id="approved",
        storyboard_version_id="storyboard",
        page_image=PageImageSource(generation_id="image", text_policy="local"),
        panels=[
            PanelPlacement(
                panel_id="p1",
                asset_version_id="image",
                frame=PixelRect(x=60, y=60, width=1920, height=2800),
            )
        ],
        text_layers=[
            TextLayer(
                layer_id="caption",
                panel_id="p1",
                kind="narration",
                text="门关上了，屋内开始秘密商议。",
                font_size=42,
                bounds=PixelRect(x=120, y=2400, width=1700, height=300),
            ),
            TextLayer(
                layer_id="free",
                panel_id="p1",
                kind="narration",
                text="页外标记",
                bounds=PixelRect(x=0, y=0, width=700, height=200),
            ),
        ],
    )
    assert detected_frames(path, 1, True) is None
    result = calibrate_lettering(document, path)
    assert result.panels == document.panels
    assert result.text_layers[0].bounds.height < 150
    assert result.text_layers[0].bounds.width < 1000
    assert result.text_layers[0].text == document.text_layers[0].text
    assert result.text_layers[0].font_size == 42
    before, after = document.text_layers[0].bounds, result.text_layers[0].bounds
    assert after.x >= before.x and after.y >= before.y
    assert after.x + after.width <= before.x + before.width
    assert after.y + after.height <= before.y + before.height
    assert result.text_layers[1] == document.text_layers[1]
    assert calibrate_lettering(result, path) == result
