from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from ..book.service import BookProductionService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/projects/{project_id}/book-production", tags=["book"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


class EstimateBookRequest(BaseModel):
    per_panel_cost_ceiling_anlas: int = Field(default=0, ge=0, le=100_000)


class CreateBookPlanRequest(EstimateBookRequest):
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    max_calls: int = Field(ge=1, le=100_000)
    max_cost_anlas: int = Field(ge=0, le=100_000_000)
    confirmed: bool


class BookTransitionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


def service(request: Request) -> BookProductionService:
    return cast(BookProductionService, request.app.state.book_production)


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


@router.post("/estimate")
def estimate_book(
    project_id: str,
    request: Request,
    body: EstimateBookRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).estimate(
        project_id,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
    )


@router.post("/plans", status_code=status.HTTP_201_CREATED)
def create_book_plan(
    project_id: str,
    request: Request,
    body: CreateBookPlanRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).create_plan(
        project_id,
        per_panel_cost_ceiling_anlas=body.per_panel_cost_ceiling_anlas,
        plan_fingerprint=body.plan_fingerprint,
        max_calls=body.max_calls,
        max_cost_anlas=body.max_cost_anlas,
        confirmed=body.confirmed,
    )


@router.get("/plans/current")
def current_book_plan(project_id: str, request: Request) -> dict[str, Any]:
    return service(request).current(project_id)


@router.post("/plans/{book_plan_id}/chapters/{book_chapter_plan_id}/approve")
def approve_book_chapter(
    project_id: str,
    book_plan_id: str,
    book_chapter_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).approve_chapter(
        project_id,
        book_plan_id,
        book_chapter_plan_id,
        expected_revision=body.expected_revision,
    )


@router.post("/plans/{book_plan_id}/chapters/{book_chapter_plan_id}/retry")
def retry_book_chapter(
    project_id: str,
    book_plan_id: str,
    book_chapter_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).retry_chapter(
        project_id,
        book_plan_id,
        book_chapter_plan_id,
        expected_revision=body.expected_revision,
    )


@router.post("/plans/{book_plan_id}/start")
def start_book_plan(
    project_id: str,
    book_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).start(
        project_id, book_plan_id, expected_revision=body.expected_revision
    )


@router.post("/plans/{book_plan_id}/advance")
def advance_book_plan(
    project_id: str,
    book_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).advance(
        project_id, book_plan_id, expected_revision=body.expected_revision
    )


@router.post("/plans/{book_plan_id}/pause")
def pause_book_plan(
    project_id: str,
    book_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).pause(
        project_id, book_plan_id, expected_revision=body.expected_revision
    )


@router.post("/plans/{book_plan_id}/resume")
def resume_book_plan(
    project_id: str,
    book_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).resume(
        project_id, book_plan_id, expected_revision=body.expected_revision
    )


@router.post("/plans/{book_plan_id}/cancel")
def cancel_book_plan(
    project_id: str,
    book_plan_id: str,
    request: Request,
    body: BookTransitionRequest,
    headers: Headers,
) -> dict[str, Any]:
    verify_session(request, headers)
    return service(request).cancel(
        project_id, book_plan_id, expected_revision=body.expected_revision
    )
