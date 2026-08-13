from __future__ import annotations

from .contracts import (
    DimensionSelectionRequestV1,
    LayoutApprovalValidationV1,
    LayoutValidationRequestV1,
    ValidateLayoutCommandV1,
)
from .dimension_selector import DimensionSelector
from .errors import LayoutApprovalConflictError, LayoutStoryboardBindingError
from .public import LayoutFacade
from .validator import LayoutValidator


class LayoutApplicationService:
    """Coordinates local validation without starting provider requests."""

    def __init__(
        self,
        store: LayoutFacade,
        validator: LayoutValidator | None = None,
        dimension_selector: DimensionSelector | None = None,
    ) -> None:
        self._store = store
        self._validator = validator or LayoutValidator()
        self._dimension_selector = dimension_selector or DimensionSelector()

    def validate_for_approval(
        self,
        command: ValidateLayoutCommandV1,
    ) -> LayoutApprovalValidationV1:
        snapshot = self._store.get_version(
            command.project_id,
            command.page_layout_draft_version_id,
        )
        if snapshot.revision != command.expected_revision:
            raise LayoutApprovalConflictError(
                f"layout revision mismatch; current version revision is {snapshot.revision}"
            )
        if snapshot.layout.content_sha256 != command.layout_content_sha256:
            raise LayoutApprovalConflictError("layout content hash does not match")
        if snapshot.storyboard != command.storyboard:
            raise LayoutStoryboardBindingError("storyboard approval binding does not match")
        layout_result = self._validator.validate(
            LayoutValidationRequestV1(
                layout=snapshot.layout,
                approved_panel_ids=snapshot.approved_panel_ids,
            )
        )
        parent_ids = {
            frame.parent_frame_id
            for frame in snapshot.layout.frames
            if frame.parent_frame_id is not None
        }
        leaf_frames = sorted(
            (
                frame
                for frame in snapshot.layout.frames
                if frame.frame_id not in parent_ids
            ),
            key=lambda frame: frame.order or 0,
        )
        outcomes = tuple(
            self._dimension_selector.select(
                DimensionSelectionRequestV1(
                    page_layout_draft_version_id=snapshot.page_layout_draft_version_id,
                    frame=frame,
                    capabilities=command.dimension_capabilities,
                    target_pixels=command.target_pixels,
                    max_crop_safe_risk=command.max_crop_safe_risk,
                )
            )
            for frame in leaf_frames
        )
        failure_paths = tuple(
            [finding.path for finding in layout_result.findings]
            + [
                f"frames[{outcome.frame_id}].dimension_selection"
                for outcome in outcomes
                if outcome.status == "unsatisfied"
            ]
        )
        return LayoutApprovalValidationV1(
            page_layout_draft_version_id=snapshot.page_layout_draft_version_id,
            layout=layout_result,
            dimension_outcomes=outcomes,
            valid=not failure_paths,
            failure_paths=failure_paths,
        )
