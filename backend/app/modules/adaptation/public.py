"""Only supported cross-module import surface for adaptation."""

from __future__ import annotations

from typing import Protocol

from .contracts import StoryboardPageSnapshotV1, StoryboardVersionRefV1


class AdaptationFacade(Protocol):
    def current_storyboard_ref(
        self,
        project_id: str,
        chapter_id: str,
    ) -> StoryboardVersionRefV1: ...

    def storyboard_page(
        self,
        project_id: str,
        storyboard_version_id: str,
        page_id: str,
    ) -> StoryboardPageSnapshotV1: ...


__all__ = ["AdaptationFacade", "StoryboardPageSnapshotV1", "StoryboardVersionRefV1"]
