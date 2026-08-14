from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.platform.durable_work.contracts import (
    EnqueueWorkRequest,
    WorkCommandReference,
    WorkExecutionSafety,
    WorkState,
)
from backend.app.platform.durable_work.leases import SQLiteWorkLeaseAdapter
from backend.app.platform.durable_work.outbox import (
    OutboxEventRequest,
    SafeEventAttribute,
    SQLiteOutboxStore,
)
from backend.app.platform.durable_work.recovery_probe import DurableRuntimeIntegrityProbe
from backend.app.platform.durable_work.retry import ExponentialBackoffPolicy
from backend.app.platform.durable_work.sqlite import (
    SQLiteDurableWorkSession,
    SQLiteDurableWorkUnitOfWork,
)
from backend.app.platform.recovery.contracts import RecoveryReportStatus, RecoveryTrigger
from backend.app.platform.recovery.coordinator import (
    RecoveryAcknowledgementRequiredError,
    RecoveryCoordinator,
    RecoveryFindingNotRepairableError,
)
from backend.app.shared_kernel import ArtifactRef, Sha256

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class SequentialIdFactory:
    def __init__(self, start: int = 60_000) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def request(
    key: str,
    *,
    execution_safety: WorkExecutionSafety = WorkExecutionSafety.LOCAL_IDEMPOTENT,
    requires_user_action: bool = False,
) -> EnqueueWorkRequest:
    return EnqueueWorkRequest(
        project_id="recovery-project",
        kind="recovery.test",
        idempotency_key=key,
        command=WorkCommandReference(
            contract="recovery.test.command",
            contract_version="1.0",
            aggregate_type="recovery_fixture",
            aggregate_id=f"aggregate-{key}",
            aggregate_version=1,
            payload_sha256=Sha256.digest(key.encode()),
        ),
        attempt_limit=3,
        not_before=NOW,
        execution_safety=execution_safety,
        requires_user_action=requires_user_action,
    )


def result_ref(seed: int) -> ArtifactRef:
    return ArtifactRef(
        artifact_type="recovery_result",
        artifact_id=UUID(int=seed),
        version=1,
        content_sha256=Sha256.digest(f"recovery-result-{seed}".encode()),
        schema_version="1.0",
    )


def leases(
    transaction: SQLiteDurableWorkSession,
    clock: MutableClock,
    ids: SequentialIdFactory,
) -> SQLiteWorkLeaseAdapter:
    return SQLiteWorkLeaseAdapter(
        transaction.connection,
        transaction.work,
        clock,
        ids,
        ExponentialBackoffPolicy(),
    )


def test_startup_reconciliation_fails_closed_and_repairs_only_after_acknowledgement(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "durable-recovery.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory()
    unit_of_work = SQLiteDurableWorkUnitOfWork(database, clock, ids)
    outbox = SQLiteOutboxStore(database, clock, ids)

    with unit_of_work.transaction() as transaction:
        idle = transaction.work.enqueue(
            request("idle-queued", requires_user_action=True)
        )
        local_expired = transaction.work.enqueue(request("local-expired"))
        external_expired = transaction.work.enqueue(
            request(
                "external-expired",
                execution_safety=WorkExecutionSafety.EXTERNAL_SIDE_EFFECT,
            )
        )
        orphan = transaction.work.enqueue(request("orphan-running"))
        completed_seed = transaction.work.enqueue(request("completed-missing-event"))

    with unit_of_work.transaction() as transaction:
        local_lease = leases(transaction, clock, ids).claim_next(
            "worker-local", timedelta(seconds=5)
        )
        assert local_lease is not None
    with unit_of_work.transaction() as transaction:
        external_lease = leases(transaction, clock, ids).claim_next(
            "worker-external", timedelta(seconds=5)
        )
        assert external_lease is not None
    assert local_lease.item.work_item_id == local_expired.work_item_id
    assert external_lease.item.work_item_id == external_expired.work_item_id

    with unit_of_work.transaction() as transaction:
        transaction.work.start(orphan.work_item_id, expected_revision=1)
        completed_start = transaction.work.start(
            completed_seed.work_item_id, expected_revision=1
        )
        completed = transaction.work.complete(
            completed_seed.work_item_id,
            expected_revision=completed_start.item.revision,
            work_attempt_id=completed_start.attempt.work_attempt_id,
            result_ref=result_ref(61_000),
            handler_version="recovery-fixture-v1",
        )

    with database.writer() as connection:
        unconfirmed_event = outbox.bind(connection).append(
            OutboxEventRequest(
                project_id="recovery-project",
                event_type="fixture.changed",
                event_version="1.0",
                aggregate_type="fixture",
                aggregate_id="fixture-unconfirmed",
                aggregate_version=1,
                aggregate_sha256=Sha256.digest(b"fixture-unconfirmed"),
                deduplication_key="fixture-unconfirmed",
                attributes=(SafeEventAttribute("state", "ready"),),
            )
        )
    sending_attempt = outbox.begin_publish(unconfirmed_event.event_id)
    clock.advance(timedelta(seconds=6))

    coordinator = RecoveryCoordinator(
        database,
        probes=(DurableRuntimeIntegrityProbe(clock, ids),),
        clock=clock,
        id_factory=ids,
    )
    report = coordinator.run(RecoveryTrigger.STARTUP)
    counts = dict(report.finding_counts)
    assert report.status is RecoveryReportStatus.NEEDS_ATTENTION
    assert report.external_requests_started == 0
    assert counts == {
        "DURABLE_COMPLETION_EVENT_MISSING": 1,
        "DURABLE_EXTERNAL_RESULT_UNKNOWN": 1,
        "DURABLE_LOCAL_RESUME_REQUIRED": 3,
        "OUTBOX_DELIVERY_UNCONFIRMED": 1,
    }

    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(idle.work_item_id).requires_user_action
        assert transaction.work.get(idle.work_item_id).attempts_started == 0
        assert transaction.work.get(local_expired.work_item_id).state is WorkState.QUEUED
        assert transaction.work.get(orphan.work_item_id).state is WorkState.QUEUED
        assert transaction.work.get(external_expired.work_item_id).state is WorkState.NEEDS_REVIEW
        assert transaction.work.get(completed.work_item_id).state is WorkState.COMPLETED

    repairable = next(finding for finding in report.findings if finding.repair_command)
    with pytest.raises(RecoveryAcknowledgementRequiredError):
        coordinator.repair(report.recovery_report_id, repairable.recovery_finding_id)
    acknowledged = coordinator.acknowledge(report.recovery_report_id)
    assert acknowledged.acknowledged_at is not None

    external_finding = next(
        finding
        for finding in report.findings
        if finding.code == "DURABLE_EXTERNAL_RESULT_UNKNOWN"
    )
    with pytest.raises(RecoveryFindingNotRepairableError):
        coordinator.repair(report.recovery_report_id, external_finding.recovery_finding_id)

    receipts = []
    for finding in report.findings:
        if finding.repair_command is None:
            continue
        receipt = coordinator.repair(report.recovery_report_id, finding.recovery_finding_id)
        receipts.append(receipt)
        assert coordinator.repair(
            report.recovery_report_id, finding.recovery_finding_id
        ) == receipt
    assert len(receipts) == 5

    with unit_of_work.transaction() as transaction:
        assert not transaction.work.get(idle.work_item_id).requires_user_action
        assert not transaction.work.get(local_expired.work_item_id).requires_user_action
        assert not transaction.work.get(orphan.work_item_id).requires_user_action
    with database.reader() as connection:
        attempt_state = connection.execute(
            """
            SELECT state FROM outbox_publish_attempts WHERE publish_attempt_id = ?
            """,
            (str(sending_attempt.publish_attempt_id),),
        ).fetchone()[0]
        completion_events = connection.execute(
            """
            SELECT COUNT(*) FROM outbox_events
            WHERE event_type = 'durable_work.completed' AND aggregate_id = ?
            """,
            (str(completed.work_item_id),),
        ).fetchone()[0]
    assert attempt_state == "failed"
    assert completion_events == 1
    assert coordinator.get(report.recovery_report_id).status is RecoveryReportStatus.NEEDS_ATTENTION


def test_app_restart_persists_safe_report_and_requires_explicit_api_recovery(
    tmp_path: Path,
) -> None:
    settings = Settings(app_data_dir=tmp_path / "app-data", environment="test")
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        first_headers = {
            "X-Manga-Maker-Session": first.app.state.local_session.token,
            "X-CSRF-Token": first.app.state.local_session.csrf_token,
        }
        unit_of_work = first.app.state.container.durable_work
        with unit_of_work.transaction() as transaction:
            queued = transaction.work.enqueue(request("restart-api"))
        assert first.get("/api/v1/system/durable-recovery").status_code == 401
        assert first.get(
            "/api/v1/system/durable-recovery", headers=first_headers
        ).status_code == 200

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        headers = {
            "X-Manga-Maker-Session": second.app.state.local_session.token,
            "X-CSRF-Token": second.app.state.local_session.csrf_token,
        }
        response = second.get("/api/v1/system/durable-recovery", headers=headers)
        assert response.status_code == 200
        report = response.json()
        assert report["trigger"] == "startup"
        assert report["external_requests_started"] == 0
        assert report["finding_counts"] == {"DURABLE_LOCAL_RESUME_REQUIRED": 1}
        assert "/Users/" not in response.text
        finding = report["findings"][0]
        repair_url = (
            f"/api/v1/system/durable-recovery/{report['recovery_report_id']}"
            f"/findings/{finding['recovery_finding_id']}/repair"
        )
        blocked = second.post(repair_url, headers=headers)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "RECOVERY_ACKNOWLEDGEMENT_REQUIRED"

        acknowledged = second.post(
            f"/api/v1/system/durable-recovery/{report['recovery_report_id']}/acknowledge",
            headers=headers,
        )
        assert acknowledged.status_code == 200
        repaired = second.post(repair_url, headers=headers)
        assert repaired.status_code == 200
        with second.app.state.container.durable_work.transaction() as transaction:
            restored = transaction.work.get(queued.work_item_id)
            assert restored.state is WorkState.QUEUED
            assert not restored.requires_user_action
            assert restored.attempts_started == 0
        assert second.app.state.container.durable_worker.stopped is False
