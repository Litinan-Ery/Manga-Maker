from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StoryboardVersionRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    storyboard_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool


class StoryboardPageSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    project_id: UUID
    chapter_id: UUID
    page_id: UUID
    storyboard: StoryboardVersionRefV1
    panel_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
