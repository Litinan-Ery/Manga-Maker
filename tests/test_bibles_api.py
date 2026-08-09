from __future__ import annotations

import hashlib
import json
from io import BytesIO
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from backend.app.adaptation.models import (
    BeatResolution,
    PageCandidate,
    PanelCandidate,
    SceneCandidate,
    StoryboardDocument,
    StoryboardRequest,
)
from backend.app.adaptation.text_model import (
    ModelCandidate,
    SecretReader,
    TextModelConfiguration,
)


class StubTextModel:
    def __init__(self, configuration: TextModelConfiguration, secret_reader: SecretReader) -> None:
        self.configuration = configuration
        self.secret_reader = secret_reader

    async def validate_configuration(self) -> bool:
        return True

    async def generate_storyboard(self, request: StoryboardRequest) -> ModelCandidate:
        self.secret_reader(self.configuration.credential_profile_id)
        scene_id = uuid4()
        document = StoryboardDocument(
            schema_version="1.0",
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
                    title="进入房间",
                    location="旧屋房间",
                    time_of_day="夜晚",
                    summary="林夏进入房间并发现一封信。",
                    beat_ids=[beat.beat_id for beat in request.story_beats],
                )
            ],
            pages=[
                PageCandidate(
                    page_id=uuid4(),
                    page_number=1,
                    turning_point="林夏发现关键线索",
                    scene_ids=[scene_id],
                    panels=[
                        PanelCandidate(
                            panel_id=uuid4(),
                            order=1,
                            purpose="表现林夏推门并观察房间",
                            shot="medium shot",
                            characters=["林夏"],
                            dialogue=[],
                            narration=[],
                            sfx=["吱呀"],
                            visual_prompt="black and white manga, woman opening a door, no text",
                            negative_prompt="watermark, text, logo",
                            source_anchor_ids=[beat.anchor_id for beat in request.story_beats],
                        )
                    ],
                )
            ],
        )
        return ModelCandidate(
            document=document,
            provider="openai-compatible",
            model=self.configuration.model,
            endpoint_host="models.example.test",
            prompt_template_version="storyboard-1.0",
            response_sha256="a" * 64,
            input_tokens=20,
            output_tokens=30,
            duration_ms=5,
            repair_attempts=0,
        )


def prepare_storyboard(
    client: TestClient, headers: dict[str, str], *, approve: bool = True
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    project = client.post("/api/v1/projects", headers=headers, json={"title": "设定测试"}).json()
    project_id = str(project["project_id"])
    text = "第一章 雨夜\n林夏推开门。\n她发现地上有一封信。\n"
    preflight = client.post(
        f"/api/v1/projects/{project_id}/source/preflight",
        headers=headers,
        files={"file": ("story.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    source = client.post(
        f"/api/v1/projects/{project_id}/source/confirm",
        headers=headers,
        json={"preflight_id": preflight["preflight_id"], "encoding": "utf-8"},
    ).json()
    chapter = source["chapters"][0]
    client.post(
        f"/api/v1/projects/{project_id}/source/chapters/{chapter['chapter_id']}/story-beats/draft",
        headers=headers,
    )
    client.post(
        "/api/v1/vault",
        headers=headers,
        json={"master_password": "unit test master password"},
    )
    client.put(
        "/api/v1/vault/profiles/text-model",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "label": "文本模型",
            "secret": "unit-secret-value",
        },
    )
    client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=headers,
        json={
            "base_url": "https://models.example.test/v1",
            "model": "unit-model",
            "credential_profile_id": "text-model",
        },
    )
    client.app.state.adaptation.provider_factory = lambda configuration, secret_reader: (
        StubTextModel(configuration, secret_reader)
    )
    storyboard_response = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    )
    assert storyboard_response.status_code == 201
    storyboard = storyboard_response.json()
    if approve:
        approval = client.post(
            f"/api/v1/projects/{project_id}/adaptation/storyboards/"
            f"{storyboard['storyboard_version_id']}/approve",
            headers=headers,
        )
        assert approval.status_code == 200
    return project_id, chapter, storyboard


def generate_bibles(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    storyboard_version_id: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/bibles/generate",
        headers=headers,
        json={"storyboard_version_id": storyboard_version_id},
    )
    assert response.status_code == 201
    return response.json()


def completed_character_document(bundle: dict[str, Any]) -> dict[str, Any]:
    document = json.loads(json.dumps(bundle["character_bible"]["document"]))
    character = document["characters"][0]
    character.update(
        {
            "narrative_role": "主角",
            "age_range": "20-25 岁",
            "face_shape": "鹅蛋脸，细长眉，明亮双眼",
            "hair": "齐肩黑发，侧分刘海",
            "body_type": "中等身高，清瘦体型",
            "outfit": ["浅色衬衫", "深色长裤"],
            "signature_features": ["左眼下方小痣"],
            "forbidden_changes": ["发长不得超过肩部", "眼下小痣不得消失"],
            "expression_range": ["警觉", "疑惑", "坚定"],
            "positive_prompt_fragment": "young woman, shoulder-length black hair, beauty mark",
            "negative_prompt_fragment": "long hair, missing beauty mark, different outfit",
        }
    )
    return document


def approve_complete_bibles(
    client: TestClient, headers: dict[str, str], project_id: str, bundle: dict[str, Any]
) -> dict[str, Any]:
    character_version = bundle["character_bible"]
    revised = client.post(
        f"/api/v1/projects/{project_id}/bibles/characters/"
        f"{character_version['version_id']}/revisions",
        headers=headers,
        json={"document": completed_character_document(bundle)},
    )
    assert revised.status_code == 201
    character_version = revised.json()
    character_approval = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/{character_version['version_id']}/approve",
        headers=headers,
    )
    assert character_approval.status_code == 200
    style_version = bundle["style_bible"]
    style_approval = client.post(
        f"/api/v1/projects/{project_id}/bibles/style/{style_version['version_id']}/approve",
        headers=headers,
    )
    assert style_approval.status_code == 200
    return client.get(
        f"/api/v1/projects/{project_id}/bibles?chapter_id={bundle['chapter_id']}"
    ).json()


def test_bible_draft_requires_current_approved_storyboard(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapter, storyboard = prepare_storyboard(client, session_headers, approve=False)

    response = client.post(
        f"/api/v1/projects/{project_id}/bibles/generate",
        headers=session_headers,
        json={"storyboard_version_id": storyboard["storyboard_version_id"]},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STORYBOARD_APPROVAL_REQUIRED"


def test_character_and_style_bibles_are_independently_versioned_and_approved(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapter, storyboard = prepare_storyboard(client, session_headers)
    bundle = generate_bibles(
        client, session_headers, project_id, storyboard["storyboard_version_id"]
    )

    assert bundle["generation_readiness"]["ready"] is False
    assert bundle["character_bible"]["document"]["characters"][0]["name"] == "林夏"
    assert bundle["character_bible"]["approval_issues"]
    assert bundle["style_bible"]["approval_issues"] == []
    blocked = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/"
        f"{bundle['character_bible']['version_id']}/approve",
        headers=session_headers,
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "BIBLE_NOT_APPROVABLE"

    ready_bundle = approve_complete_bibles(client, session_headers, project_id, bundle)

    assert ready_bundle["generation_readiness"]["ready"] is True
    assert ready_bundle["character_bible"]["approval_status"] == "approved"
    assert ready_bundle["style_bible"]["approval_status"] == "approved"
    assert ready_bundle["character_bible"]["version"] == 2
    assert ready_bundle["character_bible"]["provenance"]["affected_panel_ids"]


def test_reference_upload_creates_new_version_and_invalidates_current_readiness(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapter, storyboard = prepare_storyboard(client, session_headers)
    bundle = generate_bibles(
        client, session_headers, project_id, storyboard["storyboard_version_id"]
    )
    ready_bundle = approve_complete_bibles(client, session_headers, project_id, bundle)
    character_version = ready_bundle["character_bible"]
    character_id = character_version["document"]["characters"][0]["character_id"]

    denied = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/"
        f"{character_version['version_id']}/references",
        headers=session_headers,
        data={
            "character_id": character_id,
            "source_note": "用户本人绘制",
            "rights_confirmed": "false",
        },
        files={"file": ("portrait.png", png_bytes(), "image/png")},
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "REFERENCE_RIGHTS_NOT_CONFIRMED"

    image = png_bytes()
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/"
        f"{character_version['version_id']}/references",
        headers=session_headers,
        data={
            "character_id": character_id,
            "source_note": "用户本人绘制",
            "rights_confirmed": "true",
        },
        files={"file": ("../portrait.png", image, "application/octet-stream")},
    )
    assert uploaded.status_code == 201
    payload = uploaded.json()
    assert payload["bible"]["version"] == 3
    assert payload["bible"]["approval_status"] == "draft"
    assert payload["reference_asset"]["original_filename"] == "portrait.png"
    assert payload["reference_asset"]["sha256"] == hashlib.sha256(image).hexdigest()
    assert payload["reference_asset"]["media_type"] == "image/png"
    assert "relative_path" not in payload["reference_asset"]

    bundle_after_upload = client.get(
        f"/api/v1/projects/{project_id}/bibles?chapter_id={ready_bundle['chapter_id']}"
    ).json()
    assert bundle_after_upload["generation_readiness"]["ready"] is False
    style_version = bundle_after_upload["style_bible"]
    invalid_style = client.post(
        f"/api/v1/projects/{project_id}/bibles/style/{style_version['version_id']}/references",
        headers=session_headers,
        data={"source_note": "本人整理", "rights_confirmed": "true"},
        files={"file": ("broken.png", b"not-an-image", "image/png")},
    )
    assert invalid_style.status_code == 422
    assert invalid_style.json()["error"]["code"] == "INVALID_REFERENCE_IMAGE"
    style_upload = client.post(
        f"/api/v1/projects/{project_id}/bibles/style/{style_version['version_id']}/references",
        headers=session_headers,
        data={"source_note": "本人整理的风格样张", "rights_confirmed": "true"},
        files={"file": ("style.png", image, "image/png")},
    )
    assert style_upload.status_code == 201
    assert style_upload.json()["bible"]["version"] == 2
    assert style_upload.json()["reference_asset"]["character_id"] is None
    reference_id = payload["reference_asset"]["reference_asset_id"]
    unauthorized = client.get(
        f"/api/v1/projects/{project_id}/bibles/references/{reference_id}/content"
    )
    assert unauthorized.status_code == 401
    content = client.get(
        f"/api/v1/projects/{project_id}/bibles/references/{reference_id}/content",
        headers=session_headers,
    )
    assert content.status_code == 200
    assert content.content == image

    with client.app.state.database.reader() as connection:
        row = connection.execute(
            """
            SELECT relative_path, rights_confirmed FROM reference_assets
            WHERE bible_kind = 'character'
            """
        ).fetchone()
        character_approval_count = connection.execute(
            "SELECT COUNT(*) FROM character_bible_approvals"
        ).fetchone()[0]
    assert row["relative_path"].startswith("assets/references/")
    assert row["rights_confirmed"] == 1
    assert character_approval_count == 1


def test_storyboard_source_change_marks_bibles_stale(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, storyboard = prepare_storyboard(client, session_headers)
    bundle = generate_bibles(
        client, session_headers, project_id, storyboard["storyboard_version_id"]
    )
    ready_bundle = approve_complete_bibles(client, session_headers, project_id, bundle)
    assert ready_bundle["generation_readiness"]["ready"] is True

    redrafted = client.post(
        f"/api/v1/projects/{project_id}/source/chapters/{chapter['chapter_id']}/story-beats/draft",
        headers=session_headers,
    )
    assert redrafted.status_code == 201
    stale = client.get(
        f"/api/v1/projects/{project_id}/bibles?chapter_id={chapter['chapter_id']}"
    ).json()

    assert stale["character_bible"]["approval_status"] == "stale"
    assert stale["style_bible"]["approval_status"] == "stale"
    assert stale["generation_readiness"]["ready"] is False


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (12, 8), color=(240, 240, 240)).save(output, format="PNG")
    return output.getvalue()
