from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.adaptation.models import (
    DialogueLine,
    PageCandidate,
    PanelCandidate,
    StoryboardRequest,
)
from backend.app.adaptation.text_model import ModelCandidate
from backend.app.novelai.mock import MockNovelAIClient
from tests.test_adaptation_api import configure_vault_and_model
from tests.test_bibles_api import StubTextModel, approve_complete_bibles, generate_bibles
from tests.test_exports_api import export_preflight, export_request
from tests.test_fullpages_api import RecordingProvider
from tests.test_generation_queue import (
    DIMENSION_CAPABILITIES,
    create_job,
    ensure_approved_layout,
    estimate_plan,
    prepare_prompting,
    transition,
)

STORY_TITLE = "雨夜的钥匙"
SOURCE_TEXT = (
    "第一章 雨夜的钥匙\n"
    "成年女子林夏深夜回到旧屋，左手拿着收起的伞，轻声说我回来了。\n"
    "她用右手的铜钥匙转动门锁。\n"
    "门内透出灯光，她转过头，问了一句有人吗。\n"
    "走廊里没有人，桌上台灯照着一封信。\n"
    "她拿起信封，小心拆开。\n"
    "信上只写着明天车站见，她的眉毛扬了起来。\n"
    "她把铜钥匙放在灯边，手里握着信，站在门内看着雨，轻声说知道了。\n"
)
VISUALS = (
    (
        "wide",
        "Lin Xia stands in the doorway of a quiet old house at night. Rain is visible behind her. "
        "She holds a closed umbrella in her left hand.",
        "ただいま。",
    ),
    (
        "extreme_close_up",
        "Extreme close-up of Lin Xia's right hand holding a small brass key beside the door lock. "
        "Only her hand, cuff and the key are visible.",
        "カチャ",
    ),
    (
        "close_up",
        "Close-up of Lin Xia's face. Her eyes turn toward a light inside the house, "
        "her mouth slightly open. Rain beads remain in her shoulder-length black hair.",
        "誰かいる？",  # noqa: RUF001 -- Japanese lettering fixture.
    ),
    (
        "medium",
        "Lin Xia looks into the empty hallway. A small lamp illuminates "
        "a sealed envelope on a wooden table to her right.",
        "手紙…",
    ),
    (
        "extreme_close_up",
        "Insert shot of Lin Xia's fingertips lifting the sealed envelope. "
        "The lamp and the wood grain are visible behind her hand; her face is outside the panel.",
        "サッ",
    ),
    (
        "close_up",
        "Close-up of Lin Xia reading the opened letter. Her eyebrows lift; "
        "the unfolded paper is held below her face.",
        "明日、駅で。",
    ),
    (
        "wide",
        "Lin Xia stands still by the table with the letter in her hands. "
        "Behind her, the open doorway frames the rain. The brass key rests beside the lamp.",
        "…わかった。",
    ),
)


class MangaTextModel(StubTextModel):
    async def generate_storyboard(self, request: StoryboardRequest) -> ModelCandidate[Any]:
        candidate = await super().generate_storyboard(request)
        document = candidate.document
        pages = []
        offset = 0
        for number, count in enumerate((3, 4), start=1):
            panels = [
                PanelCandidate(
                    panel_id=uuid4(),
                    order=index + 1,
                    purpose=f"钥匙与来信的连续镜头 {offset + index + 1}",
                    shot=shot,
                    characters=["林夏"],
                    dialogue=[DialogueLine(speaker="林夏", text=text)],
                    narration=[],
                    sfx=[],
                    visual_prompt=visual,
                    negative_prompt="watermark, logo",
                    source_anchor_ids=[beat.anchor_id for beat in request.story_beats],
                )
                for index, (shot, visual, text) in enumerate(VISUALS[offset : offset + count])
            ]
            pages.append(
                PageCandidate(
                    page_id=uuid4(),
                    page_number=number,
                    page_type="standard",
                    turning_point="发现来信" if number == 1 else "决定赴约",
                    scene_ids=[document.scenes[0].scene_id],
                    panels=panels,
                )
            )
            offset += count
        # This deterministic fixture tests the image workflow; it is not an LLM adaptation result.
        document = document.model_copy(update={"pages": pages})
        return self._candidate(document, "original-manga-user-case-v1")


def prepare_manga(client: TestClient, headers: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    project = client.post("/api/v1/projects", headers=headers, json={"title": STORY_TITLE}).json()
    project_id = project["project_id"]
    source = client.post(
        f"/api/v1/projects/{project_id}/source/preflight",
        headers=headers,
        files={"file": ("rainy-key.txt", SOURCE_TEXT.encode(), "text/plain")},
    ).json()
    imported = client.post(
        f"/api/v1/projects/{project_id}/source/confirm",
        headers=headers,
        json={"preflight_id": source["preflight_id"], "encoding": "utf-8"},
    ).json()
    chapter = imported["chapters"][0]
    beats = client.post(
        f"/api/v1/projects/{project_id}/source/chapters/{chapter['chapter_id']}/story-beats/draft",
        headers=headers,
    )
    assert beats.status_code == 201, beats.text
    configure_vault_and_model(client, headers, project_id)
    client.app.state.adaptation.provider_factory = lambda configuration, secret_reader: (
        MangaTextModel(configuration, secret_reader)
    )
    response = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    )
    assert response.status_code == 201, response.text
    storyboard = response.json()
    approved = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/{storyboard['storyboard_version_id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    bundle = generate_bibles(client, headers, project_id, storyboard["storyboard_version_id"])
    approve_complete_bibles(client, headers, project_id, bundle)
    assert (
        client.put(
            "/api/v1/vault/profiles/novelai",
            headers=headers,
            json={"provider": "novelai", "label": "NovelAI", "secret": "unit-manga-key"},
        ).status_code
        == 200
    )
    configured = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=headers,
        json={
            "provider_model_id": "nai-diffusion-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 120,
        },
    )
    assert configured.status_code == 200, configured.text
    client.app.state.novelai.provider_factory = lambda _config, _reader: MockNovelAIClient()
    connection = client.post(
        f"/api/v1/projects/{project_id}/novelai/connection-test", headers=headers
    )
    assert connection.status_code == 200, connection.text
    layouts = ensure_approved_layout(client, headers, project_id, chapter["chapter_id"])
    rectangles = (
        [(0.55, 0.04, 0.40, 0.39), (0.05, 0.04, 0.45, 0.39), (0.05, 0.49, 0.90, 0.46)],
        [
            (0.52, 0.04, 0.43, 0.41),
            (0.05, 0.04, 0.42, 0.41),
            (0.62, 0.51, 0.33, 0.44),
            (0.05, 0.51, 0.52, 0.44),
        ],
    )
    for page_index, version in enumerate(layouts):
        layout = version["layout"]
        layout["reading_direction"] = "rtl_ttb"
        leaf_index = 0
        for frame in layout["frames"]:
            if frame["panel_id"] is None:
                continue
            x, y, width, height = rectangles[page_index][leaf_index]
            frame["rect"] = {"x": x, "y": y, "width": width, "height": height}
            frame["aspect_ratio"] = width * 2048 / (height * 3072)
            frame["shot_scale"] = VISUALS[leaf_index + (3 if page_index else 0)][0]
            frame["text_safe_zones"] = [
                {
                    "zone_id": str(uuid4()),
                    "kind": "dialogue",
                    "rect": {"x": 0.05, "y": 0.03, "width": 0.90, "height": 0.25},
                }
            ]
            leaf_index += 1
        saved = client.post(
            f"/api/v1/projects/{project_id}/layouts/{version['page_layout_draft_version_id']}/revisions",
            headers={**headers, "Idempotency-Key": f"manga-layout-{page_index}"},
            json={
                "expected_revision": version["revision"],
                "draft": layout,
                "storyboard_version_id": storyboard["storyboard_version_id"],
            },
        )
        assert saved.status_code == 201, saved.text
        saved_version = saved.json()
        validation = {
            "expected_revision": saved_version["revision"],
            "layout_content_sha256": saved_version["layout"]["content_sha256"],
            "storyboard_version_id": storyboard["storyboard_version_id"],
            "dimension_capabilities": DIMENSION_CAPABILITIES,
            "target_pixels": 1_572_864,
            "max_crop_safe_risk": 1.0,
        }
        validated = client.post(
            f"/api/v1/projects/{project_id}/layouts/{saved_version['page_layout_draft_version_id']}/validate",
            headers=headers,
            json=validation,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"], validated.text
        approved_layout = client.post(
            f"/api/v1/projects/{project_id}/layouts/{saved_version['page_layout_draft_version_id']}/approve",
            headers={**headers, "Idempotency-Key": f"manga-layout-approval-{page_index}"},
            json={**validation, "dimension_selections": validated.json()["dimension_outcomes"]},
        )
        assert approved_layout.status_code == 200, approved_layout.text
    prepare_prompting(client, headers, project_id, chapter["chapter_id"])
    return project_id, str(chapter["chapter_id"]), storyboard


def run_manga_workflow(
    client: TestClient,
    session_headers: dict[str, str],
    mode: str,
) -> dict[str, Any]:
    project_id, chapter_id, storyboard = prepare_manga(client, session_headers)
    assert [
        (page["page_type"], len(page["panels"])) for page in storyboard["document"]["pages"]
    ] == [("standard", 3), ("standard", 4)]
    provider = RecordingProvider()
    if mode == "panel":
        plan = estimate_plan(client, session_headers, project_id, chapter_id)
        assert plan["panel_count"] == 7 and plan["page_count"] == 2
        created = create_job(client, session_headers, project_id, chapter_id, plan)
        assert created.status_code == 201, created.text
        job = created.json()
        transition(client, session_headers, project_id, job, "start")
        client.app.state.generation_executor.provider_factory = lambda _config, _reader: provider
        asyncio.run(client.app.state.generation_executor.run_until_blocked(job["job_id"]))
        pages_response = client.post(
            f"/api/v1/projects/{project_id}/pages/draft",
            headers=session_headers,
            json={"chapter_id": chapter_id},
        )
        assert pages_response.status_code == 201, pages_response.text
        assert provider.generation_calls == 7
    else:
        client.app.state.container.full_pages.provider_factory = lambda _config, _reader: provider
        for page in storyboard["document"]["pages"]:
            response = client.post(
                f"/api/v1/projects/{project_id}/full-pages/preview",
                headers=session_headers,
                json={
                    "chapter_id": chapter_id,
                    "page_number": page["page_number"],
                    "seed": 9200 + page["page_number"],
                    "text_policy": "model",
                },
            )
            assert response.status_code == 201, response.text
            plan = response.json()
            prompt = plan["provider_payload"]["input"]
            assert "Read right to left" in prompt
            assert len([part for part in prompt.split("\n\n") if part.startswith("Panel ")]) == len(
                page["panels"]
            )
            assert "extreme close-up" in prompt
            assert prompt.count("林夏 (1girl, shoulder-length black hair, beauty mark)") == len(
                page["panels"]
            )
            assert prompt.split("Text: ")[-1] == "\n\n".join(
                panel["dialogue"][0]["text"] for panel in page["panels"]
            )
            base = f"/api/v1/projects/{project_id}/full-pages/{plan['generation_id']}"
            generated = client.post(
                base + "/generate",
                headers=session_headers,
                json={"plan_sha256": plan["plan_sha256"], "confirmed": True},
            )
            assert generated.json()["status"] == "ready", generated.text
            adopted = client.post(
                base + "/adopt",
                headers=session_headers,
                json={"expected_revision": 0, "confirmed": True},
            )
            assert adopted.status_code == 201, adopted.text
        assert provider.generation_calls == 2
    pages = client.get(
        f"/api/v1/projects/{project_id}/pages", params={"chapter_id": chapter_id}
    ).json()
    assert [len(page["document"]["panels"]) for page in pages] == [3, 4]
    assert all(page["document"]["reading_direction"] == "right_to_left" for page in pages)
    first = pages[0]["document"]["panels"]
    assert first[0]["frame"]["x"] > first[1]["frame"]["x"]
    assert first[0]["frame"]["width"] != first[1]["frame"]["width"]
    calls_before = provider.generation_calls
    preflight = export_preflight(client, session_headers, project_id, chapter_id)
    exported = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    )
    assert exported.status_code == 201, exported.text
    assert exported.json()["status"] == "completed"
    assert provider.generation_calls == calls_before
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "storyboard": storyboard,
        "pages": pages,
        "provider_payloads": provider.payloads,
        "generation_calls": provider.generation_calls,
        "export": exported.json(),
    }


@pytest.mark.parametrize("mode", ["panel", "full_page"])
def test_uc04_two_normal_manga_pages_cover_seven_panels_and_export(
    client: TestClient,
    session_headers: dict[str, str],
    mode: str,
) -> None:
    run_manga_workflow(client, session_headers, mode)


@pytest.mark.parametrize("page_number", [1, 2])
def test_uc04a_full_page_expresses_layout_topology_and_character_continuity(
    client: TestClient, session_headers: dict[str, str], page_number: int
) -> None:
    project_id, chapter_id, _ = prepare_manga(client, session_headers)
    result = client.post(
        f"/api/v1/projects/{project_id}/full-pages/preview",
        headers=session_headers,
        json={
            "chapter_id": chapter_id,
            "page_number": page_number,
            "seed": 9200 + page_number,
            "text_policy": "model",
        },
    )
    assert result.status_code == 201, result.text
    plan = result.json()
    prompt = plan["provider_payload"]["input"]
    assert "Top row: 2 panels side by side" in prompt
    assert "Panel at the upper right:" in prompt
    assert "Panel at the upper left:" in prompt
    if page_number == 1:
        assert "Bottom row: one panel spanning the full width" in prompt
        assert "Panel across the bottom:" in prompt
    else:
        assert "Bottom row: 2 panels side by side" in prompt
        assert "Panel at the lower right:" in prompt
        assert "Panel at the lower left:" in prompt
    for detail in ("20-25", "浅色衬衫", "深色长裤", "左眼下方小痣"):
        assert detail in prompt
    assert "One instant per panel" in prompt
    assert "Protect important details inside the rectangle from 0% to 100%" not in prompt
    assert "Within this panel, focus at 50%" not in prompt
    assert "hair.." not in prompt
    assert plan["external_requests_started"] == 0
    parts = [part for part in prompt.split("\n\n") if part.startswith("Panel ")]
    expected_texts = tuple(item[2] for item in (VISUALS[:3] if page_number == 1 else VISUALS[3:]))
    mapping = next(part for part in prompt.split("\n\n") if part.startswith("Lettering map:"))
    assert f'upper right panel says "{expected_texts[0]}"' in mapping
    assert f'upper left panel says "{expected_texts[1]}"' in mapping
    for part, expected in zip(parts, expected_texts, strict=True):
        assert f'"{expected}"' in part
        assert all(f'"{other}"' not in part for other in expected_texts if other != expected)
    assert "Panel 1" not in prompt and "text entries" not in prompt
    assert "panel number" in plan["provider_payload"]["parameters"]["negative_prompt"]


def test_uc04c_local_lettering_preserves_dialogue_and_speaker_separately(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter_id, _ = prepare_manga(client, session_headers)
    provider = RecordingProvider()
    client.app.state.container.full_pages.provider_factory = lambda _config, _reader: provider
    base = f"/api/v1/projects/{project_id}/full-pages"
    preview = client.post(
        base + "/preview",
        headers=session_headers,
        json={
            "chapter_id": chapter_id,
            "page_number": 1,
            "seed": 9201,
            "text_policy": "local",
        },
    )
    assert preview.status_code == 201, preview.text
    plan = preview.json()
    path = base + f"/{plan['generation_id']}"
    generated = client.post(
        path + "/generate",
        headers=session_headers,
        json={
            "confirmed": True,
            "plan_sha256": plan["plan_sha256"],
        },
    )
    assert generated.json()["status"] == "ready", generated.text
    adopted = client.post(
        path + "/adopt",
        headers=session_headers,
        json={
            "confirmed": True,
            "expected_revision": 0,
        },
    )
    assert adopted.status_code == 201, adopted.text
    layers = adopted.json()["document"]["text_layers"]
    assert [layer["text"] for layer in layers] == [item[2] for item in VISUALS[:3]]
    assert [layer["speaker"] for layer in layers] == ["林夏"] * 3
    assert provider.generation_calls == 1
