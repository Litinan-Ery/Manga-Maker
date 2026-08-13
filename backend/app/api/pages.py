from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..bootstrap.dependencies import get_composition_facade, require_local_session
from ..modules.composition.public import (
    CompositionFacade,
    CreatePageRevisionCommandV1,
    PageDocumentSnapshotV1,
)
from ..pages.models import PageDocument
from ..pages.service import PageService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/pages", tags=["pages"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]
CompositionDependency = Annotated[CompositionFacade, Depends(get_composition_facade)]
RequiredSession = Annotated[None, Depends(require_local_session)]


class DraftPagesRequest(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=64)


class CreatePageRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    document: PageDocument


class ActivatePageVersionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


def service(request: Request) -> PageService:
    return cast(PageService, request.app.state.pages)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


@router.get("/templates")
def page_templates(project_id: str, request: Request) -> list[dict[str, object]]:
    service(request).require_project(project_id)
    return service(request).template_payloads()


@router.post("/draft", status_code=status.HTTP_201_CREATED)
def draft_pages(
    project_id: str,
    request: Request,
    body: DraftPagesRequest,
    headers: Headers,
) -> list[dict[str, Any]]:
    verify_session(request, headers)
    return service(request).draft_pages(project_id, body.chapter_id)


@router.get("")
def list_pages(
    project_id: str,
    request: Request,
    chapter_id: str = Query(min_length=1, max_length=64),
) -> list[dict[str, Any]]:
    return service(request).list_pages(project_id, chapter_id)


@router.get("/{page_id}/current")
def current_page(project_id: str, page_id: str, request: Request) -> dict[str, Any]:
    return service(request).get_current(project_id, page_id)


@router.get("/{page_id}/versions/{page_version_id}")
def page_version(
    project_id: str, page_id: str, page_version_id: str, request: Request
) -> dict[str, Any]:
    return service(request).get_version(project_id, page_id, page_version_id)


@router.get("/{page_id}/versions")
def page_versions(
    project_id: str, page_id: str, request: Request
) -> list[dict[str, Any]]:
    return service(request).list_versions(project_id, page_id)


@router.post("/{page_id}/versions/{page_version_id}/activate")
def activate_page_version(
    project_id: str,
    page_id: str,
    page_version_id: str,
    request: Request,
    body: ActivatePageVersionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).activate_version(
        project_id,
        page_id,
        page_version_id,
        expected_revision=body.expected_revision,
    )


@router.post("/{page_id}/versions", status_code=status.HTTP_201_CREATED)
def create_page_revision(
    project_id: str,
    page_id: str,
    body: CreatePageRevisionRequest,
    facade: CompositionDependency,
    _session: RequiredSession,
) -> dict[str, Any]:
    command = CreatePageRevisionCommandV1(
        project_id=project_id,
        page_id=page_id,
        expected_revision=body.expected_revision,
        document=PageDocumentSnapshotV1.model_validate(body.document.model_dump(mode="json")),
    )
    return facade.create_page_revision(command).legacy_payload()


@router.get(
    "/{page_id}/versions/{page_version_id}/content",
    response_class=FileResponse,
)
def page_content(
    project_id: str,
    page_id: str,
    page_version_id: str,
    request: Request,
    headers: Headers,
) -> FileResponse:
    verify_session(request, headers)
    path = service(request).content_path(project_id, page_id, page_version_id)
    return FileResponse(path, media_type="image/png", filename="page.png")
