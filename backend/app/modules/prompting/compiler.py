from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared_kernel import canonical_sha256
from ..layout.contracts import FrameSpec, NormalizedPoint
from .contracts import (
    LayoutConstraints,
    PromptBase,
    PromptCharacter,
    PromptPackage,
    PromptPlan,
    TextModelSource,
)
from .errors import PromptCompilationError

UNIVERSAL_POSITIVE_TAGS: tuple[str, ...] = (
    "no text",
    "no letters",
    "no speech bubbles",
    "no watermark",
)
UNIVERSAL_NEGATIVE_TAGS: tuple[str, ...] = (
    "color",
    "text",
    "letters",
    "speech bubble",
    "caption",
    "page number",
    "watermark",
    "logo",
)


class CompilerContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ApprovedCharacterTagSet(CompilerContract):
    character_id: UUID
    character_tag_set_version_id: UUID
    fixed_tags: tuple[str, ...] = Field(min_length=1, max_length=60)
    fixed_tags_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    negative_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=60)

    @model_validator(mode="after")
    def hash_matches_ordered_fixed_tags(self) -> ApprovedCharacterTagSet:
        if canonical_sha256(list(self.fixed_tags)) != self.fixed_tags_sha256:
            raise ValueError("approved fixed tag hash does not match its ordered content")
        return self


class CharacterPromptDraft(CompilerContract):
    character_id: UUID
    character_tag_set_version_id: UUID
    variable_positive_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=60)
    negative_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=60)
    action: str = Field(min_length=1, max_length=500)
    order: int = Field(ge=0, le=19)
    center: NormalizedPoint


class PanelPromptDraft(CompilerContract):
    prompt_package_id: UUID
    panel_id: UUID
    base_positive_tags: tuple[str, ...] = Field(min_length=1, max_length=100)
    base_negative_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    relationship_action: str | None = Field(default=None, min_length=1, max_length=500)
    characters: tuple[CharacterPromptDraft, ...] = Field(min_length=1, max_length=3)
    style_tags: tuple[str, ...] = Field(min_length=1, max_length=100)
    continuity_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=100)


class PromptCompilationInput(CompilerContract):
    version: int = Field(ge=1)
    draft: PanelPromptDraft
    approved_tag_sets: tuple[ApprovedCharacterTagSet, ...] = Field(min_length=1, max_length=3)
    frame: FrameSpec
    frame_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_layout_draft_id: UUID
    page_layout_draft_version: int = Field(ge=1)
    text_model_source: TextModelSource


class LegacyFlatPromptSnapshot(CompilerContract):
    schema_version: Literal["legacy_flat_prompt"] = "legacy_flat_prompt"
    legacy_schema_version: str = Field(min_length=1, max_length=50)
    prompt_package_id: UUID
    panel_id: UUID
    compiled_prompt: str = Field(min_length=1, max_length=12_000)
    compiled_negative_prompt: str = Field(min_length=1, max_length=12_000)
    character_count: int = Field(ge=0, le=100)
    access: Literal["read_only"] = "read_only"
    regeneration_required: Literal[True] = True
    eligible_for_new_job: Literal[False] = False


def compile_prompt_package(source: PromptCompilationInput) -> PromptPackage:
    draft = source.draft
    if source.frame.panel_id != draft.panel_id:
        _fail(
            "PROMPT_LAYOUT_PANEL_MISMATCH",
            "PromptPlan 与版式 frame 没有引用同一个面板。",
        )

    blocks_by_id = _unique_by_character(
        draft.characters,
        "PROMPT_CHARACTER_DUPLICATE",
        lambda item: item.character_id,
    )
    tags_by_id = _unique_by_character(
        source.approved_tag_sets,
        "PROMPT_CHARACTER_TAG_SET_DUPLICATE",
        lambda item: item.character_id,
    )
    positions_by_id = _unique_by_character(
        source.frame.character_positions,
        "PROMPT_LAYOUT_CHARACTER_DUPLICATE",
        lambda item: item.character_id,
    )
    expected = set(tags_by_id)
    if set(blocks_by_id) != expected:
        _coverage_error("PROMPT_CHARACTER_COVERAGE_INVALID", expected, set(blocks_by_id))
    if set(positions_by_id) != expected:
        _coverage_error("PROMPT_LAYOUT_CHARACTER_COVERAGE_INVALID", expected, set(positions_by_id))

    ordered = sorted(draft.characters, key=lambda item: item.order)
    if [item.order for item in ordered] != list(range(len(ordered))):
        _fail(
            "PROMPT_CHARACTER_ORDER_INVALID",
            "PromptPlan 角色顺序必须从 0 开始连续且唯一。",
        )
    if len(ordered) > 1 and draft.relationship_action is None:
        _fail(
            "PROMPT_RELATIONSHIP_ACTION_REQUIRED",
            "多角色 PromptPlan 必须保留跨角色关系动作。",
        )

    base_positive = _required_tags(
        (*draft.base_positive_tags, *UNIVERSAL_POSITIVE_TAGS),
        "base positive tags",
    )
    base_negative = _tags((*draft.base_negative_tags, *UNIVERSAL_NEGATIVE_TAGS))
    style_tags = _required_tags(draft.style_tags, "style tags")
    continuity_tags = _tags(draft.continuity_tags)
    _assert_disjoint(
        "PROMPT_BASE_TAG_CONFLICT",
        ("base positive", base_positive),
        ("base negative", base_negative),
    )

    compiled_characters: list[PromptCharacter] = []
    all_fixed: dict[str, set[str]] = {}
    for tag_set in source.approved_tag_sets:
        for tag in tag_set.fixed_tags:
            folded = _fold(tag)
            all_fixed.setdefault(folded, set()).add(str(tag_set.character_id))

    for block in ordered:
        character_id = str(block.character_id)
        tag_set = tags_by_id[character_id]
        if block.character_tag_set_version_id != tag_set.character_tag_set_version_id:
            _fail(
                "PROMPT_CHARACTER_TAG_SET_MISMATCH",
                "PromptPlan 角色引用的固定 TagSet 版本不一致。",
                {"character_id": character_id},
            )
        position = positions_by_id[character_id]
        if block.center != position.center:
            _fail(
                "PROMPT_LAYOUT_CHARACTER_POSITION_MISMATCH",
                "PromptPlan 角色坐标与已批准版式不一致。",
                {
                    "character_id": character_id,
                    "expected": position.center.model_dump(mode="json"),
                    "actual": block.center.model_dump(mode="json"),
                },
            )
        fixed = _required_tags(tag_set.fixed_tags, "fixed tags")
        variable = _tags(block.variable_positive_tags)
        negative = _required_tags(
            (*tag_set.negative_tags, *block.negative_tags),
            "character negative tags",
        )
        _assert_disjoint(
            "PROMPT_CHARACTER_TAG_CONFLICT",
            ("fixed", fixed),
            ("variable positive", variable),
            ("negative", negative),
        )
        _assert_disjoint(
            "PROMPT_GLOBAL_CHARACTER_TAG_CONFLICT",
            ("global negative", base_negative),
            ("character fixed", fixed),
            ("character variable positive", variable),
        )
        for tag in (*variable, *negative):
            owners = all_fixed.get(_fold(tag), set())
            if owners and character_id not in owners:
                _fail(
                    "PROMPT_CHARACTER_TAG_CROSSOVER",
                    "角色可变或负向 tag 不能复用另一角色的固定外观 tag。",
                    {
                        "tag": tag,
                        "character_id": character_id,
                        "fixed_tag_owners": sorted(owners),
                    },
                )
        compiled_characters.append(
            PromptCharacter(
                character_id=block.character_id,
                character_tag_set_version_id=tag_set.character_tag_set_version_id,
                fixed_tags=list(fixed),
                fixed_tags_sha256=tag_set.fixed_tags_sha256,
                variable_positive_tags=list(variable),
                negative_tags=list(negative),
                action=block.action,
                order=block.order,
                center=block.center,
            )
        )

    reserved_text_zones = tuple(zone.rect for zone in source.frame.text_safe_zones)
    plan_id = uuid5(
        NAMESPACE_URL,
        f"manga-maker:prompt-plan:{draft.prompt_package_id}:{source.version}",
    )
    provisional = PromptPlan(
        prompt_plan_id=plan_id,
        version=source.version,
        panel_id=draft.panel_id,
        base=PromptBase(
            positive_tags=list(base_positive),
            negative_tags=list(base_negative),
            relationship_action=draft.relationship_action,
        ),
        characters=compiled_characters,
        style_tags=list(style_tags),
        continuity_tags=list(continuity_tags),
        layout_constraints=LayoutConstraints(
            page_layout_draft_id=source.page_layout_draft_id,
            page_layout_draft_version=source.page_layout_draft_version,
            frame_id=source.frame.frame_id,
            frame_sha256=source.frame_sha256,
            aspect_ratio=source.frame.aspect_ratio,
            focal_point=source.frame.focal_point,
            reserved_text_zones=list(reserved_text_zones),
            crop_safe_rect=source.frame.crop_safe_rect,
        ),
        content_sha256="0" * 64,
    )
    plan_hash = prompt_plan_sha256(provisional)
    plan = provisional.model_copy(update={"content_sha256": plan_hash})
    provisional_package = PromptPackage(
        prompt_package_id=draft.prompt_package_id,
        version=source.version,
        panel_id=draft.panel_id,
        text_model_source=source.text_model_source,
        prompt_plan=plan,
        prompt_plan_sha256=plan_hash,
        content_sha256="0" * 64,
        approved_content_sha256=None,
    )
    return provisional_package.model_copy(
        update={"content_sha256": prompt_package_sha256(provisional_package)}
    )


def prompt_plan_sha256(plan: PromptPlan) -> str:
    return canonical_sha256(plan.model_dump(mode="json", exclude={"content_sha256"}))


def prompt_package_sha256(package: PromptPackage) -> str:
    return canonical_sha256(
        package.model_dump(
            mode="json",
            exclude={"content_sha256", "approved_content_sha256"},
        )
    )


def require_prompt_package_integrity(package: PromptPackage) -> None:
    if prompt_plan_sha256(package.prompt_plan) != package.prompt_plan.content_sha256:
        _fail("PROMPT_PLAN_HASH_MISMATCH", "PromptPlan 内容哈希校验失败。")
    if package.prompt_plan_sha256 != package.prompt_plan.content_sha256:
        _fail("PROMPT_PLAN_HASH_MISMATCH", "PromptPackage 绑定的 PromptPlan 哈希不一致。")
    if prompt_package_sha256(package) != package.content_sha256:
        _fail("PROMPT_PACKAGE_HASH_MISMATCH", "PromptPackage 内容哈希校验失败。")


def read_legacy_flat_prompt(
    payload: dict[str, object],
    *,
    legacy_schema_version: str,
) -> LegacyFlatPromptSnapshot:
    try:
        package_id = UUID(str(payload["prompt_package_id"]))
        panel_id = UUID(str(payload["panel_id"]))
        prompt = str(payload["compiled_prompt"])
        negative = str(payload["compiled_negative_prompt"])
        blocks = payload.get("character_blocks", [])
    except (KeyError, TypeError, ValueError) as exc:
        raise PromptCompilationError(
            "LEGACY_FLAT_PROMPT_INVALID",
            "旧 PromptPackage 缺少只读恢复所需字段。",
        ) from exc
    if not isinstance(blocks, list):
        _fail("LEGACY_FLAT_PROMPT_INVALID", "旧 PromptPackage 角色块格式无效。")
        raise AssertionError("unreachable")
    return LegacyFlatPromptSnapshot(
        legacy_schema_version=legacy_schema_version,
        prompt_package_id=package_id,
        panel_id=panel_id,
        compiled_prompt=prompt,
        compiled_negative_prompt=negative,
        character_count=len(blocks),
    )


def _unique_by_character[CharacterValue](
    values: Sequence[CharacterValue],
    duplicate_code: str,
    identifier: Callable[[CharacterValue], UUID],
) -> dict[str, CharacterValue]:
    result: dict[str, CharacterValue] = {}
    for value in values:
        character_id = str(identifier(value))
        if character_id in result:
            _fail(duplicate_code, "角色必须且只能出现一次。", {"character_id": character_id})
        result[character_id] = value
    return result


def _coverage_error(code: str, expected: set[str], actual: set[str]) -> None:
    _fail(
        code,
        "目标角色、结构化角色块和版式角色位置必须逐一对应。",
        {
            "missing": sorted(expected - actual),
            "unexpected": sorted(actual - expected),
        },
    )


def _tags(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or "," in normalized or "\n" in normalized:
            _fail(
                "PROMPT_TAG_INVALID",
                "每个 tag 必须是独立、非空且不含逗号或换行的字段。",
            )
        folded = normalized.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(normalized)
    return tuple(result)


def _required_tags(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    tags = _tags(values)
    if not tags:
        _fail("PROMPT_TAG_BLOCK_EMPTY", f"{field_name} 不能为空。")
    return tags


def _assert_disjoint(code: str, *groups: tuple[str, Sequence[str]]) -> None:
    ownership: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}
    for name, values in groups:
        for value in values:
            folded = _fold(value)
            prior = ownership.get(folded)
            if prior is not None and prior != name:
                conflicts.setdefault(value, {prior}).add(name)
            else:
                ownership[folded] = name
    if conflicts:
        _fail(
            code,
            "正向、固定、可变与负向 tags 之间存在语义冲突。",
            {"conflicts": {tag: sorted(names) for tag, names in conflicts.items()}},
        )


def _fold(value: str) -> str:
    return " ".join(value.split()).casefold()


def _fail(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise PromptCompilationError(code, message, details)


__all__ = [
    "ApprovedCharacterTagSet",
    "CharacterPromptDraft",
    "LegacyFlatPromptSnapshot",
    "PanelPromptDraft",
    "PromptCompilationInput",
    "compile_prompt_package",
    "prompt_package_sha256",
    "prompt_plan_sha256",
    "read_legacy_flat_prompt",
    "require_prompt_package_integrity",
]
