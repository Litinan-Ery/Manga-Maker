from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..errors import ApplicationError
from ..generation.assets import AssetStore
from ..generation.queue import GenerationQueueService
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


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def queue_service(request: Request) -> GenerationQueueService:
    return cast(GenerationQueueService, request.app.state.generation_queue)


def asset_store(request: Request) -> AssetStore:
    return cast(AssetStore, request.app.state.asset_store)


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
) -> dict[str, Any]:
    verify_session(request, headers)
    return queue_service(request).create_job(
        project_id,
        body.chapter_id,
        plan_fingerprint=body.plan_fingerprint,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
        max_calls=body.max_calls,
        max_cost_anlas=body.max_cost_anlas,
        confirmed=body.confirmed,
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


@router.get("/assets/{asset_version_id}")
def get_asset(project_id: str, asset_version_id: str, request: Request) -> dict[str, Any]:
    return asset_store(request).get_asset(project_id, asset_version_id)


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
