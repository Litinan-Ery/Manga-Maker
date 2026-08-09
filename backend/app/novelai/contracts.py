from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

IMAGE_API_BASE_URL = "https://image.novelai.net"
SWAGGER_DOCUMENT_URL = f"{IMAGE_API_BASE_URL}/docs/doc.json"
CONTRACT_SHA256 = "f43ea4feff0d390dc65e5ed704d4cf7e75af741bb413b86981f465fb8fb556f8"
CONTRACT_FETCHED_ON = "2026-08-09"
MAPPING_VERSION = "novelai-image-2026-08-09.1"
CONNECTION_TEST_PATH = "/ai/generate-image/suggest-tags"
GENERATION_PATH = "/ai/generate-image"
UPSCALE_PATH = "/ai/upscale"
AUGMENT_PATH = "/ai/augment-image"
ENCODE_VIBE_PATH = "/ai/encode-vibe"


class NovelAIModel(StrEnum):
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

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_model_id"] = payload.pop("model")
        return payload


MODEL_PROFILES: tuple[ModelCapability, ...] = (
    ModelCapability(
        model=NovelAIModel.V45_FULL,
        label="Anime V4.5 Full",
        inpaint_model_id="nai-diffusion-4-5-full-inpainting",
        recommended=True,
        supports_precise_reference=True,
        supports_multi_character_prompt=True,
        supports_vibe_transfer=True,
        precise_reference_excludes_vibe_transfer=True,
        prompt_token_note="官方文档标注约 512 T5 tokens，Unicode 支持有限。",
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
    ),
)

MODEL_PROFILES_BY_ID = {str(profile.model): profile for profile in MODEL_PROFILES}


def require_model_profile(provider_model_id: str) -> ModelCapability:
    try:
        return MODEL_PROFILES_BY_ID[provider_model_id]
    except KeyError as exc:
        raise ValueError("unsupported NovelAI model") from exc


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
            "upscale": UPSCALE_PATH,
            "augment": AUGMENT_PATH,
            "encode_vibe": ENCODE_VIBE_PATH,
        },
        "models": [profile.to_payload() for profile in MODEL_PROFILES],
    }
