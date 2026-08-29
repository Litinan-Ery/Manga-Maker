from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import UUID
from zipfile import ZipFile

import httpx
import pytest
from PIL import Image

from backend.app.modules.production.adapters.novelai import map_prompt_plan_to_novelai
from backend.app.modules.prompting.contracts import PromptPlan
from backend.app.modules.prompting.public import prompt_plan_sha256
from backend.app.novelai.client import (
    NovelAIAuthenticationError,
    NovelAIClient,
    NovelAIConfiguration,
    NovelAIConfigurationError,
    NovelAIImageRequest,
    NovelAIInsufficientBalanceError,
    NovelAIInvalidRequestError,
    NovelAIPermissionError,
    NovelAIRateLimitError,
    NovelAIResponseFormatError,
    NovelAISubscriptionResult,
    NovelAITemporaryError,
    NovelAIUnknownOutcomeError,
    NovelAIUsageLimitUnavailableError,
    PreciseReferenceInput,
    novelai_correlation_id,
    require_opus_zero_anlas_available,
)
from backend.app.novelai.contracts import (
    CONNECTION_TEST_PATH,
    GENERATION_PATH,
    SUBSCRIPTION_PATH,
)


def test_connection_test_uses_only_tag_suggestions_and_never_generates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"tags": [{"tag": "manga", "confidence": 0.99, "count": 10}]},
        )

    client = NovelAIClient(
        NovelAIConfiguration(
            provider_model_id="nai-diffusion-5-full",
            credential_profile_id="novelai",
        ),
        lambda _profile_id: "unit-test-secret",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(client.validate_connection())

    assert result.suggestion_count == 1
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == CONNECTION_TEST_PATH
    assert request.url.path != GENERATION_PATH
    assert request.url.params["model"] == "nai-diffusion-5-full"
    assert request.url.params["prompt"] == "manga"
    assert request.headers["Authorization"] == "Bearer unit-test-secret"
    assert request.method == "GET"


def test_connection_test_accepts_strict_json_with_text_plain_content_type() -> None:
    response = httpx.Response(
        200,
        content=b'{"tags":[{"tag":"manga","confidence":0.99,"count":10}]}',
        headers={"content-type": "text/plain; charset=utf-8"},
    )
    client = make_client(httpx.MockTransport(lambda _request: response))

    result = asyncio.run(client.validate_connection())

    assert result.suggestion_count == 1


def test_subscription_probe_validates_active_opus_without_generating() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "active": True,
                "tier": 3,
                "expiresAt": 1_800_000_000,
                "isGracePeriod": False,
                "perks": {},
                "usage": {
                    "percent": 84,
                    "isNegative": False,
                    "timeUntilNextPercent": 42,
                },
            },
        )

    result = asyncio.run(make_client(httpx.MockTransport(handler)).get_subscription())

    assert result.opus_active is True
    assert result.zero_anlas_verification()["subscription_tier"] == 3
    assert result.v5_allowance_available is True
    assert result.zero_anlas_verification()["usage_percent"] == 84
    assert [request.url.path for request in requests] == [SUBSCRIPTION_PATH]
    assert all(request.url.path != GENERATION_PATH for request in requests)


def test_v5_zero_anlas_requires_explicit_available_usage_allowance() -> None:
    available = NovelAISubscriptionResult(
        active=True,
        tier=3,
        expires_at=None,
        is_grace_period=False,
        usage_percent=1,
        usage_is_negative=False,
        usage_time_until_next_percent=30,
    )
    require_opus_zero_anlas_available(available, "nai-diffusion-5-full")

    for unavailable in (
        available.__class__(
            active=True,
            tier=3,
            expires_at=None,
            is_grace_period=False,
        ),
        available.__class__(
            active=True,
            tier=3,
            expires_at=None,
            is_grace_period=False,
            usage_percent=0,
            usage_is_negative=True,
            usage_time_until_next_percent=60,
        ),
    ):
        with pytest.raises(NovelAIUsageLimitUnavailableError):
            require_opus_zero_anlas_available(unavailable, "nai-diffusion-5-full")


@pytest.mark.parametrize(
    "payload",
    [
        {"active": "yes", "tier": 3},
        {"active": True, "tier": True},
        {"active": True, "tier": 3, "expiresAt": "later"},
    ],
)
def test_subscription_probe_rejects_ambiguous_shapes(payload: dict[str, object]) -> None:
    client = make_client(
        httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    with pytest.raises(NovelAIResponseFormatError):
        asyncio.run(client.get_subscription())


@pytest.mark.parametrize(
    ("status_code", "body", "expected_error"),
    [
        (401, {}, NovelAIAuthenticationError),
        (403, {}, NovelAIPermissionError),
        (402, {}, NovelAIInsufficientBalanceError),
        (409, {"message": "insufficient Anlas"}, NovelAIInsufficientBalanceError),
        (429, {}, NovelAIRateLimitError),
        (422, {}, NovelAIInvalidRequestError),
        (503, {}, NovelAITemporaryError),
    ],
)
def test_provider_errors_are_classified_without_automatic_retry(
    status_code: int,
    body: dict[str, str],
    expected_error: type[Exception],
) -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(status_code, json=body)

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(expected_error):
        asyncio.run(client.validate_connection())
    assert request_count == 1


def test_invalid_or_non_json_response_fails_closed() -> None:
    for response in (
        httpx.Response(200, text="not json", headers={"content-type": "text/plain"}),
        httpx.Response(200, json={"unexpected": []}),
    ):
        client = make_client(httpx.MockTransport(lambda _request, item=response: item))
        with pytest.raises(NovelAIResponseFormatError):
            asyncio.run(client.validate_connection())


def test_network_error_is_temporary_and_not_retried() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ConnectError("offline", request=request)

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(NovelAITemporaryError):
        asyncio.run(client.validate_connection())
    assert request_count == 1


def make_client(
    transport: httpx.AsyncBaseTransport,
    provider_model_id: str = "nai-diffusion-5-full",
) -> NovelAIClient:
    def secret_reader(_profile_id: str) -> str:
        return "unit-test-secret"

    return NovelAIClient(
        NovelAIConfiguration(
            provider_model_id=provider_model_id,
            credential_profile_id="novelai",
        ),
        secret_reader,
        transport=transport,
    )


def png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(20, 30, 40)).save(output, format="PNG")
    return output.getvalue()


def zip_bytes(*entries: tuple[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return output.getvalue()


def test_image_generation_requests_and_accepts_safe_zip_response() -> None:
    requests: list[httpx.Request] = []
    png = png_bytes(832, 1216)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            content=zip_bytes(("image_0.png", png)),
            headers={"content-type": "application/zip"},
        )

    result = asyncio.run(
        make_client(httpx.MockTransport(handler)).generate_image(image_request())
    )

    assert result.png_bytes == png
    assert result.seed == 1234
    assert result.seed_source == "request"
    assert result.index == 0
    assert requests[0].headers["Accept"] == "application/zip"


@pytest.mark.parametrize("content_type", ["application/octet-stream", "binary/octet-stream"])
def test_image_generation_accepts_zip_magic_with_generic_binary_mime(
    content_type: str,
) -> None:
    png = png_bytes(832, 1216)
    response = httpx.Response(
        200,
        content=zip_bytes(("image_0.png", png)),
        headers={"content-type": content_type},
    )

    result = asyncio.run(
        make_client(httpx.MockTransport(lambda _request: response)).generate_image(
            image_request()
        )
    )

    assert result.png_bytes == png
    assert result.seed_source == "request"


@pytest.mark.parametrize(
    "archive",
    [
        zip_bytes(("../image_0.png", png_bytes(832, 1216))),
        zip_bytes(("image_0.txt", png_bytes(832, 1216))),
        zip_bytes(
            ("image_0.png", png_bytes(832, 1216)),
            ("image_1.png", png_bytes(832, 1216)),
        ),
    ],
)
def test_image_generation_rejects_unsafe_zip_response(archive: bytes) -> None:
    response = httpx.Response(
        201,
        content=archive,
        headers={"content-type": "application/zip"},
    )

    with pytest.raises(NovelAIResponseFormatError):
        asyncio.run(
            make_client(httpx.MockTransport(lambda _request: response)).generate_image(
                image_request()
            )
        )


def test_image_generation_uses_pinned_json_contract_and_validates_png() -> None:
    requests: list[httpx.Request] = []
    png = png_bytes(832, 1216)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "images": [
                    {
                        "image": base64.b64encode(png).decode("ascii"),
                        "index": 0,
                        "seed": 1234,
                    }
                ]
            },
        )

    client = make_client(httpx.MockTransport(handler))
    result = asyncio.run(client.generate_image(image_request()))

    assert result.png_bytes == png
    assert result.seed_source == "provider_response"
    assert result.width == 832
    assert result.height == 1216
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/ai/generate-image"
    assert request.headers["Authorization"] == "Bearer unit-test-secret"
    assert request.headers["X-Correlation-ID"] == "Ab12Cd"
    payload = json.loads(request.content)
    assert payload["action"] == "generate"
    assert payload["model"] == "nai-diffusion-5-full"
    assert payload["parameters"]["n_samples"] == 1
    assert payload["parameters"]["image_format"] == "png"
    assert payload["parameters"]["v4_prompt"]["caption"]["base_caption"] == "manga panel"
    assert "Authorization" not in str(payload)


def test_precise_reference_is_mapped_as_one_aligned_reference() -> None:
    captured: dict[str, object] = {}
    png = png_bytes(832, 1216)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "images": [
                    {
                        "image": base64.b64encode(png).decode("ascii"),
                        "index": 0,
                        "seed": 1234,
                    }
                ]
            },
        )

    request = image_request(
        provider_model_id="nai-diffusion-4-5-full",
        precise_reference=PreciseReferenceInput(
            png_base64=base64.b64encode(png_bytes(1024, 1536)).decode("ascii"),
            description="character",
            strength=0.7,
            fidelity=0.8,
        )
    )
    asyncio.run(
        make_client(
            httpx.MockTransport(handler),
            provider_model_id="nai-diffusion-4-5-full",
        ).generate_image(request)
    )

    parameters = captured["parameters"]
    assert isinstance(parameters, dict)
    assert len(parameters["director_reference_images"]) == 1
    assert parameters["director_reference_strength_values"] == [0.7]
    assert parameters["director_reference_secondary_strength_values"] == [0.8]
    assert parameters["director_reference_descriptions"][0]["caption"]["base_caption"] == (
        "character"
    )


def test_inpaint_uses_pinned_infill_fields_and_inpaint_model() -> None:
    captured: dict[str, object] = {}
    source = png_bytes(832, 1216)
    mask = BytesIO()
    Image.new("L", (832, 1216), color=0).save(mask, format="PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "images": [
                    {
                        "image": base64.b64encode(source).decode("ascii"),
                        "index": 0,
                        "seed": 1234,
                    }
                ]
            },
        )

    client = NovelAIClient(
        NovelAIConfiguration(
            provider_model_id="nai-diffusion-5-full-inpainting",
            credential_profile_id="novelai",
        ),
        lambda _profile_id: "unit-test-secret",
        transport=httpx.MockTransport(handler),
    )
    request = NovelAIImageRequest(
        correlation_id="InP4nt",
        provider_model_id="nai-diffusion-5-full-inpainting",
        prompt="correct hand",
        negative_prompt="text, watermark",
        width=832,
        height=1216,
        steps=28,
        scale=5,
        seed=1234,
        action="infill",
        source_image_base64=base64.b64encode(source).decode("ascii"),
        mask_base64=base64.b64encode(mask.getvalue()).decode("ascii"),
        inpaint_strength=0.65,
    )
    asyncio.run(client.generate_image(request))

    assert captured["action"] == "infill"
    assert captured["model"] == "nai-diffusion-5-full-inpainting"
    parameters = captured["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["image"] == request.source_image_base64
    assert parameters["mask"] == request.mask_base64
    assert parameters["strength"] == 0.65
    assert parameters["img2img"] == {
        "strength": 0.65,
        "noise": 0.0,
        "color_correct": True,
    }
    assert parameters["add_original_image"] is False
    assert "director_reference_images" not in parameters


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"images": []}),
        httpx.Response(201, json={"images": []}),
        httpx.Response(
            201,
            json={"images": [{"image": "not-base64", "index": 0, "seed": 1234}]},
        ),
        httpx.Response(
            201,
            json={
                "images": [
                    {
                        "image": base64.b64encode(png_bytes(64, 64)).decode("ascii"),
                        "index": 0,
                        "seed": 1234,
                    }
                ]
            },
        ),
    ],
)
def test_image_generation_response_fails_closed(response: httpx.Response) -> None:
    client = make_client(httpx.MockTransport(lambda _request: response))
    with pytest.raises(NovelAIResponseFormatError):
        asyncio.run(client.generate_image(image_request()))


def test_generation_network_failure_has_unknown_outcome_and_no_retry() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ReadTimeout("lost", request=request)

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(NovelAIUnknownOutcomeError):
        asyncio.run(client.generate_image(image_request()))
    assert request_count == 1


def test_generation_connect_failure_is_definite_and_retryable_by_bounded_queue() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ConnectError("offline", request=request)

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(NovelAITemporaryError):
        asyncio.run(client.generate_image(image_request()))
    assert request_count == 1


def test_invalid_frozen_payload_fails_before_secret_is_read() -> None:
    raw = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts"
            / "fixtures"
            / "v0.3"
            / "prompt-plan-double.json"
        ).read_text(encoding="utf-8")
    )
    plan = PromptPlan.model_validate(raw)
    plan = plan.model_copy(update={"content_sha256": prompt_plan_sha256(plan)})
    mapped = map_prompt_plan_to_novelai(
        prompt_plan=plan,
        generation_spec_id=UUID("01900000-0000-7000-8000-000000000602"),
        model_id="nai-diffusion-5-full",
        contract_sha256="2bd3c5fcd491016e1951f5a3f347d0207d49d4add153899405224e21fd1dc684",
        capability_snapshot_sha256="a" * 64,
        page_layout_draft_sha256="b" * 64,
        width=1216,
        height=896,
        seed=1234,
        steps=28,
        scale=5,
        sampler="k_euler_ancestral",
        noise_schedule="karras",
    )
    tampered = mapped.payload.model_copy(
        update={
            "input": "tampered base",
            "parameters": mapped.payload.parameters.model_copy(update={"prompt": "tampered base"}),
        }
    )
    secret_reads = 0

    def secret_reader(_profile_id: str) -> str:
        nonlocal secret_reads
        secret_reads += 1
        return "unit-test-secret"

    client = NovelAIClient(
        NovelAIConfiguration(
            provider_model_id="nai-diffusion-5-full",
            credential_profile_id="novelai",
        ),
        secret_reader,
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    with pytest.raises(NovelAIConfigurationError):
        request = NovelAIImageRequest(
            correlation_id="Fr0zen",
            provider_model_id="nai-diffusion-5-full",
            prompt="compatibility-only",
            negative_prompt="compatibility-only",
            width=1216,
            height=896,
            steps=28,
            scale=5,
            seed=1234,
            provider_execution_spec=mapped.execution_spec,
            frozen_payload=tampered,
        )
        asyncio.run(client.generate_image(request))
    assert secret_reads == 0


def test_correlation_id_must_match_the_six_character_swagger_contract() -> None:
    with pytest.raises(NovelAIConfigurationError, match="six alphanumeric"):
        image_request(correlation_id="correlation-123")
    with pytest.raises(NovelAIConfigurationError, match="six alphanumeric"):
        image_request(correlation_id="A1_2-3")


def test_generated_correlation_ids_match_contract_and_do_not_repeat() -> None:
    values = {novelai_correlation_id() for _ in range(32)}

    assert len(values) == 32
    assert all(len(value) == 6 and value.isalnum() for value in values)


def test_opus_zero_anlas_mode_accepts_only_pinned_free_payloads() -> None:
    valid = image_request(billing_mode="opus_zero_anlas")
    assert valid.width == 832
    assert valid.height == 1216

    with pytest.raises(NovelAIConfigurationError, match="zero-Anlas profile"):
        NovelAIImageRequest(
            correlation_id="Big001",
            provider_model_id="nai-diffusion-5-full",
            prompt="manga panel",
            negative_prompt="text, watermark",
            width=1024,
            height=1536,
            steps=28,
            scale=5,
            seed=1234,
            billing_mode="opus_zero_anlas",
        )
    with pytest.raises(NovelAIConfigurationError, match="zero-Anlas profile"):
        image_request(
            provider_model_id="nai-diffusion-4-5-full",
            billing_mode="opus_zero_anlas",
            precise_reference=PreciseReferenceInput(
                png_base64=base64.b64encode(png_bytes(832, 1216)).decode("ascii"),
                description="character",
                strength=0.7,
                fidelity=0.8,
            ),
        )


def image_request(
    *,
    provider_model_id: str = "nai-diffusion-5-full",
    precise_reference: PreciseReferenceInput | None = None,
    correlation_id: str = "Ab12Cd",
    billing_mode: Literal["standard", "opus_zero_anlas"] = "standard",
) -> NovelAIImageRequest:
    return NovelAIImageRequest(
        correlation_id=correlation_id,
        provider_model_id=provider_model_id,
        prompt="manga panel",
        negative_prompt="text, watermark",
        width=832,
        height=1216,
        steps=28,
        scale=5,
        seed=1234,
        billing_mode=billing_mode,
        precise_reference=precise_reference,
    )
