from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import SafeWorkError
from .outbox import OutboxEventSnapshot, SQLiteOutboxStore


class OutboxSink(Protocol):
    async def publish(self, event: OutboxEventSnapshot) -> None: ...


@dataclass(frozen=True, slots=True)
class PublishBatchResult:
    attempted: int
    published: int
    failed: int


class OutboxPublisher:
    """Best-effort publisher; event_id is the mandatory downstream idempotency key."""

    def __init__(self, store: SQLiteOutboxStore, sink: OutboxSink) -> None:
        self._store = store
        self._sink = sink

    async def publish_pending(self, *, limit: int = 100) -> PublishBatchResult:
        events = self._store.list_pending(limit=limit)
        published = 0
        failed = 0
        for event in events:
            attempt = self._store.begin_publish(event.event_id)
            try:
                await self._sink.publish(event)
            except Exception:
                failed += 1
                self._store.fail_publish(
                    attempt.publish_attempt_id,
                    SafeWorkError(
                        "OUTBOX_PUBLISH_FAILED",
                        "事件发布失败，将按同一 event_id 重试。",
                        retryable=True,
                    ),
                )
            else:
                self._store.confirm_publish(attempt.publish_attempt_id)
                published += 1
        return PublishBatchResult(len(events), published, failed)
