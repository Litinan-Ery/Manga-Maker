from __future__ import annotations

import sqlite3
from typing import Protocol

from .contracts import ProbeFinding, RecoveryTrigger


class IntegrityProbe(Protocol):
    name: str

    def reconcile(
        self, connection: sqlite3.Connection, trigger: RecoveryTrigger
    ) -> None: ...

    def inspect(self, connection: sqlite3.Connection) -> tuple[ProbeFinding, ...]: ...

    def repair(
        self, connection: sqlite3.Connection, finding: ProbeFinding
    ) -> str: ...
