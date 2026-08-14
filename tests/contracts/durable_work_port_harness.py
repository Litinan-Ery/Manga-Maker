from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

import pytest

from backend.app.platform.durable_work.contracts import (
    EnqueueWorkRequest,
    SafeWorkError,
    WorkAttemptState,
    WorkCommandReference,
    WorkState,
)
from backend.app.platform.durable_work.errors import (
    IdempotencyConflictError,
    InvalidWorkTransitionError,
    WorkAttemptLimitExceededError,
    WorkRevisionConflictError,
)
from backend.app.platform.durable_work.ports import DurableWorkPort
from backend.app.shared_kernel import ArtifactRef, Sha256


def work_request(
    now: datetime,
    idempotency_key: str,
    *,
    attempt_limit: int = 2,
    requires_user_action: bool = False,
) -> EnqueueWorkRequest:
    return EnqueueWorkRequest(
        project_id="project-contract",
        kind="quality.run",
        idempotency_key=idempotency_key,
        command=WorkCommandReference(
            contract="quality.run.command",
            contract_version="1.0",
            aggregate_type="panel_candidate_set",
            aggregate_id=f"aggregate-{idempotency_key}",
            aggregate_version=1,
            payload_sha256=Sha256.digest(f"payload:{idempotency_key}".encode()),
        ),
        attempt_limit=attempt_limit,
        not_before=now,
        requires_user_action=requires_user_action,
    )


def result_ref(seed: int) -> ArtifactRef:
    return ArtifactRef(
        artifact_type="quality_run",
        artifact_id=UUID(int=seed),
        version=1,
        content_sha256=Sha256.digest(f"result:{seed}".encode()),
        schema_version="1.0",
    )


def assert_durable_work_port_contract(port: DurableWorkPort, now: datetime) -> None:
    request = work_request(now, "complete-once")
    queued = port.enqueue(request)
    assert port.enqueue(request) == queued
    assert queued.state is WorkState.QUEUED
    assert queued.revision == 1
    assert queued.attempts_started == 0

    conflicting = replace(
        request,
        command=replace(request.command, aggregate_version=2),
    )
    with pytest.raises(IdempotencyConflictError):
        port.enqueue(conflicting)
    with pytest.raises(WorkRevisionConflictError):
        port.start(queued.work_item_id, expected_revision=99)

    started = port.start(queued.work_item_id, expected_revision=1)
    assert started.item.state is WorkState.RUNNING
    assert started.item.revision == 2
    assert started.item.attempts_started == 1
    assert started.attempt.ordinal == 1
    assert started.attempt.state is WorkAttemptState.RUNNING
    assert not started.attempt.external_request_started

    result = result_ref(900)
    completed = port.complete(
        queued.work_item_id,
        expected_revision=2,
        work_attempt_id=started.attempt.work_attempt_id,
        result_ref=result,
        handler_version="quality-handler-v1",
    )
    assert completed.state is WorkState.COMPLETED
    assert completed.revision == 3
    assert completed.result_ref == result
    assert port.list_attempts(queued.work_item_id)[0].state is WorkAttemptState.COMPLETED
    receipts = port.list_handler_receipts(queued.work_item_id)
    assert len(receipts) == 1
    assert receipts[0].completed_revision == 3
    assert receipts[0].result_content_sha256 == result.content_sha256
    with pytest.raises(InvalidWorkTransitionError):
        port.complete(
            queued.work_item_id,
            expected_revision=3,
            work_attempt_id=started.attempt.work_attempt_id,
            result_ref=result,
            handler_version="quality-handler-v1",
        )

    cancel_queued = port.enqueue(work_request(now, "cancel-before-complete"))
    cancel_started = port.start(cancel_queued.work_item_id, expected_revision=1)
    canceled = port.cancel(cancel_queued.work_item_id, expected_revision=2)
    assert canceled.state is WorkState.CANCELED
    assert port.list_attempts(canceled.work_item_id)[0].state is WorkAttemptState.CANCELED
    with pytest.raises(InvalidWorkTransitionError):
        port.complete(
            canceled.work_item_id,
            expected_revision=3,
            work_attempt_id=cancel_started.attempt.work_attempt_id,
            result_ref=result_ref(901),
            handler_version="quality-handler-v1",
        )

    limited = port.enqueue(work_request(now, "attempt-limit", attempt_limit=1))
    limited_start = port.start(limited.work_item_id, expected_revision=1)
    failed = port.fail(
        limited.work_item_id,
        expected_revision=2,
        work_attempt_id=limited_start.attempt.work_attempt_id,
        error=SafeWorkError("TEMPORARY_FAILURE", "暂时无法完成。", retryable=True),
    )
    assert failed.state is WorkState.FAILED
    assert failed.attempts_started == failed.attempt_limit == 1
    assert failed.last_safe_error is not None and failed.last_safe_error.retryable
    with pytest.raises(WorkAttemptLimitExceededError):
        port.start(failed.work_item_id, expected_revision=3)
