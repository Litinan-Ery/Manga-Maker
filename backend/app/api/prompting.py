from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from ..prompting.models import CharacterTagBundleDocument, PromptDraftBundleDocument
from ..prompting.service import PromptingService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/prompting", tags=["prompting"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class GenerateArtifactRequest(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=64)
    confirmed_data_send: bool = False


class ReviseCharacterTagsRequest(BaseModel):
    document: CharacterTagBundleDocument


class RevisePromptBundleRequest(BaseModel):
    document: PromptDraftBundleDocument


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def prompting_service(request: Request) -> PromptingService:
    return cast(PromptingService, request.app.state.prompting)


@router.get("")
def get_prompting_workflow(
    project_id: str,
    request: Request,
    chapter_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, Any]:
    return prompting_service(request).get_workflow(project_id, chapter_id)


@router.post("/character-tags/generate", status_code=status.HTTP_201_CREATED)
async def generate_character_tags(
    project_id: str,
    request: Request,
    body: GenerateArtifactRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return await prompting_service(request).generate_character_tags(
        project_id,
        body.chapter_id,
        confirmed_data_send=body.confirmed_data_send,
    )


@router.post(
    "/character-tags/{version_id}/revisions", status_code=status.HTTP_201_CREATED
)
def revise_character_tags(
    project_id: str,
    version_id: str,
    request: Request,
    body: ReviseCharacterTagsRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return prompting_service(request).revise_character_tags(
        project_id, version_id, body.document
    )


@router.post("/character-tags/{version_id}/approve")
def approve_character_tags(
    project_id: str,
    version_id: str,
    request: Request,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return prompting_service(request).approve_character_tags(project_id, version_id)


@router.post("/prompt-bundles/generate", status_code=status.HTTP_201_CREATED)
async def generate_prompt_bundle(
    project_id: str,
    request: Request,
    body: GenerateArtifactRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return await prompting_service(request).generate_prompt_bundle(
        project_id,
        body.chapter_id,
        confirmed_data_send=body.confirmed_data_send,
    )


@router.post("/prompt-bundles/{version_id}/revisions", status_code=status.HTTP_201_CREATED)
def revise_prompt_bundle(
    project_id: str,
    version_id: str,
    request: Request,
    body: RevisePromptBundleRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return prompting_service(request).revise_prompt_bundle(
        project_id, version_id, body.document
    )


@router.post("/prompt-bundles/{version_id}/approve")
def approve_prompt_bundle(
    project_id: str,
    version_id: str,
    request: Request,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return prompting_service(request).approve_prompt_bundle(project_id, version_id)
