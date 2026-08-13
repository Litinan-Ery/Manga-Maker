from __future__ import annotations

import re
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..bootstrap.dependencies import (
    get_adaptation_facade,
    get_layout_facade,
    get_lineage_facade,
    require_local_session,
)
from ..modules.adaptation.public import AdaptationFacade
from ..modules.layout.contracts import (
    ApproveLayoutCommandV1,
    CreateLayoutDraftCommandV1,
    DimensionCapabilitySetV1,
    DimensionSelectionV1,
    LayoutApprovalV1,
    LayoutApprovalValidationV1,
    LayoutVersionSnapshotV1,
    PageLayoutDraft,
    SaveLayoutDraftCommandV1,
    ValidateLayoutCommandV1,
)
from ..modules.layout.domain import frame_content_sha256
from ..modules.layout.errors import (
    DimensionCapabilityIntegrityError,
    LayoutApprovalConflictError,
    LayoutIdempotencyConflictError,
    LayoutIdentityConflictError,
    LayoutNotFoundError,
    LayoutPanelCoverageError,
    LayoutRevisionConflictError,
    LayoutSnapshotIntegrityError,
    LayoutStoryboardBindingError,
)
from ..modules.layout.public import LayoutFacade
from ..modules.layout.service import LayoutApplicationService
from ..modules.lineage.contracts import (
    ArtifactVersionRefV1,
    InvalidateArtifactCommandV1,
    RegisterArtifactCommandV1,
    RegisterDependencyCommandV1,
)
from ..modules.lineage.errors import ArtifactNotFoundError
from ..modules.lineage.public import LineageFacade
from ..shared_kernel import canonical_sha256

router = APIRouter(prefix="/api/v1/projects/{project_id}/layouts", tags=["layouts"])
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class LayoutApiContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateLayoutDraftRequest(LayoutApiContract):
    chapter_id: UUID
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    draft: PageLayoutDraft


class SaveLayoutDraftRequest(LayoutApiContract):
    expected_revision: int = Field(ge=1)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    draft: PageLayoutDraft


class ValidateLayoutRequest(LayoutApiContract):
    expected_revision: int = Field(ge=1)
    layout_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    dimension_capabilities: DimensionCapabilitySetV1
    target_pixels: int = Field(ge=4_096, le=268_435_456)
    max_crop_safe_risk: float = Field(default=0, ge=0, le=1)


class ApproveLayoutRequest(LayoutApiContract):
    expected_revision: int = Field(ge=1)
    layout_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    dimension_capabilities: DimensionCapabilitySetV1
    target_pixels: int = Field(ge=4_096, le=268_435_456)
    max_crop_safe_risk: float = Field(default=0, ge=0, le=1)
    dimension_selections: tuple[DimensionSelectionV1, ...]


def _idempotency_key(value: str) -> str:
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be an opaque value of at most 128 characters",
        )
    return value


def _raise_layout_error(error: Exception) -> None:
    if isinstance(error, LayoutRevisionConflictError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LAYOUT_REVISION_CONFLICT",
                "current_revision": error.current_revision,
            },
        ) from error
    if isinstance(error, LayoutPanelCoverageError):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LAYOUT_PANEL_COVERAGE_INVALID",
                "missing_panel_ids": [str(value) for value in error.missing],
                "unexpected_panel_ids": [str(value) for value in error.unexpected],
            },
        ) from error
    if isinstance(error, LayoutNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, LayoutSnapshotIntegrityError):
        raise HTTPException(
            status_code=409,
            detail="layout snapshot integrity check failed",
        ) from error
    if isinstance(error, ValueError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    if isinstance(
        error,
        (
            LayoutApprovalConflictError,
            LayoutIdempotencyConflictError,
            LayoutIdentityConflictError,
            LayoutStoryboardBindingError,
            DimensionCapabilityIntegrityError,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise error


@router.post(
    "/drafts",
    response_model=LayoutVersionSnapshotV1,
    status_code=status.HTTP_201_CREATED,
)
def create_layout_draft(
    project_id: UUID,
    body: CreateLayoutDraftRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
    adaptation: Annotated[AdaptationFacade, Depends(get_adaptation_facade)],
    lineage: Annotated[LineageFacade, Depends(get_lineage_facade)],
    _authorized: Annotated[None, Depends(require_local_session)],
) -> LayoutVersionSnapshotV1:
    key = _idempotency_key(idempotency_key)
    try:
        page = adaptation.storyboard_page(
            str(project_id),
            body.storyboard_version_id,
            str(body.draft.page_id),
        )
        if page.chapter_id != body.chapter_id:
            raise LayoutStoryboardBindingError("storyboard page belongs to another chapter")
        command = CreateLayoutDraftCommandV1(
            project_id=project_id,
            chapter_id=body.chapter_id,
            storyboard=page.storyboard,
            approved_panel_ids=page.panel_ids,
            draft=body.draft,
        )
        created = layout.create_draft(
            command,
            idempotency_key=key,
            request_sha256=canonical_sha256(command),
        )
        _register_layout_lineage(lineage, created)
        return created
    except Exception as error:
        _raise_layout_error(error)
        raise AssertionError("unreachable") from error


@router.get("/drafts/{page_layout_draft_id}", response_model=LayoutVersionSnapshotV1)
def get_current_layout(
    project_id: UUID,
    page_layout_draft_id: UUID,
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
) -> LayoutVersionSnapshotV1:
    try:
        return layout.current_layout(project_id, page_layout_draft_id)
    except Exception as error:
        _raise_layout_error(error)
        raise AssertionError("unreachable") from error


@router.get("", response_model=tuple[LayoutVersionSnapshotV1, ...])
def list_current_layouts(
    project_id: UUID,
    chapter_id: Annotated[UUID, Query()],
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
) -> tuple[LayoutVersionSnapshotV1, ...]:
    return layout.list_current_layouts(project_id, chapter_id)


@router.get(
    "/drafts/{page_layout_draft_id}/versions",
    response_model=tuple[LayoutVersionSnapshotV1, ...],
)
def list_layout_versions(
    project_id: UUID,
    page_layout_draft_id: UUID,
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
) -> tuple[LayoutVersionSnapshotV1, ...]:
    return layout.list_layout_versions(project_id, page_layout_draft_id)


@router.post(
    "/{page_layout_draft_version_id}/revisions",
    response_model=LayoutVersionSnapshotV1,
    status_code=status.HTTP_201_CREATED,
)
def save_layout_revision(
    project_id: UUID,
    page_layout_draft_version_id: UUID,
    body: SaveLayoutDraftRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
    adaptation: Annotated[AdaptationFacade, Depends(get_adaptation_facade)],
    lineage: Annotated[LineageFacade, Depends(get_lineage_facade)],
    _authorized: Annotated[None, Depends(require_local_session)],
) -> LayoutVersionSnapshotV1:
    key = _idempotency_key(idempotency_key)
    try:
        parent = layout.get_version(project_id, page_layout_draft_version_id)
        current = layout.current_layout(project_id, parent.layout.page_layout_draft_id)
        if current.page_layout_draft_version_id != page_layout_draft_version_id:
            raise LayoutRevisionConflictError(current.revision)
        page = adaptation.storyboard_page(
            str(project_id),
            body.storyboard_version_id,
            str(parent.layout.page_id),
        )
        command = SaveLayoutDraftCommandV1(
            project_id=project_id,
            page_layout_draft_id=parent.layout.page_layout_draft_id,
            expected_revision=body.expected_revision,
            storyboard=page.storyboard,
            approved_panel_ids=page.panel_ids,
            draft=body.draft,
        )
        saved = layout.save_draft(
            command,
            idempotency_key=key,
            request_sha256=canonical_sha256(command),
        )
        _register_layout_lineage(lineage, saved)
        if saved.page_layout_draft_version_id != parent.page_layout_draft_version_id:
            _invalidate_changed_frames(lineage, parent, saved)
        return saved
    except Exception as error:
        _raise_layout_error(error)
        raise AssertionError("unreachable") from error


@router.post(
    "/{page_layout_draft_version_id}/validate",
    response_model=LayoutApprovalValidationV1,
)
def validate_layout(
    project_id: UUID,
    page_layout_draft_version_id: UUID,
    body: ValidateLayoutRequest,
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
    adaptation: Annotated[AdaptationFacade, Depends(get_adaptation_facade)],
    _authorized: Annotated[None, Depends(require_local_session)],
) -> LayoutApprovalValidationV1:
    try:
        snapshot = layout.get_version(project_id, page_layout_draft_version_id)
        page = adaptation.storyboard_page(
            str(project_id),
            body.storyboard_version_id,
            str(snapshot.layout.page_id),
        )
        return LayoutApplicationService(layout).validate_for_approval(
            ValidateLayoutCommandV1(
                project_id=project_id,
                page_layout_draft_version_id=page_layout_draft_version_id,
                expected_revision=body.expected_revision,
                layout_content_sha256=body.layout_content_sha256,
                storyboard=page.storyboard,
                dimension_capabilities=body.dimension_capabilities,
                target_pixels=body.target_pixels,
                max_crop_safe_risk=body.max_crop_safe_risk,
            )
        )
    except Exception as error:
        _raise_layout_error(error)
        raise AssertionError("unreachable") from error


@router.post(
    "/{page_layout_draft_version_id}/approve",
    response_model=LayoutApprovalV1,
)
def approve_layout(
    project_id: UUID,
    page_layout_draft_version_id: UUID,
    body: ApproveLayoutRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
    adaptation: Annotated[AdaptationFacade, Depends(get_adaptation_facade)],
    _authorized: Annotated[None, Depends(require_local_session)],
) -> LayoutApprovalV1:
    key = _idempotency_key(idempotency_key)
    try:
        snapshot = layout.get_version(project_id, page_layout_draft_version_id)
        page = adaptation.storyboard_page(
            str(project_id),
            body.storyboard_version_id,
            str(snapshot.layout.page_id),
        )
        leaf_ids = {
            frame.frame_id for frame in snapshot.layout.frames if frame.panel_id is not None
        }
        selection_ids = {selection.frame_id for selection in body.dimension_selections}
        if selection_ids != leaf_ids:
            missing = sorted(leaf_ids - selection_ids, key=str)
            unexpected = sorted(selection_ids - leaf_ids, key=str)
            raise LayoutApprovalConflictError(
                "dimension selection frame coverage mismatch: "
                f"missing={','.join(map(str, missing))}; "
                f"unexpected={','.join(map(str, unexpected))}"
            )
        validation = LayoutApplicationService(layout).validate_for_approval(
            ValidateLayoutCommandV1(
                project_id=project_id,
                page_layout_draft_version_id=page_layout_draft_version_id,
                expected_revision=body.expected_revision,
                layout_content_sha256=body.layout_content_sha256,
                storyboard=page.storyboard,
                dimension_capabilities=body.dimension_capabilities,
                target_pixels=body.target_pixels,
                max_crop_safe_risk=body.max_crop_safe_risk,
            )
        )
        if not validation.valid:
            raise LayoutApprovalConflictError(
                "layout validation failed at " + ",".join(validation.failure_paths)
            )
        expected_selections = tuple(
            outcome
            for outcome in validation.dimension_outcomes
            if isinstance(outcome, DimensionSelectionV1)
        )
        expected_by_frame = {
            selection.frame_id: selection.content_sha256
            for selection in expected_selections
        }
        submitted_by_frame = {
            selection.frame_id: selection.content_sha256
            for selection in body.dimension_selections
        }
        if submitted_by_frame != expected_by_frame:
            raise LayoutApprovalConflictError(
                "dimension selections do not match synchronous validation"
            )
        command = ApproveLayoutCommandV1(
            project_id=project_id,
            page_layout_draft_id=snapshot.layout.page_layout_draft_id,
            page_layout_draft_version_id=page_layout_draft_version_id,
            expected_revision=body.expected_revision,
            layout_content_sha256=body.layout_content_sha256,
            storyboard=page.storyboard,
            dimension_selections=body.dimension_selections,
        )
        return layout.approve_layout(
            command,
            idempotency_key=key,
            request_sha256=canonical_sha256(command),
        )
    except Exception as error:
        _raise_layout_error(error)
        raise AssertionError("unreachable") from error


@router.get("/{page_layout_draft_version_id}/impact")
def layout_impact(
    project_id: UUID,
    page_layout_draft_version_id: UUID,
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
    lineage: Annotated[LineageFacade, Depends(get_lineage_facade)],
    layout_content_sha256: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
) -> dict[str, object]:
    snapshot = layout.get_version(project_id, page_layout_draft_version_id)
    if snapshot.layout.content_sha256 != layout_content_sha256:
        raise HTTPException(status_code=409, detail="layout content hash does not match")
    artifact = ArtifactVersionRefV1(
        project_id=str(project_id),
        artifact_type="page_layout_draft",
        artifact_id=str(snapshot.layout.page_layout_draft_id),
        version=snapshot.layout.version,
        content_sha256=layout_content_sha256,
        schema_version=snapshot.layout.schema_version,
    )
    try:
        impacts = lineage.impact_preview(artifact)
    except ArtifactNotFoundError:
        impacts = ()
    return {
        "contract_version": "1.0",
        "origin": artifact.model_dump(mode="json"),
        "impacts": [impact.model_dump(mode="json") for impact in impacts],
        "external_requests_started": 0,
    }


@router.get(
    "/{page_layout_draft_version_id}/approval",
    response_model=LayoutApprovalV1 | None,
)
def get_layout_approval(
    project_id: UUID,
    page_layout_draft_version_id: UUID,
    layout: Annotated[LayoutFacade, Depends(get_layout_facade)],
) -> LayoutApprovalV1 | None:
    return layout.approval_for_version(project_id, page_layout_draft_version_id)


def _register_layout_lineage(
    lineage: LineageFacade,
    snapshot: LayoutVersionSnapshotV1,
) -> None:
    if snapshot.storyboard is None:
        return
    storyboard = ArtifactVersionRefV1(
        project_id=str(snapshot.project_id),
        artifact_type="storyboard",
        artifact_id=snapshot.storyboard.storyboard_id,
        version=snapshot.storyboard.version,
        content_sha256=snapshot.storyboard.content_sha256,
        schema_version="1.0",
    )
    layout = ArtifactVersionRefV1(
        project_id=str(snapshot.project_id),
        artifact_type="page_layout_draft",
        artifact_id=str(snapshot.layout.page_layout_draft_id),
        version=snapshot.layout.version,
        content_sha256=snapshot.layout.content_sha256,
        schema_version=snapshot.layout.schema_version,
    )
    storyboard = lineage.register_artifact(RegisterArtifactCommandV1(artifact=storyboard))
    layout = lineage.register_artifact(RegisterArtifactCommandV1(artifact=layout))
    lineage.register_dependency(
        RegisterDependencyCommandV1(
            upstream=storyboard,
            downstream=layout,
            edge_type="storyboard_to_layout",
        )
    )
    for frame in snapshot.layout.frames:
        frame_ref = lineage.register_artifact(
            RegisterArtifactCommandV1(
                artifact=ArtifactVersionRefV1(
                    project_id=str(snapshot.project_id),
                    artifact_type="frame",
                    artifact_id=str(frame.frame_id),
                    version=snapshot.layout.version,
                    content_sha256=frame_content_sha256(frame),
                    schema_version="1.0",
                )
            )
        )
        lineage.register_dependency(
            RegisterDependencyCommandV1(
                upstream=storyboard,
                downstream=frame_ref,
                edge_type="storyboard_to_layout",
            )
        )


def _invalidate_changed_frames(
    lineage: LineageFacade,
    parent: LayoutVersionSnapshotV1,
    saved: LayoutVersionSnapshotV1,
) -> None:
    next_hashes = {
        frame.frame_id: frame_content_sha256(frame) for frame in saved.layout.frames
    }
    for frame in parent.layout.frames:
        previous_hash = frame_content_sha256(frame)
        if next_hashes.get(frame.frame_id) == previous_hash:
            continue
        origin = ArtifactVersionRefV1(
            project_id=str(parent.project_id),
            artifact_type="frame",
            artifact_id=str(frame.frame_id),
            version=parent.layout.version,
            content_sha256=previous_hash,
            schema_version="1.0",
        )
        lineage.invalidate(
            InvalidateArtifactCommandV1(
                source_event_id=uuid5(
                    NAMESPACE_URL,
                    "manga-maker:layout-frame-change:"
                    f"{saved.page_layout_draft_version_id}:{frame.frame_id}:{previous_hash}",
                ),
                origin=origin,
                reason_code="LAYOUT_FRAME_CHANGED",
            )
        )
