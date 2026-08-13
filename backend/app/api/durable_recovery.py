from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ..bootstrap.dependencies import get_recovery_coordinator, require_local_session
from ..errors import ApplicationError
from ..platform.recovery.contracts import RecoveryTrigger
from ..platform.recovery.coordinator import (
    RecoveryAcknowledgementRequiredError,
    RecoveryCoordinator,
    RecoveryFindingNotRepairableError,
    RecoveryReportNotFoundError,
)

router = APIRouter(prefix="/api/v1/system/durable-recovery", tags=["system"])
Authorized = Annotated[None, Depends(require_local_session)]
Coordinator = Annotated[RecoveryCoordinator, Depends(get_recovery_coordinator)]


@router.get("")
def latest_recovery(
    coordinator: Coordinator,
    _authorized: Authorized,
) -> dict[str, object]:
    report = coordinator.latest()
    if report is None:
        return {
            "status": "not_run",
            "findings": [],
            "external_requests_started": 0,
        }
    return report.payload()


@router.post("/checks")
def run_manual_check(
    coordinator: Coordinator,
    _authorized: Authorized,
) -> dict[str, object]:
    return coordinator.run(RecoveryTrigger.MANUAL).payload()


@router.post("/{report_id}/acknowledge")
def acknowledge_recovery(
    report_id: UUID,
    coordinator: Coordinator,
    _authorized: Authorized,
) -> dict[str, object]:
    try:
        return coordinator.acknowledge(report_id).payload()
    except RecoveryReportNotFoundError as exc:
        raise ApplicationError("RECOVERY_REPORT_NOT_FOUND", "没有找到恢复报告。", 404) from exc


@router.post("/{report_id}/findings/{finding_id}/repair")
def repair_finding(
    report_id: UUID,
    finding_id: UUID,
    coordinator: Coordinator,
    _authorized: Authorized,
) -> dict[str, str]:
    try:
        return coordinator.repair(report_id, finding_id).payload()
    except RecoveryAcknowledgementRequiredError as exc:
        raise ApplicationError(
            "RECOVERY_ACKNOWLEDGEMENT_REQUIRED",
            "请先查看并确认恢复范围。",
            409,
        ) from exc
    except RecoveryFindingNotRepairableError as exc:
        raise ApplicationError(
            "RECOVERY_REQUIRES_MANUAL_REVIEW",
            "该问题必须人工检查，不能自动修复。",
            409,
        ) from exc
    except RecoveryReportNotFoundError as exc:
        raise ApplicationError("RECOVERY_FINDING_NOT_FOUND", "没有找到恢复问题。", 404) from exc
