from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApprovedBibleSetSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    character_bible_version_id: str = Field(min_length=1, max_length=64)
    character_bible_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_bible_version_id: str = Field(min_length=1, max_length=64)
    style_bible_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    character_tag_bundle_version_id: str = Field(min_length=1, max_length=64)
    character_tag_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
