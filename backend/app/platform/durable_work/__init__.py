"""Durable work contracts and in-memory test adapter."""

from .contracts import (
    EnqueueWorkRequest,
    LeasedWorkSnapshot,
    SafeWorkError,
    WorkAttemptSnapshot,
    WorkAttemptState,
    WorkCommandReference,
    WorkExecutionSafety,
    WorkHandlerReceiptSnapshot,
    WorkItemSnapshot,
    WorkLeaseSnapshot,
    WorkStartSnapshot,
    WorkState,
)
from .fake import InMemoryDurableWorkAdapter
from .ports import DurableWorkPort, WorkLeasePort

__all__ = [
    "DurableWorkPort",
    "EnqueueWorkRequest",
    "InMemoryDurableWorkAdapter",
    "LeasedWorkSnapshot",
    "SafeWorkError",
    "WorkAttemptSnapshot",
    "WorkAttemptState",
    "WorkCommandReference",
    "WorkExecutionSafety",
    "WorkHandlerReceiptSnapshot",
    "WorkItemSnapshot",
    "WorkLeasePort",
    "WorkLeaseSnapshot",
    "WorkStartSnapshot",
    "WorkState",
]
