from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from ...shared_kernel import ArtifactRef, Clock, IdFactory
from .contracts import (
    LeasedWorkSnapshot,
    SafeWorkError,
    WorkState,
    validate_safe_key,
)
from .errors import WorkLeaseLostError
from .handlers import (
    HandlerRegistry,
    PermanentHandlerFailure,
    RegisteredWorkHandler,
    RetryableHandlerFailure,
    WorkHandlerContext,
)
from .leases import SQLiteWorkLeaseAdapter
from .retry import RetryPolicy
from .sqlite import SQLiteDurableWorkSession, SQLiteDurableWorkUnitOfWork
from .wakeup import InProcessWorkerWakeup


class WorkerOutcome(StrEnum):
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    CANCELED = "canceled"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    work_item_id: UUID
    outcome: WorkerOutcome


class SQLiteWorkHandlerContext(WorkHandlerContext):
    def __init__(
        self,
        worker: DurableWorker,
        lease_token: UUID,
        *,
        external_request_started: bool,
    ) -> None:
        self._worker = worker
        self._lease_token = lease_token
        self._external_request_started = external_request_started

    @property
    def external_request_started(self) -> bool:
        return self._external_request_started

    def mark_external_request_started(self) -> None:
        self._worker.mark_external_request_started(self._lease_token)
        self._external_request_started = True

    def renew_lease(self) -> None:
        self._worker.renew_lease(self._lease_token)


class DurableWorker:
    """Single-worker runtime; every durable transition remains in SQLite."""

    def __init__(
        self,
        *,
        owner: str,
        unit_of_work: SQLiteDurableWorkUnitOfWork,
        handlers: HandlerRegistry,
        clock: Clock,
        id_factory: IdFactory,
        retry_policy: RetryPolicy,
        wakeup: InProcessWorkerWakeup,
        lease_duration: timedelta = timedelta(seconds=30),
        idle_poll_interval: timedelta = timedelta(seconds=1),
    ) -> None:
        validate_safe_key("worker owner", owner)
        if lease_duration <= timedelta(0) or lease_duration > timedelta(minutes=15):
            raise ValueError("lease_duration must be positive and no longer than 15 minutes")
        if idle_poll_interval <= timedelta(0):
            raise ValueError("idle_poll_interval must be positive")
        self.owner = owner
        self._unit_of_work = unit_of_work
        self._handlers = handlers
        self._clock = clock
        self._id_factory = id_factory
        self._retry_policy = retry_policy
        self._wakeup = wakeup
        self._lease_duration = lease_duration
        self._idle_poll_interval = idle_poll_interval
        self._paused = False
        self._stopped = False
        self._run_lock = asyncio.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stopped(self) -> bool:
        return self._stopped

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if self._stopped:
            raise RuntimeError("stopped worker cannot be resumed")
        self._paused = False
        self._wakeup.notify()

    def stop(self) -> None:
        self._stopped = True
        self._wakeup.notify()

    def wake(self) -> None:
        self._wakeup.notify()

    async def run_once(self) -> WorkerRunResult | None:
        async with self._run_lock:
            if self._paused or self._stopped:
                return None
            with self._unit_of_work.transaction() as transaction:
                leases = self._leases(transaction)
                leases.recover_expired()
                claimed = leases.claim_next(self.owner, self._lease_duration)
            if claimed is None:
                return None

            registration = self._handlers.resolve(claimed.item.kind)
            if registration is None:
                return self._fail_known(
                    claimed,
                    SafeWorkError(
                        "HANDLER_NOT_REGISTERED",
                        "没有为该工作类型注册处理器。",
                    ),
                )
            if registration.execution_safety is not claimed.item.execution_safety:
                return self._fail_known(
                    claimed,
                    SafeWorkError(
                        "HANDLER_SAFETY_MISMATCH",
                        "工作与处理器的执行安全级别不一致。",
                    ),
                )

            context = SQLiteWorkHandlerContext(
                self,
                claimed.lease.lease_token,
                external_request_started=claimed.attempt.external_request_started,
            )
            try:
                result_ref = await registration.handler(context, claimed.item)
            except RetryableHandlerFailure as exc:
                if context.external_request_started:
                    return self._needs_review(claimed, exc.error)
                return self._fail_retryable(claimed, exc.error)
            except PermanentHandlerFailure as exc:
                return self._fail_known(claimed, exc.error)
            except Exception:
                if context.external_request_started:
                    return self._needs_review(
                        claimed,
                        SafeWorkError(
                            "EXTERNAL_RESULT_UNKNOWN",
                            "外部处理器中断，结果未知，需要人工检查。",
                        ),
                    )
                return self._fail_retryable(
                    claimed,
                    SafeWorkError(
                        "HANDLER_UNEXPECTED",
                        "本地处理器意外中断。",
                        retryable=True,
                    ),
                )
            return self._complete(claimed, registration, result_ref)

    async def serve(self) -> None:
        while not self._stopped:
            if self._paused:
                await self._wakeup.wait()
                continue
            result = await self.run_once()
            if result is None:
                await self._wakeup.wait(self._idle_poll_interval.total_seconds())

    def renew_lease(self, lease_token: UUID) -> None:
        with self._unit_of_work.transaction() as transaction:
            self._leases(transaction).renew(lease_token, self._lease_duration)

    def mark_external_request_started(self, lease_token: UUID) -> None:
        with self._unit_of_work.transaction() as transaction:
            self._leases(transaction).mark_external_request_started(lease_token)

    def _complete(
        self,
        claimed: LeasedWorkSnapshot,
        registration: RegisteredWorkHandler,
        result_ref: ArtifactRef,
    ) -> WorkerRunResult:
        try:
            with self._unit_of_work.transaction() as transaction:
                leases = self._leases(transaction)
                leases.verify(claimed.lease.lease_token)
                current = transaction.work.get(claimed.item.work_item_id)
                if current.state is WorkState.CANCELED:
                    leases.release(claimed.lease.lease_token)
                    return WorkerRunResult(current.work_item_id, WorkerOutcome.CANCELED)
                completed = transaction.work.complete(
                    current.work_item_id,
                    expected_revision=current.revision,
                    work_attempt_id=claimed.attempt.work_attempt_id,
                    result_ref=result_ref,
                    handler_version=registration.handler_version,
                )
                leases.release(claimed.lease.lease_token)
                return WorkerRunResult(completed.work_item_id, WorkerOutcome.COMPLETED)
        except WorkLeaseLostError:
            return WorkerRunResult(claimed.item.work_item_id, WorkerOutcome.LEASE_LOST)

    def _fail_retryable(
        self, claimed: LeasedWorkSnapshot, error: SafeWorkError
    ) -> WorkerRunResult:
        retry_at = self._clock.now() + self._retry_policy.delay_for(
            claimed.attempt.ordinal
        )
        return self._fail(claimed, error, retry_not_before=retry_at)

    def _fail_known(
        self, claimed: LeasedWorkSnapshot, error: SafeWorkError
    ) -> WorkerRunResult:
        return self._fail(claimed, error, retry_not_before=None)

    def _fail(
        self,
        claimed: LeasedWorkSnapshot,
        error: SafeWorkError,
        *,
        retry_not_before: datetime | None,
    ) -> WorkerRunResult:
        try:
            with self._unit_of_work.transaction() as transaction:
                leases = self._leases(transaction)
                leases.verify(claimed.lease.lease_token)
                current = transaction.work.get(claimed.item.work_item_id)
                if current.state is WorkState.CANCELED:
                    leases.release(claimed.lease.lease_token)
                    return WorkerRunResult(current.work_item_id, WorkerOutcome.CANCELED)
                failed = transaction.work.fail(
                    current.work_item_id,
                    expected_revision=current.revision,
                    work_attempt_id=claimed.attempt.work_attempt_id,
                    error=error,
                    retry_not_before=retry_not_before,
                )
                leases.release(claimed.lease.lease_token)
                outcome = (
                    WorkerOutcome.RETRY_SCHEDULED
                    if failed.state is WorkState.QUEUED
                    else WorkerOutcome.FAILED
                )
                return WorkerRunResult(failed.work_item_id, outcome)
        except WorkLeaseLostError:
            return WorkerRunResult(claimed.item.work_item_id, WorkerOutcome.LEASE_LOST)

    def _needs_review(
        self, claimed: LeasedWorkSnapshot, error: SafeWorkError
    ) -> WorkerRunResult:
        try:
            with self._unit_of_work.transaction() as transaction:
                reviewed = self._leases(transaction).move_to_needs_review(
                    claimed.lease.lease_token,
                    SafeWorkError(error.code, error.message, retryable=False),
                )
                return WorkerRunResult(reviewed.work_item_id, WorkerOutcome.NEEDS_REVIEW)
        except WorkLeaseLostError:
            return WorkerRunResult(claimed.item.work_item_id, WorkerOutcome.LEASE_LOST)

    def _leases(self, transaction: SQLiteDurableWorkSession) -> SQLiteWorkLeaseAdapter:
        return SQLiteWorkLeaseAdapter(
            transaction.connection,
            transaction.work,
            self._clock,
            self._id_factory,
            self._retry_policy,
        )
