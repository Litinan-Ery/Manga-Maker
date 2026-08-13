from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from .contracts import (
    FrameSpec,
    LayoutValidationFindingV1,
    LayoutValidationRequestV1,
    LayoutValidationResultV1,
    NormalizedRect,
    PageLayoutDraft,
)
from .domain import layout_content_sha256, layout_leaf_panel_ids


@dataclass(frozen=True, slots=True)
class _AbsoluteRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


class LayoutValidator:
    """Pure deterministic validation before approval or paid generation."""

    def validate(self, request: LayoutValidationRequestV1) -> LayoutValidationResultV1:
        layout = request.layout
        findings: list[LayoutValidationFindingV1] = []
        computed_hash = layout_content_sha256(layout)
        if layout.content_sha256 != computed_hash:
            findings.append(
                self._finding(
                    "LAYOUT_HASH_MISMATCH",
                    "content_sha256",
                    "layout content hash does not match its canonical semantic payload",
                )
            )

        by_id = {frame.frame_id: frame for frame in layout.frames}
        children: dict[UUID, list[UUID]] = {frame_id: [] for frame_id in by_id}
        for frame in layout.frames:
            if frame.parent_frame_id is not None:
                children[frame.parent_frame_id].append(frame.frame_id)
        roots = [frame for frame in layout.frames if frame.parent_frame_id is None]
        absolute = self._absolute_rectangles(layout)

        root = roots[0]
        if not self._is_full_canvas(root.rect):
            findings.append(
                self._finding(
                    "ROOT_NOT_FULL_CANVAS",
                    f"frames[{root.frame_id}].rect",
                    "root frame must cover the normalized page canvas",
                    root.frame_id,
                )
            )
        canvas_aspect = layout.canvas.width / layout.canvas.height
        profile_matches = (
            layout.page_profile in {"print_portrait_2_3", "digital_portrait_2_3"}
            and math.isclose(canvas_aspect, 2 / 3, rel_tol=0, abs_tol=0.01)
        ) or (
            layout.page_profile == "vertical_strip"
            and layout.canvas.height > layout.canvas.width
        )
        if not profile_matches:
            findings.append(
                self._finding(
                    "PAGE_PROFILE_CANVAS_MISMATCH",
                    "canvas",
                    "page canvas dimensions do not match the selected page profile",
                )
            )

        leaf_frames = [frame for frame in layout.frames if not children[frame.frame_id]]
        expected_panels = set(request.approved_panel_ids)
        actual_panels = set(layout_leaf_panel_ids(layout))
        for panel_id in sorted(expected_panels - actual_panels, key=str):
            findings.append(
                self._finding(
                    "PANEL_MISSING_FRAME",
                    f"approved_panel_ids[{panel_id}]",
                    "approved storyboard panel has no leaf frame",
                )
            )
        for panel_id in sorted(actual_panels - expected_panels, key=str):
            frame = next(frame for frame in leaf_frames if frame.panel_id == panel_id)
            findings.append(
                self._finding(
                    "FRAME_HAS_UNKNOWN_PANEL",
                    f"frames[{frame.frame_id}].panel_id",
                    "leaf frame references a panel outside the approved storyboard",
                    frame.frame_id,
                )
            )

        for frame in layout.frames:
            rect = absolute[frame.frame_id]
            numeric_values = (
                rect.x,
                rect.y,
                rect.width,
                rect.height,
                frame.aspect_ratio,
                frame.focal_point.x,
                frame.focal_point.y,
            )
            if not all(math.isfinite(value) for value in numeric_values):
                findings.append(
                    self._finding(
                        "NON_FINITE_GEOMETRY",
                        f"frames[{frame.frame_id}]",
                        "frame geometry must contain only finite numbers",
                        frame.frame_id,
                    )
                )
            if (
                rect.x < 0
                or rect.y < 0
                or rect.right > 1 + 1e-9
                or rect.bottom > 1 + 1e-9
            ):
                findings.append(
                    self._finding(
                        "FRAME_OUTSIDE_CANVAS",
                        f"frames[{frame.frame_id}].rect",
                        "frame must remain inside the normalized page canvas",
                        frame.frame_id,
                    )
                )
            rendered_aspect = (
                rect.width * layout.canvas.width / (rect.height * layout.canvas.height)
            )
            if not math.isclose(
                frame.aspect_ratio,
                rendered_aspect,
                rel_tol=0,
                abs_tol=1e-9,
            ):
                findings.append(
                    self._finding(
                        "FRAME_ASPECT_RATIO_MISMATCH",
                        f"frames[{frame.frame_id}].aspect_ratio",
                        "frame aspect ratio does not match its page-space rectangle",
                        frame.frame_id,
                    )
                )
            for position in frame.character_positions:
                if not all(
                    math.isfinite(value)
                    for value in (position.center.x, position.center.y)
                ):
                    findings.append(
                        self._finding(
                            "CHARACTER_POSITION_INVALID",
                            f"frames[{frame.frame_id}].character_positions",
                            "character positions must be finite and inside their frame",
                            frame.frame_id,
                        )
                    )
            for zone in frame.text_safe_zones:
                if not self._rect_is_normalized(zone.rect):
                    findings.append(
                        self._finding(
                            "TEXT_SAFE_ZONE_INVALID",
                            f"frames[{frame.frame_id}].text_safe_zones[{zone.zone_id}]",
                            "text safe zones must be finite and inside their frame",
                            frame.frame_id,
                        )
                    )
            if not self._rect_is_normalized(frame.crop_safe_rect):
                findings.append(
                    self._finding(
                        "CROP_SAFE_RECT_INVALID",
                        f"frames[{frame.frame_id}].crop_safe_rect",
                        "crop-safe rectangle must be finite and inside its frame",
                        frame.frame_id,
                    )
                )

        for frame in leaf_frames:
            rect = absolute[frame.frame_id]
            if rect.width * rect.height < request.rules.minimum_leaf_area_ratio:
                findings.append(
                    self._finding(
                        "LEAF_AREA_TOO_SMALL",
                        f"frames[{frame.frame_id}].rect",
                        "leaf frame area is below the configured page-area minimum",
                        frame.frame_id,
                    )
                )

        for index, first in enumerate(leaf_frames):
            for second in leaf_frames[index + 1 :]:
                first_rect = absolute[first.frame_id]
                second_rect = absolute[second.frame_id]
                overlap = self._intersection_area(first_rect, second_rect)
                if overlap > request.rules.overlap_tolerance_ratio:
                    findings.append(
                        self._finding(
                            "ILLEGAL_FRAME_OVERLAP",
                            f"frames[{first.frame_id},{second.frame_id}]",
                            "leaf frames overlap in page coordinates",
                            first.frame_id,
                            second.frame_id,
                        )
                    )
                    continue
                gutter = self._gutter(first_rect, second_rect)
                if gutter + 1e-12 < request.rules.minimum_gutter_ratio:
                    findings.append(
                        self._finding(
                            "GUTTER_TOO_SMALL",
                            f"frames[{first.frame_id},{second.frame_id}]",
                            "leaf-frame gutter is below the configured minimum",
                            first.frame_id,
                            second.frame_id,
                        )
                    )

        findings.extend(self._reading_order_findings(request, leaf_frames))
        ordered = tuple(
            sorted(
                findings,
                key=lambda finding: (
                    finding.code,
                    finding.path,
                    tuple(map(str, finding.frame_ids)),
                ),
            )
        )
        return LayoutValidationResultV1(
            rule_version=request.rules.rule_version,
            layout_content_sha256=computed_hash,
            valid=not ordered,
            findings=ordered,
        )

    def _reading_order_findings(
        self,
        request: LayoutValidationRequestV1,
        leaf_frames: list[FrameSpec],
    ) -> list[LayoutValidationFindingV1]:
        frames = {frame.frame_id: frame for frame in leaf_frames}
        adjacency: dict[UUID, list[UUID]] = {frame_id: [] for frame_id in frames}
        findings: list[LayoutValidationFindingV1] = []
        for constraint in request.reading_order_constraints:
            before = frames.get(constraint.before_frame_id)
            after = frames.get(constraint.after_frame_id)
            if before is None or after is None:
                findings.append(
                    self._finding(
                        "READING_ORDER_UNKNOWN_FRAME",
                        "reading_order_constraints",
                        "reading order constraints must reference leaf frames",
                        constraint.before_frame_id,
                        constraint.after_frame_id,
                    )
                )
                continue
            adjacency[constraint.before_frame_id].append(constraint.after_frame_id)
            if before.order is not None and after.order is not None and before.order >= after.order:
                findings.append(
                    self._finding(
                        "READING_ORDER_CONFLICT",
                        "reading_order_constraints",
                        "reading order edge conflicts with the frame order values",
                        constraint.before_frame_id,
                        constraint.after_frame_id,
                    )
                )
        cycle = self._first_cycle(adjacency)
        if cycle is not None:
            findings.append(
                self._finding(
                    "READING_ORDER_CYCLE",
                    "reading_order_constraints",
                    "reading order constraints must form an acyclic graph",
                    *cycle,
                )
            )
        return findings

    @staticmethod
    def _absolute_rectangles(layout: PageLayoutDraft) -> dict[UUID, _AbsoluteRect]:
        by_id = {frame.frame_id: frame for frame in layout.frames}
        result: dict[UUID, _AbsoluteRect] = {}

        def resolve(frame_id: UUID) -> _AbsoluteRect:
            existing = result.get(frame_id)
            if existing is not None:
                return existing
            frame = by_id[frame_id]
            if frame.parent_frame_id is None:
                absolute = _AbsoluteRect(
                    frame.rect.x,
                    frame.rect.y,
                    frame.rect.width,
                    frame.rect.height,
                )
            else:
                parent = resolve(frame.parent_frame_id)
                absolute = _AbsoluteRect(
                    parent.x + frame.rect.x * parent.width,
                    parent.y + frame.rect.y * parent.height,
                    frame.rect.width * parent.width,
                    frame.rect.height * parent.height,
                )
            result[frame_id] = absolute
            return absolute

        for current_id in by_id:
            resolve(current_id)
        return result

    @staticmethod
    def _rect_is_normalized(rect: NormalizedRect) -> bool:
        values = (rect.x, rect.y, rect.width, rect.height)
        return (
            all(math.isfinite(value) for value in values)
            and rect.x >= 0
            and rect.y >= 0
            and rect.width > 0
            and rect.height > 0
            and rect.x + rect.width <= 1 + 1e-9
            and rect.y + rect.height <= 1 + 1e-9
        )

    @staticmethod
    def _is_full_canvas(rect: NormalizedRect) -> bool:
        return all(
            math.isclose(actual, expected, abs_tol=1e-9)
            for actual, expected in (
                (rect.x, 0),
                (rect.y, 0),
                (rect.width, 1),
                (rect.height, 1),
            )
        )

    @staticmethod
    def _intersection_area(first: _AbsoluteRect, second: _AbsoluteRect) -> float:
        width = max(0.0, min(first.right, second.right) - max(first.x, second.x))
        height = max(0.0, min(first.bottom, second.bottom) - max(first.y, second.y))
        return width * height

    @staticmethod
    def _gutter(first: _AbsoluteRect, second: _AbsoluteRect) -> float:
        horizontal = max(first.x - second.right, second.x - first.right, 0.0)
        vertical = max(first.y - second.bottom, second.y - first.bottom, 0.0)
        return max(horizontal, vertical)

    @staticmethod
    def _first_cycle(adjacency: dict[UUID, list[UUID]]) -> tuple[UUID, ...] | None:
        visited: set[UUID] = set()
        active: list[UUID] = []
        active_set: set[UUID] = set()

        def visit(node: UUID) -> tuple[UUID, ...] | None:
            visited.add(node)
            active.append(node)
            active_set.add(node)
            for target in sorted(adjacency[node], key=str):
                if target in active_set:
                    start = active.index(target)
                    return (*active[start:], target)
                if target not in visited:
                    cycle = visit(target)
                    if cycle is not None:
                        return cycle
            active.pop()
            active_set.remove(node)
            return None

        for node in sorted(adjacency, key=str):
            if node not in visited:
                cycle = visit(node)
                if cycle is not None:
                    return cycle
        return None

    @staticmethod
    def _finding(
        code: str,
        path: str,
        message: str,
        *frame_ids: UUID,
    ) -> LayoutValidationFindingV1:
        return LayoutValidationFindingV1(
            code=code,
            path=path,
            message=message,
            frame_ids=frame_ids,
        )
