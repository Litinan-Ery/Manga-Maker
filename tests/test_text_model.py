from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.app.adaptation.models import (
    SourceBeatInput,
    StoryboardDocument,
    StoryboardRequest,
    validate_storyboard_semantics,
)
from backend.app.adaptation.text_model import (
    OpenAICompatibleTextModel,
    TextModelConfiguration,
    TextModelConfigurationError,
    TextModelStructuredOutputError,
)


def storyboard_request() -> StoryboardRequest:
    return StoryboardRequest(
        chapter_id="chapter-1",
        chapter_version=1,
        chapter_text="林夏推开门。",
        story_beats=[
            SourceBeatInput(
                beat_id="beat-1",
                anchor_id="anchor-1",
                excerpt="林夏推开门。",
            )
        ],
        page_budget=2,
    )


def valid_storyboard() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "storyboard_id": "018f0f65-8f2f-7e65-8000-123456789abc",
        "chapter_version": 1,
        "beat_resolutions": [
            {
                "beat_id": "beat-1",
                "status": "represented",
                "reason": None,
                "page_numbers": [1],
            }
        ],
        "scenes": [
            {
                "scene_id": "018f0f65-8f2f-7e65-8000-123456789abf",
                "order": 1,
                "title": "进入房间",
                "location": "旧屋房间",
                "time_of_day": "夜晚",
                "summary": "林夏推门进入房间。",
                "beat_ids": ["beat-1"],
            }
        ],
        "pages": [
            {
                "page_id": "018f0f65-8f2f-7e65-8000-123456789abd",
                "page_number": 1,
                "turning_point": "主角进入房间",
                "scene_ids": ["018f0f65-8f2f-7e65-8000-123456789abf"],
                "panels": [
                    {
                        "panel_id": "018f0f65-8f2f-7e65-8000-123456789abe",
                        "order": 1,
                        "purpose": "表现林夏推门",
                        "shot": "medium shot",
                        "characters": ["林夏"],
                        "dialogue": [],
                        "narration": [],
                        "sfx": ["吱呀"],
                        "visual_prompt": "black and white manga, woman opening a door, no text",
                        "negative_prompt": "watermark, text, logo",
                        "source_anchor_ids": ["anchor-1"],
                    }
                ],
            }
        ],
    }


def envelope(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 80},
    }


def test_openai_compatible_adapter_validates_and_records_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://models.example.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer unit-value"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "林夏推开门" in body["messages"][1]["content"]
        return httpx.Response(200, json=envelope(json.dumps(valid_storyboard())))

    provider = OpenAICompatibleTextModel(
        TextModelConfiguration(
            base_url="https://models.example.test/v1",
            model="unit-model",
            credential_profile_id="text-model",
        ),
        lambda _profile_id: "unit-value",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(provider.generate_storyboard(storyboard_request()))

    assert result.document.pages[0].panels[0].source_anchor_ids == ["anchor-1"]
    assert result.input_tokens == 120
    assert result.output_tokens == 80
    assert result.repair_attempts == 0
    assert len(result.response_sha256) == 64
    assert result.endpoint_host == "models.example.test"


def test_invalid_output_is_repaired_at_most_twice() -> None:
    responses = iter(["not-json", json.dumps(valid_storyboard())])
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=envelope(next(responses)))

    provider = OpenAICompatibleTextModel(
        TextModelConfiguration(
            base_url="https://models.example.test/v1",
            model="unit-model",
            credential_profile_id="text-model",
        ),
        lambda _profile_id: "unit-value",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(provider.generate_storyboard(storyboard_request()))

    assert result.repair_attempts == 1
    assert result.input_tokens == 240
    assert len(requests) == 2
    repair_body = json.loads(requests[1].content)
    assert "validation_problem" in repair_body["messages"][1]["content"]


def test_invalid_output_stops_after_two_repairs() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=envelope("still-not-json"))

    provider = OpenAICompatibleTextModel(
        TextModelConfiguration(
            base_url="https://models.example.test/v1",
            model="unit-model",
            credential_profile_id="text-model",
        ),
        lambda _profile_id: "unit-value",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(TextModelStructuredOutputError, match="after 2 repairs"):
        asyncio.run(provider.generate_storyboard(storyboard_request()))
    assert calls == 3


def test_scene_mapping_must_cover_each_non_omitted_story_beat() -> None:
    document = StoryboardDocument.model_validate(valid_storyboard())
    document = document.model_copy(
        update={"scenes": [document.scenes[0].model_copy(update={"beat_ids": ["unknown-beat"]})]}
    )

    with pytest.raises(ValueError, match="story beats that were not supplied"):
        validate_storyboard_semantics(document, storyboard_request())


def test_plain_http_is_limited_to_loopback_hosts() -> None:
    with pytest.raises(TextModelConfigurationError, match="must use HTTPS"):
        TextModelConfiguration(
            base_url="http://models.example.test/v1",
            model="unit-model",
            credential_profile_id="text-model",
        )

    configuration = TextModelConfiguration(
        base_url="http://127.0.0.1:8080/v1",
        model="local-model",
        credential_profile_id="text-model",
    )
    assert configuration.base_url == "http://127.0.0.1:8080/v1"
