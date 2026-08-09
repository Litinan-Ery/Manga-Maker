from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .contracts import CONNECTION_TEST_PATH, IMAGE_API_BASE_URL, require_model_profile


class NovelAIError(Exception):
    """Base class for normalized provider failures without response bodies."""


class NovelAIConfigurationError(NovelAIError):
    pass


class NovelAIAuthenticationError(NovelAIError):
    pass


class NovelAIPermissionError(NovelAIError):
    pass


class NovelAIInsufficientBalanceError(NovelAIError):
    pass


class NovelAIRateLimitError(NovelAIError):
    pass


class NovelAIInvalidRequestError(NovelAIError):
    pass


class NovelAITemporaryError(NovelAIError):
    pass


class NovelAIResponseFormatError(NovelAIError):
    pass


class SecretReader(Protocol):
    def __call__(self, profile_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class NovelAIConfiguration:
    provider_model_id: str
    credential_profile_id: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        require_model_profile(self.provider_model_id)
        if not self.credential_profile_id.strip():
            raise NovelAIConfigurationError("credential profile id must not be empty")
        if not 1 <= self.timeout_seconds <= 180:
            raise NovelAIConfigurationError("timeout must be between 1 and 180 seconds")


@dataclass(frozen=True, slots=True)
class NovelAIConnectionResult:
    provider_model_id: str
    suggestion_count: int


class NovelAIProvider(Protocol):
    async def validate_connection(self) -> NovelAIConnectionResult: ...


class NovelAIClient:
    """Minimal, non-generating NovelAI client used by the explicit connection test."""

    def __init__(
        self,
        configuration: NovelAIConfiguration,
        secret_reader: SecretReader,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.configuration = configuration
        self.secret_reader = secret_reader
        self.transport = transport

    async def validate_connection(self) -> NovelAIConnectionResult:
        secret = self.secret_reader(self.configuration.credential_profile_id)
        try:
            async with httpx.AsyncClient(
                base_url=IMAGE_API_BASE_URL,
                transport=self.transport,
                timeout=self.configuration.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    CONNECTION_TEST_PATH,
                    params={
                        "model": self.configuration.provider_model_id,
                        "prompt": "manga",
                        "lang": "en",
                    },
                    headers={
                        "Authorization": f"Bearer {secret}",
                        "Accept": "application/json",
                        "User-Agent": "MangaMaker/0.1 local-app",
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise NovelAITemporaryError("NovelAI is temporarily unreachable") from exc

        raise_for_provider_status(response)
        suggestion_count = validate_tag_suggestion_response(response)
        return NovelAIConnectionResult(
            provider_model_id=self.configuration.provider_model_id,
            suggestion_count=suggestion_count,
        )


def raise_for_provider_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    if status == 401:
        raise NovelAIAuthenticationError("NovelAI credentials were rejected")
    if status == 403:
        raise NovelAIPermissionError("NovelAI denied access")
    if status == 402 or response_indicates_insufficient_balance(response):
        raise NovelAIInsufficientBalanceError("NovelAI balance or subscription is insufficient")
    if status == 429:
        raise NovelAIRateLimitError("NovelAI rate limit was reached")
    if status in {400, 404, 409, 422}:
        raise NovelAIInvalidRequestError("NovelAI rejected the request")
    if status >= 500:
        raise NovelAITemporaryError("NovelAI returned a server error")
    raise NovelAIInvalidRequestError("NovelAI rejected the request")


def response_indicates_insufficient_balance(response: httpx.Response) -> bool:
    if response.status_code not in {400, 409, 422}:
        return False
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    serialized = json.dumps(payload, ensure_ascii=True).lower()[:4096]
    return any(
        marker in serialized
        for marker in ("insufficient", "not enough", "anlas", "subscription required")
    )


def validate_tag_suggestion_response(response: httpx.Response) -> int:
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        raise NovelAIResponseFormatError("NovelAI returned a non-JSON connection response")
    try:
        payload: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NovelAIResponseFormatError("NovelAI returned invalid JSON") from exc
    if not isinstance(payload, dict) or "tags" not in payload:
        raise NovelAIResponseFormatError("NovelAI tag response is missing tags")
    tags = payload["tags"]
    if isinstance(tags, dict):
        return 1 if isinstance(tags.get("tag"), str) else 0
    if isinstance(tags, list):
        return sum(isinstance(item, dict) and isinstance(item.get("tag"), str) for item in tags)
    raise NovelAIResponseFormatError("NovelAI tags have an unexpected shape")
