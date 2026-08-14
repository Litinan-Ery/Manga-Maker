from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from ..bootstrap.dependencies import get_outbox_store, require_local_session
from ..platform.durable_work.outbox import SQLiteOutboxStore
from ..shared_kernel import canonical_json_bytes

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["events"])


@router.get("/events", response_class=StreamingResponse)
def replay_project_events(
    project_id: str,
    outbox: Annotated[SQLiteOutboxStore, Depends(get_outbox_store)],
    _authorized: Annotated[None, Depends(require_local_session)],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> StreamingResponse:
    events = outbox.replay(
        project_id,
        after_sequence=last_event_id or 0,
        limit=limit,
    )

    def stream() -> Iterator[bytes]:
        for event in events:
            data = canonical_json_bytes(event.public_payload()).decode("utf-8")
            yield (
                f"id: {event.project_sequence}\n"
                f"event: {event.event_type}\n"
                f"data: {data}\n\n"
            ).encode()
        yield b"retry: 2000\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )
