from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.app.novelai.client import (
    NovelAIAuthenticationError,
    NovelAIClient,
    NovelAIConfiguration,
    NovelAIImageRequest,
    NovelAIInsufficientBalanceError,
    NovelAIInvalidRequestError,
    NovelAIPermissionError,
    NovelAIRateLimitError,
    NovelAIResponseFormatError,
    NovelAITemporaryError,
    NovelAIUnknownOutcomeError,
    PreciseReferenceInput,
)
from backend.app.novelai.contracts import CONNECTION_TEST_PATH, GENERATION_PATH


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
            provider_model_id="nai-diffusion-4-5-full",
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
    assert request.url.params["model"] == "nai-diffusion-4-5-full"
    assert request.url.params["prompt"] == "manga"
    assert request.headers["Authorization"] == "Bearer unit-test-secret"
    assert request.method == "GET"


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


def make_client(transport: httpx.AsyncBaseTransport) -> NovelAIClient:
    def secret_reader(_profile_id: str) -> str:
        return "unit-test-secret"

    return NovelAIClient(
        NovelAIConfiguration(
            provider_model_id="nai-diffusion-4-5-full",
            credential_profile_id="novelai",
        ),
        secret_reader,
        transport=transport,
    )


def png_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(20, 30, 40)).save(output, format="PNG")
    return output.getvalue()


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
    assert result.width == 832
    assert result.height == 1216
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/ai/generate-image"
    assert request.headers["Authorization"] == "Bearer unit-test-secret"
    assert request.headers["X-Correlation-ID"] == "correlation-123"
    payload = json.loads(request.content)
    assert payload["action"] == "generate"
    assert payload["model"] == "nai-diffusion-4-5-full"
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
        precise_reference=PreciseReferenceInput(
            png_base64=base64.b64encode(png_bytes(1024, 1536)).decode("ascii"),
            description="character",
            strength=0.7,
            fidelity=0.8,
        )
    )
    asyncio.run(make_client(httpx.MockTransport(handler)).generate_image(request))

    parameters = captured["parameters"]
    assert isinstance(parameters, dict)
    assert len(parameters["director_reference_images"]) == 1
    assert parameters["director_reference_strength_values"] == [0.7]
    assert parameters["director_reference_secondary_strength_values"] == [0.8]
    assert parameters["director_reference_descriptions"][0]["caption"]["base_caption"] == (
        "character"
    )


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


def image_request(
    *, precise_reference: PreciseReferenceInput | None = None
) -> NovelAIImageRequest:
    return NovelAIImageRequest(
        correlation_id="correlation-123",
        provider_model_id="nai-diffusion-4-5-full",
        prompt="manga panel",
        negative_prompt="text, watermark",
        width=832,
        height=1216,
        steps=28,
        scale=5,
        seed=1234,
        precise_reference=precise_reference,
    )
