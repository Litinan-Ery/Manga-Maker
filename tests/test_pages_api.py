from __future__ import annotations

import asyncio
import copy
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from backend.app.novelai.mock import MockNovelAIClient
from tests.test_generation_queue import prepare_job, transition


def test_page_draft_and_revision_render_without_external_image_calls(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    calls_before_layout = provider.generation_calls

    assert page["version"] == 1
    assert page["page_revision"] == 2
    assert page["document"]["width"] == 2048
    assert page["document"]["height"] == 3072
    assert page["external_requests_started"] == 0
    assert len(page["render_sha256"]) == 64
    assert page["document"]["text_layers"][0]["text"] == "吱呀"

    content_url = (
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions/"
        f"{page['page_version_id']}/content"
    )
    assert client.get(content_url).status_code == 401
    original = client.get(content_url, headers=session_headers)
    assert original.status_code == 200
    with Image.open(BytesIO(original.content)) as image:
        assert image.size == (2048, 3072)

    document = copy.deepcopy(page["document"])
    document["text_layers"][0]["text"] = "哐当! 新的音效"
    document["panels"][0]["focal_x"] = 0.2
    document["panels"][0]["zoom"] = 1.25
    revised = client.post(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions",
        headers=session_headers,
        json={"expected_revision": page["page_revision"], "document": document},
    )
    assert revised.status_code == 201, revised.text
    next_page = revised.json()
    assert next_page["version"] == 2
    assert next_page["parent_page_version_id"] == page["page_version_id"]
    assert next_page["render_sha256"] != page["render_sha256"]
    assert provider.generation_calls == calls_before_layout

    old_version = client.get(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions/"
        f"{page['page_version_id']}"
    )
    assert old_version.status_code == 200
    assert old_version.json()["is_current"] is False
    assert old_version.json()["document"]["text_layers"][0]["text"] == "吱呀"

    duplicate = client.post(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions",
        headers=session_headers,
        json={
            "expected_revision": next_page["page_revision"],
            "document": next_page["document"],
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["page_version_id"] == next_page["page_version_id"]
    assert provider.generation_calls == calls_before_layout


def test_page_revision_rejects_conflict_panel_removal_and_text_overflow(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    endpoint = f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions"

    conflict = client.post(
        endpoint,
        headers=session_headers,
        json={"expected_revision": 1, "document": page["document"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "PAGE_REVISION_CONFLICT"

    missing_panel = copy.deepcopy(page["document"])
    missing_panel["panels"] = []
    rejected = client.post(
        endpoint,
        headers=session_headers,
        json={"expected_revision": page["page_revision"], "document": missing_panel},
    )
    assert rejected.status_code == 422

    overflow = copy.deepcopy(page["document"])
    overflow["text_layers"][0].update(
        {
            "text": "这是一段不能被静默截断的中文文字" * 12,
            "font_size": 180,
            "bounds": {"x": 100, "y": 100, "width": 160, "height": 96},
        }
    )
    overflowed = client.post(
        endpoint,
        headers=session_headers,
        json={"expected_revision": page["page_revision"], "document": overflow},
    )
    assert overflowed.status_code == 422
    assert overflowed.json()["error"]["code"] == "PAGE_RENDER_INVALID"
    assert provider.generation_calls == 1


def test_page_templates_cover_one_to_six_panels(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, _provider, _page = prepare_page(client, session_headers)
    response = client.get(
        f"/api/v1/projects/{prepared['project_id']}/pages/templates"
    )
    assert response.status_code == 200
    templates = response.json()
    assert [item["panel_count"] for item in templates[:6]] == [1, 2, 3, 4, 5, 6]
    assert len(templates) == 16
    assert {item["layout_mode"] for item in templates} == {"page", "vertical_strip"}
    assert all(
        "standard" not in item["compatible_page_types"]
        for item in templates
        if item["panel_count"] <= 2
    )
    assert all(
        set(item["compatible_page_types"])
        == {"standard", "cover", "splash", "special"}
        for item in templates
        if item["panel_count"] >= 3
    )
    assert next(item for item in templates if item["template_id"] == "strip-6") == {
        "template_id": "strip-6",
        "label": "6 格·竖向条漫",
        "panel_count": 6,
        "compatible_page_types": ["standard", "cover", "splash", "special"],
        "width": 1440,
        "height": 9484,
        "reading_direction": "top_to_bottom",
        "layout_mode": "vertical_strip",
        "frames": [
            {"x": 96, "y": 96 + index * 1536, "width": 1248, "height": 1500}
            for index in range(6)
        ],
    }


def prepare_page(
    client: TestClient, session_headers: dict[str, str]
) -> tuple[dict[str, Any], MockNovelAIClient, dict[str, Any]]:
    prepared = prepare_job(client, session_headers, title_suffix="页面")
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    provider = MockNovelAIClient()
    client.app.state.generation_executor.provider_factory = (
        lambda _configuration, _secret_reader: provider
    )
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))
    drafted = client.post(
        f"/api/v1/projects/{prepared['project_id']}/pages/draft",
        headers=session_headers,
        json={"chapter_id": prepared["chapter"]["chapter_id"]},
    )
    assert drafted.status_code == 201, drafted.text
    pages = drafted.json()
    assert len(pages) == 1
    return prepared, provider, pages[0]
