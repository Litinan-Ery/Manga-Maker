"""Only supported cross-module import surface for the asset catalog."""

from __future__ import annotations

from typing import Protocol

from .contracts import AssetCatalogItemSnapshotV1


class AssetCatalogFacade(Protocol):
    def list_items(self, project_id: str) -> tuple[AssetCatalogItemSnapshotV1, ...]: ...


__all__ = ["AssetCatalogFacade", "AssetCatalogItemSnapshotV1"]
