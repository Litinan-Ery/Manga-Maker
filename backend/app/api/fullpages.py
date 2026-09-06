from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..bootstrap.container import AppContainer
from ..bootstrap.dependencies import get_app_container, require_local_session
from ..fullpages.models import FullPageOptions

router = APIRouter(prefix="/api/v1/projects/{project_id}/full-pages", tags=["full-pages"])
Container = Annotated[AppContainer, Depends(get_app_container)]
Session = Annotated[None, Depends(require_local_session)]


class FullPageApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool


class FullPageAdoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    confirmed: bool


@router.get("/sources")
def sources(
    project_id: str, container: Container, chapter_id: str = Query(min_length=1, max_length=64)
) -> dict[str, Any]:
    return container.full_pages.sources(project_id, chapter_id)


@router.post("/preview", status_code=201)
def preview(
    project_id: str, body: FullPageOptions, container: Container, _session: Session
) -> dict[str, Any]:
    return container.full_pages.preview(project_id, body)


@router.get("")
def history(
    project_id: str, container: Container, chapter_id: str = Query(min_length=1, max_length=64)
) -> list[dict[str, Any]]:
    return container.full_pages.list_generations(project_id, chapter_id)


@router.get("/{generation_id}")
def generation(project_id: str, generation_id: str, container: Container) -> dict[str, Any]:
    return container.full_pages.get(project_id, generation_id)


@router.post("/{generation_id}/generate")
async def generate(
    project_id: str,
    generation_id: str,
    body: FullPageApproval,
    container: Container,
    _session: Session,
) -> dict[str, Any]:
    return await container.full_pages.generate(project_id, generation_id, **body.model_dump())


@router.post("/{generation_id}/adopt", status_code=201)
def adopt(
    project_id: str,
    generation_id: str,
    body: FullPageAdoption,
    container: Container,
    _session: Session,
) -> dict[str, Any]:
    return container.full_pages.adopt(project_id, generation_id, **body.model_dump())


@router.get("/{generation_id}/content", response_class=FileResponse)
def content(
    project_id: str, generation_id: str, container: Container, _session: Session
) -> FileResponse:
    return FileResponse(
        container.full_pages.content_path(project_id, generation_id),
        media_type="image/png",
        filename="full-page.png",
    )
