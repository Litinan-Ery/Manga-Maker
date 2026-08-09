from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.app.novelai.client import (
    NovelAIAuthenticationError,
    NovelAIClient,
    NovelAIConfiguration,
    NovelAIInsufficientBalanceError,
    NovelAIInvalidRequestError,
    NovelAIPermissionError,
    NovelAIRateLimitError,
    NovelAIResponseFormatError,
    NovelAITemporaryError,
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
