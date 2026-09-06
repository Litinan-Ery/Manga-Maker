from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..bootstrap.dependencies import get_workflow_context_service, require_local_session
from ..platform.context_budget import ContextObservation, InputEvent
from ..workflows.book_production.contracts import UnitReference
from ..workflows.book_production.public import Lease, TaskSnapshot, WorkflowContextService

router = APIRouter(prefix="/api/v1/projects/{project_id}/workflows", tags=["workflow-context"])
Service = Annotated[WorkflowContextService, Depends(get_workflow_context_service)]
Authorized = Annotated[None, Depends(require_local_session)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimRequest(RequestModel):
    writer_id: str = Field(min_length=1, max_length=80)
    ttl: float = Field(default=60, ge=1, le=600)


class MutationRequest(RequestModel):
    lease: Lease
    expected_revision: int = Field(ge=0)


class CheckpointRequest(MutationRequest):
    snapshot: TaskSnapshot


class CreateRequest(RequestModel):
    snapshot: TaskSnapshot


class RefreshRequest(MutationRequest):
    units: tuple[UnitReference, ...] = Field(max_length=1000)
    next_action: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_units(self) -> RefreshRequest:
        if len({unit.unit_id for unit in self.units}) != len(self.units):
            raise ValueError("unit IDs must be unique")
        if len({unit.page_number for unit in self.units}) != len(self.units):
            raise ValueError("page numbers must be unique")
        return self


class CaptureRequest(RequestModel):
    objective: str = Field(min_length=1, max_length=2000)


class ResumeRequest(MutationRequest):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObservationRequest(MutationRequest):
    observation: ContextObservation
    pending: tuple[InputEvent, ...] = Field(default=(), max_length=100)
    planned_output: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def unique_events(self) -> ObservationRequest:
        sequences = [event.sequence for event in self.pending]
        if len(sequences) != len(set(sequences)):
            raise ValueError("input event sequences must be unique")
        return self


class EvidenceRequest(MutationRequest):
    items: list[dict[str, Any]] = Field(max_length=10000)


@router.get("")
def list_runs(project_id: UUID, service: Service, _authorized: Authorized) -> dict[str, Any]:
    return {
        "runs": [
            state.model_dump(mode="json", include={"run_id", "revision", "state"})
            for state in service.store.list_runs(str(project_id))[:20]
        ]
    }


@router.post("/from-project", status_code=201)
def capture_run(
    project_id: UUID, request: CaptureRequest, service: Service, _authorized: Authorized
) -> dict[str, Any]:
    snapshot = service.reader.capture(str(project_id), request.objective)
    return create_run(project_id, CreateRequest(snapshot=snapshot), service, _authorized)


@router.post("", status_code=201)
def create_run(
    project_id: UUID, request: CreateRequest, service: Service, _authorized: Authorized
) -> dict[str, Any]:
    project = str(project_id)
    state = service.store.create(project)
    run = str(state.run_id)
    lease = service.store.claim(project, run, f"create-{run}")
    try:
        service.save(project, run, request.snapshot, lease, 0)
    finally:
        service.store.release(project, run, lease)
    return service.summary(project, run)


@router.get("/{run_id}/context")
def context(
    project_id: UUID, run_id: UUID, service: Service, _authorized: Authorized
) -> dict[str, Any]:
    return service.summary(str(project_id), str(run_id))


@router.post("/{run_id}/leases")
def claim(
    project_id: UUID, run_id: UUID, request: ClaimRequest, service: Service, _authorized: Authorized
) -> dict[str, Any]:
    return service.store.claim(
        str(project_id), str(run_id), request.writer_id, ttl=request.ttl
    ).model_dump(mode="json")


@router.post("/{run_id}/leases/release")
def release(
    project_id: UUID,
    run_id: UUID,
    request: MutationRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, bool]:
    service.store.release(str(project_id), str(run_id), request.lease)
    return {"released": True}


@router.post("/{run_id}/checkpoints")
def save_checkpoint(
    project_id: UUID,
    run_id: UUID,
    request: CheckpointRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    return service.save(
        str(project_id), str(run_id), request.snapshot, request.lease, request.expected_revision
    )


@router.get("/{run_id}/evidence")
def evidence(
    project_id: UUID,
    run_id: UUID,
    service: Service,
    _authorized: Authorized,
    artifact_id: UUID | None = None,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return service.evidence(
        str(project_id),
        str(run_id),
        str(artifact_id) if artifact_id else None,
        cursor=cursor,
        limit=limit,
    )


@router.post("/{run_id}/checkpoints/refresh")
def refresh_checkpoint(
    project_id: UUID,
    run_id: UUID,
    request: RefreshRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    return service.refresh_units(
        str(project_id),
        str(run_id),
        request.lease,
        request.expected_revision,
        request.units,
        request.next_action,
    )


@router.post("/{run_id}/evidence")
def record_evidence(
    project_id: UUID,
    run_id: UUID,
    request: EvidenceRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    return service.record_evidence(
        str(project_id), str(run_id), request.items, request.lease, request.expected_revision
    )


@router.get("/{run_id}/evidence/{artifact_id}/items/{index}")
def evidence_detail(
    project_id: UUID,
    run_id: UUID,
    artifact_id: UUID,
    index: int,
    service: Service,
    _authorized: Authorized,
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return service.evidence_detail(str(project_id), str(run_id), str(artifact_id), index, offset)


@router.post("/{run_id}/recovery-plan")
def recovery_plan(
    project_id: UUID, run_id: UUID, service: Service, _authorized: Authorized
) -> dict[str, Any]:
    plan = service.reconcile(str(project_id), str(run_id))
    return {key: value for key, value in plan.items() if key not in {"units", "problems"}} | {
        "problem_count": len(plan["problems"]),
        "problems": plan["problems"][:10],
    }


@router.post("/{run_id}/recovery-bundles")
def recovery_bundle(
    project_id: UUID,
    run_id: UUID,
    request: MutationRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    return service.bundle(str(project_id), str(run_id), request.lease, request.expected_revision)


@router.post("/{run_id}/resume")
def resume(
    project_id: UUID,
    run_id: UUID,
    request: ResumeRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    return service.resume(
        str(project_id), str(run_id), request.lease, request.expected_revision, request.plan_hash
    )


@router.post("/{run_id}/observations")
def observe(
    project_id: UUID,
    run_id: UUID,
    request: ObservationRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    state, decision = service.coordinator.observe(
        str(project_id),
        str(run_id),
        request.lease,
        request.expected_revision,
        request.observation,
        request.pending,
        request.planned_output,
    )
    return {"runtime": state.model_dump(mode="json"), "decision": decision.model_dump(mode="json")}


@router.post("/{run_id}/compact")
def compact(
    project_id: UUID,
    run_id: UUID,
    request: MutationRequest,
    service: Service,
    _authorized: Authorized,
) -> dict[str, Any]:
    return service.coordinator.compact(
        str(project_id), str(run_id), request.lease, request.expected_revision
    ).model_dump(mode="json")
