from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...shared_kernel import ArtifactRef
from .contracts import SafeWorkError, WorkExecutionSafety, WorkItemSnapshot, validate_safe_key


class WorkHandlerContext(Protocol):
    @property
    def external_request_started(self) -> bool: ...

    def mark_external_request_started(self) -> None: ...

    def renew_lease(self) -> None: ...


class WorkHandler(Protocol):
    async def __call__(
        self, context: WorkHandlerContext, item: WorkItemSnapshot
    ) -> ArtifactRef: ...


@dataclass(frozen=True, slots=True)
class RegisteredWorkHandler:
    kind: str
    handler_version: str
    execution_safety: WorkExecutionSafety
    handler: WorkHandler

    def __post_init__(self) -> None:
        validate_safe_key("kind", self.kind)
        validate_safe_key("handler_version", self.handler_version)


class HandlerRegistryError(RuntimeError):
    pass


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, RegisteredWorkHandler] = {}

    def register(self, registration: RegisteredWorkHandler) -> None:
        if registration.kind in self._handlers:
            raise HandlerRegistryError(f"handler kind {registration.kind!r} is already registered")
        self._handlers[registration.kind] = registration

    def resolve(self, kind: str) -> RegisteredWorkHandler | None:
        return self._handlers.get(kind)

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(self._handlers)


class RetryableHandlerFailure(RuntimeError):
    def __init__(self, error: SafeWorkError) -> None:
        if not error.retryable:
            raise ValueError("retryable handler failure requires retryable=True")
        super().__init__(error.message)
        self.error = error


class PermanentHandlerFailure(RuntimeError):
    def __init__(self, error: SafeWorkError) -> None:
        if error.retryable:
            raise ValueError("permanent handler failure requires retryable=False")
        super().__init__(error.message)
        self.error = error
