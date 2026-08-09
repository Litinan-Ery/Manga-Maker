from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..novelai.service import NovelAIService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/novelai", tags=["novelai"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class NovelAIConfigurationRequest(BaseModel):
    provider_model_id: str = Field(min_length=1, max_length=100)
    credential_profile_id: str = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=30, ge=1, le=180)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def novelai_service(request: Request) -> NovelAIService:
    return cast(NovelAIService, request.app.state.novelai)


@router.get("/capabilities")
def get_capabilities(project_id: str, request: Request) -> dict[str, Any]:
    return novelai_service(request).capabilities(project_id)


@router.get("/config")
def get_configuration(project_id: str, request: Request) -> dict[str, Any]:
    return novelai_service(request).get_configuration(project_id)


@router.put("/config")
def save_configuration(
    project_id: str,
    request: Request,
    body: NovelAIConfigurationRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return novelai_service(request).save_configuration(
        project_id,
        provider_model_id=body.provider_model_id,
        credential_profile_id=body.credential_profile_id,
        timeout_seconds=body.timeout_seconds,
    )


@router.post("/connection-test")
async def test_connection(
    project_id: str, request: Request, headers: Headers
) -> dict[str, Any]:
    verify_session(request, headers)
    return await novelai_service(request).test_connection(project_id)
