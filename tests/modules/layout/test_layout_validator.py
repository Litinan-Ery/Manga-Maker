from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.modules.layout.contracts import (
    LayoutValidationRequestV1,
    NormalizedPoint,
    NormalizedRect,
    PageLayoutDraft,
    ReadingOrderConstraintV1,
)
from backend.app.modules.layout.domain import (
    layout_leaf_panel_ids,
    materialize_layout_version,
)
from backend.app.modules.layout.validator import LayoutValidator

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "fixtures" / "v0.3"


def materialized_fixture(name: str) -> PageLayoutDraft:
    raw = PageLayoutDraft.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))
    return materialize_layout_version(
        raw,
        page_layout_draft_id=raw.page_layout_draft_id,
        version=raw.version,
    )


@pytest.mark.parametrize("fixture_name", ("page-layout-draft.json", "page-layout-six-panel.json"))
def test_standard_and_six_panel_golden_layouts_are_valid_and_deterministic(
    fixture_name: str,
) -> None:
    layout = materialized_fixture(fixture_name)
    request = LayoutValidationRequestV1(
        layout=layout,
        approved_panel_ids=layout_leaf_panel_ids(layout),
    )

    first = LayoutValidator().validate(request)
    second = LayoutValidator().validate(request)

    assert first == second
    assert first.valid
    assert first.findings == ()
    assert first.layout_content_sha256 == layout.content_sha256
    assert first.external_requests_started == 0


def test_validator_reports_panel_area_overlap_gutter_and_root_errors_by_path() -> None:
    layout = materialized_fixture("page-layout-six-panel.json")
    leaves = [frame for frame in layout.frames if frame.panel_id is not None]
    first, second = leaves[:2]
    changed_frames = []
    for frame in layout.frames:
        if frame.frame_id == first.frame_id:
            changed_frames.append(
                frame.model_copy(
                    update={"rect": NormalizedRect(x=0.02, y=0.02, width=0.02, height=0.02)}
                )
            )
        elif frame.frame_id == second.frame_id:
            changed_frames.append(frame.model_copy(update={"rect": first.rect}))
        elif frame.parent_frame_id is None:
            changed_frames.append(
                frame.model_copy(
                    update={"rect": NormalizedRect(x=0.01, y=0.01, width=0.98, height=0.98)}
                )
            )
        else:
            changed_frames.append(frame)
    changed = materialize_layout_version(
        layout.model_copy(update={"frames": changed_frames}),
        page_layout_draft_id=layout.page_layout_draft_id,
        version=1,
    )
    result = LayoutValidator().validate(
        LayoutValidationRequestV1(
            layout=changed,
            approved_panel_ids=layout_leaf_panel_ids(changed),
        )
    )

    codes = {finding.code for finding in result.findings}
    assert not result.valid
    assert "ROOT_NOT_FULL_CANVAS" in codes
    assert "LEAF_AREA_TOO_SMALL" in codes
    assert "ILLEGAL_FRAME_OVERLAP" in codes
    assert all(finding.path for finding in result.findings)

    gutter_frames = [
        frame.model_copy(
            update={"rect": NormalizedRect(x=0.495, y=0.02, width=0.485, height=0.306)}
        )
        if frame.frame_id == second.frame_id
        else frame
        for frame in layout.frames
    ]
    gutter_layout = materialize_layout_version(
        layout.model_copy(update={"frames": gutter_frames}),
        page_layout_draft_id=layout.page_layout_draft_id,
        version=1,
    )
    gutter_result = LayoutValidator().validate(
        LayoutValidationRequestV1(
            layout=gutter_layout,
            approved_panel_ids=layout_leaf_panel_ids(gutter_layout),
        )
    )
    assert "GUTTER_TOO_SMALL" in {finding.code for finding in gutter_result.findings}


def test_validator_reports_exact_panel_coverage_and_reading_order_cycles() -> None:
    layout = materialized_fixture("page-layout-draft.json")
    leaves = [frame for frame in layout.frames if frame.panel_id is not None]
    request = LayoutValidationRequestV1(
        layout=layout,
        approved_panel_ids=(leaves[0].panel_id,),
        reading_order_constraints=(
            ReadingOrderConstraintV1(
                before_frame_id=leaves[0].frame_id,
                after_frame_id=leaves[1].frame_id,
            ),
            ReadingOrderConstraintV1(
                before_frame_id=leaves[1].frame_id,
                after_frame_id=leaves[0].frame_id,
            ),
        ),
    )

    result = LayoutValidator().validate(request)
    codes = {finding.code for finding in result.findings}

    assert not result.valid
    assert codes >= {
        "FRAME_HAS_UNKNOWN_PANEL",
        "READING_ORDER_CONFLICT",
        "READING_ORDER_CYCLE",
    }


def test_contracts_reject_non_finite_and_out_of_frame_geometry_before_validation() -> None:
    with pytest.raises(ValidationError):
        NormalizedPoint(x=float("nan"), y=0.5)
    with pytest.raises(ValidationError, match="remain within"):
        NormalizedRect(x=0.9, y=0, width=0.2, height=1)
