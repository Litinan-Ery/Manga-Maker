from __future__ import annotations

import json
from typing import Any, Literal
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
from backend.app.adaptation.text_model import (
    ModelCandidate,
    SecretReader,
    TextModelAuthenticationError,
    TextModelConfiguration,
)


class StubTextModel:
    def __init__(
        self,
        configuration: TextModelConfiguration,
        secret_reader: SecretReader,
        *,
        resolution_status: Literal["represented", "unresolved"] = "represented",
    ) -> None:
        self.configuration = configuration
        self.secret_reader = secret_reader
        self.resolution_status = resolution_status

    async def validate_configuration(self) -> bool:
        return (
            self.secret_reader(self.configuration.credential_profile_id) == "unit-credential-value"
        )

    async def generate_storyboard(self, request: StoryboardRequest) -> ModelCandidate:
        assert (
            self.secret_reader(self.configuration.credential_profile_id) == "unit-credential-value"
        )
        document = storyboard_document(request, self.resolution_status)
        return ModelCandidate(
            document=document,
            provider="openai-compatible",
            model=self.configuration.model,
            endpoint_host="models.example.test",
            prompt_template_version="storyboard-1.0",
            response_sha256="a" * 64,
            input_tokens=120,
            output_tokens=80,
            duration_ms=25,
            repair_attempts=0,
        )


class AuthenticationFailureTextModel(StubTextModel):
    async def generate_storyboard(self, request: StoryboardRequest) -> ModelCandidate:
        raise TextModelAuthenticationError("rejected")


def storyboard_document(
    request: StoryboardRequest, status: Literal["represented", "unresolved"]
) -> StoryboardDocument:
    resolution_status = status  # keep the test fixture readable at construction sites
    scene_id = uuid4()
    return StoryboardDocument(
        schema_version="1.0",
        storyboard_id=uuid4(),
        chapter_version=request.chapter_version,
        beat_resolutions=[
            BeatResolution(
                beat_id=beat.beat_id,
                status=resolution_status,
                reason=None,
                page_numbers=[1] if resolution_status != "unresolved" else [],
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
                turning_point="主角进入房间",
                scene_ids=[scene_id],
                panels=[
                    PanelCandidate(
                        panel_id=uuid4(),
                        order=1,
                        purpose="表现主角推门并观察房间",
                        shot="medium shot",
                        characters=["林夏"],
                        dialogue=[],
                        narration=[],
                        sfx=["吱呀"],
                        visual_prompt="black and white manga, character opening a door, no text",
                        negative_prompt="watermark, text, logo",
                        source_anchor_ids=[beat.anchor_id for beat in request.story_beats],
                    )
                ],
            )
        ],
    )


def create_project_and_source(
    client: TestClient, headers: dict[str, str]
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    project = client.post("/api/v1/projects", headers=headers, json={"title": "改编测试"}).json()
    text = "第一章 雨夜\n林夏推开门。\n她发现地上有一封信。\n"
    preflight = client.post(
        f"/api/v1/projects/{project['project_id']}/source/preflight",
        headers=headers,
        files={"file": ("story.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    confirmed = client.post(
        f"/api/v1/projects/{project['project_id']}/source/confirm",
        headers=headers,
        json={"preflight_id": preflight["preflight_id"], "encoding": "utf-8"},
    ).json()
    chapter = confirmed["chapters"][0]
    beat_set = client.post(
        f"/api/v1/projects/{project['project_id']}/source/chapters/"
        f"{chapter['chapter_id']}/story-beats/draft",
        headers=headers,
    ).json()
    return project["project_id"], chapter, beat_set


def configure_vault_and_model(
    client: TestClient, headers: dict[str, str], project_id: str
) -> dict[str, Any]:
    created = client.post(
        "/api/v1/vault",
        headers=headers,
        json={"master_password": "unit test master password"},
    )
    assert created.status_code == 201
    saved_secret = client.put(
        "/api/v1/vault/profiles/text-model",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "label": "文本模型",
            "secret": "unit-credential-value",
        },
    )
    assert saved_secret.status_code == 200
    configured = client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=headers,
        json={
            "base_url": "https://models.example.test/v1",
            "model": "unit-model",
            "credential_profile_id": "text-model",
            "timeout_seconds": 30,
            "temperature": 0.1,
        },
    )
    assert configured.status_code == 200
    return configured.json()


def install_stub(
    client: TestClient,
    *,
    resolution_status: Literal["represented", "unresolved"] = "represented",
) -> None:
    client.app.state.adaptation.provider_factory = lambda configuration, secret_reader: (
        StubTextModel(
            configuration,
            secret_reader,
            resolution_status=resolution_status,
        )
    )


def test_text_model_configuration_uses_four_fields_and_reuses_saved_secret(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = client.post(
        "/api/v1/projects",
        headers=session_headers,
        json={"title": "文本模型配置测试"},
    ).json()
    project_id = str(project["project_id"])
    created = client.post(
        "/api/v1/vault",
        headers=session_headers,
        json={"master_password": "unit test master password"},
    )
    assert created.status_code == 201

    missing_secret = client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=session_headers,
        json={
            "remark_name": "主力分镜模型",
            "url": "https://models.example.test/v1",
            "request_model": "unit-model",
        },
    )
    assert missing_secret.status_code == 422
    assert missing_secret.json()["error"]["code"] == "TEXT_MODEL_CREDENTIAL_REQUIRED"

    saved = client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=session_headers,
        json={
            "remark_name": "  主力分镜模型  ",
            "url": "https://models.example.test/v1",
            "key_password": "unit-credential-value",
            "request_model": "unit-model",
        },
    )
    assert saved.status_code == 200
    first = saved.json()
    assert first["remark_name"] == "主力分镜模型"
    assert first["url"] == "https://models.example.test/v1"
    assert first["request_model"] == "unit-model"
    assert first["provider_api_url"] == first["url"]
    assert first["model_name"] == first["request_model"]
    assert first["credential_fingerprint"] == "…alue"
    assert "key_password" not in first
    assert "unit-credential-value" not in json.dumps(first)

    updated = client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=session_headers,
        json={
            "remark_name": "备用分镜模型",
            "url": "https://backup.example.test/v1",
            "request_model": "updated-model",
        },
    )
    assert updated.status_code == 200
    second = updated.json()
    assert second["remark_name"] == "备用分镜模型"
    assert second["url"] == "https://backup.example.test/v1"
    assert second["request_model"] == "updated-model"
    assert second["revision"] == 2
    assert second["credential_fingerprint"] == first["credential_fingerprint"]
    assert client.app.state.vault.get_secret(second["credential_profile_id"]) == (
        "unit-credential-value"
    )

    loaded = client.get(
        f"/api/v1/projects/{project_id}/adaptation/text-model"
    )
    assert loaded.status_code == 200
    assert loaded.json()["remark_name"] == "备用分镜模型"
    with client.app.state.database.reader() as connection:
        row = connection.execute(
            """
            SELECT remark_name, base_url, model, credential_profile_id
            FROM text_model_configs WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        audits = connection.execute(
            "SELECT payload_json FROM audit_events WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    assert row is not None
    assert dict(row) == {
        "remark_name": "备用分镜模型",
        "base_url": "https://backup.example.test/v1",
        "model": "updated-model",
        "credential_profile_id": f"text-model-{project_id}",
    }
    assert "unit-credential-value" not in json.dumps([dict(item) for item in audits])


def test_configuration_generation_revision_and_approval_are_versioned(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _beat_set = create_project_and_source(client, session_headers)
    configuration = configure_vault_and_model(client, session_headers, project_id)
    assert configuration["credential_fingerprint"] == "…alue"
    assert "unit-credential-value" not in json.dumps(configuration)
    install_stub(client)

    connection_test = client.post(
        f"/api/v1/projects/{project_id}/adaptation/text-model/test",
        headers=session_headers,
    )
    assert connection_test.status_code == 200
    assert connection_test.json()["status"] == "ok"

    generated = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    )
    assert generated.status_code == 201
    first = generated.json()
    assert first["version"] == 1
    assert first["approval_status"] == "draft"
    assert first["unresolved_count"] == 0
    assert first["document"]["storyboard_id"] == first["storyboard_id"]
    assert first["provenance"]["input_tokens"] == 120

    approved = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{first['storyboard_version_id']}/approve",
        headers=session_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "approved"
    first_approval_hash = approved.json()["approval_hash"]
    assert len(first_approval_hash) == 64

    approved_again = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{first['storyboard_version_id']}/approve",
        headers=session_headers,
    )
    assert approved_again.status_code == 200
    assert approved_again.json()["approval_hash"] == first_approval_hash

    revised_document = first["document"]
    revised_document["pages"][0]["turning_point"] = "主角发现关键线索"
    revised = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{first['storyboard_version_id']}/revisions",
        headers=session_headers,
        json={"document": revised_document},
    )
    assert revised.status_code == 201
    second = revised.json()
    assert second["version"] == 2
    assert second["approval_status"] == "draft"
    assert second["provenance"]["change_type"] == "manual_edit"
    assert second["document"]["pages"][0]["turning_point"] == "主角发现关键线索"

    old_version = client.get(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/{first['storyboard_version_id']}"
    ).json()
    assert old_version["is_current"] is False
    assert old_version["approval_status"] == "approved"

    old_revision = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{first['storyboard_version_id']}/revisions",
        headers=session_headers,
        json={"document": revised_document},
    )
    assert old_revision.status_code == 409

    secret = "unit-credential-value"
    with client.app.state.database.reader() as connection:
        stored = connection.execute(
            "SELECT document_json, provenance_json FROM storyboard_versions"
        ).fetchall()
        audit = connection.execute("SELECT payload_json FROM audit_events").fetchall()
    assert secret not in json.dumps([dict(row) for row in stored])
    assert secret not in json.dumps([dict(row) for row in audit])


def test_unresolved_story_beats_block_approval(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _beat_set = create_project_and_source(client, session_headers)
    configure_vault_and_model(client, session_headers, project_id)
    install_stub(client, resolution_status="unresolved")

    generated = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    ).json()
    assert generated["unresolved_count"] == len(generated["document"]["beat_resolutions"])

    approval = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{generated['storyboard_version_id']}/approve",
        headers=session_headers,
    )
    assert approval.status_code == 422
    assert approval.json()["error"]["code"] == "STORYBOARD_NOT_APPROVABLE"


def test_new_beat_set_marks_storyboard_stale(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _beat_set = create_project_and_source(client, session_headers)
    configure_vault_and_model(client, session_headers, project_id)
    install_stub(client)
    generated = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    ).json()

    redrafted = client.post(
        f"/api/v1/projects/{project_id}/source/chapters/{chapter['chapter_id']}/story-beats/draft",
        headers=session_headers,
    )
    assert redrafted.status_code == 201
    stale = client.get(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/{generated['storyboard_version_id']}"
    ).json()
    assert stale["approval_status"] == "stale"

    approval = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{generated['storyboard_version_id']}/approve",
        headers=session_headers,
    )
    assert approval.status_code == 409
    assert approval.json()["error"]["code"] == "STORYBOARD_SOURCE_STALE"


def test_locked_vault_and_authentication_failure_are_classified(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _beat_set = create_project_and_source(client, session_headers)
    configure_vault_and_model(client, session_headers, project_id)
    install_stub(client)

    locked = client.post("/api/v1/vault/lock", headers=session_headers)
    assert locked.status_code == 200
    connection_test = client.post(
        f"/api/v1/projects/{project_id}/adaptation/text-model/test",
        headers=session_headers,
    )
    assert connection_test.status_code == 423
    assert connection_test.json()["error"]["code"] == "VAULT_LOCKED"

    unlocked = client.post(
        "/api/v1/vault/unlock",
        headers=session_headers,
        json={"master_password": "unit test master password"},
    )
    assert unlocked.status_code == 200
    client.app.state.adaptation.provider_factory = lambda configuration, secret_reader: (
        AuthenticationFailureTextModel(configuration, secret_reader)
    )
    generation = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "page_budget": 2},
    )
    assert generation.status_code == 424
    assert generation.json()["error"]["code"] == "TEXT_MODEL_AUTHENTICATION_FAILED"
