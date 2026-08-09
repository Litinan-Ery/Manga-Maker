from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..library.service import AssetLibraryService, LibraryKind
from ..security import session_headers

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/asset-library",
    tags=["asset-library"],
)
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class LibraryItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: LibraryKind
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)


class CreateLibraryItemRequest(LibraryItemInput):
    source_asset_version_id: str = Field(min_length=1, max_length=64)


class UpdateLibraryItemRequest(LibraryItemInput):
    expected_revision: int = Field(ge=1)


class LibraryStatusRequest(BaseModel):
    expected_revision: int = Field(ge=1)


def service(request: Request) -> AssetLibraryService:
    return cast(AssetLibraryService, request.app.state.asset_library)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


@router.get("")
def list_library_items(
    project_id: str,
    request: Request,
    include_archived: bool = Query(default=False),
) -> list[dict[str, Any]]:
    return service(request).list_items(project_id, include_archived=include_archived)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_library_item(
    project_id: str,
    request: Request,
    body: CreateLibraryItemRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).create_item(project_id, **body.model_dump())


@router.put("/{library_item_id}")
def update_library_item(
    project_id: str,
    library_item_id: str,
    request: Request,
    body: UpdateLibraryItemRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).update_item(
        project_id, library_item_id, **body.model_dump()
    )


def set_item_status(
    project_id: str,
    library_item_id: str,
    request: Request,
    body: LibraryStatusRequest,
    headers: Headers,
    target_status: Literal["active", "archived"],
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).set_status(
        project_id,
        library_item_id,
        status=target_status,
        expected_revision=body.expected_revision,
    )


@router.post("/{library_item_id}/archive")
def archive_library_item(
    project_id: str,
    library_item_id: str,
    request: Request,
    body: LibraryStatusRequest,
    headers: Headers,
) -> dict[str, Any]:
    return set_item_status(
        project_id, library_item_id, request, body, headers, "archived"
    )


@router.post("/{library_item_id}/restore")
def restore_library_item(
    project_id: str,
    library_item_id: str,
    request: Request,
    body: LibraryStatusRequest,
    headers: Headers,
) -> dict[str, Any]:
    return set_item_status(
        project_id, library_item_id, request, body, headers, "active"
    )


@router.get("/{library_item_id}/content", response_class=FileResponse)
def library_item_content(
    project_id: str,
    library_item_id: str,
    request: Request,
    headers: Headers,
) -> FileResponse:
    verify_session(request, headers)
    path = service(request).content_path(project_id, library_item_id)
    return FileResponse(path, media_type="image/png", filename="library-asset.png")
