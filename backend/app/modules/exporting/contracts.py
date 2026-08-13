from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExportRevisionSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    export_revision_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    status: Literal["staging", "completed", "failed"]
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_sha256s: tuple[str, ...] = Field(default=(), max_length=20_000)
