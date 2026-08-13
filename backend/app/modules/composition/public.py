"""Only supported cross-module import surface for composition."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    CreatePageRevisionCommandV1,
    PageDocumentSnapshotV1,
    PageVersionSnapshotV1,
)


class CompositionFacade(Protocol):
    def create_page_revision(
        self,
        command: CreatePageRevisionCommandV1,
    ) -> PageVersionSnapshotV1: ...


__all__ = [
    "CompositionFacade",
    "CreatePageRevisionCommandV1",
    "PageDocumentSnapshotV1",
    "PageVersionSnapshotV1",
]
