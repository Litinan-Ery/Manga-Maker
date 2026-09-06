from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...platform.context_budget import ContextObservation


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    usage: bool = False
    compaction: bool = False
    context_replacement: bool = False


class HostFailure(Exception):
    def __init__(self, *, retryable: bool = True) -> None:
        super().__init__("Context host operation failed")
        self.retryable = retryable


class ContextHostPort(Protocol):
    def capabilities(self) -> HostCapabilities: ...

    def compact(self, *, checkpoint_id: str, request_id: str) -> ContextObservation: ...


class LocalHandoffHost:
    """The app has no authority to rewrite an external Codex task's history."""

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities()

    def compact(self, *, checkpoint_id: str, request_id: str) -> ContextObservation:
        raise HostFailure(retryable=False)
