"""Only supported cross-module import surface for project source."""

from __future__ import annotations

from typing import Protocol

from .contracts import CreateProjectCommandV1, ProjectSnapshotV1


class ProjectSourceFacade(Protocol):
    def create_project(self, command: CreateProjectCommandV1) -> ProjectSnapshotV1: ...

    def list_projects(self) -> tuple[ProjectSnapshotV1, ...]: ...


__all__ = ["CreateProjectCommandV1", "ProjectSnapshotV1", "ProjectSourceFacade"]
