from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ..ids import uuid7


class IdFactory(Protocol):
    def new(self) -> UUID: ...


class Uuid7IdFactory:
    def new(self) -> UUID:
        return uuid7()
