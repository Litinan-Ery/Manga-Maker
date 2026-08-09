from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from ..continuity.models import ContinuityLedgerDocument
from ..continuity.service import ContinuityService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/continuity", tags=["continuity"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class DraftContinuityRequest(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=64)


class ContinuityDocumentRequest(BaseModel):
    document: ContinuityLedgerDocument


def service(request: Request) -> ContinuityService:
    return cast(ContinuityService, request.app.state.continuity)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


@router.get("")
def current_continuity(project_id: str, request: Request) -> dict[str, Any]:
    return service(request).current(project_id)


@router.get("/versions")
def continuity_versions(project_id: str, request: Request) -> list[dict[str, Any]]:
    return service(request).versions(project_id)


@router.post("/draft", status_code=status.HTTP_201_CREATED)
def draft_continuity(
    project_id: str,
    request: Request,
    body: DraftContinuityRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).draft(project_id, body.chapter_id)


@router.post("/{version_id}/impact")
def continuity_impact(
    project_id: str,
    version_id: str,
    request: Request,
    body: ContinuityDocumentRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).impact(project_id, version_id, body.document)


@router.post("/{version_id}/revisions", status_code=status.HTTP_201_CREATED)
def revise_continuity(
    project_id: str,
    version_id: str,
    request: Request,
    body: ContinuityDocumentRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).revise(project_id, version_id, body.document)


@router.post("/{version_id}/approve")
def approve_continuity(
    project_id: str, version_id: str, request: Request, headers: Headers
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).approve(project_id, version_id)
