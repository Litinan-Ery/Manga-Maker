from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from pydantic import BaseModel, Field

from ..errors import ApplicationError
from ..ingestion.txt import ChapterBoundary, TxtIngestionService
from ..projects import ProjectService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConfirmSourceRequest(BaseModel):
    preflight_id: str
    encoding: str = Field(min_length=1, max_length=64)


class ChapterBoundaryRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class ReplaceChaptersRequest(BaseModel):
    source_file_id: str
    chapters: list[ChapterBoundaryRequest] = Field(min_length=1, max_length=10000)


class CreateAnchorRequest(BaseModel):
    chapter_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def project_service(request: Request) -> ProjectService:
    return cast(ProjectService, request.app.state.projects)


def ingestion_service(request: Request) -> TxtIngestionService:
    return cast(TxtIngestionService, request.app.state.ingestion)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    request: Request, body: CreateProjectRequest, headers: Headers
) -> dict[str, Any]:
    verify_session(request, headers)
    project = project_service(request).create(body.title)
    return asdict(project)


@router.get("")
def list_projects(request: Request) -> list[dict[str, Any]]:
    return [asdict(project) for project in project_service(request).list()]


@router.get("/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    return asdict(project_service(request).get(project_id))


@router.post("/{project_id}/source/preflight", status_code=status.HTTP_201_CREATED)
async def preflight_source(
    project_id: str,
    request: Request,
    headers: Headers,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    verify_session(request, headers)
    project_service(request).require_writable(project_id)
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise ApplicationError(
            code="TXT_FILE_TOO_LARGE",
            message="TXT 文件超过 P0 的 10 MB 安全上限。",
            status_code=413,
        )
    return ingestion_service(request).preflight(project_id, file.filename or "source.txt", content)


@router.post("/{project_id}/source/confirm", status_code=status.HTTP_201_CREATED)
def confirm_source(
    project_id: str,
    request: Request,
    body: ConfirmSourceRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    project_service(request).require_writable(project_id)
    return ingestion_service(request).confirm(project_id, body.preflight_id, body.encoding)


@router.get("/{project_id}/source/chapters")
def current_chapters(project_id: str, request: Request) -> dict[str, Any]:
    return ingestion_service(request).current_chapters(project_id)


@router.get("/{project_id}/source/chapters/{chapter_id}/text")
def chapter_text(project_id: str, chapter_id: str, request: Request) -> dict[str, Any]:
    return ingestion_service(request).chapter_text(project_id, chapter_id)


@router.get("/{project_id}/source/chapters/{chapter_id}/story-beats")
def current_story_beats(project_id: str, chapter_id: str, request: Request) -> dict[str, Any]:
    return ingestion_service(request).current_story_beats(project_id, chapter_id)


@router.post(
    "/{project_id}/source/chapters/{chapter_id}/story-beats/draft",
    status_code=status.HTTP_201_CREATED,
)
def draft_story_beats(
    project_id: str,
    chapter_id: str,
    request: Request,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    project_service(request).require_writable(project_id)
    return ingestion_service(request).draft_story_beats(project_id, chapter_id)


@router.put("/{project_id}/source/chapters")
def replace_chapters(
    project_id: str,
    request: Request,
    body: ReplaceChaptersRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    project_service(request).require_writable(project_id)
    boundaries = [
        ChapterBoundary(
            title=chapter.title,
            start_offset=chapter.start_offset,
            end_offset=chapter.end_offset,
        )
        for chapter in body.chapters
    ]
    return ingestion_service(request).replace_chapters(project_id, body.source_file_id, boundaries)


@router.post("/{project_id}/source/anchors", status_code=status.HTTP_201_CREATED)
def create_anchor(
    project_id: str,
    request: Request,
    body: CreateAnchorRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    project_service(request).require_writable(project_id)
    return ingestion_service(request).create_anchor(
        project_id, body.chapter_id, body.start_offset, body.end_offset
    )
