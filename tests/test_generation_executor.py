from __future__ import annotations

import asyncio
import errno
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.novelai.client import (
    NovelAIGeneratedImage,
    NovelAIImageRequest,
    NovelAITemporaryError,
    NovelAIUnknownOutcomeError,
)
from backend.app.novelai.mock import MockNovelAIClient
from tests.test_generation_queue import (
    create_job,
    estimate_plan,
    prepare_generation_inputs,
    prepare_job,
    prepare_prompting,
    transition,
)


def test_mock_executor_creates_immutable_png_provenance_and_asset_version(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="素材")
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    provider = MockNovelAIClient()
    install_image_provider(client, provider)

    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    job = client.app.state.generation_queue.get_job(
        prepared["project_id"], started["job_id"]
    )
    assert job["status"] == "completed"
    assert job["items_claimed"] == 1
    assert job["calls_started"] == 1
    assert job["calls_completed"] == 1
    assert job["allocated_cost_anlas"] == 10
    assert job["recorded_cost_anlas"] == 0
    assert job["unverified_cost_calls"] == 1
    assert provider.generation_calls == 1
    assert job["items"][0]["asset_version_id"]

    assets = client.get(
        f"/api/v1/projects/{prepared['project_id']}/generation/assets"
    )
    assert assets.status_code == 200
    asset = assets.json()[0]
    assert asset["version"] == 1
    assert asset["is_current"] is True
    assert (asset["width"], asset["height"]) == (832, 1216)

    denied = client.get(
        f"/api/v1/projects/{prepared['project_id']}/generation/assets/"
        f"{asset['asset_version_id']}/content"
    )
    assert denied.status_code == 401
    content = client.get(
        f"/api/v1/projects/{prepared['project_id']}/generation/assets/"
        f"{asset['asset_version_id']}/content",
        headers=session_headers,
    )
    assert content.status_code == 200
    with Image.open(BytesIO(content.content)) as image:
        assert image.size == (832, 1216)

    with client.app.state.database.reader() as connection:
        row = connection.execute(
            """
            SELECT av.original_relative_path, av.provenance_relative_path,
                   gs.document_json, p.workspace_path
            FROM asset_versions av
            JOIN generation_specs gs ON gs.spec_id = av.spec_id
            JOIN projects p ON p.project_id = av.project_id
            WHERE av.asset_version_id = ?
            """,
            (asset["asset_version_id"],),
        ).fetchone()
    workspace = Path(str(row["workspace_path"]))
    original = workspace / str(row["original_relative_path"])
    provenance_path = workspace / str(row["provenance_relative_path"])
    assert original.read_bytes() == content.content
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    spec = json.loads(str(row["document_json"]))
    assert provenance["image_sha256"] == asset["image_sha256"]
    assert provenance["cost_record_status"] == "not_reported"
    assert provenance["credential_included"] is False
    assert provenance["correlation_id"] == spec["correlation_id"]
    assert provenance["generated_at"].endswith("+00:00")
    assert spec["seed"] == asset["seed"]
    assert "no text" in spec["prompt"]
    serialized = json.dumps({"provenance": provenance, "spec": spec})
    assert "unit-novelai-secret" not in serialized


def test_precise_reference_is_compiled_once_with_hashes_and_padding(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, ready_bundle = prepare_generation_inputs(client, session_headers)
    character = ready_bundle["character_bible"]
    character_id = character["document"]["characters"][0]["character_id"]
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/"
        f"{character['version_id']}/references",
        headers=session_headers,
        data={
            "character_id": character_id,
            "source_note": "用户本人绘制的角色参考",
            "rights_confirmed": "true",
        },
        files={"file": ("character.png", image_bytes(200, 400), "image/png")},
    )
    assert uploaded.status_code == 201
    next_character = uploaded.json()["bible"]
    approved = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/"
        f"{next_character['version_id']}/approve",
        headers=session_headers,
    )
    assert approved.status_code == 200

    prepare_prompting(
        client,
        session_headers,
        project_id,
        str(chapter["chapter_id"]),
    )

    estimate = estimate_plan(client, session_headers, project_id, chapter["chapter_id"])
    created = create_job(
        client, session_headers, project_id, chapter["chapter_id"], estimate
    )
    assert created.status_code == 201
    started = transition(client, session_headers, project_id, created.json(), "start")
    provider = MockNovelAIClient()
    install_image_provider(client, provider)
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    with client.app.state.database.reader() as connection:
        spec_row = connection.execute("SELECT document_json FROM generation_specs").fetchone()
    spec = json.loads(str(spec_row["document_json"]))
    assert len(spec["references"]) == 1
    reference = spec["references"][0]
    assert reference["description"] == "character"
    assert (reference["prepared_width"], reference["prepared_height"]) == (1024, 1536)
    assert len(reference["original_sha256"]) == 64
    assert len(reference["prepared_sha256"]) == 64


def test_temporary_provider_errors_retry_only_within_frozen_limits(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    estimate = estimate_plan(client, session_headers, project_id, chapter["chapter_id"])
    created = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "per_panel_cost_ceiling_anlas": 10,
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": 3,
            "max_cost_anlas": 30,
            "confirmed": True,
        },
    )
    assert created.status_code == 201
    started = transition(client, session_headers, project_id, created.json(), "start")
    provider = TwoTemporaryFailures()
    install_image_provider(client, provider)
    client.app.state.generation_executor.retry_sleep = no_sleep

    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    job = client.app.state.generation_queue.get_job(project_id, started["job_id"])
    assert job["status"] == "completed"
    assert job["calls_started"] == 3
    assert job["allocated_cost_anlas"] == 30
    assert job["items"][0]["attempt_count"] == 3
    assert provider.generation_calls == 3


def test_unknown_provider_outcome_stops_without_replay(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="未知")
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    provider = MockNovelAIClient(
        generation_failure=NovelAIUnknownOutcomeError("connection lost")
    )
    install_image_provider(client, provider)

    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    job = client.app.state.generation_queue.get_job(
        prepared["project_id"], started["job_id"]
    )
    assert job["status"] == "needs_review"
    assert job["calls_started"] == 1
    assert job["calls_completed"] == 0
    assert job["items"][0]["status"] == "needs_review"
    assert provider.generation_calls == 1
    assert client.app.state.asset_store.current_assets(prepared["project_id"]) == []


def test_disk_full_after_provider_response_stops_without_replay(
    client: TestClient,
    session_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="生成磁盘不足")
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    provider = MockNovelAIClient()
    install_image_provider(client, provider)

    def disk_full(_path: Path, _payload: bytes) -> None:
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr("backend.app.generation.assets.write_synced", disk_full)
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    job = client.app.state.generation_queue.get_job(
        prepared["project_id"], started["job_id"]
    )
    assert job["status"] == "needs_review"
    assert job["calls_started"] == 1
    assert job["calls_completed"] == 0
    assert job["items"][0]["last_error_code"] == "LOCAL_STORAGE_FULL"
    assert provider.generation_calls == 1
    assert client.app.state.generation_queue.claim_next(started["job_id"]) is None
    assert client.app.state.asset_store.current_assets(prepared["project_id"]) == []


def test_locked_vault_fails_before_provider_request_is_counted(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="凭证锁定")
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    provider = MockNovelAIClient()
    install_image_provider(client, provider)
    client.app.state.vault.lock()

    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    job = client.app.state.generation_queue.get_job(
        prepared["project_id"], started["job_id"]
    )
    assert job["status"] == "failed"
    assert job["calls_started"] == 0
    assert job["allocated_cost_anlas"] == 0
    assert provider.generation_calls == 0


def test_execute_endpoint_requires_exact_user_confirmation(
    client: TestClient,
    session_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="确认")
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    endpoint = (
        f"/api/v1/projects/{prepared['project_id']}/generation/jobs/"
        f"{started['job_id']}/execute"
    )
    scheduled: list[str] = []

    def record_schedule(job_id: str) -> bool:
        scheduled.append(job_id)
        return True

    monkeypatch.setattr(
        client.app.state.generation_executor,
        "schedule",
        record_schedule,
    )

    missing = client.post(
        endpoint,
        headers=session_headers,
        json={"expected_revision": started["revision"]},
    )
    wrong = client.post(
        endpoint,
        headers=session_headers,
        json={
            "expected_revision": started["revision"],
            "confirmation": "YES",
        },
    )
    assert missing.status_code == 422
    assert wrong.status_code == 422
    assert scheduled == []

    accepted = client.post(
        endpoint,
        headers=session_headers,
        json={
            "expected_revision": started["revision"],
            "confirmation": "I_CONFIRM_NOVELAI_IMAGE_GENERATION",
        },
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "scheduled"
    assert scheduled == [started["job_id"]]


class TwoTemporaryFailures(MockNovelAIClient):
    failures_remaining = 2

    async def generate_image(
        self, request: NovelAIImageRequest
    ) -> NovelAIGeneratedImage:
        self.generation_calls += 1
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise NovelAITemporaryError("temporary")
        self.generation_calls -= 1
        return await super().generate_image(request)


async def no_sleep(_delay: float) -> None:
    return None


def install_image_provider(client: TestClient, provider: Any) -> None:
    client.app.state.generation_executor.provider_factory = (
        lambda _configuration, _secret_reader: provider
    )


def image_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(200, 190, 180)).save(output, format="PNG")
    return output.getvalue()
