from __future__ import annotations

from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .....shared_kernel import canonical_sha256
from ....prompting.public import PromptPlan, prompt_plan_sha256
from ...contracts import ProviderCharacterCaption, ProviderExecutionSpec
from ...errors import ProviderMappingError

NOVELAI_V4_MAPPING_VERSION = "novelai-image-2026-08-09.3-v03-opus-zero-anlas-1"
SUPPORTED_V4_MODEL_PREFIX = "nai-diffusion-4"
MAX_V03_CHARACTERS = 3


class NovelAIAdapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class NovelAICoordinates(NovelAIAdapterContract):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class NovelAIV4CharacterCaption(NovelAIAdapterContract):
    char_caption: str = Field(min_length=1, max_length=12_000)
    centers: tuple[NovelAICoordinates, ...] = Field(min_length=1, max_length=1)


class NovelAIV4ExternalCaption(NovelAIAdapterContract):
    base_caption: str = Field(min_length=1, max_length=12_000)
    char_captions: tuple[NovelAIV4CharacterCaption, ...] = Field(
        min_length=1,
        max_length=MAX_V03_CHARACTERS,
    )


class NovelAIV4Condition(NovelAIAdapterContract):
    caption: NovelAIV4ExternalCaption
    use_coords: Literal[True] = True
    use_order: Literal[True] = True
    legacy_uc: Literal[False] | None = None


class NovelAIReferenceCaption(NovelAIAdapterContract):
    base_caption: Literal["character", "style", "character&style"]
    char_captions: tuple[()] = ()


class NovelAIReferenceDescription(NovelAIAdapterContract):
    caption: NovelAIReferenceCaption
    legacy_uc: Literal[False] = False
    use_coords: Literal[False] = False
    use_order: Literal[True] = True


class NovelAIImg2ImgParameters(NovelAIAdapterContract):
    strength: float = Field(ge=0.1, le=1)
    noise: float = Field(default=0.0, ge=0, le=0)
    color_correct: Literal[True] = True


class NovelAIGenerationParameters(NovelAIAdapterContract):
    width: int = Field(ge=64, le=2048, multiple_of=64)
    height: int = Field(ge=64, le=2048, multiple_of=64)
    steps: int = Field(ge=1, le=50)
    scale: float = Field(ge=0, le=10)
    sampler: Literal["k_euler", "k_euler_ancestral", "k_dpmpp_2m", "k_dpmpp_sde"]
    noise_schedule: Literal["karras", "exponential", "polyexponential"]
    seed: int = Field(ge=0, le=4_294_967_287)
    n_samples: Literal[1] = 1
    negative_prompt: str = Field(min_length=1, max_length=12_000)
    prompt: str = Field(min_length=1, max_length=12_000)
    qualityToggle: Literal[True] = True
    ucPreset: Literal[3] = 3
    params_version: Literal[3] = 3
    cfg_rescale: Literal[0] = 0
    dynamic_thresholding: Literal[False] = False
    legacy: Literal[False] = False
    legacy_v3_extend: Literal[False] = False
    prefer_brownian: bool
    deliberate_euler_ancestral_bug: Literal[False] = False
    image_format: Literal["png"] = "png"
    v4_prompt: NovelAIV4Condition
    v4_negative_prompt: NovelAIV4Condition
    director_reference_images: tuple[str, ...] | None = Field(
        default=None, min_length=1, max_length=1
    )
    director_reference_descriptions: tuple[NovelAIReferenceDescription, ...] | None = Field(
        default=None, min_length=1, max_length=1
    )
    director_reference_strength_values: tuple[float, ...] | None = Field(
        default=None, min_length=1, max_length=1
    )
    director_reference_secondary_strength_values: tuple[float, ...] | None = Field(
        default=None, min_length=1, max_length=1
    )
    director_reference_information_extracted: tuple[float, ...] | None = Field(
        default=None, min_length=1, max_length=1
    )
    image: str | None = Field(default=None, min_length=1)
    mask: str | None = Field(default=None, min_length=1)
    strength: float | None = Field(default=None, ge=0.1, le=1)
    noise: float | None = Field(default=None, ge=0, le=0)
    img2img: NovelAIImg2ImgParameters | None = None
    add_original_image: Literal[False] | None = None
    color_correct: Literal[True] | None = None

    @model_validator(mode="after")
    def aligned_structured_captions_and_optional_inputs(
        self,
    ) -> NovelAIGenerationParameters:
        positive = self.v4_prompt.caption.char_captions
        negative = self.v4_negative_prompt.caption.char_captions
        if len(positive) != len(negative):
            raise ValueError("positive and negative character caption counts must match")
        if [item.centers for item in positive] != [item.centers for item in negative]:
            raise ValueError("positive and negative character coordinates must match")
        reference_fields = (
            self.director_reference_images,
            self.director_reference_descriptions,
            self.director_reference_strength_values,
            self.director_reference_secondary_strength_values,
            self.director_reference_information_extracted,
        )
        populated_references = [value for value in reference_fields if value is not None]
        if populated_references and (
            len(populated_references) != len(reference_fields)
            or len({len(value) for value in populated_references}) != 1
        ):
            raise ValueError("precise reference arrays must be complete and aligned")
        if self.director_reference_information_extracted is not None and any(
            value != 1.0 for value in self.director_reference_information_extracted
        ):
            raise ValueError("precise reference extraction values must be frozen at 1.0")
        inpaint_fields = (
            self.image,
            self.mask,
            self.strength,
            self.noise,
            self.img2img,
            self.add_original_image,
            self.color_correct,
        )
        if any(value is not None for value in inpaint_fields) and not all(
            value is not None for value in inpaint_fields
        ):
            raise ValueError("inpaint fields must be complete")
        if self.width * self.height > 3_047_424:
            raise ValueError("image dimensions exceed the provider pixel limit")
        return self


class NovelAIV4Payload(NovelAIAdapterContract):
    action: Literal["generate", "infill"]
    input: str = Field(min_length=1, max_length=12_000)
    model: str = Field(min_length=1, max_length=100)
    parameters: NovelAIGenerationParameters

    @model_validator(mode="after")
    def action_matches_inputs(self) -> NovelAIV4Payload:
        has_inpaint = self.parameters.image is not None
        if (self.action == "infill") != has_inpaint:
            raise ValueError("infill action and image inputs must agree")
        if self.input != self.parameters.prompt:
            raise ValueError("root input must match the frozen base prompt")
        if self.parameters.v4_prompt.legacy_uc is not None:
            raise ValueError("positive V4 condition cannot contain legacy_uc")
        if self.parameters.v4_negative_prompt.legacy_uc is not False:
            raise ValueError("negative V4 condition must disable legacy_uc")
        return self


class MappedNovelAIExecution(NovelAIAdapterContract):
    execution_spec: ProviderExecutionSpec
    payload: NovelAIV4Payload


def map_prompt_plan_to_novelai(
    *,
    prompt_plan: PromptPlan,
    generation_spec_id: UUID,
    model_id: str,
    contract_sha256: str,
    capability_snapshot_sha256: str,
    page_layout_draft_sha256: str,
    width: int,
    height: int,
    seed: int,
    provider_execution_spec_id: UUID | None = None,
    seed_material: str | None = None,
    steps: int,
    scale: float,
    sampler: str,
    noise_schedule: str,
    mapping_version: str = NOVELAI_V4_MAPPING_VERSION,
    action: Literal["generate", "infill"] = "generate",
    reference: dict[str, Any] | None = None,
    source_image_base64: str | None = None,
    mask_base64: str | None = None,
    inpaint_strength: float | None = None,
    edit_prompt: str | None = None,
) -> MappedNovelAIExecution:
    """Map stable PromptPlan semantics to one frozen, allowlisted NovelAI V4 payload."""

    if prompt_plan_sha256(prompt_plan) != prompt_plan.content_sha256:
        _fail("PROMPT_PLAN_HASH_MISMATCH", "PromptPlan 内容哈希校验失败。")
    if not model_id.startswith(SUPPORTED_V4_MODEL_PREFIX):
        _fail(
            "NOVELAI_MULTI_CHARACTER_UNSUPPORTED",
            "当前模型能力不支持结构化 NovelAI V4 多角色提示。",
            {"model_id": model_id},
        )
    if mapping_version != NOVELAI_V4_MAPPING_VERSION:
        _fail(
            "NOVELAI_MAPPING_VERSION_UNSUPPORTED",
            "冻结的 NovelAI 映射版本不受当前适配器支持。",
            {"mapping_version": mapping_version},
        )
    if not 1 <= len(prompt_plan.characters) <= MAX_V03_CHARACTERS:
        _fail(
            "MULTI_CHARACTER_CONTRACT_INVALID",
            "PromptPlan 角色数量不在冻结能力范围内。",
        )
    ordered = sorted(prompt_plan.characters, key=lambda item: item.order)
    if [item.order for item in ordered] != list(range(len(ordered))):
        _fail(
            "MULTI_CHARACTER_CONTRACT_INVALID",
            "PromptPlan 角色顺序必须从 0 开始连续。",
        )

    fixed_tags = {_fold(tag) for item in ordered for tag in item.fixed_tags}
    base_positive_tags = _ordered_tags(
        (
            *prompt_plan.style_tags,
            *prompt_plan.base.positive_tags,
            *(
                (prompt_plan.base.relationship_action,)
                if prompt_plan.base.relationship_action
                else ()
            ),
            *prompt_plan.continuity_tags,
        ),
        "base positive tags",
    )
    leaked = sorted(tag for tag in base_positive_tags if _fold(tag) in fixed_tags)
    if leaked:
        _fail(
            "NOVELAI_BASE_CHARACTER_TAG_LEAK",
            "角色固定外观 tags 不能混入 NovelAI base caption。",
            {"tags": leaked},
        )
    base_negative_tags = _ordered_tags(
        prompt_plan.base.negative_tags,
        "base negative tags",
    )
    captions: list[ProviderCharacterCaption] = []
    positive_payload: list[NovelAIV4CharacterCaption] = []
    negative_payload: list[NovelAIV4CharacterCaption] = []
    for character in ordered:
        positive_tags = _ordered_tags(
            (*character.fixed_tags, *character.variable_positive_tags, character.action),
            "character positive tags",
        )
        negative_tags = _ordered_tags(character.negative_tags, "character negative tags")
        coordinate = NovelAICoordinates(x=character.center.x, y=character.center.y)
        captions.append(
            ProviderCharacterCaption(
                character_id=character.character_id,
                order=character.order,
                center=character.center,
                positive_tags=list(positive_tags),
                negative_tags=list(negative_tags),
            )
        )
        positive_payload.append(
            NovelAIV4CharacterCaption(
                char_caption=_join_tags(positive_tags),
                centers=(coordinate,),
            )
        )
        negative_payload.append(
            NovelAIV4CharacterCaption(
                char_caption=_join_tags(negative_tags),
                centers=(coordinate,),
            )
        )

    base_prompt_parts = list(base_positive_tags)
    if edit_prompt is not None:
        normalized_edit = " ".join(edit_prompt.split())
        if not normalized_edit:
            _fail("NOVELAI_EDIT_PROMPT_EMPTY", "局部重绘提示词不能为空。")
        base_prompt_parts.append(normalized_edit)
    base_prompt = _join_tags(tuple(base_prompt_parts))
    base_negative = _join_tags(base_negative_tags)
    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "steps": steps,
        "scale": scale,
        "sampler": sampler,
        "noise_schedule": noise_schedule,
        "seed": seed,
        "n_samples": 1,
        "prompt": base_prompt,
        "negative_prompt": base_negative,
        "qualityToggle": True,
        "ucPreset": 3,
        "params_version": 3,
        "cfg_rescale": 0,
        "dynamic_thresholding": False,
        "legacy": False,
        "legacy_v3_extend": False,
        "prefer_brownian": sampler == "k_euler_ancestral",
        "deliberate_euler_ancestral_bug": False,
        "image_format": "png",
        "v4_prompt": {
            "caption": {
                "base_caption": base_prompt,
                "char_captions": [item.model_dump(mode="json") for item in positive_payload],
            },
            "use_coords": True,
            "use_order": True,
        },
        "v4_negative_prompt": {
            "caption": {
                "base_caption": base_negative,
                "char_captions": [item.model_dump(mode="json") for item in negative_payload],
            },
            "legacy_uc": False,
            "use_coords": True,
            "use_order": True,
        },
    }
    if reference is not None:
        try:
            parameters.update(
                {
                    "director_reference_images": [str(reference["png_base64"])],
                    "director_reference_descriptions": [
                        {
                            "caption": {
                                "base_caption": str(reference["description"]),
                                "char_captions": [],
                            },
                            "legacy_uc": False,
                            "use_coords": False,
                            "use_order": True,
                        }
                    ],
                    "director_reference_strength_values": [float(reference["strength"])],
                    "director_reference_secondary_strength_values": [float(reference["fidelity"])],
                    "director_reference_information_extracted": [1.0],
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            _fail("NOVELAI_REFERENCE_INVALID", "Precise Reference 输入不完整。")
            raise AssertionError("unreachable") from exc
    if action == "infill":
        if not source_image_base64 or not mask_base64 or inpaint_strength is None:
            _fail("NOVELAI_INPAINT_INPUT_INVALID", "局部重绘输入不完整。")
        if reference is not None:
            _fail("NOVELAI_INPAINT_REFERENCE_CONFLICT", "P0 局部重绘不叠加参考图。")
        parameters.update(
            {
                "image": source_image_base64,
                "mask": mask_base64,
                "strength": inpaint_strength,
                "noise": 0.0,
                "img2img": {
                    "strength": inpaint_strength,
                    "noise": 0.0,
                    "color_correct": True,
                },
                "add_original_image": False,
                "color_correct": True,
            }
        )
    try:
        payload = NovelAIV4Payload(
            action=action,
            input=base_prompt,
            model=model_id,
            parameters=NovelAIGenerationParameters.model_validate(parameters),
        )
    except ValueError as exc:
        raise ProviderMappingError(
            "MULTI_CHARACTER_CONTRACT_INVALID",
            "NovelAI V4 结构化载荷未通过本地契约校验。",
        ) from exc
    payload_sha256 = _payload_sha256(payload)
    spec_id = provider_execution_spec_id or uuid5(
        NAMESPACE_URL,
        "manga-maker:provider-execution:"
        f"{seed_material or generation_spec_id}:{mapping_version}",
    )
    execution_spec = ProviderExecutionSpec(
        provider_execution_spec_id=spec_id,
        version=1,
        generation_spec_id=generation_spec_id,
        action=action,
        mapping_version=mapping_version,
        contract_sha256=contract_sha256,
        capability_snapshot_sha256=capability_snapshot_sha256,
        model_id=model_id,
        prompt_plan_id=prompt_plan.prompt_plan_id,
        prompt_plan_version=prompt_plan.version,
        prompt_plan_sha256=prompt_plan.content_sha256,
        page_layout_draft_id=prompt_plan.layout_constraints.page_layout_draft_id,
        page_layout_draft_version=prompt_plan.layout_constraints.page_layout_draft_version,
        page_layout_draft_sha256=page_layout_draft_sha256,
        width=width,
        height=height,
        seed=seed,
        base_positive_tags=list(base_positive_tags),
        base_negative_tags=list(base_negative_tags),
        character_captions=captions,
        payload_sha256=payload_sha256,
    )
    return MappedNovelAIExecution(execution_spec=execution_spec, payload=payload)


def require_frozen_novelai_payload(
    execution_spec: ProviderExecutionSpec,
    payload: NovelAIV4Payload | dict[str, Any],
) -> NovelAIV4Payload:
    """Revalidate a persisted payload without remapping or reading a credential."""

    try:
        validated = NovelAIV4Payload.model_validate(payload)
    except ValueError as exc:
        raise ProviderMappingError(
            "NOVELAI_FROZEN_PAYLOAD_INVALID",
            "冻结的 NovelAI 载荷结构无效。",
        ) from exc
    if _payload_sha256(validated) != execution_spec.payload_sha256:
        _fail("NOVELAI_FROZEN_PAYLOAD_HASH_MISMATCH", "冻结的 NovelAI 载荷哈希不一致。")
    if (
        validated.model != execution_spec.model_id
        or validated.action != execution_spec.action
        or validated.parameters.width != execution_spec.width
        or validated.parameters.height != execution_spec.height
        or validated.parameters.seed != execution_spec.seed
    ):
        _fail("NOVELAI_FROZEN_PAYLOAD_SPEC_MISMATCH", "冻结载荷与执行规格不一致。")
    positive = validated.parameters.v4_prompt.caption.char_captions
    negative = validated.parameters.v4_negative_prompt.caption.char_captions
    if len(positive) != len(execution_spec.character_captions) or len(negative) != len(
        execution_spec.character_captions
    ):
        _fail("MULTI_CHARACTER_CONTRACT_INVALID", "冻结载荷角色区块数量不一致。")
    expected_centers = [
        (caption.center.x, caption.center.y)
        for caption in sorted(execution_spec.character_captions, key=lambda item: item.order)
    ]
    actual_positive = [(item.centers[0].x, item.centers[0].y) for item in positive]
    actual_negative = [(item.centers[0].x, item.centers[0].y) for item in negative]
    if actual_positive != expected_centers or actual_negative != expected_centers:
        _fail("MULTI_CHARACTER_CONTRACT_INVALID", "冻结载荷角色顺序或坐标不一致。")
    if any(not item.char_caption.strip() for item in (*positive, *negative)):
        _fail("MULTI_CHARACTER_CONTRACT_INVALID", "冻结载荷包含空角色 caption。")
    expected_positive = [
        _join_tags(tuple(caption.positive_tags))
        for caption in sorted(execution_spec.character_captions, key=lambda item: item.order)
    ]
    expected_negative = [
        _join_tags(tuple(caption.negative_tags))
        for caption in sorted(execution_spec.character_captions, key=lambda item: item.order)
    ]
    if [item.char_caption for item in positive] != expected_positive or [
        item.char_caption for item in negative
    ] != expected_negative:
        _fail("MULTI_CHARACTER_CONTRACT_INVALID", "冻结载荷角色 caption 与执行规格不一致。")
    expected_base = _join_tags(tuple(execution_spec.base_positive_tags))
    if execution_spec.action == "generate" and validated.input != expected_base:
        _fail("NOVELAI_FROZEN_PAYLOAD_SPEC_MISMATCH", "冻结载荷 base caption 不一致。")
    if validated.parameters.v4_negative_prompt.caption.base_caption != _join_tags(
        tuple(execution_spec.base_negative_tags)
    ):
        _fail("NOVELAI_FROZEN_PAYLOAD_SPEC_MISMATCH", "冻结载荷负向 base 不一致。")
    return validated


def _ordered_tags(values: tuple[str, ...] | list[str], field_name: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or "," in normalized or "\n" in normalized:
            _fail(
                "NOVELAI_TAG_INVALID",
                f"{field_name} 必须由独立、非空且不含逗号或换行的字段组成。",
            )
        folded = _fold(normalized)
        if folded in seen:
            continue
        seen.add(folded)
        result.append(normalized)
    if not result:
        _fail("NOVELAI_CAPTION_EMPTY", f"{field_name} 不能为空。")
    return tuple(result)


def _join_tags(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def _payload_sha256(payload: NovelAIV4Payload) -> str:
    return canonical_sha256(payload.model_dump(mode="json", exclude_none=True))


def _fold(value: str) -> str:
    return " ".join(value.split()).casefold()


def _fail(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise ProviderMappingError(code, message, details)
