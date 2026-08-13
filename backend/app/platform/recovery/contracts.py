from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from ..durable_work.contracts import validate_safe_key


class RecoveryTrigger(StrEnum):
    STARTUP = "startup"
    MANUAL = "manual"


class RecoveryReportStatus(StrEnum):
    HEALTHY = "healthy"
    NEEDS_ATTENTION = "needs_attention"
    REPAIRED = "repaired"


class RecoverySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RecoveryFindingStatus(StrEnum):
    OPEN = "open"
    REPAIRED = "repaired"


@dataclass(frozen=True, slots=True)
class ProbeFinding:
    owner: str
    code: str
    severity: RecoverySeverity
    subject_type: str
    subject_id: str
    repair_command: str | None

    def __post_init__(self) -> None:
        for name, value in (
            ("owner", self.owner),
            ("code", self.code),
            ("subject_type", self.subject_type),
            ("subject_id", self.subject_id),
        ):
            validate_safe_key(name, value)
        if self.repair_command is not None:
            validate_safe_key("repair_command", self.repair_command)


@dataclass(frozen=True, slots=True)
class RecoveryFindingSnapshot:
    recovery_finding_id: UUID
    owner: str
    code: str
    severity: RecoverySeverity
    subject_type: str
    subject_id: str
    repair_command: str | None
    status: RecoveryFindingStatus

    def payload(self) -> dict[str, object]:
        return {
            "recovery_finding_id": str(self.recovery_finding_id),
            "owner": self.owner,
            "code": self.code,
            "severity": self.severity.value,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "repair_command": self.repair_command,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class RecoveryReportSnapshot:
    recovery_report_id: UUID
    trigger: RecoveryTrigger
    status: RecoveryReportStatus
    created_at: datetime
    acknowledged_at: datetime | None
    finding_counts: tuple[tuple[str, int], ...]
    findings: tuple[RecoveryFindingSnapshot, ...]
    external_requests_started: int = 0

    def payload(self) -> dict[str, object]:
        return {
            "recovery_report_id": str(self.recovery_report_id),
            "trigger": self.trigger.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "acknowledged_at": (
                self.acknowledged_at.isoformat() if self.acknowledged_at else None
            ),
            "finding_counts": dict(self.finding_counts),
            "findings": [finding.payload() for finding in self.findings],
            "external_requests_started": self.external_requests_started,
        }


@dataclass(frozen=True, slots=True)
class RecoveryRepairReceipt:
    recovery_finding_id: UUID
    receipt_id: UUID
    result_code: str
    repaired_at: datetime

    def payload(self) -> dict[str, str]:
        return {
            "recovery_finding_id": str(self.recovery_finding_id),
            "receipt_id": str(self.receipt_id),
            "result_code": self.result_code,
            "repaired_at": self.repaired_at.isoformat(),
        }
