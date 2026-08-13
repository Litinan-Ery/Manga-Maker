"""Public import surface for layout contracts."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ..adaptation.contracts import StoryboardVersionRefV1
from .contracts import (
    ApprovedChapterLayoutSnapshotV1,
    ApprovedFrameSnapshotV1,
    ApprovedPageLayoutSnapshotV1,
    ApproveLayoutCommandV1,
    CreateLayoutDraftCommandV1,
    DimensionCapabilityCandidateV1,
    DimensionCapabilitySetV1,
    DimensionSelectionFailureV1,
    DimensionSelectionRequestV1,
    DimensionSelectionV1,
    FrameSpec,
    ImportLegacyLayoutCommandV1,
    LayoutApprovalV1,
    LayoutPageRequirementV1,
    LayoutValidationRequestV1,
    LayoutValidationResultV1,
    LayoutVersionSnapshotV1,
    PageLayoutDraft,
    SaveLayoutDraftCommandV1,
)
from .dimension_selector import DimensionSelector
from .validator import LayoutValidator


class LayoutFacade(Protocol):
    def create_draft(
        self,
        command: CreateLayoutDraftCommandV1,
        *,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> LayoutVersionSnapshotV1: ...

    def import_legacy(
        self, command: ImportLegacyLayoutCommandV1
    ) -> LayoutVersionSnapshotV1: ...

    def save_draft(
        self,
        command: SaveLayoutDraftCommandV1,
        *,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> LayoutVersionSnapshotV1: ...

    def approve_layout(
        self,
        command: ApproveLayoutCommandV1,
        *,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> LayoutApprovalV1: ...

    def get_layout(self, layout_id: str, version: int) -> PageLayoutDraft: ...

    def get_version(
        self,
        project_id: UUID,
        page_layout_draft_version_id: UUID,
    ) -> LayoutVersionSnapshotV1: ...

    def current_layout(
        self,
        project_id: UUID,
        page_layout_draft_id: UUID,
    ) -> LayoutVersionSnapshotV1: ...

    def list_layout_versions(
        self,
        project_id: UUID,
        page_layout_draft_id: UUID,
    ) -> tuple[LayoutVersionSnapshotV1, ...]: ...

    def list_current_layouts(
        self,
        project_id: UUID,
        chapter_id: UUID,
    ) -> tuple[LayoutVersionSnapshotV1, ...]: ...

    def get_approval(self, project_id: UUID, approval_id: UUID) -> LayoutApprovalV1: ...

    def approval_for_version(
        self,
        project_id: UUID,
        page_layout_draft_version_id: UUID,
    ) -> LayoutApprovalV1 | None: ...

    def approved_chapter_snapshot(
        self,
        project_id: UUID,
        chapter_id: UUID,
        storyboard: StoryboardVersionRefV1,
        pages: tuple[LayoutPageRequirementV1, ...],
    ) -> ApprovedChapterLayoutSnapshotV1: ...


__all__ = [
    "ApproveLayoutCommandV1",
    "ApprovedChapterLayoutSnapshotV1",
    "ApprovedFrameSnapshotV1",
    "ApprovedPageLayoutSnapshotV1",
    "CreateLayoutDraftCommandV1",
    "DimensionCapabilityCandidateV1",
    "DimensionCapabilitySetV1",
    "DimensionSelectionFailureV1",
    "DimensionSelectionRequestV1",
    "DimensionSelectionV1",
    "DimensionSelector",
    "FrameSpec",
    "ImportLegacyLayoutCommandV1",
    "LayoutApprovalV1",
    "LayoutFacade",
    "LayoutPageRequirementV1",
    "LayoutValidationRequestV1",
    "LayoutValidationResultV1",
    "LayoutValidator",
    "LayoutVersionSnapshotV1",
    "PageLayoutDraft",
    "SaveLayoutDraftCommandV1",
]
