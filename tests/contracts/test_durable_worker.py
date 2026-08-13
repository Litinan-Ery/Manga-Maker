from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.database import Database
from backend.app.platform.durable_work.contracts import (
    EnqueueWorkRequest,
    SafeWorkError,
    WorkCommandReference,
    WorkExecutionSafety,
    WorkItemSnapshot,
    WorkState,
)
from backend.app.platform.durable_work.errors import WorkLeaseLostError
from backend.app.platform.durable_work.handlers import (
    HandlerRegistry,
    HandlerRegistryError,
    PermanentHandlerFailure,
    RegisteredWorkHandler,
    RetryableHandlerFailure,
    WorkHandlerContext,
)
from backend.app.platform.durable_work.leases import SQLiteWorkLeaseAdapter
from backend.app.platform.durable_work.retry import ExponentialBackoffPolicy
from backend.app.platform.durable_work.sqlite import (
    SQLiteDurableWorkSession,
    SQLiteDurableWorkUnitOfWork,
)
from backend.app.platform.durable_work.wakeup import InProcessWorkerWakeup
from backend.app.platform.durable_work.worker import (
    DurableWorker,
    WorkerOutcome,
)
from backend.app.shared_kernel import ArtifactRef, Sha256

NOW = datetime(2026, 8, 13, 7, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class SequentialIdFactory:
    def __init__(self, start: int = 1_000) -> None:
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
    attempt_limit: int = 2,
    handler_key: str | None = None,
) -> EnqueueWorkRequest:
    return EnqueueWorkRequest(
        project_id="worker-project",
        kind=f"handler.{handler_key or key}",
        idempotency_key=key,
        command=WorkCommandReference(
            contract="worker.test.command",
            contract_version="1.0",
            aggregate_type="test_aggregate",
            aggregate_id=f"aggregate-{key}",
            aggregate_version=1,
            payload_sha256=Sha256.digest(key.encode()),
        ),
        attempt_limit=attempt_limit,
        not_before=NOW,
        requires_user_action=requires_user_action,
        execution_safety=execution_safety,
    )


def result_ref(seed: int) -> ArtifactRef:
    return ArtifactRef(
        artifact_type="worker_test_result",
        artifact_id=UUID(int=seed),
        version=1,
        content_sha256=Sha256.digest(f"worker-result-{seed}".encode()),
        schema_version="1.0",
    )


def leases(
    transaction: SQLiteDurableWorkSession,
    clock: MutableClock,
    ids: SequentialIdFactory,
    policy: ExponentialBackoffPolicy,
) -> SQLiteWorkLeaseAdapter:
    return SQLiteWorkLeaseAdapter(
        transaction.connection,
        transaction.work,
        clock,
        ids,
        policy,
    )


def test_competing_workers_lease_once_then_expiry_allows_a_new_owner(tmp_path: Path) -> None:
    database = Database(tmp_path / "leases.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory()
    policy = ExponentialBackoffPolicy(timedelta(seconds=1), timedelta(seconds=4))
    unit_of_work = SQLiteDurableWorkUnitOfWork(database, clock, ids)

    with unit_of_work.transaction() as transaction:
        queued = transaction.work.enqueue(request("lease-race"))
        first = leases(transaction, clock, ids, policy).claim_next(
            "worker-a", timedelta(seconds=10)
        )
        assert first is not None
        assert leases(transaction, clock, ids, policy).claim_next(
            "worker-b", timedelta(seconds=10)
        ) is None

    clock.advance(timedelta(seconds=2))
    with unit_of_work.transaction() as transaction:
        renewed = leases(transaction, clock, ids, policy).renew(
            first.lease.lease_token, timedelta(seconds=10)
        )
        assert renewed.lease_revision == 2
        assert renewed.lease_owner == "worker-a"

    clock.advance(timedelta(seconds=11))
    with unit_of_work.transaction() as transaction:
        recovered = leases(transaction, clock, ids, policy).recover_expired()
        assert len(recovered) == 1
        assert recovered[0].state is WorkState.QUEUED
        with pytest.raises(WorkLeaseLostError):
            leases(transaction, clock, ids, policy).verify(first.lease.lease_token)

    clock.advance(timedelta(seconds=1))
    with unit_of_work.transaction() as transaction:
        second = leases(transaction, clock, ids, policy).claim_next(
            "worker-b", timedelta(seconds=10)
        )
        assert second is not None
        assert second.item.work_item_id == queued.work_item_id
        assert second.attempt.ordinal == 2
        assert second.lease.lease_owner == "worker-b"
        assert second.lease.lease_token != first.lease.lease_token


def test_two_threads_competing_for_one_item_create_only_one_active_lease(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lease-threads.db")
    database.migrate()
    clock = MutableClock()
    setup_ids = SequentialIdFactory(1_500)
    setup_uow = SQLiteDurableWorkUnitOfWork(database, clock, setup_ids)
    with setup_uow.transaction() as transaction:
        queued = transaction.work.enqueue(request("thread-race"))

    barrier = threading.Barrier(2)

    def claim(owner: str, id_start: int) -> object:
        worker_ids = SequentialIdFactory(id_start)
        unit_of_work = SQLiteDurableWorkUnitOfWork(database, clock, worker_ids)
        barrier.wait()
        with unit_of_work.transaction() as transaction:
            return leases(
                transaction,
                clock,
                worker_ids,
                ExponentialBackoffPolicy(),
            ).claim_next(owner, timedelta(seconds=10))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda arguments: claim(*arguments),
                (("worker-a", 1_600), ("worker-b", 1_700)),
            )
        )
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    with database.reader() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM worker_leases WHERE work_item_id = ?",
            (str(queued.work_item_id),),
        ).fetchone()[0]
    assert active == 1


def test_expired_external_attempt_moves_to_needs_review_and_is_not_reclaimed(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "external-lease.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory(2_000)
    policy = ExponentialBackoffPolicy()
    unit_of_work = SQLiteDurableWorkUnitOfWork(database, clock, ids)

    with unit_of_work.transaction() as transaction:
        queued = transaction.work.enqueue(
            request("external-expiry", execution_safety=WorkExecutionSafety.EXTERNAL_SIDE_EFFECT)
        )
        claimed = leases(transaction, clock, ids, policy).claim_next(
            "worker-a", timedelta(seconds=5)
        )
        assert claimed is not None and claimed.attempt.external_request_started

    clock.advance(timedelta(seconds=6))
    with unit_of_work.transaction() as transaction:
        recovered = leases(transaction, clock, ids, policy).recover_expired()
        assert recovered[0].state is WorkState.NEEDS_REVIEW
        assert recovered[0].last_safe_error is not None
        assert recovered[0].last_safe_error.code == "EXTERNAL_RESULT_UNKNOWN"
        assert leases(transaction, clock, ids, policy).claim_next(
            "worker-b", timedelta(seconds=5)
        ) is None
        assert transaction.work.get(queued.work_item_id).attempts_started == 1


class RetryOnceHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, context: WorkHandlerContext, _item: WorkItemSnapshot
    ) -> ArtifactRef:
        assert not context.external_request_started
        context.renew_lease()
        self.calls += 1
        if self.calls == 1:
            raise RetryableHandlerFailure(
                SafeWorkError("LOCAL_RETRY", "本地步骤稍后重试。", retryable=True)
            )
        return result_ref(3_000)


class PermanentFailureHandler:
    async def __call__(
        self, _context: WorkHandlerContext, _item: WorkItemSnapshot
    ) -> ArtifactRef:
        raise PermanentHandlerFailure(
            SafeWorkError("LOCAL_INVALID", "本地输入无法处理。")
        )


class ExternalUnknownHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, context: WorkHandlerContext, _item: WorkItemSnapshot
    ) -> ArtifactRef:
        self.calls += 1
        assert context.external_request_started
        raise RuntimeError("simulated crash after possible external request")


class SuccessHandler:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.calls = 0

    async def __call__(
        self, _context: WorkHandlerContext, _item: WorkItemSnapshot
    ) -> ArtifactRef:
        self.calls += 1
        return result_ref(self.seed)


def worker(
    database: Database,
    clock: MutableClock,
    ids: SequentialIdFactory,
    registry: HandlerRegistry,
    *,
    owner: str = "runtime-worker",
) -> tuple[DurableWorker, SQLiteDurableWorkUnitOfWork]:
    unit_of_work = SQLiteDurableWorkUnitOfWork(database, clock, ids)
    runtime = DurableWorker(
        owner=owner,
        unit_of_work=unit_of_work,
        handlers=registry,
        clock=clock,
        id_factory=ids,
        retry_policy=ExponentialBackoffPolicy(
            timedelta(seconds=1), timedelta(seconds=8)
        ),
        wakeup=InProcessWorkerWakeup(),
        lease_duration=timedelta(seconds=10),
    )
    return runtime, unit_of_work


def test_worker_retries_local_work_but_not_permanent_failure(tmp_path: Path) -> None:
    database = Database(tmp_path / "worker-retry.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory(4_000)
    registry = HandlerRegistry()
    retry_handler = RetryOnceHandler()
    registry.register(
        RegisteredWorkHandler(
            "handler.local-retry", "local-retry-v1",
            WorkExecutionSafety.LOCAL_IDEMPOTENT, retry_handler
        )
    )
    registry.register(
        RegisteredWorkHandler(
            "handler.permanent", "permanent-v1",
            WorkExecutionSafety.LOCAL_IDEMPOTENT, PermanentFailureHandler()
        )
    )
    runtime, unit_of_work = worker(database, clock, ids, registry)
    with unit_of_work.transaction() as transaction:
        retry_item = transaction.work.enqueue(request("local-retry"))
        permanent_item = transaction.work.enqueue(
            request("permanent", attempt_limit=3)
        )

    first = asyncio.run(runtime.run_once())
    assert first is not None and first.outcome is WorkerOutcome.RETRY_SCHEDULED
    assert asyncio.run(runtime.run_once()) is not None  # permanent work is independently claimable
    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(permanent_item.work_item_id).state is WorkState.FAILED
        assert transaction.work.get(permanent_item.work_item_id).attempts_started == 1
        assert transaction.work.get(retry_item.work_item_id).state is WorkState.QUEUED

    assert asyncio.run(runtime.run_once()) is None
    clock.advance(timedelta(seconds=1))
    completed = asyncio.run(runtime.run_once())
    assert completed is not None and completed.outcome is WorkerOutcome.COMPLETED
    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(retry_item.work_item_id).state is WorkState.COMPLETED
        assert len(transaction.work.list_attempts(retry_item.work_item_id)) == 2
    assert retry_handler.calls == 2


def test_external_unknown_user_action_pause_and_lost_wakeup_are_safe(tmp_path: Path) -> None:
    database = Database(tmp_path / "worker-safety.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory(5_000)
    registry = HandlerRegistry()
    external = ExternalUnknownHandler()
    local = SuccessHandler(5_500)
    registry.register(
        RegisteredWorkHandler(
            "handler.external", "external-v1",
            WorkExecutionSafety.EXTERNAL_SIDE_EFFECT, external
        )
    )
    registry.register(
        RegisteredWorkHandler(
            "handler.local", "local-v1", WorkExecutionSafety.LOCAL_IDEMPOTENT, local
        )
    )
    runtime, unit_of_work = worker(database, clock, ids, registry)
    with unit_of_work.transaction() as transaction:
        external_item = transaction.work.enqueue(
            request("external", execution_safety=WorkExecutionSafety.EXTERNAL_SIDE_EFFECT)
        )
        gated = transaction.work.enqueue(
            request(
                "external-gated",
                execution_safety=WorkExecutionSafety.EXTERNAL_SIDE_EFFECT,
                requires_user_action=True,
                handler_key="external",
            )
        )

    reviewed = asyncio.run(runtime.run_once())
    assert reviewed is not None and reviewed.outcome is WorkerOutcome.NEEDS_REVIEW
    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(external_item.work_item_id).state is WorkState.NEEDS_REVIEW
        assert transaction.work.get(gated.work_item_id).attempts_started == 0
    assert asyncio.run(runtime.run_once()) is None
    assert external.calls == 1

    runtime.pause()
    with unit_of_work.transaction() as transaction:
        local_item = transaction.work.enqueue(request("local"))
    assert asyncio.run(runtime.run_once()) is None
    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(local_item.work_item_id).attempts_started == 0

    runtime.resume()
    # No background task is required: even if a wake signal is lost, the persisted row is found.
    completed = asyncio.run(runtime.run_once())
    assert completed is not None and completed.outcome is WorkerOutcome.COMPLETED
    assert local.calls == 1


def test_pause_or_cancel_during_local_handler_prevents_new_claims_or_overwrite(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "worker-control.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory(6_000)
    registry = HandlerRegistry()
    runtime, unit_of_work = worker(database, clock, ids, registry)

    class PauseHandler:
        async def __call__(
            self, _context: WorkHandlerContext, _item: WorkItemSnapshot
        ) -> ArtifactRef:
            runtime.pause()
            return result_ref(6_500)

    registry.register(
        RegisteredWorkHandler(
            "handler.pause", "pause-v1", WorkExecutionSafety.LOCAL_IDEMPOTENT,
            PauseHandler()
        )
    )
    with unit_of_work.transaction() as transaction:
        paused_item = transaction.work.enqueue(request("pause"))
        waiting_item = transaction.work.enqueue(request("waiting"))

    paused_result = asyncio.run(runtime.run_once())
    assert paused_result is not None and paused_result.outcome is WorkerOutcome.COMPLETED
    assert runtime.paused
    assert asyncio.run(runtime.run_once()) is None
    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(paused_item.work_item_id).state is WorkState.COMPLETED
        assert transaction.work.get(waiting_item.work_item_id).attempts_started == 0
        transaction.work.cancel(waiting_item.work_item_id, expected_revision=1)

    cancel_registry = HandlerRegistry()
    cancel_runtime, cancel_uow = worker(
        database, clock, ids, cancel_registry, owner="cancel-worker"
    )

    class CancelHandler:
        async def __call__(
            self, _context: WorkHandlerContext, item: WorkItemSnapshot
        ) -> ArtifactRef:
            with cancel_uow.transaction() as transaction:
                current = transaction.work.get(item.work_item_id)
                transaction.work.cancel(current.work_item_id, expected_revision=current.revision)
            return result_ref(6_600)

    cancel_registry.register(
        RegisteredWorkHandler(
            "handler.cancel", "cancel-v1", WorkExecutionSafety.LOCAL_IDEMPOTENT,
            CancelHandler()
        )
    )
    with cancel_uow.transaction() as transaction:
        canceled_item = transaction.work.enqueue(request("cancel"))
    canceled_result = asyncio.run(cancel_runtime.run_once())
    assert canceled_result is not None and canceled_result.outcome is WorkerOutcome.CANCELED
    with cancel_uow.transaction() as transaction:
        assert transaction.work.get(canceled_item.work_item_id).state is WorkState.CANCELED
        assert transaction.work.get(canceled_item.work_item_id).result_ref is None


def test_handler_registry_rejects_duplicate_kinds() -> None:
    registry = HandlerRegistry()
    registration = RegisteredWorkHandler(
        "handler.duplicate",
        "duplicate-v1",
        WorkExecutionSafety.LOCAL_IDEMPOTENT,
        SuccessHandler(7_000),
    )
    registry.register(registration)
    with pytest.raises(HandlerRegistryError, match="already registered"):
        registry.register(registration)


def test_worker_startup_scans_persisted_rows_when_the_wakeup_signal_was_lost(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lost-wakeup.db")
    database.migrate()
    clock = MutableClock()
    ids = SequentialIdFactory(8_000)
    preexisting_uow = SQLiteDurableWorkUnitOfWork(database, clock, ids)
    with preexisting_uow.transaction() as transaction:
        queued = transaction.work.enqueue(request("startup-scan"))

    registry = HandlerRegistry()
    runtime, unit_of_work = worker(database, clock, ids, registry)

    class StopAfterSuccess:
        async def __call__(
            self, _context: WorkHandlerContext, _item: WorkItemSnapshot
        ) -> ArtifactRef:
            runtime.stop()
            return result_ref(8_500)

    registry.register(
        RegisteredWorkHandler(
            "handler.startup-scan",
            "startup-scan-v1",
            WorkExecutionSafety.LOCAL_IDEMPOTENT,
            StopAfterSuccess(),
        )
    )

    asyncio.run(asyncio.wait_for(runtime.serve(), timeout=1))
    with unit_of_work.transaction() as transaction:
        assert transaction.work.get(queued.work_item_id).state is WorkState.COMPLETED
