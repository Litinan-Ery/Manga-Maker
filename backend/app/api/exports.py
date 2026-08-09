from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ApplicationError
from ..exports.service import MAX_PACKAGE_COMPRESSED_BYTES, ExportService
from ..security import session_headers

router = APIRouter(tags=["exports"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class ExportPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    page_version_ids: list[str] | None = Field(default=None, min_length=1, max_length=64)


class CreateExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    page_version_ids: list[str] = Field(min_length=1, max_length=64)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    confirmed: bool


class RestorePackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


def service(request: Request) -> ExportService:
    return cast(ExportService, request.app.state.exports)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


@router.post("/api/v1/projects/{project_id}/exports/preflight")
def preflight_export(
    project_id: str,
    request: Request,
    body: ExportPreflightRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).preflight_export(project_id, body.chapter_id, body.page_version_ids)


@router.post("/api/v1/projects/{project_id}/exports", status_code=status.HTTP_201_CREATED)
def create_export(
    project_id: str,
    request: Request,
    body: CreateExportRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).create_export(
        project_id,
        body.chapter_id,
        body.page_version_ids,
        body.plan_fingerprint,
        confirmed=body.confirmed,
    )


@router.get("/api/v1/projects/{project_id}/exports")
def list_exports(project_id: str, request: Request) -> list[dict[str, Any]]:
    return service(request).list_exports(project_id)


@router.get("/api/v1/projects/{project_id}/exports/{export_revision_id}")
def get_export(project_id: str, export_revision_id: str, request: Request) -> dict[str, Any]:
    return service(request).get_export(project_id, export_revision_id)


@router.get(
    "/api/v1/projects/{project_id}/exports/{export_revision_id}/files/{export_file_id}",
    response_class=FileResponse,
)
def download_export_file(
    project_id: str,
    export_revision_id: str,
    export_file_id: str,
    request: Request,
    headers: Headers,
) -> FileResponse:
    verify_session(request, headers)
    path, filename, media_type = service(request).export_file_path(
        project_id, export_revision_id, export_file_id
    )
    return FileResponse(path, media_type=media_type, filename=filename)


@router.post("/api/v1/imports/preflight", status_code=status.HTTP_201_CREATED)
async def preflight_import(
    request: Request,
    headers: Headers,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    verify_session(request, headers)
    content = await file.read(MAX_PACKAGE_COMPRESSED_BYTES + 1)
    if len(content) > MAX_PACKAGE_COMPRESSED_BYTES:
        raise ApplicationError("PROJECT_PACKAGE_TOO_LARGE", "工程包超过 512 MB 安全上限。", 413)
    return service(request).preflight_package(file.filename or "project.manga-maker.zip", content)


@router.post("/api/v1/imports/{import_preflight_id}/restore")
def restore_import(
    import_preflight_id: str,
    request: Request,
    body: RestorePackageRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).restore_package(import_preflight_id, confirmed=body.confirmed)
