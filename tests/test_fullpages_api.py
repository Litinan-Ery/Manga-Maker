from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.novelai.client import (
    NovelAIGeneratedImage,
    NovelAIImageRequest,
    NovelAIUnknownOutcomeError,
    image_request_payload,
)
from backend.app.novelai.mock import MockNovelAIClient
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_generation_queue import prepare_generation_inputs, prepare_job, transition


class RecordingProvider(MockNovelAIClient):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, Any]] = []

    async def generate_image(self, request: NovelAIImageRequest) -> NovelAIGeneratedImage:
        self.payloads.append(image_request_payload(request))
        return await super().generate_image(request)


def test_uc03_full_page_preview_is_one_frozen_page_and_does_not_generate(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    response = client.post(
        f"/api/v1/projects/{project_id}/full-pages/preview",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "page_number": 1,
            "text_policy": "local",
            "seed": 12345,
            "cost_ceiling_anlas": 0,
        },
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["status"] == "draft"
    assert plan["generation_calls"] == 1
    assert plan["external_requests_started"] == 0
    assert plan["provider_payload"]["parameters"]["n_samples"] == 1
    assert "manga" in plan["provider_payload"]["input"]
    assert "Panel " in plan["provider_payload"]["input"]


@pytest.mark.parametrize("text_policy", ["local", "model"])
def test_uc03_preview_execute_review_and_adopt_without_duplicate_calls(
    client: TestClient,
    session_headers: dict[str, str],
    text_policy: str,
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    provider = RecordingProvider()
    client.app.state.container.full_pages.provider_factory = lambda _config, _reader: provider
    base = f"/api/v1/projects/{project_id}/full-pages"
    preview = client.post(
        base + "/preview",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "page_number": 1,
            "text_policy": text_policy,
            "seed": 42,
            "cost_ceiling_anlas": 0,
        },
    )
    assert preview.status_code == 201, preview.text
    plan = preview.json()
    path = base + f"/{plan['generation_id']}"
    params = plan["provider_payload"]["parameters"]
    assert params["qualityToggle"] is False
    assert params["tag_hint_uc_preset"] == 0
    assert params["tag_hint_qt"] == 0
    assert "ucPreset" not in params
    assert params["v4_prompt"]["caption"]["char_captions"] == []
    if text_policy == "model":
        assert "no text" not in plan["provider_payload"]["input"]
        assert plan["provider_payload"]["input"].endswith("Text: 吱呀")
        assert "text" not in params["negative_prompt"].split(", ")
    denied = client.post(
        path + "/generate",
        headers=session_headers,
        json={
            "plan_sha256": "0" * 64,
            "confirmed": True,
        },
    )
    assert denied.status_code == 409
    assert provider.generation_calls == provider.subscription_calls == 0
    generated = client.post(
        path + "/generate",
        headers=session_headers,
        json={
            "plan_sha256": plan["plan_sha256"],
            "confirmed": True,
        },
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["status"] == "ready", generated.text
    assert provider.payloads == [plan["provider_payload"]]
    assert generated.json()["external_requests_started"] == 2
    replay = client.post(
        path + "/generate",
        headers=session_headers,
        json={
            "plan_sha256": plan["plan_sha256"],
            "confirmed": True,
        },
    )
    assert replay.status_code == 200
    assert provider.generation_calls == provider.subscription_calls == 1
    assert client.get(path + "/content", headers=session_headers).content.startswith(b"\x89PNG")
    adopted = client.post(
        path + "/adopt",
        headers=session_headers,
        json={
            "expected_revision": 0,
            "confirmed": True,
        },
    )
    assert adopted.status_code == 201, adopted.text
    document = adopted.json()["document"]
    assert document["page_image"] == {
        "generation_id": plan["generation_id"],
        "text_policy": text_policy,
    }
    assert bool(document["text_layers"]) is (text_policy == "local")
    again = client.post(
        path + "/adopt",
        headers=session_headers,
        json={
            "expected_revision": 0,
            "confirmed": True,
        },
    )
    assert again.json()["page_version_id"] == adopted.json()["page_version_id"]
    assert provider.generation_calls == 1

    preflight = export_preflight(client, session_headers, project_id, chapter["chapter_id"])
    exported_response = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(preflight),
    )
    assert exported_response.status_code == 201, exported_response.text
    exported = exported_response.json()
    package = next(item for item in exported["files"] if item["kind"] == "engineering_package")
    downloaded = download_file(client, session_headers, project_id, exported, package)
    with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
        records = json.loads(archive.read("records.json"))
        assert len(records["tables"]["full_page_generations"]) == 1
    preflight_import = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={
            "file": ("full-page.manga-maker.zip", downloaded, "application/zip"),
        },
    )
    assert preflight_import.status_code == 201, preflight_import.text
    restored_response = client.post(
        f"/api/v1/imports/{preflight_import.json()['import_preflight_id']}/restore",
        headers=session_headers,
        json={"confirmed": True},
    )
    assert restored_response.status_code == 200, restored_response.text
    restored_id = restored_response.json()["project_id"]
    restored_chapters = client.get(f"/api/v1/projects/{restored_id}/source/chapters").json()
    restored_chapter = restored_chapters["chapters"][0]["chapter_id"]
    restored_pages = client.get(
        f"/api/v1/projects/{restored_id}/pages", params={"chapter_id": restored_chapter}
    ).json()
    restored_page = restored_pages[0]
    assert restored_page["render_sha256"] == adopted.json()["render_sha256"]
    restored_history = client.get(
        f"/api/v1/projects/{restored_id}/full-pages", params={"chapter_id": restored_chapter}
    )
    assert restored_history.status_code == 200, restored_history.text
    assert restored_history.json()[0]["generation_id"] != plan["generation_id"]
    next_document = restored_page["document"]
    next_document["show_page_number"] = True
    edited = client.post(
        f"/api/v1/projects/{restored_id}/pages/{restored_page['page_id']}/versions",
        headers=session_headers,
        json={"expected_revision": restored_page["page_revision"], "document": next_document},
    )
    assert edited.status_code == 201, edited.text
    assert provider.generation_calls == 1


def test_uc03_unknown_outcome_is_not_replayed_after_refresh_or_restart(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    provider = RecordingProvider()
    provider.generation_failure = NovelAIUnknownOutcomeError("uncertain")
    service = client.app.state.container.full_pages
    service.provider_factory = lambda _config, _reader: provider
    base = f"/api/v1/projects/{project_id}/full-pages"
    options = {"chapter_id": chapter["chapter_id"], "page_number": 1, "seed": 77}
    plan = client.post(base + "/preview", headers=session_headers, json=options).json()
    path = base + f"/{plan['generation_id']}/generate"
    approval = {"plan_sha256": plan["plan_sha256"], "confirmed": True}
    failed = client.post(path, headers=session_headers, json=approval)
    assert failed.json()["status"] == "needs_review"
    assert failed.json()["error_code"] == "FULL_PAGE_PROVIDER_OUTCOME_UNKNOWN"
    service.reconcile_startup()
    assert (
        client.post(path, headers=session_headers, json=approval).json()["status"] == "needs_review"
    )
    assert provider.generation_calls == 1
    # A separate, explicitly previewed approval is required for a new attempt.
    provider.generation_failure = None
    next_plan = client.post(base + "/preview", headers=session_headers, json=options).json()
    assert next_plan["generation_id"] != plan["generation_id"]
    result = client.post(
        base + f"/{next_plan['generation_id']}/generate",
        headers=session_headers,
        json={
            "plan_sha256": next_plan["plan_sha256"],
            "confirmed": True,
        },
    )
    assert result.json()["status"] == "ready", result.text
    assert provider.generation_calls == 2


def test_uc03_layout_changes_invalidate_preview_before_any_provider_request(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    provider = RecordingProvider()
    client.app.state.container.full_pages.provider_factory = lambda _config, _reader: provider
    base = f"/api/v1/projects/{project_id}/full-pages"
    plan = client.post(
        base + "/preview",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "page_number": 1,
            "seed": 99,
        },
    ).json()
    with client.app.state.database.writer() as connection:
        connection.execute("UPDATE page_layout_drafts SET is_current = 0")
    blocked = client.post(
        base + f"/{plan['generation_id']}/generate",
        headers=session_headers,
        json={
            "plan_sha256": plan["plan_sha256"],
            "confirmed": True,
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert provider.generation_calls == provider.subscription_calls == 0


@pytest.mark.parametrize("first_mode", ["panel", "full_page"])
def test_uc04_both_generation_modes_share_one_serial_provider_boundary(
    client: TestClient,
    session_headers: dict[str, str],
    first_mode: str,
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="串行门禁")
    project_id = prepared["project_id"]
    job = transition(client, session_headers, project_id, prepared["job"], "start")
    queue = client.app.state.generation_queue
    claims: list[Any] = []

    class ConcurrentProbe(RecordingProvider):
        async def get_subscription(self):
            claims.append(queue.claim_next(job["job_id"]))
            return await super().get_subscription()

    provider = ConcurrentProbe()
    client.app.state.container.full_pages.provider_factory = lambda _config, _reader: provider
    base = f"/api/v1/projects/{project_id}/full-pages"
    plan = client.post(
        base + "/preview",
        headers=session_headers,
        json={
            "chapter_id": prepared["chapter"]["chapter_id"],
            "page_number": 1,
            "seed": 100,
        },
    ).json()
    if first_mode == "panel":
        assert queue.claim_next(job["job_id"]) is not None
    response = client.post(
        base + f"/{plan['generation_id']}/generate",
        headers=session_headers,
        json={
            "plan_sha256": plan["plan_sha256"],
            "confirmed": True,
        },
    )
    if first_mode == "panel":
        assert response.status_code == 409, response.text
        assert provider.generation_calls == provider.subscription_calls == 0
    else:
        assert response.json()["status"] == "ready", response.text
        assert claims == [None]
