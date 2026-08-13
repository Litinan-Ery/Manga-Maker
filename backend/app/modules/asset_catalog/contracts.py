from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetCatalogItemSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    library_item_id: str = Field(min_length=1, max_length=64)
    source_asset_version_id: str = Field(min_length=1, max_length=64)
    kind: Literal["character", "prop", "location", "panel"]
    name: str = Field(min_length=1, max_length=120)
    tags: tuple[str, ...] = Field(default=(), max_length=100)
    status: Literal["active", "archived"]
    revision: int = Field(ge=1)
