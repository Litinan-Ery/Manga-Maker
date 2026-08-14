"""Public import surface for text execution contracts."""

from __future__ import annotations

from typing import Protocol

from .contracts import TextStageRun, TokenBudget, TruncationReport


class TextExecutionFacade(Protocol):
    def get_stage_run(self, run_id: str, version: int) -> TextStageRun: ...


__all__ = ["TextExecutionFacade", "TextStageRun", "TokenBudget", "TruncationReport"]
