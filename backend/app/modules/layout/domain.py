from __future__ import annotations

from typing import Any
from uuid import UUID

from ...shared_kernel import canonical_sha256
from .contracts import FrameSpec, PageLayoutDraft
from .errors import LayoutPanelCoverageError


def layout_leaf_panel_ids(layout: PageLayoutDraft) -> tuple[UUID, ...]:
    parent_ids = {
        frame.parent_frame_id
        for frame in layout.frames
        if frame.parent_frame_id is not None
    }
    leaves = [frame for frame in layout.frames if frame.frame_id not in parent_ids]
    ordered = sorted(leaves, key=lambda frame: frame.order or 0)
    return tuple(frame.panel_id for frame in ordered if frame.panel_id is not None)


def require_exact_panel_coverage(
    layout: PageLayoutDraft,
    approved_panel_ids: tuple[UUID, ...],
) -> None:
    layout_panels = set(layout_leaf_panel_ids(layout))
    approved_panels = set(approved_panel_ids)
    missing = tuple(sorted(approved_panels - layout_panels, key=str))
    unexpected = tuple(sorted(layout_panels - approved_panels, key=str))
    if missing or unexpected or len(layout_panels) != len(approved_panel_ids):
        raise LayoutPanelCoverageError(missing=missing, unexpected=unexpected)


def canonical_layout_payload(layout: PageLayoutDraft) -> dict[str, Any]:
    """Return semantic layout content, excluding mutable approval/version metadata."""

    payload = layout.model_dump(
        mode="json",
        exclude={"version", "content_sha256", "approved_content_sha256"},
    )
    frames = payload.get("frames")
    assert isinstance(frames, list)
    for frame in frames:
        assert isinstance(frame, dict)
        positions = frame.get("character_positions")
        zones = frame.get("text_safe_zones")
        if isinstance(positions, list):
            positions.sort(key=lambda item: str(item["character_id"]))
        if isinstance(zones, list):
            zones.sort(key=lambda item: str(item["zone_id"]))
    frames.sort(key=lambda frame: str(frame["frame_id"]))
    return payload


def layout_content_sha256(layout: PageLayoutDraft) -> str:
    return canonical_sha256(canonical_layout_payload(layout))


def frame_content_sha256(frame: FrameSpec) -> str:
    """Hash one semantic frame independently for minimum-scope invalidation."""

    return canonical_sha256(frame.model_dump(mode="json"))


def materialize_layout_version(
    draft: PageLayoutDraft,
    *,
    page_layout_draft_id: UUID,
    version: int,
) -> PageLayoutDraft:
    untrusted = draft.model_dump(mode="json")
    untrusted.update(
        {
            "page_layout_draft_id": str(page_layout_draft_id),
            "version": version,
            "content_sha256": "0" * 64,
            "approved_content_sha256": None,
        }
    )
    provisional = PageLayoutDraft.model_validate(untrusted)
    untrusted["content_sha256"] = layout_content_sha256(provisional)
    return PageLayoutDraft.model_validate(untrusted)
