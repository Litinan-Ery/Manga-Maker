from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from backend.app.modules.layout.contracts import (
    DimensionCapabilityCandidateV1,
    DimensionCapabilitySetV1,
    DimensionSelectionFailureV1,
    DimensionSelectionRequestV1,
    DimensionSelectionV1,
    FrameSpec,
    PixelDimensions,
)
from backend.app.modules.layout.dimension_selector import (
    DimensionSelector,
    dimension_capability_sha256,
    dimension_selection_sha256,
    materialize_dimension_capability_set,
)
from backend.app.modules.layout.errors import DimensionCapabilityIntegrityError

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "fixtures" / "v0.3"
LAYOUT_VERSION_ID = UUID("01900000-0000-7000-8000-000000009999")


def capabilities() -> DimensionCapabilitySetV1:
    return DimensionCapabilitySetV1.model_validate_json(
        (FIXTURES / "dimension-capabilities.json").read_text(encoding="utf-8")
    )


def cases() -> list[dict[str, Any]]:
    payload = json.loads(
        (FIXTURES / "dimension-selection-cases.json").read_text(encoding="utf-8")
    )
    return list(payload["cases"])


@pytest.mark.parametrize("case", cases(), ids=lambda case: str(case["name"]))
def test_golden_dimension_cases_have_stable_selected_or_unsatisfied_results(
    case: dict[str, Any],
) -> None:
    request = DimensionSelectionRequestV1(
        page_layout_draft_version_id=LAYOUT_VERSION_ID,
        frame=FrameSpec.model_validate(case["frame"]),
        capabilities=capabilities(),
        target_pixels=int(case["target_pixels"]),
        max_crop_safe_risk=float(case["max_crop_safe_risk"]),
    )

    first = DimensionSelector().select(request)
    second = DimensionSelector().select(request)

    assert first == second
    assert first.status == case["expected_status"]
    assert first.content_sha256 == dimension_selection_sha256(first)
    if isinstance(first, DimensionSelectionV1):
        assert first.selected_candidate_key == case["expected_candidate_key"]
        assert first.ranked_candidates[0].candidate_key == case["expected_candidate_key"]
        assert first.ranked_candidates[0].crop_safe_satisfied
    else:
        assert isinstance(first, DimensionSelectionFailureV1)
        assert case["expected_candidate_key"] is None
        assert first.failure_reason == "no_candidate_preserves_crop_safe_rect"
        assert not any(score.crop_safe_satisfied for score in first.ranked_candidates)


def test_capability_hash_is_canonical_and_candidate_order_does_not_change_selection() -> None:
    source = capabilities()
    assert source.content_sha256 == dimension_capability_sha256(source)
    reordered = source.model_copy(update={"candidates": tuple(reversed(source.candidates))})
    assert dimension_capability_sha256(reordered) == source.content_sha256
    case = cases()[0]
    base = DimensionSelectionRequestV1(
        page_layout_draft_version_id=LAYOUT_VERSION_ID,
        frame=FrameSpec.model_validate(case["frame"]),
        capabilities=source,
        target_pixels=int(case["target_pixels"]),
    )

    assert DimensionSelector().select(base) == DimensionSelector().select(
        base.model_copy(update={"capabilities": reordered})
    )


def test_ties_break_by_cost_then_fixed_candidate_key() -> None:
    tied = materialize_dimension_capability_set(
        capability_snapshot_id="tie-fixture",
        mapping_version="provider-neutral-dimensions-v1",
        candidates=(
            DimensionCapabilityCandidateV1(
                candidate_key="economy-z",
                dimensions=PixelDimensions(width=1024, height=1024),
                pixel_limit=1_048_576,
                cost_rank=1,
            ),
            DimensionCapabilityCandidateV1(
                candidate_key="premium-a",
                dimensions=PixelDimensions(width=1024, height=1024),
                pixel_limit=1_048_576,
                cost_rank=2,
            ),
            DimensionCapabilityCandidateV1(
                candidate_key="economy-a",
                dimensions=PixelDimensions(width=1024, height=1024),
                pixel_limit=1_048_576,
                cost_rank=1,
            ),
        ),
    )
    square_case = next(case for case in cases() if case["name"] == "near_square")
    result = DimensionSelector().select(
        DimensionSelectionRequestV1(
            page_layout_draft_version_id=LAYOUT_VERSION_ID,
            frame=FrameSpec.model_validate(square_case["frame"]),
            capabilities=tied,
            target_pixels=1_048_576,
        )
    )

    assert isinstance(result, DimensionSelectionV1)
    assert [score.candidate_key for score in result.ranked_candidates] == [
        "economy-a",
        "economy-z",
        "premium-a",
    ]
    assert result.selected_candidate_key == "economy-a"


def test_changed_capabilities_require_a_new_hash_and_affect_the_selection_contract() -> None:
    source = capabilities()
    changed_candidate = source.candidates[0].model_copy(update={"cost_rank": 99})
    changed_without_hash = source.model_copy(
        update={"candidates": (changed_candidate, *source.candidates[1:])}
    )
    case = cases()[0]
    request = DimensionSelectionRequestV1(
        page_layout_draft_version_id=LAYOUT_VERSION_ID,
        frame=FrameSpec.model_validate(case["frame"]),
        capabilities=changed_without_hash,
        target_pixels=int(case["target_pixels"]),
    )
    with pytest.raises(DimensionCapabilityIntegrityError, match="hash"):
        DimensionSelector().select(request)

    changed = materialize_dimension_capability_set(
        capability_snapshot_id=source.capability_snapshot_id,
        mapping_version="provider-neutral-dimensions-v2",
        candidates=changed_without_hash.candidates,
    )
    assert changed.content_sha256 != source.content_sha256
    changed_result = DimensionSelector().select(
        request.model_copy(update={"capabilities": changed})
    )
    original_result = DimensionSelector().select(
        request.model_copy(update={"capabilities": source})
    )
    assert changed_result.content_sha256 != original_result.content_sha256


def test_selector_source_has_no_provider_http_or_credential_dependencies() -> None:
    source = inspect.getsource(__import__(
        "backend.app.modules.layout.dimension_selector",
        fromlist=["DimensionSelector"],
    )).lower()
    for forbidden in ("novelai", "httpx", "requests", "credential", "vault"):
        assert forbidden not in source
