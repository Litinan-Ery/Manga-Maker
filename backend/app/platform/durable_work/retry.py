from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol


class RetryPolicy(Protocol):
    def delay_for(self, attempt_ordinal: int) -> timedelta: ...


@dataclass(frozen=True, slots=True)
class ExponentialBackoffPolicy:
    base_delay: timedelta = timedelta(seconds=1)
    maximum_delay: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be positive")
        if self.maximum_delay < self.base_delay:
            raise ValueError("maximum_delay must be at least base_delay")

    def delay_for(self, attempt_ordinal: int) -> timedelta:
        if attempt_ordinal < 1:
            raise ValueError("attempt_ordinal must be positive")
        multiplier = 2 ** (attempt_ordinal - 1)
        calculated = timedelta(seconds=self.base_delay.total_seconds() * multiplier)
        return min(calculated, self.maximum_delay)
