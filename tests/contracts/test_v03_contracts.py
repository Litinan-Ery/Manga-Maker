from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.contracts.v03 import SCHEMA_MODELS, rendered_schemas, schema_directory
from backend.app.modules.layout.contracts import PageLayoutDraft
from backend.app.modules.production.contracts import ProviderExecutionSpec
from backend.app.modules.prompting.contracts import PromptPackage, PromptPlan
from backend.app.modules.review.contracts import ReviewSnapshot
from backend.app.modules.text_execution.contracts import TextStageRun
from backend.app.novelai.contracts import MAPPING_VERSION
from backend.app.shared_kernel.canonical_json import canonical_json_bytes, canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "contracts" / "fixtures" / "v0.3"


def load_fixture(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_page_layout_fixture_contains_hierarchy_and_generation_constraints() -> None:
    layout = PageLayoutDraft.model_validate(load_fixture("page-layout-draft.json"))

    assert layout.page_profile == "print_portrait_2_3"
    assert len(layout.frames) == 3
    root = next(frame for frame in layout.frames if frame.parent_frame_id is None)
    leaves = [frame for frame in layout.frames if frame.parent_frame_id == root.frame_id]
    assert [frame.order for frame in leaves] == [1, 2]
    assert {frame.shot_scale for frame in leaves} == {"medium", "wide"}
    assert all(frame.character_positions for frame in leaves)
    assert all(frame.text_safe_zones for frame in leaves)
    assert all(frame.crop_safe_rect.width < 1 for frame in leaves)


@pytest.mark.parametrize(
    ("fixture_name", "expected_characters"),
    (
        ("prompt-plan-single.json", 1),
        ("prompt-plan-double.json", 2),
        ("prompt-plan-triple.json", 3),
    ),
)
def test_prompt_plan_fixtures_keep_character_blocks_separate(
    fixture_name: str,
    expected_characters: int,
) -> None:
    plan = PromptPlan.model_validate(load_fixture(fixture_name))

    assert len(plan.characters) == expected_characters
    assert [character.order for character in plan.characters] == list(range(expected_characters))
    assert all(character.action for character in plan.characters)
    assert all(character.fixed_tags for character in plan.characters)
    assert all(character.negative_tags for character in plan.characters)
    assert len({character.center for character in plan.characters}) == expected_characters
    assert (plan.base.relationship_action is not None) == (expected_characters > 1)


def test_provider_fixture_keeps_positive_negative_and_position_per_character() -> None:
    spec = ProviderExecutionSpec.model_validate(load_fixture("provider-execution-spec.json"))

    assert spec.provider == "novelai"
    assert spec.mapping_version == MAPPING_VERSION
    assert [caption.order for caption in spec.character_captions] == [0, 1]
    assert all(caption.positive_tags for caption in spec.character_captions)
    assert all(caption.negative_tags for caption in spec.character_captions)
    assert spec.character_captions[0].center != spec.character_captions[1].center


def test_prompt_package_binds_model_source_plan_and_approval_hashes() -> None:
    package = PromptPackage.model_validate(load_fixture("prompt-package-double.json"))

    assert package.panel_id == package.prompt_plan.panel_id
    assert package.prompt_plan_sha256 == package.prompt_plan.content_sha256
    assert package.text_model_source.prompt_template_version == "panel-plan-v2"
    assert package.approved_content_sha256 == package.content_sha256


def test_review_fixture_covers_candidates_findings_decisions_waiver_and_staleness() -> None:
    snapshot = ReviewSnapshot.model_validate(load_fixture("review-snapshot.json"))

    assert len(snapshot.candidates) == 2
    assert {finding.severity for finding in snapshot.quality_findings} == {
        "blocker",
        "warning",
        "info",
    }
    assert {finding.status for finding in snapshot.quality_findings} == {
        "open",
        "resolved",
        "waived",
    }
    waived = next(finding for finding in snapshot.quality_findings if finding.status == "waived")
    assert waived.waiver is not None
    assert {decision.decision for decision in snapshot.decision_history} == {
        "accepted",
        "rejected",
        "needs_fix",
    }
    assert snapshot.page_approval is not None
    assert snapshot.page_approval.state == "stale"
    assert snapshot.page_approval.stale_reason


def test_text_stage_fixture_declares_budget_truncation_and_checkpoint() -> None:
    run = TextStageRun.model_validate(load_fixture("text-stage-run.json"))

    assert run.state == "completed"
    assert run.token_budget.status == "fits"
    assert run.checkpoint_id is not None
    assert run.truncation_report.hard_constraints_preserved
    assert {item.policy for item in run.truncation_report.items} == {"summarized", "preserved"}


def test_python_canonical_bytes_and_hash_match_the_cross_language_fixture() -> None:
    fixture = load_fixture("canonical-hash.json")

    assert canonical_json_bytes(fixture["value"]).decode("utf-8") == fixture["canonical_json"]
    assert canonical_sha256(fixture["value"]) == fixture["sha256"]


@pytest.mark.parametrize(
    ("model", "fixture_name", "forbidden_field"),
    (
        (PageLayoutDraft, "page-layout-draft.json", "workspace_path"),
        (PromptPlan, "prompt-plan-double.json", "api_token"),
        (ProviderExecutionSpec, "provider-execution-spec.json", "authorization_header"),
        (ReviewSnapshot, "review-snapshot.json", "source_absolute_path"),
        (TextStageRun, "text-stage-run.json", "api_key"),
    ),
)
def test_sensitive_fields_and_absolute_paths_are_rejected(
    model: type[Any],
    fixture_name: str,
    forbidden_field: str,
) -> None:
    payload = deepcopy(load_fixture(fixture_name))
    payload[forbidden_field] = "/Users/example/private" if "path" in forbidden_field else "secret"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "fixture_name"),
    (
        (PageLayoutDraft, "page-layout-draft.json"),
        (PromptPlan, "prompt-plan-double.json"),
        (ProviderExecutionSpec, "provider-execution-spec.json"),
        (ReviewSnapshot, "review-snapshot.json"),
        (TextStageRun, "text-stage-run.json"),
    ),
)
def test_unknown_schema_versions_fail_closed(model: type[Any], fixture_name: str) -> None:
    payload = deepcopy(load_fixture(fixture_name))
    payload["schema_version"] = "999.0"

    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_checked_in_json_schemas_are_current_and_closed() -> None:
    destination = schema_directory(ROOT)
    expected = rendered_schemas()

    assert {path.name for path in destination.glob("*.schema.json")} == set(expected)
    for filename, content in expected.items():
        assert (destination / filename).read_bytes() == content
        schema = json.loads(content)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(filename)
        assert schema["additionalProperties"] is False
        assert "schema_version" in schema["properties"]
        assert schema["properties"]["schema_version"]["const"] in {"1.0", "2.0"}

    assert set(SCHEMA_MODELS) == {filename.removesuffix(".schema.json") for filename in expected}
