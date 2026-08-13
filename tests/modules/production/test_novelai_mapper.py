from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.modules.production.adapters.novelai import (
    NOVELAI_V4_MAPPING_VERSION,
    MappedNovelAIExecution,
    map_prompt_plan_to_novelai,
    require_frozen_novelai_payload,
)
from backend.app.modules.production.errors import ProviderMappingError
from backend.app.modules.prompting.contracts import PromptPlan
from backend.app.modules.prompting.public import prompt_plan_sha256

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "fixtures" / "v0.3"
CONTRACT_SHA256 = "f43ea4feff0d390dc65e5ed704d4cf7e75af741bb413b86981f465fb8fb556f8"
CAPABILITY_SHA256 = "a" * 64
GENERATION_SPEC_ID = UUID("01900000-0000-7000-8000-000000000602")


def load_plan(name: str) -> PromptPlan:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    plan = PromptPlan.model_validate(payload)
    return plan.model_copy(update={"content_sha256": prompt_plan_sha256(plan)})


def mapped(plan: PromptPlan, **overrides: object) -> MappedNovelAIExecution:
    arguments: dict[str, object] = {
        "prompt_plan": plan,
        "generation_spec_id": GENERATION_SPEC_ID,
        "model_id": "nai-diffusion-4-5-full",
        "contract_sha256": CONTRACT_SHA256,
        "capability_snapshot_sha256": CAPABILITY_SHA256,
        "page_layout_draft_sha256": "b" * 64,
        "width": 1216,
        "height": 896,
        "seed": 424242,
        "steps": 28,
        "scale": 5.0,
        "sampler": "k_euler_ancestral",
        "noise_schedule": "karras",
    }
    arguments.update(overrides)
    return map_prompt_plan_to_novelai(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fixture_name", "character_count"),
    (
        ("prompt-plan-single.json", 1),
        ("prompt-plan-double.json", 2),
        ("prompt-plan-triple.json", 3),
    ),
)
def test_single_double_triple_mapping_is_aligned_and_deterministic(
    fixture_name: str,
    character_count: int,
) -> None:
    plan = load_plan(fixture_name)
    first = mapped(plan)
    second = mapped(plan)

    assert first == second
    assert first.execution_spec.mapping_version == NOVELAI_V4_MAPPING_VERSION
    assert first.execution_spec.payload_sha256 == second.execution_spec.payload_sha256
    positive = first.payload.parameters.v4_prompt.caption.char_captions
    negative = first.payload.parameters.v4_negative_prompt.caption.char_captions
    assert len(positive) == len(negative) == character_count
    assert all(item.char_caption for item in (*positive, *negative))
    assert [item.centers for item in positive] == [item.centers for item in negative]
    assert [item.order for item in first.execution_spec.character_captions] == list(
        range(character_count)
    )
    assert all(
        action in positive[index].char_caption
        for index, action in enumerate(character.action for character in plan.characters)
    )
    base = first.payload.parameters.v4_prompt.caption.base_caption
    base_fields = {value.strip().casefold() for value in base.split(",")}
    assert all(
        tag.casefold() not in base_fields
        for character in plan.characters
        for tag in character.fixed_tags
    )
    if plan.base.relationship_action is not None:
        assert plan.base.relationship_action in base
        assert all(plan.base.relationship_action not in item.char_caption for item in positive)


def test_swapping_character_order_keeps_caption_coordinate_action_together() -> None:
    plan = load_plan("prompt-plan-double.json")
    swapped_characters = [
        plan.characters[1].model_copy(update={"order": 0}),
        plan.characters[0].model_copy(update={"order": 1}),
    ]
    provisional = plan.model_copy(
        update={"characters": swapped_characters, "content_sha256": "0" * 64}
    )
    swapped = provisional.model_copy(update={"content_sha256": prompt_plan_sha256(provisional)})

    original = mapped(plan)
    changed = mapped(swapped)

    assert [item.character_id for item in changed.execution_spec.character_captions] == [
        plan.characters[1].character_id,
        plan.characters[0].character_id,
    ]
    actual_center = changed.payload.parameters.v4_prompt.caption.char_captions[0].centers[0]
    assert actual_center.model_dump() == plan.characters[1].center.model_dump()
    assert plan.characters[1].action in (
        changed.payload.parameters.v4_prompt.caption.char_captions[0].char_caption
    )
    assert changed.execution_spec.payload_sha256 != original.execution_spec.payload_sha256


@pytest.mark.parametrize(
    ("model_id", "mapping_version", "expected_code"),
    (
        (
            "nai-diffusion-3",
            NOVELAI_V4_MAPPING_VERSION,
            "NOVELAI_MULTI_CHARACTER_UNSUPPORTED",
        ),
        (
            "nai-diffusion-4-5-full",
            "novelai-image-future",
            "NOVELAI_MAPPING_VERSION_UNSUPPORTED",
        ),
    ),
)
def test_unsupported_capability_or_mapping_version_fails_closed(
    model_id: str,
    mapping_version: str,
    expected_code: str,
) -> None:
    with pytest.raises(ProviderMappingError) as error:
        mapped(
            load_plan("prompt-plan-double.json"),
            model_id=model_id,
            mapping_version=mapping_version,
        )
    assert error.value.code == expected_code


@pytest.mark.parametrize("mutation", ("drop_negative", "wrong_order", "empty_caption"))
def test_frozen_payload_rejects_count_order_or_empty_caption(mutation: str) -> None:
    result = mapped(load_plan("prompt-plan-double.json"))
    payload = result.payload.model_dump(mode="json", exclude_none=True)
    if mutation == "drop_negative":
        payload["parameters"]["v4_negative_prompt"]["caption"]["char_captions"].pop()
    elif mutation == "wrong_order":
        payload["parameters"]["v4_prompt"]["caption"]["char_captions"].reverse()
    else:
        payload["parameters"]["v4_prompt"]["caption"]["char_captions"][0]["char_caption"] = ""

    with pytest.raises(ProviderMappingError):
        require_frozen_novelai_payload(result.execution_spec, payload)


def test_provider_spec_fixture_is_materialized_by_the_current_mapper() -> None:
    result = mapped(load_plan("prompt-plan-double.json"))
    fixture = json.loads((FIXTURES / "provider-execution-spec.json").read_text(encoding="utf-8"))

    assert result.execution_spec.mapping_version == fixture["mapping_version"]
    assert result.execution_spec.contract_sha256 == fixture["contract_sha256"]
    assert result.execution_spec.model_id == "nai-diffusion-4-5-full"
    assert len(result.execution_spec.character_captions) == len(fixture["character_captions"])
