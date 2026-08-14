from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from ...shared_kernel import canonical_json_bytes
from .errors import LayoutSnapshotIntegrityError


class LayoutWorkspaceSnapshotStore:
    """Append-only canonical JSON snapshots below each local project workspace."""

    def __init__(self, projects_dir: Path) -> None:
        self._projects_dir = projects_dir.resolve()

    def layout_relative_path(
        self,
        page_layout_draft_id: UUID,
        version: int,
        page_layout_draft_version_id: UUID,
    ) -> str:
        return str(
            Path("layouts")
            / "versions"
            / str(page_layout_draft_id)
            / f"{version:04d}-{page_layout_draft_version_id}.json"
        )

    def approval_relative_path(
        self,
        page_layout_draft_id: UUID,
        version: int,
        approval_id: UUID,
    ) -> str:
        return str(
            Path("layouts")
            / "versions"
            / str(page_layout_draft_id)
            / f"{version:04d}-approval-{approval_id}.json"
        )

    def write(
        self,
        project_id: UUID,
        relative_path: str,
        payload: dict[str, Any],
    ) -> str:
        destination = self._safe_path(project_id, relative_path)
        content = canonical_json_bytes(payload)
        digest = hashlib.sha256(content).hexdigest()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                os.chmod(destination, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(destination.parent)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise LayoutSnapshotIntegrityError(
                    "immutable layout snapshot path already contains different bytes"
                ) from None
        return digest

    def read(
        self,
        project_id: UUID,
        relative_path: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        source = self._safe_path(project_id, relative_path)
        try:
            content = source.read_bytes()
        except FileNotFoundError:
            raise LayoutSnapshotIntegrityError("layout workspace snapshot is missing") from None
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise LayoutSnapshotIntegrityError("layout workspace snapshot hash is invalid")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LayoutSnapshotIntegrityError(
                "layout workspace snapshot is not valid JSON"
            ) from exc
        if not isinstance(payload, dict) or canonical_json_bytes(payload) != content:
            raise LayoutSnapshotIntegrityError("layout workspace snapshot is not canonical JSON")
        return payload

    def _safe_path(self, project_id: UUID, relative_path: str) -> Path:
        workspace = (self._projects_dir / str(project_id)).resolve()
        if not workspace.is_relative_to(self._projects_dir):  # pragma: no cover - UUID guards it
            raise LayoutSnapshotIntegrityError("project workspace escapes the configured root")
        candidate = (workspace / relative_path).resolve()
        if not candidate.is_relative_to(workspace):
            raise LayoutSnapshotIntegrityError("layout snapshot path escapes its project workspace")
        return candidate

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
