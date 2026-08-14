from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectSourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CreateProjectCommandV1(ProjectSourceContract):
    contract_version: Literal["1.0"] = "1.0"
    title: str = Field(min_length=1, max_length=200)


class ProjectSnapshotV1(ProjectSourceContract):
    contract_version: Literal["1.0"] = "1.0"
    project_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    status: Literal["draft", "active", "archived"]
    revision: int = Field(ge=1)
    created_at: str = Field(min_length=1, max_length=100)
    updated_at: str = Field(min_length=1, max_length=100)
