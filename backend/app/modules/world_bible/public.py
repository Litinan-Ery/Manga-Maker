"""Only supported cross-module import surface for world bible."""

from __future__ import annotations

from typing import Protocol

from .contracts import ApprovedBibleSetSnapshotV1


class WorldBibleFacade(Protocol):
    def approved_bible_set(
        self,
        project_id: str,
        chapter_id: str,
    ) -> ApprovedBibleSetSnapshotV1: ...


__all__ = ["ApprovedBibleSetSnapshotV1", "WorldBibleFacade"]
