from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from backend.app.adaptation.models import (
    SourceBeatInput,
    StoryboardDocument,
    StoryboardRequest,
    validate_storyboard_semantics,
)
from backend.app.adaptation.page_policy import (
    STORYBOARD_PAGE_POLICY_VERSION,
    StoryboardPagePolicyError,
    validate_storyboard_page_policy,
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
        "schema_version": "1.1",
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
                "page_type": "splash",
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


def storyboard_with(page_type: str | None, panel_count: int) -> dict[str, Any]:
    payload = copy.deepcopy(valid_storyboard())
    page = payload["pages"][0]
    if page_type is None:
        page.pop("page_type")
    else:
        page["page_type"] = page_type
    base_panel = page["panels"][0]
    page["panels"] = [
        {
            **copy.deepcopy(base_panel),
            "panel_id": f"018f0f65-8f2f-7e65-8000-{index:012x}",
            "order": index,
        }
        for index in range(1, panel_count + 1)
    ]
    return payload


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


@pytest.mark.parametrize("panel_count", [3, 6])
def test_standard_pages_accept_three_to_six_panels(panel_count: int) -> None:
    document = StoryboardDocument.model_validate(storyboard_with("standard", panel_count))

    validate_storyboard_page_policy(document)


@pytest.mark.parametrize("page_type", ["cover", "splash", "special"])
@pytest.mark.parametrize("panel_count", [1, 2, 6])
def test_special_pages_accept_one_to_six_panels(
    page_type: str,
    panel_count: int,
) -> None:
    document = StoryboardDocument.model_validate(storyboard_with(page_type, panel_count))

    validate_storyboard_page_policy(document)


@pytest.mark.parametrize("panel_count", [1, 2])
def test_standard_pages_reject_fewer_than_three_panels(panel_count: int) -> None:
    document = StoryboardDocument.model_validate(storyboard_with("standard", panel_count))

    with pytest.raises(StoryboardPagePolicyError) as error:
        validate_storyboard_page_policy(document)

    finding = error.value.findings[0]
    assert finding.code == "STORYBOARD_PAGE_POLICY_INVALID"
    assert finding.path == "$.pages[0].panels"
    assert finding.page_type == "standard"
    assert finding.panel_count == panel_count
    assert (finding.minimum_panels, finding.maximum_panels) == (3, 6)


@pytest.mark.parametrize(
    ("page_type", "panel_count"),
    [("standard", 0), ("standard", 7), ("unknown", 3)],
)
def test_empty_over_limit_and_unknown_page_shapes_fail_schema(
    page_type: str,
    panel_count: int,
) -> None:
    with pytest.raises(ValidationError):
        StoryboardDocument.model_validate(storyboard_with(page_type, panel_count))


def test_missing_page_type_and_legacy_schema_are_not_inferred() -> None:
    missing_type = StoryboardDocument.model_validate(storyboard_with(None, 3))
    with pytest.raises(StoryboardPagePolicyError) as missing_error:
        validate_storyboard_page_policy(missing_type)
    assert missing_error.value.findings[0].path == "$.pages[0].page_type"

    legacy_payload = storyboard_with(None, 1)
    legacy_payload["schema_version"] = "1.0"
    legacy = StoryboardDocument.model_validate(legacy_payload)
    with pytest.raises(StoryboardPagePolicyError) as legacy_error:
        validate_storyboard_page_policy(legacy)
    assert legacy_error.value.findings[0].code == "STORYBOARD_UPGRADE_REQUIRED"
    assert legacy.pages[0].page_type is None


def test_page_policy_violation_enters_bounded_structured_repair() -> None:
    invalid = storyboard_with("standard", 1)
    responses = iter([json.dumps(invalid), json.dumps(valid_storyboard())])
    request_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
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
    repair_payload = json.loads(request_bodies[1]["messages"][1]["content"])
    assert repair_payload["request_constraints"]["page_policy_version"] == (
        STORYBOARD_PAGE_POLICY_VERSION
    )
    assert "standard 页面需要 3-6 格" in repair_payload["validation_problem"]


@pytest.mark.parametrize("invalid_kind", ["missing", "unknown", "empty", "over_limit"])
def test_invalid_page_shapes_enter_structured_repair(invalid_kind: str) -> None:
    invalid = storyboard_with("special", 1)
    page = invalid["pages"][0]
    if invalid_kind == "missing":
        page.pop("page_type")
    elif invalid_kind == "unknown":
        page["page_type"] = "poster"
    elif invalid_kind == "empty":
        page["panels"] = []
    else:
        invalid = storyboard_with("standard", 7)
    responses = iter([json.dumps(invalid), json.dumps(valid_storyboard())])
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
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
    assert calls == 2


def test_page_policy_failure_stops_after_two_repairs() -> None:
    invalid = json.dumps(storyboard_with("standard", 1))
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=envelope(invalid))

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
