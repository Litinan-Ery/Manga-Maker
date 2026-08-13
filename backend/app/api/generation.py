from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..errors import ApplicationError
from ..generation.assets import AssetStore
from ..generation.queue import GenerationQueueService
from ..generation.revisions import RevisionOperation, RevisionService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/generation", tags=["generation"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class EstimateRequest(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=64)
    per_panel_cost_ceiling_anlas: int = Field(default=10, ge=0, le=100_000)


class CreateJobRequest(EstimateRequest):
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    max_calls: int = Field(ge=1, le=10_000)
    max_cost_anlas: int = Field(ge=0, le=10_000_000)
    confirmed: bool


class TransitionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class ExecuteRequest(TransitionRequest):
    confirmation: Literal["I_CONFIRM_NOVELAI_IMAGE_GENERATION"]


class RevisionEstimateRequest(BaseModel):
    operation: RevisionOperation
    page_id: str = Field(min_length=1, max_length=64)
    panel_id: str | None = Field(default=None, min_length=1, max_length=64)
    mask_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    edit_prompt: str | None = Field(default=None, min_length=1, max_length=2_000)
    inpaint_strength: float | None = Field(default=None, ge=0.1, le=1)
    per_panel_cost_ceiling_anlas: int = Field(default=10, ge=0, le=100_000)


class CreateRevisionJobRequest(RevisionEstimateRequest):
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    max_calls: int = Field(ge=1, le=10_000)
    max_cost_anlas: int = Field(ge=0, le=10_000_000)
    confirmed: bool


class ActivateAssetRequest(BaseModel):
    panel_id: str = Field(min_length=1, max_length=64)
    expected_current_asset_version_id: str = Field(min_length=1, max_length=64)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def queue_service(request: Request) -> GenerationQueueService:
    return cast(GenerationQueueService, request.app.state.generation_queue)


def asset_store(request: Request) -> AssetStore:
    return cast(AssetStore, request.app.state.asset_store)


def revision_service(request: Request) -> RevisionService:
    return cast(RevisionService, request.app.state.revisions)


def idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be an opaque value of at most 128 characters",
        )
    return normalized


@router.post("/estimate")
def estimate(
    project_id: str,
    request: Request,
    body: EstimateRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return queue_service(request).estimate(
        project_id,
        body.chapter_id,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
    )


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_job(
    project_id: str,
    request: Request,
    body: CreateJobRequest,
    headers: Headers,
    raw_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    verify_session(request, headers)
    key = idempotency_key(raw_idempotency_key)
    request_sha256 = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return queue_service(request).create_job(
        project_id,
        body.chapter_id,
        plan_fingerprint=body.plan_fingerprint,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
        max_calls=body.max_calls,
        max_cost_anlas=body.max_cost_anlas,
        confirmed=body.confirmed,
        idempotency_key=key,
        request_sha256=request_sha256,
    )


@router.get("/jobs")
def list_jobs(project_id: str, request: Request) -> list[dict[str, Any]]:
    return queue_service(request).list_jobs(project_id)


@router.get("/jobs/{job_id}")
def get_job(project_id: str, job_id: str, request: Request) -> dict[str, Any]:
    return queue_service(request).get_job(project_id, job_id)


@router.post("/jobs/{job_id}/start")
def start_job(
    project_id: str,
    job_id: str,
    request: Request,
    body: TransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return queue_service(request).start_job(
        project_id, job_id, expected_revision=body.expected_revision
    )


@router.post("/jobs/{job_id}/pause")
def pause_job(
    project_id: str,
    job_id: str,
    request: Request,
    body: TransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return queue_service(request).pause_job(
        project_id, job_id, expected_revision=body.expected_revision
    )


@router.post("/jobs/{job_id}/resume")
def resume_job(
    project_id: str,
    job_id: str,
    request: Request,
    body: TransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return queue_service(request).resume_job(
        project_id, job_id, expected_revision=body.expected_revision
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    project_id: str,
    job_id: str,
    request: Request,
    body: TransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return queue_service(request).cancel_job(
        project_id, job_id, expected_revision=body.expected_revision
    )


@router.post("/jobs/{job_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_job(
    project_id: str,
    job_id: str,
    request: Request,
    body: ExecuteRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    job = queue_service(request).get_job(project_id, job_id)
    if job["revision"] != body.expected_revision:
        raise ApplicationError(
            "GENERATION_JOB_REVISION_CONFLICT", "队列状态已变化，请刷新后重试。", 409
        )
    if job["status"] != "running":
        raise ApplicationError(
            "GENERATION_JOB_TRANSITION_INVALID", "请先由用户启动队列。", 409
        )
    scheduled = request.app.state.generation_executor.schedule(job_id)
    return {
        "status": "scheduled" if scheduled else "already_running",
        "job_id": job_id,
        "bounded_user_action_id": job["user_action_id"],
    }


@router.get("/assets")
def list_assets(project_id: str, request: Request) -> list[dict[str, Any]]:
    return asset_store(request).current_assets(project_id)


@router.get("/assets/panels/{panel_id}/versions")
def list_asset_versions(
    project_id: str, panel_id: str, request: Request
) -> list[dict[str, Any]]:
    return asset_store(request).list_asset_versions(project_id, panel_id)


@router.get("/assets/{asset_version_id}")
def get_asset(project_id: str, asset_version_id: str, request: Request) -> dict[str, Any]:
    return asset_store(request).get_asset(project_id, asset_version_id)


@router.post("/assets/{asset_version_id}/activate")
def activate_asset(
    project_id: str,
    asset_version_id: str,
    request: Request,
    body: ActivateAssetRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return asset_store(request).activate_asset_version(
        project_id,
        body.panel_id,
        asset_version_id,
        expected_current_asset_version_id=body.expected_current_asset_version_id,
    )


@router.get("/assets/{asset_version_id}/content", response_class=FileResponse)
def get_asset_content(
    project_id: str,
    asset_version_id: str,
    request: Request,
    headers: Headers,
) -> FileResponse:
    verify_session(request, headers)
    path = asset_store(request).asset_content_path(project_id, asset_version_id)
    return FileResponse(path, media_type="image/png", filename="panel.png")


@router.post("/masks", status_code=status.HTTP_201_CREATED)
async def create_mask(
    project_id: str,
    request: Request,
    headers: Headers,
    panel_id: str = Form(min_length=1, max_length=64),
    parent_asset_version_id: str = Form(min_length=1, max_length=64),
    mask: UploadFile = File(),  # noqa: B008
) -> dict[str, Any]:
    verify_session(request, headers)
    raw = await mask.read(8 * 1024 * 1024 + 1)
    return revision_service(request).create_mask(
        project_id, panel_id, parent_asset_version_id, raw
    )


@router.get("/masks/{mask_asset_id}/content", response_class=FileResponse)
def mask_content(
    project_id: str,
    mask_asset_id: str,
    request: Request,
    headers: Headers,
) -> FileResponse:
    verify_session(request, headers)
    path = revision_service(request).mask_content_path(project_id, mask_asset_id)
    return FileResponse(path, media_type="image/png", filename="mask.png")


@router.post("/revisions/estimate")
def estimate_revision(
    project_id: str,
    request: Request,
    body: RevisionEstimateRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return revision_service(request).estimate(
        project_id,
        body.operation,
        body.page_id,
        panel_id=body.panel_id,
        mask_asset_id=body.mask_asset_id,
        edit_prompt=body.edit_prompt,
        inpaint_strength=body.inpaint_strength,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
    )


@router.post("/revisions/jobs", status_code=status.HTTP_201_CREATED)
def create_revision_job(
    project_id: str,
    request: Request,
    body: CreateRevisionJobRequest,
    headers: Headers,
    raw_idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    verify_session(request, headers)
    key = idempotency_key(raw_idempotency_key)
    request_sha256 = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return revision_service(request).create_job(
        project_id,
        body.operation,
        body.page_id,
        panel_id=body.panel_id,
        mask_asset_id=body.mask_asset_id,
        edit_prompt=body.edit_prompt,
        inpaint_strength=body.inpaint_strength,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
        plan_fingerprint=body.plan_fingerprint,
        max_calls=body.max_calls,
        max_cost_anlas=body.max_cost_anlas,
        confirmed=body.confirmed,
        idempotency_key=key,
        request_sha256=request_sha256,
    )
