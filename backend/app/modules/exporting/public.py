"""Only supported cross-module import surface for exporting."""

from __future__ import annotations

from typing import Protocol

from .contracts import ExportRevisionSnapshotV1


class ExportingFacade(Protocol):
    def get_export(
        self,
        project_id: str,
        export_revision_id: str,
    ) -> ExportRevisionSnapshotV1: ...


__all__ = ["ExportRevisionSnapshotV1", "ExportingFacade"]
