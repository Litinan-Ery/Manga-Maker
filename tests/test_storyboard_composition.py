from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.adaptation.models import PageCandidate, PanelCandidate
from backend.app.errors import ApplicationError
from backend.app.modules.layout.contracts import FrameSpec, PageLayoutDraft
from backend.app.pages.layout import compose_layout_document, place_text_layers
from backend.app.pages.models import PageLayoutSource, PixelRect, TextLayer
from tests.test_generation_queue import DIMENSION_CAPABILITIES
from tests.test_pages_api import prepare_page


def revise_layout(
    client: TestClient, headers: dict[str, str], prepared: dict[str, Any], *, approve: bool
) -> dict[str, Any]:
    project_id = prepared["project_id"]
    chapter_id = prepared["chapter"]["chapter_id"]
    current = client.get(
        f"/api/v1/projects/{project_id}/layouts", params={"chapter_id": chapter_id}
    ).json()[0]
    draft = deepcopy(current["layout"])
    draft["version"] += 1
    draft["reading_direction"] = "rtl_ttb"
    leaf = next(frame for frame in draft["frames"] if frame["panel_id"])
    leaf["rect"] = {"x": 0.15, "y": 0.12, "width": 0.7, "height": 0.7}
    leaf["aspect_ratio"] = 2 / 3
    leaf["focal_point"] = {"x": 0.25, "y": 0.65}
    leaf["text_safe_zones"] = [
        {
            "zone_id": str(uuid4()),
            "kind": "sfx",
            "rect": {"x": 0.05, "y": 0.1, "width": 0.35, "height": 0.2},
        }
    ]
    saved = client.post(
        f"/api/v1/projects/{project_id}/layouts/"
        f"{current['page_layout_draft_version_id']}/revisions",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "expected_revision": current["revision"],
            "storyboard_version_id": prepared["job"]["storyboard_version_id"],
            "draft": draft,
        },
    )
    assert saved.status_code == 201, saved.text
    snapshot = saved.json()
    if approve:
        body = {
            "expected_revision": snapshot["revision"],
            "layout_content_sha256": snapshot["layout"]["content_sha256"],
            "storyboard_version_id": prepared["job"]["storyboard_version_id"],
            "dimension_capabilities": DIMENSION_CAPABILITIES,
            "target_pixels": 1_572_864,
            "max_crop_safe_risk": 1.0,
        }
        endpoint = (
            f"/api/v1/projects/{project_id}/layouts/{snapshot['page_layout_draft_version_id']}"
        )
        validation = client.post(endpoint + "/validate", headers=headers, json=body)
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"], validation.text
        result = client.post(
            endpoint + "/approve",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={**body, "dimension_selections": validation.json()["dimension_outcomes"]},
        )
        assert result.status_code == 200, result.text
    return dict(snapshot)


def test_uc01_approved_layout_recomposes_existing_assets_and_preserves_edits(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, original = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    document = deepcopy(original["document"])
    document["text_layers"][0]["text"] = "新的声音"
    edited = client.post(
        f"/api/v1/projects/{project_id}/pages/{original['page_id']}/versions",
        headers=session_headers,
        json={"expected_revision": original["page_revision"], "document": document},
    )
    assert edited.status_code == 201, edited.text
    snapshot = revise_layout(client, session_headers, prepared, approve=True)
    calls = provider.generation_calls
    response = client.post(
        f"/api/v1/projects/{project_id}/pages/draft",
        headers=session_headers,
        json={"chapter_id": prepared["chapter"]["chapter_id"]},
    )
    assert response.status_code == 201, response.text
    page = response.json()[0]
    assert page["document"]["reading_direction"] == "right_to_left"
    assert (
        page["document"]["layout_source"]["version_id"] == snapshot["page_layout_draft_version_id"]
    )
    placement = page["document"]["panels"][0]
    assert placement["frame"] == {"x": 307, "y": 369, "width": 1434, "height": 2150}
    assert (placement["focal_x"], placement["focal_y"]) == (0.25, 0.65)
    assert placement["asset_version_id"] == original["document"]["panels"][0]["asset_version_id"]
    layer = page["document"]["text_layers"][0]
    assert layer["text"] == "新的声音"
    assert layer["bounds"] == {"x": 379, "y": 584, "width": 502, "height": 430}
    assert page["parent_page_version_id"] == edited.json()["page_version_id"]
    repeated = client.post(
        f"/api/v1/projects/{project_id}/pages/draft",
        headers=session_headers,
        json={"chapter_id": prepared["chapter"]["chapter_id"]},
    )
    assert repeated.json()[0]["page_version_id"] == page["page_version_id"]
    assert provider.generation_calls == calls


def test_uc01_unapproved_layout_does_not_silently_reuse_a_default_page(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, _original = prepare_page(client, session_headers)
    revise_layout(client, session_headers, prepared, approve=False)
    calls = provider.generation_calls
    result = client.post(
        f"/api/v1/projects/{prepared['project_id']}/pages/draft",
        headers=session_headers,
        json={"chapter_id": prepared["chapter"]["chapter_id"]},
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "PAGE_LAYOUT_NOT_READY"
    assert provider.generation_calls == calls


def test_uc01_three_unequal_panels_resolve_nested_geometry_and_reading_order() -> None:
    panels = [
        PanelCandidate(
            panel_id=uuid4(),
            order=index,
            purpose="推进线索",
            shot="medium shot",
            characters=[],
            dialogue=[],
            narration=[],
            sfx=[],
            visual_prompt="a room",
            negative_prompt="text",
            source_anchor_ids=["anchor-1"],
        )
        for index in range(1, 4)
    ]
    page = PageCandidate(
        page_id=uuid4(),
        page_number=1,
        page_type="standard",
        turning_point="发现线索",
        scene_ids=[uuid4()],
        panels=panels,
    )
    root_id, row_id = uuid4(), uuid4()

    def frame(parent: Any, rect: tuple[float, float, float, float], order: int | None) -> FrameSpec:
        return FrameSpec(
            frame_id=uuid4(),
            parent_frame_id=parent,
            panel_id=panels[order - 1].panel_id if order else None,
            order=order,
            rect=dict(zip(("x", "y", "width", "height"), rect, strict=True)),
            aspect_ratio=1,
            shot_scale="medium",
            focal_point={"x": 0.3, "y": 0.6},
            crop_safe_rect={"x": 0, "y": 0, "width": 1, "height": 1},
        )

    root = frame(None, (0, 0, 1, 1), None).model_copy(update={"frame_id": root_id})
    row = frame(root_id, (0.05, 0.05, 0.9, 0.4), None).model_copy(update={"frame_id": row_id})
    right = frame(row_id, (0.55, 0, 0.45, 1), 1)
    left = frame(row_id, (0, 0, 0.5, 1), 2)
    bottom = frame(root_id, (0.05, 0.5, 0.9, 0.45), 3)
    layout = PageLayoutDraft(
        page_layout_draft_id=uuid4(),
        version=1,
        page_id=page.page_id,
        page_profile="print_portrait_2_3",
        canvas={"width": 2048, "height": 3072},
        reading_direction="rtl_ttb",
        frames=[root, bottom, row, left, right],
        content_sha256="a" * 64,
    )
    result = compose_layout_document(
        page,
        "storyboard-1",
        layout,
        PageLayoutSource(version_id="layout-1", content_sha256="a" * 64),
        {str(panel.panel_id): {"asset_version_id": f"asset-{panel.order}"} for panel in panels},
    )
    assert [item.panel_id for item in result.panels] == [str(item.panel_id) for item in panels]
    assert result.panels[0].frame.model_dump() == {
        "x": 1116,
        "y": 154,
        "width": 830,
        "height": 1228,
    }
    assert result.panels[1].frame.width == 922
    assert result.panels[2].frame.y == 1536
    assert result.reading_direction == "right_to_left"


def test_uc01_text_layers_share_a_reserved_zone_without_overlap_and_fail_if_too_small() -> None:
    bounds = PixelRect(x=100, y=200, width=1000, height=1000)
    frame = FrameSpec(
        frame_id=uuid4(),
        panel_id=uuid4(),
        order=1,
        rect={"x": 0, "y": 0, "width": 1, "height": 1},
        aspect_ratio=1,
        shot_scale="medium",
        focal_point={"x": 0.5, "y": 0.5},
        crop_safe_rect={"x": 0, "y": 0, "width": 1, "height": 1},
        text_safe_zones=[
            {
                "zone_id": uuid4(),
                "kind": "any",
                "rect": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4},
            }
        ],
    )
    layers = [
        TextLayer(
            layer_id=str(uuid4()),
            panel_id=str(frame.panel_id),
            kind="dialogue",
            text="有人吗?",
            bounds=bounds,
        )
        for _ in range(2)
    ]
    result = place_text_layers(layers, frame, bounds)
    assert result[0].bounds.y + result[0].bounds.height < result[1].bounds.y
    assert result[1].bounds.y + result[1].bounds.height == 700
    with pytest.raises(ApplicationError, match="文字安全区不足"):
        place_text_layers(layers * 5, frame, bounds)
