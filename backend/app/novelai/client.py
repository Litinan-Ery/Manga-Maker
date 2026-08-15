from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import secrets
import stat
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol
from zipfile import BadZipFile, ZipFile

import httpx
from PIL import Image, UnidentifiedImageError

from ..modules.production.adapters.novelai import (
    NovelAIV4Payload,
    require_frozen_novelai_payload,
)
from ..modules.production.contracts import ProviderExecutionSpec
from ..modules.production.errors import ProviderMappingError
from .contracts import (
    CONNECTION_TEST_PATH,
    GENERATION_PATH,
    IMAGE_API_BASE_URL,
    OPUS_TIER,
    OPUS_ZERO_ANLAS_DIMENSIONS,
    OPUS_ZERO_ANLAS_MAX_STEPS,
    OPUS_ZERO_ANLAS_PROFILE_VERSION,
    SUBSCRIPTION_PATH,
    require_inpaint_model_profile,
    require_model_profile,
)

MAX_GENERATED_IMAGE_BYTES = 32 * 1024 * 1024
MAX_GENERATED_ARCHIVE_BYTES = MAX_GENERATED_IMAGE_BYTES + (1024 * 1024)
MAX_GENERATED_PIXELS = 4_000_000
MAX_GENERATED_ZIP_RATIO = 100
NOVELAI_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{6}$")
logger = logging.getLogger(__name__)


def novelai_correlation_id() -> str:
    """Return the six-character request id required by the Image API contract."""

    return secrets.token_hex(3)


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


class NovelAIOpusRequiredError(NovelAIError):
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
    billing_mode: Literal["standard", "opus_zero_anlas"] = "standard"
    precise_reference: PreciseReferenceInput | None = None
    action: Literal["generate", "infill"] = "generate"
    source_image_base64: str | None = None
    mask_base64: str | None = None
    inpaint_strength: float | None = None
    provider_execution_spec: ProviderExecutionSpec | None = None
    frozen_payload: NovelAIV4Payload | None = None

    def __post_init__(self) -> None:
        if (self.provider_execution_spec is None) != (self.frozen_payload is None):
            raise NovelAIConfigurationError(
                "provider execution spec and frozen payload must be provided together"
            )
        if self.provider_execution_spec is not None and self.frozen_payload is not None:
            try:
                payload = require_frozen_novelai_payload(
                    self.provider_execution_spec,
                    self.frozen_payload,
                )
            except ProviderMappingError as exc:
                raise NovelAIConfigurationError(exc.message) from exc
            if str(self.provider_execution_spec.generation_spec_id) == str(
                self.provider_execution_spec.provider_execution_spec_id
            ):
                raise NovelAIConfigurationError("provider execution identifiers must differ")
            frozen_action = "infill" if self.action == "infill" else "generate"
            if payload.action != frozen_action:
                raise NovelAIConfigurationError("frozen payload action does not match request")
        if self.action == "generate":
            require_model_profile(self.provider_model_id)
            if (
                self.source_image_base64 is not None
                or self.mask_base64 is not None
                or self.inpaint_strength is not None
            ):
                raise NovelAIConfigurationError("generate request cannot include inpaint inputs")
        else:
            require_inpaint_model_profile(self.provider_model_id)
            if not self.source_image_base64 or not self.mask_base64:
                raise NovelAIConfigurationError("inpaint request requires image and mask")
            if self.precise_reference is not None:
                raise NovelAIConfigurationError(
                    "P0 inpaint does not combine precise reference inputs"
                )
            if self.inpaint_strength is None or not 0.1 <= self.inpaint_strength <= 1:
                raise NovelAIConfigurationError("inpaint strength must be between 0.1 and 1")
        if not NOVELAI_CORRELATION_ID_PATTERN.fullmatch(self.correlation_id):
            raise NovelAIConfigurationError(
                "correlation id must contain exactly six alphanumeric characters"
            )
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
        if self.billing_mode == "opus_zero_anlas":
            require_opus_zero_anlas_request(self)


@dataclass(frozen=True, slots=True)
class NovelAIGeneratedImage:
    png_bytes: bytes
    seed: int
    seed_source: Literal["provider_response", "request"]
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
        try:
            require_model_profile(self.provider_model_id)
        except ValueError:
            require_inpaint_model_profile(self.provider_model_id)
        if not self.credential_profile_id.strip():
            raise NovelAIConfigurationError("credential profile id must not be empty")
        if not 1 <= self.timeout_seconds <= 180:
            raise NovelAIConfigurationError("timeout must be between 1 and 180 seconds")


@dataclass(frozen=True, slots=True)
class NovelAIConnectionResult:
    provider_model_id: str
    suggestion_count: int


@dataclass(frozen=True, slots=True)
class NovelAISubscriptionResult:
    active: bool
    tier: int
    expires_at: int | None
    is_grace_period: bool

    @property
    def opus_active(self) -> bool:
        return self.active and self.tier == OPUS_TIER

    def zero_anlas_verification(self) -> dict[str, Any]:
        return {
            "profile_version": OPUS_ZERO_ANLAS_PROFILE_VERSION,
            "subscription_active": self.active,
            "subscription_tier": self.tier,
            "is_grace_period": self.is_grace_period,
            "opus_active": self.opus_active,
        }


class NovelAIProvider(Protocol):
    async def validate_connection(self) -> NovelAIConnectionResult: ...

    async def get_subscription(self) -> NovelAISubscriptionResult: ...

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

    async def get_subscription(self) -> NovelAISubscriptionResult:
        secret = self.secret_reader(self.configuration.credential_profile_id)
        try:
            async with httpx.AsyncClient(
                base_url=IMAGE_API_BASE_URL,
                transport=self.transport,
                timeout=self.configuration.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    SUBSCRIPTION_PATH,
                    headers={
                        "Authorization": f"Bearer {secret}",
                        "Accept": "application/json",
                        "User-Agent": "MangaMaker/0.1 local-app",
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise NovelAITemporaryError("NovelAI subscription endpoint is unreachable") from exc
        raise_for_provider_status(response)
        return validate_subscription_response(response)

    async def generate_image(self, request: NovelAIImageRequest) -> NovelAIGeneratedImage:
        if request.provider_model_id != self.configuration.provider_model_id:
            raise NovelAIConfigurationError("request model does not match pinned configuration")
        payload = image_request_payload(request)
        secret = self.secret_reader(self.configuration.credential_profile_id)
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
                        "Accept": "application/zip",
                        "Content-Type": "application/json",
                        "User-Agent": "MangaMaker/0.1 local-app",
                        "X-Correlation-ID": request.correlation_id,
                    },
                    json=payload,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning(
                "NovelAI image request failed before send: correlation_id=%s error_type=%s",
                request.correlation_id,
                type(exc).__name__,
            )
            raise NovelAITemporaryError(
                "NovelAI image endpoint could not be reached before sending"
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "NovelAI image request transport outcome is unknown: "
                "correlation_id=%s error_type=%s",
                request.correlation_id,
                type(exc).__name__,
            )
            raise NovelAIUnknownOutcomeError(
                "NovelAI image request outcome is unknown; automatic replay is disabled"
            ) from exc
        raise_for_provider_status(response)
        try:
            if response.status_code not in {200, 201}:
                raise NovelAIResponseFormatError(
                    "NovelAI returned an unexpected success status"
                )
            return validate_image_generation_response(response, request)
        except NovelAIResponseFormatError as exc:
            logger.warning(
                "NovelAI image response validation failed: correlation_id=%s status=%s "
                "content_type=%r content_length=%s content_magic=%s reason=%s",
                request.correlation_id,
                response.status_code,
                response.headers.get("content-type", ""),
                len(response.content),
                response.content[:8].hex(),
                str(exc),
            )
            raise


def image_request_payload(request: NovelAIImageRequest) -> dict[str, Any]:
    if request.provider_execution_spec is not None and request.frozen_payload is not None:
        try:
            payload = require_frozen_novelai_payload(
                request.provider_execution_spec,
                request.frozen_payload,
            )
        except ProviderMappingError as exc:
            raise NovelAIConfigurationError(exc.message) from exc
        return payload.model_dump(mode="json", exclude_none=True)
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
    if request.action == "infill":
        parameters.update(
            {
                "image": request.source_image_base64,
                "mask": request.mask_base64,
                "strength": request.inpaint_strength,
                "noise": 0.0,
                "img2img": {
                    "strength": request.inpaint_strength,
                    "noise": 0.0,
                    "color_correct": True,
                },
                "add_original_image": False,
                "color_correct": True,
            }
        )
    return {
        "action": request.action,
        "input": request.prompt,
        "model": request.provider_model_id,
        "parameters": parameters,
    }


def require_opus_zero_anlas_request(request: NovelAIImageRequest) -> None:
    """Fail closed unless the exact outbound request fits NovelAI's Opus allowance."""

    payload = image_request_payload(request)
    require_opus_zero_anlas_payload(payload)
    if (
        request.precise_reference is not None
        or request.source_image_base64 is not None
        or request.mask_base64 is not None
        or request.inpaint_strength is not None
    ):
        raise NovelAIConfigurationError(
            "request is outside the pinned NovelAI Opus zero-Anlas profile"
        )


def require_opus_zero_anlas_payload(
    payload: NovelAIV4Payload | dict[str, Any],
) -> None:
    """Validate a mapped or serialized provider payload against the pinned free profile."""

    serialized = (
        payload.model_dump(mode="json", exclude_none=True)
        if isinstance(payload, NovelAIV4Payload)
        else payload
    )
    try:
        action = str(serialized["action"])
        model = str(serialized["model"])
        parameters = serialized["parameters"]
        assert isinstance(parameters, dict)
        width = int(parameters["width"])
        height = int(parameters["height"])
        steps = int(parameters["steps"])
        n_samples = int(parameters["n_samples"])
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise NovelAIConfigurationError(
            "zero-Anlas request is missing frozen eligibility fields"
        ) from exc

    image_inputs = (
        "image",
        "mask",
        "img2img",
        "director_reference_images",
        "director_reference_descriptions",
        "director_reference_strength_values",
        "director_reference_secondary_strength_values",
        "director_reference_information_extracted",
    )
    eligible = (
        action == "generate"
        and model.startswith("nai-diffusion-4-5-")
        and (width, height) in OPUS_ZERO_ANLAS_DIMENSIONS
        and steps <= OPUS_ZERO_ANLAS_MAX_STEPS
        and n_samples == 1
        and all(parameters.get(field) is None for field in image_inputs)
    )
    if not eligible:
        raise NovelAIConfigurationError(
            "request is outside the pinned NovelAI Opus zero-Anlas profile"
        )


def require_active_opus(subscription: NovelAISubscriptionResult) -> None:
    if not subscription.opus_active:
        raise NovelAIOpusRequiredError(
            "an active NovelAI Opus subscription is required for zero-Anlas generation"
        )


def validate_image_generation_response(
    response: httpx.Response, request: NovelAIImageRequest
) -> NovelAIGeneratedImage:
    content_type = response.headers.get("content-type", "").lower()
    media_type = content_type.split(";", 1)[0].strip()
    if "json" in content_type:
        return validate_json_image_generation_response(response, request)
    if "zip" in content_type or (
        media_type in {"", "application/octet-stream", "binary/octet-stream", "application/binary"}
        and response.content.startswith(b"PK")
    ):
        return validate_zip_image_generation_response(response, request)
    raise NovelAIResponseFormatError("NovelAI returned an unsupported image response")


def validate_json_image_generation_response(
    response: httpx.Response, request: NovelAIImageRequest
) -> NovelAIGeneratedImage:
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
    width, height = validate_generated_png(raw, request)
    seed = item.get("seed")
    index = item.get("index")
    if not isinstance(seed, int) or not 0 <= seed <= 4_294_967_295:
        raise NovelAIResponseFormatError("NovelAI response seed is invalid")
    if index != 0:
        raise NovelAIResponseFormatError("NovelAI response index is invalid")
    return NovelAIGeneratedImage(
        png_bytes=raw,
        seed=seed,
        seed_source="provider_response",
        index=index,
        width=width,
        height=height,
    )


def validate_zip_image_generation_response(
    response: httpx.Response, request: NovelAIImageRequest
) -> NovelAIGeneratedImage:
    raw_archive = response.content
    if (
        not raw_archive
        or len(raw_archive) > MAX_GENERATED_ARCHIVE_BYTES
        or not raw_archive.startswith(b"PK")
    ):
        raise NovelAIResponseFormatError("NovelAI ZIP is empty, oversized, or invalid")
    try:
        with ZipFile(BytesIO(raw_archive)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                raise NovelAIResponseFormatError(
                    "NovelAI ZIP must contain exactly one image file"
                )
            entry = entries[0]
            path = PurePosixPath(entry.filename)
            mode = (entry.external_attr >> 16) & 0o170000
            if (
                entry.is_dir()
                or entry.flag_bits & 0x1
                or "\\" in entry.filename
                or path.is_absolute()
                or len(path.parts) != 1
                or path.name != entry.filename
                or path.suffix.lower() != ".png"
                or mode not in {0, stat.S_IFREG}
            ):
                raise NovelAIResponseFormatError("NovelAI ZIP contains an unsafe image entry")
            if (
                entry.file_size <= 0
                or entry.file_size > MAX_GENERATED_IMAGE_BYTES
                or entry.compress_size <= 0
                or entry.file_size > entry.compress_size * MAX_GENERATED_ZIP_RATIO
            ):
                raise NovelAIResponseFormatError("NovelAI ZIP image size is unsafe")
            with archive.open(entry, "r") as image_file:
                raw = image_file.read(MAX_GENERATED_IMAGE_BYTES + 1)
    except NovelAIResponseFormatError:
        raise
    except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
        raise NovelAIResponseFormatError("NovelAI ZIP failed safe decoding") from exc
    if len(raw) > MAX_GENERATED_IMAGE_BYTES:
        raise NovelAIResponseFormatError("NovelAI ZIP image is oversized")
    width, height = validate_generated_png(raw, request)
    return NovelAIGeneratedImage(
        png_bytes=raw,
        seed=request.seed,
        seed_source="request",
        index=0,
        width=width,
        height=height,
    )


def validate_generated_png(
    raw: bytes, request: NovelAIImageRequest
) -> tuple[int, int]:
    if not raw or len(raw) > MAX_GENERATED_IMAGE_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise NovelAIResponseFormatError("NovelAI image is empty, oversized, or not PNG")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG":
                    raise NovelAIResponseFormatError("NovelAI image decoder did not confirm PNG")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_GENERATED_PIXELS:
                    raise NovelAIResponseFormatError("NovelAI PNG dimensions are invalid")
                image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as exc:
        raise NovelAIResponseFormatError("NovelAI PNG failed safe decoding") from exc
    if (width, height) != (request.width, request.height):
        raise NovelAIResponseFormatError("NovelAI PNG dimensions do not match the request")
    return width, height


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
    try:
        payload: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        if "json" not in content_type:
            raise NovelAIResponseFormatError(
                "NovelAI returned a non-JSON connection response"
            ) from exc
        raise NovelAIResponseFormatError("NovelAI returned invalid JSON") from exc
    if not isinstance(payload, dict) or "tags" not in payload:
        raise NovelAIResponseFormatError("NovelAI tag response is missing tags")
    tags = payload["tags"]
    if isinstance(tags, dict):
        return 1 if isinstance(tags.get("tag"), str) else 0
    if isinstance(tags, list):
        return sum(isinstance(item, dict) and isinstance(item.get("tag"), str) for item in tags)
    raise NovelAIResponseFormatError("NovelAI tags have an unexpected shape")


def validate_subscription_response(response: httpx.Response) -> NovelAISubscriptionResult:
    try:
        payload: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NovelAIResponseFormatError("NovelAI returned invalid subscription JSON") from exc
    if not isinstance(payload, dict):
        raise NovelAIResponseFormatError("NovelAI subscription response must be an object")
    active = payload.get("active")
    tier = payload.get("tier")
    expires_at = payload.get("expiresAt")
    grace = payload.get("isGracePeriod", False)
    if not isinstance(active, bool):
        raise NovelAIResponseFormatError("NovelAI subscription active flag is invalid")
    if isinstance(tier, bool) or not isinstance(tier, int) or not 0 <= tier <= 100:
        raise NovelAIResponseFormatError("NovelAI subscription tier is invalid")
    if expires_at is not None and (
        isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at < 0
    ):
        raise NovelAIResponseFormatError("NovelAI subscription expiry is invalid")
    if not isinstance(grace, bool):
        raise NovelAIResponseFormatError("NovelAI subscription grace flag is invalid")
    return NovelAISubscriptionResult(
        active=active,
        tier=tier,
        expires_at=expires_at,
        is_grace_period=grace,
    )
