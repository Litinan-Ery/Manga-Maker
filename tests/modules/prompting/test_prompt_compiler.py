from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from backend.app.modules.layout.contracts import FrameSpec, PageLayoutDraft
from backend.app.modules.layout.domain import frame_content_sha256
from backend.app.modules.prompting.contracts import PromptPlan, TextModelSource
from backend.app.modules.prompting.public import (
    ApprovedCharacterTagSet,
    CharacterPromptDraft,
    PanelPromptDraft,
    PromptCompilationError,
    PromptCompilationInput,
    compile_prompt_package,
    read_legacy_flat_prompt,
    require_prompt_package_integrity,
)
from backend.app.shared_kernel import canonical_sha256

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "fixtures" / "v0.3"


def load(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def materialized_plan(name: str) -> PromptPlan:
    raw = PromptPlan.model_validate(load(name))
    provisional = raw.model_copy(update={"content_sha256": "0" * 64})
    return provisional.model_copy(
        update={
            "content_sha256": canonical_sha256(
                provisional.model_dump(mode="json", exclude={"content_sha256"})
            )
        }
    )


def input_from_fixture(name: str) -> PromptCompilationInput:
    plan = materialized_plan(name)
    layout = PageLayoutDraft.model_validate(load("page-layout-draft.json"))
    frame = next(
        item for item in layout.frames if item.frame_id == plan.layout_constraints.frame_id
    )
    tag_sets = tuple(
        ApprovedCharacterTagSet(
            character_id=character.character_id,
            character_tag_set_version_id=character.character_tag_set_version_id,
            fixed_tags=tuple(character.fixed_tags),
            fixed_tags_sha256=canonical_sha256(character.fixed_tags),
            negative_tags=tuple(character.negative_tags),
        )
        for character in plan.characters
    )
    characters = tuple(
        CharacterPromptDraft(
            character_id=character.character_id,
            character_tag_set_version_id=character.character_tag_set_version_id,
            variable_positive_tags=tuple(character.variable_positive_tags),
            negative_tags=(),
            action=character.action,
            order=character.order,
            center=character.center,
        )
        for character in plan.characters
    )
    return PromptCompilationInput(
        version=plan.version,
        draft=PanelPromptDraft(
            prompt_package_id=UUID("01900000-0000-7000-8000-000000000204"),
            panel_id=plan.panel_id,
            base_positive_tags=tuple(plan.base.positive_tags),
            base_negative_tags=tuple(plan.base.negative_tags),
            relationship_action=plan.base.relationship_action,
            characters=characters,
            style_tags=tuple(plan.style_tags),
            continuity_tags=tuple(plan.continuity_tags),
        ),
        approved_tag_sets=tag_sets,
        frame=frame,
        frame_sha256=frame_content_sha256(frame),
        page_layout_draft_id=layout.page_layout_draft_id,
        page_layout_draft_version=layout.version,
        text_model_source=TextModelSource(
            text_model_profile_id=UUID("01900000-0000-7000-8000-000000000205"),
            profile_version=3,
            model_name="fixture-structured-model",
            prompt_template_version="panel-plan-v2",
            text_stage_run_id=UUID("01900000-0000-7000-8000-000000000701"),
        ),
    )


@pytest.mark.parametrize(
    ("fixture_name", "character_count"),
    (
        ("prompt-plan-single.json", 1),
        ("prompt-plan-double.json", 2),
    ),
)
def test_compiler_preserves_structured_character_fields_and_is_deterministic(
    fixture_name: str,
    character_count: int,
) -> None:
    source = input_from_fixture(fixture_name)

    first = compile_prompt_package(source)
    second = compile_prompt_package(source)

    assert first == second
    assert first.content_sha256 == second.content_sha256
    assert len(first.prompt_plan.characters) == character_count
    assert [item.order for item in first.prompt_plan.characters] == list(
        range(character_count)
    )
    assert [item.action for item in first.prompt_plan.characters] == [
        item.action for item in source.draft.characters
    ]
    assert [item.center for item in first.prompt_plan.characters] == [
        item.center for item in source.draft.characters
    ]
    assert [item.fixed_tags for item in first.prompt_plan.characters] == [
        list(item.fixed_tags) for item in source.approved_tag_sets
    ]
    assert [item.negative_tags for item in first.prompt_plan.characters] == [
        list(item.negative_tags) for item in source.approved_tag_sets
    ]
    assert first.prompt_plan.base.relationship_action == source.draft.relationship_action
    require_prompt_package_integrity(first)


def test_compiler_supports_three_characters_without_flattening() -> None:
    plan = materialized_plan("prompt-plan-triple.json")
    frame_payload = deepcopy(load("page-layout-draft.json")["frames"][1])
    frame_payload.update(
        {
            "frame_id": str(plan.layout_constraints.frame_id),
            "panel_id": str(plan.panel_id),
            "character_positions": [
                {
                    "character_id": str(character.character_id),
                    "center": character.center.model_dump(mode="json"),
                    "prominence": "primary" if character.order == 1 else "secondary",
                }
                for character in plan.characters
            ],
        }
    )
    frame = FrameSpec.model_validate(frame_payload)
    base = input_from_fixture("prompt-plan-double.json")
    source = PromptCompilationInput(
        version=1,
        draft=PanelPromptDraft(
            prompt_package_id=base.draft.prompt_package_id,
            panel_id=plan.panel_id,
            base_positive_tags=tuple(plan.base.positive_tags),
            base_negative_tags=tuple(plan.base.negative_tags),
            relationship_action=plan.base.relationship_action,
            characters=tuple(
                CharacterPromptDraft(
                    character_id=item.character_id,
                    character_tag_set_version_id=item.character_tag_set_version_id,
                    variable_positive_tags=tuple(item.variable_positive_tags),
                    action=item.action,
                    order=item.order,
                    center=item.center,
                )
                for item in plan.characters
            ),
            style_tags=tuple(plan.style_tags),
            continuity_tags=tuple(plan.continuity_tags),
        ),
        approved_tag_sets=tuple(
            ApprovedCharacterTagSet(
                character_id=item.character_id,
                character_tag_set_version_id=item.character_tag_set_version_id,
                fixed_tags=tuple(item.fixed_tags),
                fixed_tags_sha256=canonical_sha256(item.fixed_tags),
                negative_tags=tuple(item.negative_tags),
            )
            for item in plan.characters
        ),
        frame=frame,
        frame_sha256=frame_content_sha256(frame),
        page_layout_draft_id=plan.layout_constraints.page_layout_draft_id,
        page_layout_draft_version=1,
        text_model_source=base.text_model_source,
    )

    package = compile_prompt_package(source)

    assert len(package.prompt_plan.characters) == 3
    assert package.prompt_plan.base.relationship_action == plan.base.relationship_action
    assert all(item.action for item in package.prompt_plan.characters)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("missing_character", "PROMPT_CHARACTER_COVERAGE_INVALID"),
        ("wrong_position", "PROMPT_LAYOUT_CHARACTER_POSITION_MISMATCH"),
        ("fixed_variable_conflict", "PROMPT_CHARACTER_TAG_CONFLICT"),
        ("positive_negative_conflict", "PROMPT_CHARACTER_TAG_CONFLICT"),
    ),
)
def test_compiler_blocks_coverage_position_and_tag_conflicts(
    mutation: str,
    expected_code: str,
) -> None:
    source = input_from_fixture("prompt-plan-double.json")
    draft = source.draft
    characters = list(draft.characters)
    if mutation == "missing_character":
        characters.pop()
    elif mutation == "wrong_position":
        characters[0] = characters[0].model_copy(
            update={"center": characters[0].center.model_copy(update={"x": 0.99})}
        )
    elif mutation == "fixed_variable_conflict":
        characters[0] = characters[0].model_copy(
            update={"variable_positive_tags": (source.approved_tag_sets[0].fixed_tags[0],)}
        )
    else:
        characters[0] = characters[0].model_copy(
            update={"negative_tags": (characters[0].variable_positive_tags[0],)}
        )
    changed = source.model_copy(
        update={"draft": draft.model_copy(update={"characters": tuple(characters)})}
    )

    with pytest.raises(PromptCompilationError) as exc_info:
        compile_prompt_package(changed)

    assert exc_info.value.code == expected_code


def test_legacy_flat_prompt_reader_is_explicitly_read_only_and_job_ineligible() -> None:
    legacy = read_legacy_flat_prompt(
        {
            "prompt_package_id": "01900000-0000-7000-8000-000000000204",
            "panel_id": "01900000-0000-7000-8000-000000000102",
            "compiled_prompt": "flat positive prompt",
            "compiled_negative_prompt": "flat negative prompt",
            "character_blocks": [{"character_id": "one"}, {"character_id": "two"}],
        },
        legacy_schema_version="1.0",
    )

    assert legacy.schema_version == "legacy_flat_prompt"
    assert legacy.access == "read_only"
    assert legacy.regeneration_required is True
    assert legacy.eligible_for_new_job is False
    assert legacy.character_count == 2
