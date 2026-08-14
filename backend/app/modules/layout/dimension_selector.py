from __future__ import annotations

import math
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ...shared_kernel import canonical_sha256
from .contracts import (
    DimensionCandidateScoreV1,
    DimensionCapabilityCandidateV1,
    DimensionCapabilitySetV1,
    DimensionSelectionFailureV1,
    DimensionSelectionRequestV1,
    DimensionSelectionV1,
)
from .errors import DimensionCapabilityIntegrityError

DIMENSION_SELECTOR_RULE_VERSION = "dimension-selector-v1"
DimensionSelectionOutcome = DimensionSelectionV1 | DimensionSelectionFailureV1


def canonical_capability_payload(capabilities: DimensionCapabilitySetV1) -> dict[str, Any]:
    payload = capabilities.model_dump(mode="json", exclude={"content_sha256"})
    candidates = payload.get("candidates")
    assert isinstance(candidates, list)
    candidates.sort(key=lambda candidate: str(candidate["candidate_key"]))
    return payload


def dimension_capability_sha256(capabilities: DimensionCapabilitySetV1) -> str:
    return canonical_sha256(canonical_capability_payload(capabilities))


def materialize_dimension_capability_set(
    *,
    capability_snapshot_id: str,
    mapping_version: str,
    candidates: tuple[DimensionCapabilityCandidateV1, ...],
) -> DimensionCapabilitySetV1:
    provisional = DimensionCapabilitySetV1(
        capability_snapshot_id=capability_snapshot_id,
        mapping_version=mapping_version,
        candidates=candidates,
        content_sha256="0" * 64,
    )
    return provisional.model_copy(
        update={"content_sha256": dimension_capability_sha256(provisional)}
    )


def canonical_dimension_selection_payload(
    selection: DimensionSelectionOutcome,
) -> dict[str, Any]:
    excluded = {"content_sha256"}
    if isinstance(selection, DimensionSelectionV1):
        excluded.add("dimension_selection_id")
    return selection.model_dump(mode="json", exclude=excluded)


def dimension_selection_sha256(selection: DimensionSelectionOutcome) -> str:
    return canonical_sha256(canonical_dimension_selection_payload(selection))


class DimensionSelector:
    """Deterministic, provider-neutral size ranking with crop-safe diagnostics."""

    rule_version = DIMENSION_SELECTOR_RULE_VERSION

    def select(
        self,
        request: DimensionSelectionRequestV1,
    ) -> DimensionSelectionOutcome:
        if dimension_capability_sha256(request.capabilities) != request.capabilities.content_sha256:
            raise DimensionCapabilityIntegrityError(
                "dimension capability snapshot hash does not match its candidates"
            )
        scores = tuple(
            sorted(
                (
                    self._score(candidate, request)
                    for candidate in request.capabilities.candidates
                ),
                key=lambda score: (
                    score.aspect_ratio_error,
                    score.crop_safe_risk,
                    score.target_pixel_delta,
                    score.cost_rank,
                    score.candidate_key,
                ),
            )
        )
        selected = next((score for score in scores if score.crop_safe_satisfied), None)
        common = {
            "contract_version": "1.0",
            "page_layout_draft_version_id": str(request.page_layout_draft_version_id),
            "frame_id": str(request.frame.frame_id),
            "capability_snapshot_id": request.capabilities.capability_snapshot_id,
            "capability_snapshot_sha256": request.capabilities.content_sha256,
            "rule_version": self.rule_version,
            "target_aspect_ratio": request.frame.aspect_ratio,
            "ranked_candidates": [score.model_dump(mode="json") for score in scores],
        }
        if selected is None:
            payload = {
                **common,
                "status": "unsatisfied",
                "failure_reason": "no_candidate_preserves_crop_safe_rect",
            }
            return DimensionSelectionFailureV1(
                **payload,
                content_sha256=canonical_sha256(payload),
            )

        payload = {
            **common,
            "status": "selected",
            "selected_candidate_key": selected.candidate_key,
            "selected": selected.dimensions.model_dump(mode="json"),
            "expected_crop_ratio": selected.expected_crop_ratio,
            "selection_reason": (
                "stable rank: aspect ratio error, crop-safe risk, target pixels, "
                "cost rank, candidate key"
            ),
        }
        content_sha256 = canonical_sha256(payload)
        return DimensionSelectionV1(
            **payload,
            dimension_selection_id=uuid5(
                NAMESPACE_URL,
                f"manga-maker:dimension-selection:{content_sha256}",
            ),
            content_sha256=content_sha256,
        )

    @staticmethod
    def _score(
        candidate: DimensionCapabilityCandidateV1,
        request: DimensionSelectionRequestV1,
    ) -> DimensionCandidateScoreV1:
        target_aspect = candidate.dimensions.width / candidate.dimensions.height
        source_aspect = request.frame.aspect_ratio
        aspect_error = abs(target_aspect / source_aspect - 1)
        expected_crop_ratio, crop_safe_risk = DimensionSelector._crop_metrics(
            source_aspect,
            target_aspect,
            request,
        )
        return DimensionCandidateScoreV1(
            candidate_key=candidate.candidate_key,
            dimensions=candidate.dimensions,
            aspect_ratio_error=round(aspect_error, 12),
            crop_safe_risk=round(crop_safe_risk, 12),
            expected_crop_ratio=round(expected_crop_ratio, 12),
            target_pixel_delta=abs(
                candidate.dimensions.width * candidate.dimensions.height
                - request.target_pixels
            ),
            cost_rank=candidate.cost_rank,
            crop_safe_satisfied=crop_safe_risk <= request.max_crop_safe_risk + 1e-12,
        )

    @staticmethod
    def _crop_metrics(
        source_aspect: float,
        target_aspect: float,
        request: DimensionSelectionRequestV1,
    ) -> tuple[float, float]:
        safe = request.frame.crop_safe_rect
        if math.isclose(source_aspect, target_aspect, rel_tol=0, abs_tol=1e-15):
            viewport = (0.0, 0.0, 1.0, 1.0)
            crop_ratio = 0.0
        elif target_aspect < source_aspect:
            retained_width = target_aspect / source_aspect
            left = min(
                max(request.frame.focal_point.x - retained_width / 2, 0.0),
                1.0 - retained_width,
            )
            viewport = (left, 0.0, retained_width, 1.0)
            crop_ratio = 1.0 - retained_width
        else:
            retained_height = source_aspect / target_aspect
            top = min(
                max(request.frame.focal_point.y - retained_height / 2, 0.0),
                1.0 - retained_height,
            )
            viewport = (0.0, top, 1.0, retained_height)
            crop_ratio = 1.0 - retained_height

        viewport_x, viewport_y, viewport_width, viewport_height = viewport
        intersection_width = max(
            0.0,
            min(safe.x + safe.width, viewport_x + viewport_width)
            - max(safe.x, viewport_x),
        )
        intersection_height = max(
            0.0,
            min(safe.y + safe.height, viewport_y + viewport_height)
            - max(safe.y, viewport_y),
        )
        safe_area = safe.width * safe.height
        retained_safe_area = intersection_width * intersection_height
        risk = max(0.0, min(1.0, 1.0 - retained_safe_area / safe_area))
        return crop_ratio, risk
