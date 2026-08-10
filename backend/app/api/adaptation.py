from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator

from ..adaptation.models import StoryboardDocument
from ..adaptation.service import AdaptationService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/adaptation", tags=["adaptation"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class TextModelConfigurationRequest(BaseModel):
    provider_api_url: str = Field(
        min_length=1,
        max_length=2048,
        validation_alias=AliasChoices("provider_api_url", "base_url"),
    )
    model_name: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("model_name", "model"),
    )
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    credential_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    timeout_seconds: float = Field(default=60, ge=1, le=180)
    temperature: float = Field(default=0.2, ge=0, le=2)

    @model_validator(mode="after")
    def require_secret_input(self) -> TextModelConfigurationRequest:
        if self.api_key is None and self.credential_profile_id is None:
            raise ValueError("api_key is required")
        return self


class GenerateStoryboardRequest(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=64)
    page_budget: int = Field(ge=1, le=64)
    adaptation_preferences: list[str] = Field(default_factory=list, max_length=20)


class ReviseStoryboardRequest(BaseModel):
    document: StoryboardDocument


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def adaptation_service(request: Request) -> AdaptationService:
    return cast(AdaptationService, request.app.state.adaptation)


@router.get("/text-model")
def get_text_model_configuration(project_id: str, request: Request) -> dict[str, Any]:
    return adaptation_service(request).get_configuration(project_id)


@router.put("/text-model")
def save_text_model_configuration(
    project_id: str,
    request: Request,
    body: TextModelConfigurationRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return adaptation_service(request).save_configuration(
        project_id,
        base_url=body.provider_api_url,
        model=body.model_name,
        api_key=body.api_key.get_secret_value() if body.api_key is not None else None,
        credential_profile_id=body.credential_profile_id,
        timeout_seconds=body.timeout_seconds,
        temperature=body.temperature,
    )


@router.post("/text-model/test")
async def test_text_model_configuration(
    project_id: str, request: Request, headers: Headers
) -> dict[str, Any]:
    verify_session(request, headers)
    return await adaptation_service(request).test_configuration(project_id)


@router.post("/storyboards/generate", status_code=status.HTTP_201_CREATED)
async def generate_storyboard(
    project_id: str,
    request: Request,
    body: GenerateStoryboardRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return await adaptation_service(request).generate_storyboard(
        project_id,
        body.chapter_id,
        page_budget=body.page_budget,
        adaptation_preferences=body.adaptation_preferences,
    )


@router.get("/storyboards/current")
def get_current_storyboard(
    project_id: str,
    request: Request,
    chapter_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, Any]:
    return adaptation_service(request).get_current_storyboard(project_id, chapter_id)


@router.get("/storyboards/{storyboard_version_id}")
def get_storyboard_version(
    project_id: str, storyboard_version_id: str, request: Request
) -> dict[str, Any]:
    return adaptation_service(request).get_storyboard_version(project_id, storyboard_version_id)


@router.post(
    "/storyboards/{storyboard_version_id}/revisions",
    status_code=status.HTTP_201_CREATED,
)
def revise_storyboard(
    project_id: str,
    storyboard_version_id: str,
    request: Request,
    body: ReviseStoryboardRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return adaptation_service(request).revise_storyboard(
        project_id, storyboard_version_id, body.document
    )


@router.post("/storyboards/{storyboard_version_id}/approve")
def approve_storyboard(
    project_id: str,
    storyboard_version_id: str,
    request: Request,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return adaptation_service(request).approve_storyboard(project_id, storyboard_version_id)
