from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

IMAGE_API_BASE_URL = "https://image.novelai.net"
SWAGGER_DOCUMENT_URL = f"{IMAGE_API_BASE_URL}/docs/doc.json"
CONTRACT_SHA256 = "2bd3c5fcd491016e1951f5a3f347d0207d49d4add153899405224e21fd1dc684"
CONTRACT_FETCHED_ON = "2026-08-29"
MAPPING_VERSION = "novelai-image-2026-08-29.4-v5-full-1"
CONNECTION_TEST_PATH = "/ai/generate-image/suggest-tags"
GENERATION_PATH = "/ai/generate-image"
SUBSCRIPTION_PATH = "/user/subscription"
UPSCALE_PATH = "/ai/upscale"
AUGMENT_PATH = "/ai/augment-image"
ENCODE_VIBE_PATH = "/ai/encode-vibe"

# NovelAI documents the Opus no-Anlas allowance as one image at a time, up to
# 1024x1024 pixels, at 28 steps or fewer, without another image as a base.  The
# portrait/landscape sizes below are NovelAI's documented normal base
# resolution and stay below the one-megapixel ceiling.
OPUS_TIER = 3
OPUS_ZERO_ANLAS_PROFILE_VERSION = "novelai-opus-zero-anlas-2026-08-29.2"
OPUS_ZERO_ANLAS_MAX_PIXELS = 1024 * 1024
OPUS_ZERO_ANLAS_MAX_STEPS = 28
OPUS_ZERO_ANLAS_SAMPLE_COUNT = 1
OPUS_ZERO_ANLAS_DIMENSIONS: tuple[tuple[int, int], ...] = (
    (832, 1216),
    (1216, 832),
    (1024, 1024),
)


class NovelAIModel(StrEnum):
    V5_FULL = "nai-diffusion-5-full"
    V5_CURATED = "nai-diffusion-5-curated"
    V45_FULL = "nai-diffusion-4-5-full"
    V45_CURATED = "nai-diffusion-4-5-curated"
    V4_FULL = "nai-diffusion-4-full"
    V4_CURATED = "nai-diffusion-4-curated-preview"
    ANIME_V3 = "nai-diffusion-3"
    FURRY_V3 = "nai-diffusion-furry-3"


@dataclass(frozen=True, slots=True)
class ModelCapability:
    model: NovelAIModel
    label: str
    inpaint_model_id: str
    recommended: bool
    supports_precise_reference: bool
    supports_multi_character_prompt: bool
    supports_vibe_transfer: bool
    precise_reference_excludes_vibe_transfer: bool
    prompt_token_note: str
    default_steps: int
    default_scale: float
    params_version: int
    uc_preset: int

    @property
    def supports_opus_zero_anlas(self) -> bool:
        return str(self.model).startswith(("nai-diffusion-5-", "nai-diffusion-4-5-"))

    @property
    def opus_allowance_is_usage_limited(self) -> bool:
        return str(self.model).startswith("nai-diffusion-5-")

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_model_id"] = payload.pop("model")
        payload["supports_opus_zero_anlas"] = self.supports_opus_zero_anlas
        payload["opus_allowance_is_usage_limited"] = self.opus_allowance_is_usage_limited
        return payload


MODEL_PROFILES: tuple[ModelCapability, ...] = (
    ModelCapability(
        model=NovelAIModel.V5_FULL,
        label="NovelAI Diffusion V5 Full",
        inpaint_model_id="nai-diffusion-5-full-inpainting",
        recommended=True,
        supports_precise_reference=False,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=False,
        precise_reference_excludes_vibe_transfer=False,
        prompt_token_note=(
            "V5 Full 为当前完整模型，官方标注有效提示容量约 1471 tokens，"
            "文字渲染约支持 750 个文本字符。"
        ),
        default_steps=23,
        default_scale=7.0,
        params_version=4,
        uc_preset=4,
    ),
    ModelCapability(
        model=NovelAIModel.V5_CURATED,
        label="NovelAI Diffusion V5 Curated",
        inpaint_model_id="nai-diffusion-5-curated-inpainting",
        recommended=False,
        supports_precise_reference=False,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=False,
        precise_reference_excludes_vibe_transfer=False,
        prompt_token_note=(
            "V5 Curated 为收敛版本，官方标注有效提示容量约 703 tokens，"
            "文字渲染约支持 374 个文本字符。"
        ),
        default_steps=23,
        default_scale=7.0,
        params_version=4,
        uc_preset=4,
    ),
    ModelCapability(
        model=NovelAIModel.V45_FULL,
        label="Anime V4.5 Full",
        inpaint_model_id="nai-diffusion-4-5-full-inpainting",
        recommended=False,
        supports_precise_reference=True,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=True,
        prompt_token_note="官方文档标注约 512 T5 tokens，Unicode 支持有限。",
        default_steps=23,
        default_scale=5.0,
        params_version=4,
        uc_preset=3,
    ),
    ModelCapability(
        model=NovelAIModel.V45_CURATED,
        label="Anime V4.5 Curated",
        inpaint_model_id="nai-diffusion-4-5-curated-inpainting",
        recommended=False,
        supports_precise_reference=True,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=True,
        prompt_token_note="官方文档标注约 512 T5 tokens，Unicode 支持有限。",
        default_steps=23,
        default_scale=5.0,
        params_version=4,
        uc_preset=3,
    ),
    ModelCapability(
        model=NovelAIModel.V4_FULL,
        label="Anime V4 Full",
        inpaint_model_id="nai-diffusion-4-full-inpainting",
        recommended=False,
        supports_precise_reference=False,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=False,
        prompt_token_note="旧版模型，仅为已有工作流兼容保留。",
        default_steps=23,
        default_scale=5.5,
        params_version=4,
        uc_preset=3,
    ),
    ModelCapability(
        model=NovelAIModel.V4_CURATED,
        label="Anime V4 Curated",
        inpaint_model_id="nai-diffusion-4-curated-inpainting",
        recommended=False,
        supports_precise_reference=False,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=False,
        prompt_token_note="旧版模型，仅为已有工作流兼容保留。",
        default_steps=23,
        default_scale=5.5,
        params_version=4,
        uc_preset=3,
    ),
    ModelCapability(
        model=NovelAIModel.ANIME_V3,
        label="Anime V3",
        inpaint_model_id="nai-diffusion-3-inpainting",
        recommended=False,
        supports_precise_reference=False,
        supports_multi_character_prompt=False,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=False,
        prompt_token_note="旧版模型，不用于 P0 的精确角色参考工作流。",
        default_steps=23,
        default_scale=5.0,
        params_version=4,
        uc_preset=3,
    ),
    ModelCapability(
        model=NovelAIModel.FURRY_V3,
        label="Furry V3",
        inpaint_model_id="nai-diffusion-furry-3-inpainting",
        recommended=False,
        supports_precise_reference=False,
        supports_multi_character_prompt=False,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=False,
        prompt_token_note="面向 furry 内容的旧版模型。",
        default_steps=23,
        default_scale=6.2,
        params_version=4,
        uc_preset=3,
    ),
)

MODEL_PROFILES_BY_ID = {str(profile.model): profile for profile in MODEL_PROFILES}
INPAINT_PROFILES_BY_ID = {profile.inpaint_model_id: profile for profile in MODEL_PROFILES}


def require_model_profile(provider_model_id: str) -> ModelCapability:
    try:
        return MODEL_PROFILES_BY_ID[provider_model_id]
    except KeyError as exc:
        raise ValueError("unsupported NovelAI model") from exc


def require_inpaint_model_profile(inpaint_model_id: str) -> ModelCapability:
    try:
        return INPAINT_PROFILES_BY_ID[inpaint_model_id]
    except KeyError as exc:
        raise ValueError("unsupported NovelAI inpaint model") from exc


def contract_payload() -> dict[str, Any]:
    return {
        "source_url": SWAGGER_DOCUMENT_URL,
        "sha256": CONTRACT_SHA256,
        "fetched_on": CONTRACT_FETCHED_ON,
        "swagger_version": "2.0",
        "api_title": "Omegalaser API",
        "api_version": "1.0",
        "mapping_version": MAPPING_VERSION,
        "allowed_paths": {
            "connection_test": CONNECTION_TEST_PATH,
            "generation": GENERATION_PATH,
            "subscription": SUBSCRIPTION_PATH,
            "upscale": UPSCALE_PATH,
            "augment": AUGMENT_PATH,
            "encode_vibe": ENCODE_VIBE_PATH,
        },
        "opus_zero_anlas_profile": {
            "profile_version": OPUS_ZERO_ANLAS_PROFILE_VERSION,
            "required_tier": OPUS_TIER,
            "max_pixels": OPUS_ZERO_ANLAS_MAX_PIXELS,
            "max_steps": OPUS_ZERO_ANLAS_MAX_STEPS,
            "n_samples": OPUS_ZERO_ANLAS_SAMPLE_COUNT,
            "requires_single_image": True,
            "allows_base_or_reference_image": False,
            "v5_allowance_is_usage_limited": True,
            "default_dimensions": [
                {"width": width, "height": height}
                for width, height in OPUS_ZERO_ANLAS_DIMENSIONS
            ],
            "official_docs": [
                "https://docs.novelai.net/en/subscription/",
                "https://docs.novelai.net/en/image/stepsguidance/",
                "https://docs.novelai.net/en/image/",
                "https://docs.novelai.net/en/faq/#opus-usage-limits",
            ],
        },
        "models": [profile.to_payload() for profile in MODEL_PROFILES],
    }
