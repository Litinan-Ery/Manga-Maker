from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

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


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def queue_service(request: Request) -> GenerationQueueService:
    return cast(GenerationQueueService, request.app.state.generation_queue)


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
