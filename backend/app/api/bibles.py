from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..bibles.models import CharacterBibleDocument, StyleBibleDocument
from ..bibles.service import MAX_REFERENCE_BYTES, BibleService
from ..errors import ApplicationError
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/bibles", tags=["bibles"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class GenerateBibleBundleRequest(BaseModel):
    storyboard_version_id: str


class ReviseCharacterBibleRequest(BaseModel):
    document: CharacterBibleDocument


class ReviseStyleBibleRequest(BaseModel):
    document: StyleBibleDocument


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def bible_service(request: Request) -> BibleService:
    return cast(BibleService, request.app.state.bibles)


@router.get("")
def get_bible_bundle(
    project_id: str,
    request: Request,
    chapter_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> dict[str, Any]:
    return bible_service(request).get_bundle(project_id, chapter_id)


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_bible_bundle(
    project_id: str,
    request: Request,
    body: GenerateBibleBundleRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return bible_service(request).generate_bundle(project_id, body.storyboard_version_id)


@router.post("/characters/{version_id}/revisions", status_code=status.HTTP_201_CREATED)
def revise_character_bible(
    project_id: str,
    version_id: str,
    request: Request,
    body: ReviseCharacterBibleRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return bible_service(request).revise_character_bible(project_id, version_id, body.document)


@router.post("/styles/{version_id}/revisions", status_code=status.HTTP_201_CREATED)
def revise_style_bible(
    project_id: str,
    version_id: str,
    request: Request,
    body: ReviseStyleBibleRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return bible_service(request).revise_style_bible(project_id, version_id, body.document)


@router.post("/{kind}/{version_id}/approve")
def approve_bible(
    project_id: str,
    kind: Literal["character", "style"],
    version_id: str,
    request: Request,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return bible_service(request).approve(project_id, kind, version_id)


@router.post("/{kind}/{version_id}/references", status_code=status.HTTP_201_CREATED)
async def attach_reference(
    project_id: str,
    kind: Literal["character", "style"],
    version_id: str,
    request: Request,
    headers: Headers,
    file: Annotated[UploadFile, File()],
    source_note: Annotated[str, Form(min_length=1, max_length=500)],
    rights_confirmed: Annotated[bool, Form()],
    character_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    verify_session(request, headers)
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_REFERENCE_BYTES:
            raise ApplicationError(
                code="INVALID_REFERENCE_IMAGE_SIZE",
                message="参考图不得超过 10 MB。",
                status_code=413,
            )
        chunks.append(chunk)
    return bible_service(request).attach_reference(
        project_id,
        kind,
        version_id,
        character_id=character_id,
        original_filename=file.filename or "reference-image",
        data=b"".join(chunks),
        source_note=source_note,
        rights_confirmed=rights_confirmed,
    )


@router.get("/references/{reference_asset_id}/content")
def get_reference_content(
    project_id: str,
    reference_asset_id: str,
    request: Request,
    headers: Headers,
) -> FileResponse:
    verify_session(request, headers)
    path, media_type, filename = bible_service(request).reference_content(
        project_id, reference_asset_id
    )
    return FileResponse(path, media_type=media_type, filename=filename)
