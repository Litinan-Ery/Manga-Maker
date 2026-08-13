from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from ...shared_kernel import Clock, IdFactory, canonical_json_bytes
from .contracts import (
    ProbeFinding,
    RecoveryFindingSnapshot,
    RecoveryFindingStatus,
    RecoveryRepairReceipt,
    RecoveryReportSnapshot,
    RecoveryReportStatus,
    RecoverySeverity,
    RecoveryTrigger,
)
from .ports import IntegrityProbe


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recovery timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("recovery timestamp is not timezone-aware")
    return _utc(parsed)


class RecoveryDatabase(Protocol):
    def reader(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def writer(self) -> AbstractContextManager[sqlite3.Connection]: ...


class RecoveryCoordinatorError(RuntimeError):
    pass


class RecoveryReportNotFoundError(RecoveryCoordinatorError):
    pass


class RecoveryAcknowledgementRequiredError(RecoveryCoordinatorError):
    pass


class RecoveryFindingNotRepairableError(RecoveryCoordinatorError):
    pass


class RecoveryCoordinator:
    """Aggregates owner probes and delegates every mutation back to the owning probe."""

    def __init__(
        self,
        database: RecoveryDatabase,
        probes: tuple[IntegrityProbe, ...],
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        names = [probe.name for probe in probes]
        if len(names) != len(set(names)):
            raise ValueError("integrity probe names must be unique")
        self._database = database
        self._probes = {probe.name: probe for probe in probes}
        self._clock = clock
        self._id_factory = id_factory

    def run(self, trigger: RecoveryTrigger) -> RecoveryReportSnapshot:
        with self._database.writer() as connection:
            for probe in self._probes.values():
                probe.reconcile(connection, trigger)
            findings = tuple(
                finding
                for probe in self._probes.values()
                for finding in probe.inspect(connection)
            )
            report_id = self._id_factory.new()
            created_at = _utc(self._clock.now())
            counts = Counter(finding.code for finding in findings)
            summary = {
                "finding_counts": dict(sorted(counts.items())),
                "external_requests_started": 0,
            }
            status = (
                RecoveryReportStatus.HEALTHY
                if not findings
                else RecoveryReportStatus.NEEDS_ATTENTION
            )
            connection.execute(
                """
                INSERT INTO recovery_reports(
                    recovery_report_id, trigger, status, summary_json,
                    external_requests_started, created_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    str(report_id),
                    trigger.value,
                    status.value,
                    canonical_json_bytes(summary).decode("utf-8"),
                    _iso(created_at),
                ),
            )
            for finding in findings:
                connection.execute(
                    """
                    INSERT INTO recovery_findings(
                        recovery_finding_id, recovery_report_id, owner, code, severity,
                        subject_type, subject_id, repair_command
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(self._id_factory.new()),
                        str(report_id),
                        finding.owner,
                        finding.code,
                        finding.severity.value,
                        finding.subject_type,
                        finding.subject_id,
                        finding.repair_command,
                    ),
                )
            return self._get(connection, report_id)

    def latest(self) -> RecoveryReportSnapshot | None:
        with self._database.reader() as connection:
            row = connection.execute(
                """
                SELECT recovery_report_id FROM recovery_reports
                ORDER BY created_at DESC, recovery_report_id DESC LIMIT 1
                """
            ).fetchone()
            return self._get(connection, UUID(str(row[0]))) if row is not None else None

    def get(self, report_id: UUID) -> RecoveryReportSnapshot:
        with self._database.reader() as connection:
            return self._get(connection, report_id)

    def acknowledge(self, report_id: UUID) -> RecoveryReportSnapshot:
        with self._database.writer() as connection:
            report = self._get(connection, report_id)
            if report.acknowledged_at is None:
                connection.execute(
                    "UPDATE recovery_reports SET acknowledged_at = ? WHERE recovery_report_id = ?",
                    (_iso(self._clock.now()), str(report_id)),
                )
            return self._get(connection, report_id)

    def repair(
        self, report_id: UUID, finding_id: UUID
    ) -> RecoveryRepairReceipt:
        with self._database.writer() as connection:
            report = self._get(connection, report_id)
            if report.acknowledged_at is None:
                raise RecoveryAcknowledgementRequiredError(
                    "recovery report must be acknowledged before repair"
                )
            existing = connection.execute(
                """
                SELECT * FROM recovery_repair_receipts WHERE recovery_finding_id = ?
                """,
                (str(finding_id),),
            ).fetchone()
            if existing is not None:
                return self._receipt(existing)
            finding_row = connection.execute(
                """
                SELECT * FROM recovery_findings
                WHERE recovery_report_id = ? AND recovery_finding_id = ?
                """,
                (str(report_id), str(finding_id)),
            ).fetchone()
            if finding_row is None:
                raise RecoveryReportNotFoundError("recovery finding was not found")
            finding = self._probe_finding(finding_row)
            if finding.repair_command is None:
                raise RecoveryFindingNotRepairableError("recovery finding has no repair command")
            probe = self._probes.get(finding.owner)
            if probe is None:
                raise RecoveryFindingNotRepairableError(
                    f"recovery probe {finding.owner!r} is not registered"
                )
            result_code = probe.repair(connection, finding)
            receipt_id = self._id_factory.new()
            repaired_at = _utc(self._clock.now())
            connection.execute(
                """
                INSERT INTO recovery_repair_receipts(
                    recovery_finding_id, receipt_id, result_code, repaired_at
                ) VALUES (?, ?, ?, ?)
                """,
                (str(finding_id), str(receipt_id), result_code, _iso(repaired_at)),
            )
            connection.execute(
                "UPDATE recovery_findings SET status = 'repaired' WHERE recovery_finding_id = ?",
                (str(finding_id),),
            )
            remaining = connection.execute(
                """
                SELECT COUNT(*) FROM recovery_findings
                WHERE recovery_report_id = ? AND status = 'open'
                """,
                (str(report_id),),
            ).fetchone()[0]
            if int(remaining) == 0:
                connection.execute(
                    "UPDATE recovery_reports SET status = 'repaired' WHERE recovery_report_id = ?",
                    (str(report_id),),
                )
            return RecoveryRepairReceipt(finding_id, receipt_id, result_code, repaired_at)

    @staticmethod
    def _get(
        connection: sqlite3.Connection, report_id: UUID
    ) -> RecoveryReportSnapshot:
        row = connection.execute(
            "SELECT * FROM recovery_reports WHERE recovery_report_id = ?",
            (str(report_id),),
        ).fetchone()
        if row is None:
            raise RecoveryReportNotFoundError(f"recovery report {report_id} was not found")
        finding_rows = connection.execute(
            """
            SELECT * FROM recovery_findings
            WHERE recovery_report_id = ?
            ORDER BY severity DESC, code, subject_id
            """,
            (str(report_id),),
        ).fetchall()
        summary = json.loads(str(row["summary_json"]))
        acknowledged = row["acknowledged_at"]
        return RecoveryReportSnapshot(
            recovery_report_id=report_id,
            trigger=RecoveryTrigger(str(row["trigger"])),
            status=RecoveryReportStatus(str(row["status"])),
            created_at=_datetime(row["created_at"]),
            acknowledged_at=_datetime(acknowledged) if acknowledged is not None else None,
            finding_counts=tuple(
                (str(code), int(count))
                for code, count in sorted(summary["finding_counts"].items())
            ),
            findings=tuple(
                RecoveryFindingSnapshot(
                    recovery_finding_id=UUID(str(finding["recovery_finding_id"])),
                    owner=str(finding["owner"]),
                    code=str(finding["code"]),
                    severity=RecoverySeverity(str(finding["severity"])),
                    subject_type=str(finding["subject_type"]),
                    subject_id=str(finding["subject_id"]),
                    repair_command=(
                        str(finding["repair_command"])
                        if finding["repair_command"] is not None
                        else None
                    ),
                    status=RecoveryFindingStatus(str(finding["status"])),
                )
                for finding in finding_rows
            ),
            external_requests_started=int(row["external_requests_started"]),
        )

    @staticmethod
    def _probe_finding(row: sqlite3.Row) -> ProbeFinding:
        repair_command = row["repair_command"]
        return ProbeFinding(
            owner=str(row["owner"]),
            code=str(row["code"]),
            severity=RecoverySeverity(str(row["severity"])),
            subject_type=str(row["subject_type"]),
            subject_id=str(row["subject_id"]),
            repair_command=(str(repair_command) if repair_command is not None else None),
        )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> RecoveryRepairReceipt:
        return RecoveryRepairReceipt(
            recovery_finding_id=UUID(str(row["recovery_finding_id"])),
            receipt_id=UUID(str(row["receipt_id"])),
            result_code=str(row["result_code"]),
            repaired_at=_datetime(row["repaired_at"]),
        )
