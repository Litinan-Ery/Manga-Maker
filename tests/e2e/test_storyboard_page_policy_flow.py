from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.adaptation.models import (
    BeatResolution,
    PageCandidate,
    PanelCandidate,
    SceneCandidate,
    StoryboardDocument,
    StoryboardRequest,
)
from backend.app.adaptation.text_model import ModelCandidate
from tests.test_adaptation_api import (
    StubTextModel,
    configure_vault_and_model,
    create_project_and_source,
)

ROOT = Path(__file__).resolve().parents[2]
DIMENSION_CAPABILITIES = json.loads(
    (ROOT / "contracts" / "fixtures" / "v0.3" / "dimension-capabilities.json").read_text(
        encoding="utf-8"
    )
)


class PagePolicyTextModel(StubTextModel):
    async def generate_storyboard(
        self,
        request: StoryboardRequest,
    ) -> ModelCandidate[StoryboardDocument]:
        self.secret_reader(self.configuration.credential_profile_id)
        scene_id = uuid4()
        anchors = [beat.anchor_id for beat in request.story_beats]

        def panel(order: int, anchor_id: str) -> PanelCandidate:
            return PanelCandidate(
                panel_id=uuid4(),
                order=order,
                purpose=f"逐页分镜 {order}",
                shot="medium shot",
                characters=["林夏"],
                dialogue=[],
                narration=[],
                sfx=[],
                visual_prompt="black and white manga, no text",
                negative_prompt="watermark, text, logo",
                source_anchor_ids=[anchor_id],
            )

        document = StoryboardDocument(
            schema_version="1.1",
            storyboard_id=uuid4(),
            chapter_version=request.chapter_version,
            beat_resolutions=[
                BeatResolution(
                    beat_id=beat.beat_id,
                    status="represented",
                    page_numbers=[1],
                )
                for beat in request.story_beats
            ],
            scenes=[
                SceneCandidate(
                    scene_id=scene_id,
                    order=1,
                    title="雨夜发现",
                    location="旧屋",
                    time_of_day="夜晚",
                    summary="用一页大场面和一页普通叙事页呈现来源节拍。",
                    beat_ids=[beat.beat_id for beat in request.story_beats],
                )
            ],
            pages=[
                PageCandidate(
                    page_id=uuid4(),
                    page_number=1,
                    page_type="splash",
                    turning_point="通页建立雨夜氛围",
                    scene_ids=[scene_id],
                    panels=[
                        panel(1, anchors[0]).model_copy(
                            update={"source_anchor_ids": anchors}
                        )
                    ],
                ),
                PageCandidate(
                    page_id=uuid4(),
                    page_number=2,
                    page_type="standard",
                    turning_point="三格推进线索",
                    scene_ids=[scene_id],
                    panels=[panel(index, anchors[0]) for index in range(1, 4)],
                ),
            ],
        )
        return ModelCandidate(
            document=document,
            provider="openai-compatible",
            model=self.configuration.model,
            endpoint_host="models.example.test",
            prompt_template_version="storyboard-1.1",
            response_sha256="b" * 64,
            input_tokens=100,
            output_tokens=100,
            duration_ms=10,
            repair_attempts=0,
        )


def test_storyboard_1_1_pages_map_one_to_one_to_approved_layout_leaves(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    project_id, chapter, _beat_set = create_project_and_source(client, session_headers)
    configure_vault_and_model(client, session_headers, project_id)
    client.app.state.adaptation.provider_factory = (
        lambda configuration, secret_reader: PagePolicyTextModel(
            configuration,
            secret_reader,
        )
    )

    generated_response = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    )
    assert generated_response.status_code == 201, generated_response.text
    storyboard = generated_response.json()
    assert storyboard["page_policy_valid"] is True
    assert [
        (page["page_type"], len(page["panels"]))
        for page in storyboard["document"]["pages"]
    ] == [("splash", 1), ("standard", 3)]

    approval = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{storyboard['storyboard_version_id']}/approve",
        headers=session_headers,
    )
    assert approval.status_code == 200, approval.text

    for page in storyboard["document"]["pages"]:
        draft = layout_draft(page)
        created_response = client.post(
            f"/api/v1/projects/{project_id}/layouts/drafts",
            headers={
                **session_headers,
                "Idempotency-Key": f"policy-layout-create-{page['page_id']}",
            },
            json={
                "chapter_id": chapter["chapter_id"],
                "storyboard_version_id": storyboard["storyboard_version_id"],
                "draft": draft,
            },
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        leaves = [
            frame for frame in created["layout"]["frames"] if frame["panel_id"] is not None
        ]
        assert [frame["panel_id"] for frame in leaves] == [
            panel["panel_id"] for panel in page["panels"]
        ]

        validation_body = {
            "expected_revision": created["revision"],
            "layout_content_sha256": created["layout"]["content_sha256"],
            "storyboard_version_id": storyboard["storyboard_version_id"],
            "dimension_capabilities": DIMENSION_CAPABILITIES,
            "target_pixels": 1_572_864,
            "max_crop_safe_risk": 1.0,
        }
        validated = client.post(
            f"/api/v1/projects/{project_id}/layouts/"
            f"{created['page_layout_draft_version_id']}/validate",
            headers=session_headers,
            json=validation_body,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True
        approved = client.post(
            f"/api/v1/projects/{project_id}/layouts/"
            f"{created['page_layout_draft_version_id']}/approve",
            headers={
                **session_headers,
                "Idempotency-Key": f"policy-layout-approve-{page['page_id']}",
            },
            json={
                **validation_body,
                "dimension_selections": validated.json()["dimension_outcomes"],
            },
        )
        assert approved.status_code == 200, approved.text


def layout_draft(page: dict[str, Any]) -> dict[str, Any]:
    panels = list(page["panels"])
    root_id = str(uuid4())
    panel_count = len(panels)
    slot_height = 0.92 / panel_count
    leaf_height = slot_height - 0.02
    frames: list[dict[str, Any]] = [
        {
            "frame_id": root_id,
            "parent_frame_id": None,
            "panel_id": None,
            "order": None,
            "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
            "aspect_ratio": 2 / 3,
            "shot_scale": "establishing",
            "focal_point": {"x": 0.5, "y": 0.5},
            "character_positions": [],
            "text_safe_zones": [],
            "crop_safe_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
        }
    ]
    for index, panel in enumerate(panels, start=1):
        frames.append(
            {
                "frame_id": str(uuid4()),
                "parent_frame_id": root_id,
                "panel_id": panel["panel_id"],
                "order": index,
                "rect": {
                    "x": 0.04,
                    "y": 0.04 + (index - 1) * slot_height,
                    "width": 0.92,
                    "height": leaf_height,
                },
                "aspect_ratio": (0.92 * 2048) / (leaf_height * 3072),
                "shot_scale": "medium",
                "focal_point": {"x": 0.5, "y": 0.5},
                "character_positions": [],
                "text_safe_zones": [],
                "crop_safe_rect": {"x": 0.04, "y": 0.04, "width": 0.92, "height": 0.92},
            }
        )
    return {
        "schema_version": "1.0",
        "page_layout_draft_id": str(uuid4()),
        "version": 1,
        "page_id": page["page_id"],
        "page_profile": "print_portrait_2_3",
        "canvas": {"width": 2048, "height": 3072},
        "reading_direction": "ltr_ttb",
        "frames": frames,
        "content_sha256": "0" * 64,
        "approved_content_sha256": None,
    }
