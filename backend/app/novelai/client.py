from __future__ import annotations

import base64
import binascii
import json
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

import httpx
from PIL import Image, UnidentifiedImageError

from .contracts import (
    CONNECTION_TEST_PATH,
    GENERATION_PATH,
    IMAGE_API_BASE_URL,
    require_model_profile,
)

MAX_GENERATED_IMAGE_BYTES = 32 * 1024 * 1024
MAX_GENERATED_PIXELS = 4_000_000


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


class NovelAIUnknownOutcomeError(NovelAIError):
    """The request may have reached the provider; automatic replay is unsafe."""


@dataclass(frozen=True, slots=True)
class PreciseReferenceInput:
    png_base64: str
    description: str
    strength: float
    fidelity: float

    def __post_init__(self) -> None:
        if self.description not in {"character", "style", "character&style"}:
            raise NovelAIConfigurationError("invalid precise reference description")
        if not 0 <= self.strength <= 1 or not 0 <= self.fidelity <= 1:
            raise NovelAIConfigurationError("reference strength and fidelity must be 0-1")


@dataclass(frozen=True, slots=True)
class NovelAIImageRequest:
    correlation_id: str
    provider_model_id: str
    prompt: str
    negative_prompt: str
    width: int
    height: int
    steps: int
    scale: float
    seed: int
    sampler: str = "k_euler_ancestral"
    noise_schedule: str = "karras"
    precise_reference: PreciseReferenceInput | None = None

    def __post_init__(self) -> None:
        require_model_profile(self.provider_model_id)
        if not self.correlation_id.strip() or len(self.correlation_id) > 100:
            raise NovelAIConfigurationError("correlation id is invalid")
        if not self.prompt.strip():
            raise NovelAIConfigurationError("image prompt must not be empty")
        if not 64 <= self.width <= 2048 or not 64 <= self.height <= 2048:
            raise NovelAIConfigurationError("image dimensions are outside the local allowlist")
        if self.width % 64 or self.height % 64:
            raise NovelAIConfigurationError("image dimensions must be multiples of 64")
        if self.width * self.height > 3_047_424:
            raise NovelAIConfigurationError("image dimensions exceed the provider pixel limit")
        if not 1 <= self.steps <= 50:
            raise NovelAIConfigurationError("steps must be between 1 and 50")
        if not 0 <= self.scale <= 10:
            raise NovelAIConfigurationError("scale must be between 0 and 10")
        if not 0 <= self.seed <= 4_294_967_287:
            raise NovelAIConfigurationError("seed is outside the supported range")
        if self.sampler not in {"k_euler", "k_euler_ancestral", "k_dpmpp_2m", "k_dpmpp_sde"}:
            raise NovelAIConfigurationError("sampler is outside the local allowlist")
        if self.noise_schedule not in {"karras", "exponential", "polyexponential"}:
            raise NovelAIConfigurationError("noise schedule is outside the local allowlist")


@dataclass(frozen=True, slots=True)
class NovelAIGeneratedImage:
    png_bytes: bytes
    seed: int
    index: int
    width: int
    height: int


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

    async def generate_image(self, request: NovelAIImageRequest) -> NovelAIGeneratedImage: ...


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

    async def generate_image(self, request: NovelAIImageRequest) -> NovelAIGeneratedImage:
        if request.provider_model_id != self.configuration.provider_model_id:
            raise NovelAIConfigurationError("request model does not match pinned configuration")
        secret = self.secret_reader(self.configuration.credential_profile_id)
        payload = image_request_payload(request)
        try:
            async with httpx.AsyncClient(
                base_url=IMAGE_API_BASE_URL,
                transport=self.transport,
                timeout=self.configuration.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    GENERATION_PATH,
                    headers={
                        "Authorization": f"Bearer {secret}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "MangaMaker/0.1 local-app",
                        "X-Correlation-ID": request.correlation_id,
                    },
                    json=payload,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise NovelAITemporaryError(
                "NovelAI image endpoint could not be reached before sending"
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise NovelAIUnknownOutcomeError(
                "NovelAI image request outcome is unknown; automatic replay is disabled"
            ) from exc
        raise_for_provider_status(response)
        if response.status_code != 201:
            raise NovelAIResponseFormatError("NovelAI returned an unexpected success status")
        return validate_image_generation_response(response, request)


def image_request_payload(request: NovelAIImageRequest) -> dict[str, Any]:
    is_v4 = "diffusion-4" in request.provider_model_id
    parameters: dict[str, Any] = {
        "width": request.width,
        "height": request.height,
        "steps": request.steps,
        "scale": request.scale,
        "sampler": request.sampler,
        "noise_schedule": request.noise_schedule,
        "seed": request.seed,
        "n_samples": 1,
        "negative_prompt": request.negative_prompt,
        "qualityToggle": True,
        "ucPreset": 3,
        "params_version": 3 if is_v4 else 1,
        "cfg_rescale": 0,
        "dynamic_thresholding": False,
        "legacy": False,
        "legacy_v3_extend": False,
        "prefer_brownian": request.sampler == "k_euler_ancestral",
        "deliberate_euler_ancestral_bug": False,
        "image_format": "png",
    }
    if is_v4:
        parameters["v4_prompt"] = {
            "caption": {"base_caption": request.prompt, "char_captions": []},
            "use_coords": False,
            "use_order": True,
        }
        parameters["v4_negative_prompt"] = {
            "caption": {"base_caption": request.negative_prompt, "char_captions": []},
            "legacy_uc": False,
        }
    reference = request.precise_reference
    if reference is not None:
        profile = require_model_profile(request.provider_model_id)
        if not profile.supports_precise_reference:
            raise NovelAIConfigurationError("selected model does not support precise reference")
        parameters.update(
            {
                "director_reference_images": [reference.png_base64],
                "director_reference_descriptions": [
                    {
                        "caption": {
                            "base_caption": reference.description,
                            "char_captions": [],
                        },
                        "legacy_uc": False,
                        "use_coords": False,
                        "use_order": True,
                    }
                ],
                "director_reference_strength_values": [reference.strength],
                "director_reference_secondary_strength_values": [reference.fidelity],
                "director_reference_information_extracted": [1.0],
            }
        )
    return {
        "action": "generate",
        "input": request.prompt,
        "model": request.provider_model_id,
        "parameters": parameters,
    }


def validate_image_generation_response(
    response: httpx.Response, request: NovelAIImageRequest
) -> NovelAIGeneratedImage:
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        raise NovelAIResponseFormatError("NovelAI returned a non-JSON image response")
    try:
        payload: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NovelAIResponseFormatError("NovelAI returned invalid image JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
        raise NovelAIResponseFormatError("NovelAI image response is missing images")
    images = payload["images"]
    if len(images) != 1 or not isinstance(images[0], dict):
        raise NovelAIResponseFormatError("NovelAI must return exactly one image")
    item = images[0]
    if not isinstance(item.get("image"), str):
        raise NovelAIResponseFormatError("NovelAI image payload is missing base64 data")
    try:
        raw = base64.b64decode(item["image"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise NovelAIResponseFormatError("NovelAI image base64 is invalid") from exc
    if not raw or len(raw) > MAX_GENERATED_IMAGE_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise NovelAIResponseFormatError("NovelAI image is empty, oversized, or not PNG")
    Image.MAX_IMAGE_PIXELS = MAX_GENERATED_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                image.load()
                if image.format != "PNG":
                    raise NovelAIResponseFormatError("NovelAI image decoder did not confirm PNG")
                width, height = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as exc:
        raise NovelAIResponseFormatError("NovelAI PNG failed safe decoding") from exc
    if (width, height) != (request.width, request.height):
        raise NovelAIResponseFormatError("NovelAI PNG dimensions do not match the request")
    seed = item.get("seed")
    index = item.get("index")
    if not isinstance(seed, int) or not 0 <= seed <= 4_294_967_295:
        raise NovelAIResponseFormatError("NovelAI response seed is invalid")
    if index != 0:
        raise NovelAIResponseFormatError("NovelAI response index is invalid")
    return NovelAIGeneratedImage(
        png_bytes=raw,
        seed=seed,
        index=index,
        width=width,
        height=height,
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
