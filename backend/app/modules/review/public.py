"""Public import surface for review contracts."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    PageApproval,
    PanelCandidate,
    PanelCandidateSet,
    QualityFinding,
    ReviewDecision,
    ReviewSnapshot,
)


class ReviewFacade(Protocol):
    def get_review(self, candidate_set_id: str, version: int) -> ReviewSnapshot: ...


__all__ = [
    "PageApproval",
    "PanelCandidate",
    "PanelCandidateSet",
    "QualityFinding",
    "ReviewDecision",
    "ReviewFacade",
    "ReviewSnapshot",
]
