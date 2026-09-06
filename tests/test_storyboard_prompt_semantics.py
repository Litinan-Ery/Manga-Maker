from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.modules.layout.contracts import NormalizedPoint, NormalizedRect
from backend.app.modules.layout.domain import frame_content_sha256
from backend.app.modules.production.adapters.novelai import require_frozen_novelai_payload
from backend.app.modules.prompting.contracts import PromptPlan
from backend.app.modules.prompting.public import compile_prompt_package, prompt_plan_sha256
from backend.app.shared_kernel import canonical_sha256
from tests.modules.production.test_novelai_mapper import mapped
from tests.modules.prompting.test_prompt_compiler import input_from_fixture, load


@pytest.mark.parametrize(
    "field", ["shot_scale", "focal_point", "crop_safe_rect", "text_safe_zones"]
)
def test_uc02_visual_layout_changes_reach_the_provider_at_a_fixed_seed(field: str) -> None:
    source = input_from_fixture("prompt-plan-single.json")
    updates = {
        "shot_scale": "extreme_close_up",
        "focal_point": NormalizedPoint(x=0.12, y=0.78),
        "crop_safe_rect": NormalizedRect(x=0.2, y=0.2, width=0.5, height=0.5),
        "text_safe_zones": [],
    }
    frame = source.frame.model_copy(update={field: updates[field]})
    changed = source.model_copy(
        update={"frame": frame, "frame_sha256": frame_content_sha256(frame)}
    )
    first = mapped(compile_prompt_package(source).prompt_plan).payload
    second = mapped(compile_prompt_package(changed).prompt_plan).payload
    assert first.parameters.seed == second.parameters.seed
    assert first.input != second.input
    if field == "shot_scale":
        assert "extreme close-up" in second.input
        assert "medium shot" not in second.input
    if field == "focal_point":
        assert "12%" in second.input and "78%" in second.input


def test_uc02_metadata_change_does_not_change_visual_semantics() -> None:
    source = input_from_fixture("prompt-plan-single.json")
    frame = source.frame.model_copy(update={"frame_id": uuid4()})
    changed = source.model_copy(
        update={"frame": frame, "frame_sha256": frame_content_sha256(frame)}
    )
    assert (
        mapped(compile_prompt_package(source).prompt_plan).payload
        == mapped(compile_prompt_package(changed).prompt_plan).payload
    )


def test_uc02_natural_language_keeps_commas_and_paragraphs() -> None:
    source = input_from_fixture("prompt-plan-single.json")
    description = "She opens the door, then stops.\nA single chair stands beneath the window."
    draft = source.draft.model_copy(update={"visual_description": description})
    result = compile_prompt_package(source.model_copy(update={"draft": draft}))
    execution = mapped(result.prompt_plan)
    assert description in execution.payload.input
    assert (
        require_frozen_novelai_payload(execution.execution_spec, execution.payload)
        == execution.payload
    )


def test_uc02_legacy_prompt_hash_remains_readable() -> None:
    raw = load("prompt-plan-single.json")
    expected = canonical_sha256(
        {key: value for key, value in raw.items() if key != "content_sha256"}
    )
    assert prompt_plan_sha256(PromptPlan.model_validate(raw)) == expected


def test_uc04_manga_uses_explicit_negative_tags_instead_of_a_screentone_ban() -> None:
    result = mapped(
        compile_prompt_package(input_from_fixture("prompt-plan-single.json")).prompt_plan
    )
    assert result.payload.parameters.tag_hint_uc_preset == 0
    assert result.payload.parameters.ucPreset is None
