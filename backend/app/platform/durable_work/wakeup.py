from __future__ import annotations

import asyncio


class InProcessWorkerWakeup:
    """Best-effort wake signal; durable work rows remain the source of truth."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def notify(self) -> None:
        self._event.set()

    async def wait(self, timeout_seconds: float | None = None) -> None:
        if timeout_seconds is None:
            await self._event.wait()
        else:
            try:
                await asyncio.wait_for(self._event.wait(), timeout_seconds)
            except TimeoutError:
                return
        self._event.clear()
