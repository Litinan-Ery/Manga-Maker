from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..novelai.client import NovelAIGeneratedImage
from .models import GenerationSpecDocument
from .queue import GenerationQueueService


class AssetStore:
    def __init__(self, database: Database, queue: GenerationQueueService) -> None:
        self.database = database
        self.queue = queue

    def persist_spec(self, document: GenerationSpecDocument) -> str:
        serialized = canonical_json(document.model_dump(mode="json"))
        spec_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self.database.writer() as connection:
            attempt = connection.execute(
                """
                SELECT gi.item_id, gi.status
                FROM generation_attempts ga
                JOIN generation_job_items gi ON gi.item_id = ga.item_id
                WHERE ga.attempt_id = ? AND ga.item_id = ?
                """,
                (document.attempt_id, document.item_id),
            ).fetchone()
            if attempt is None or str(attempt["status"]) != "running":
                raise ApplicationError(
                    "GENERATION_ATTEMPT_NOT_RUNNING", "生成尝试已失效，未保存请求规格。", 409
                )
            connection.execute(
                """
                INSERT INTO generation_specs(
                    spec_id, attempt_id, item_id, schema_version,
                    document_json, spec_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document.spec_id,
                    document.attempt_id,
                    document.item_id,
                    document.schema_version,
                    serialized,
                    spec_sha256,
                ),
            )
        return spec_sha256

    def register_generated_image(
        self,
        document: GenerationSpecDocument,
        generated: NovelAIGeneratedImage,
        *,
        spec_sha256: str,
        recorded_cost_anlas: int | None,
    ) -> dict[str, Any]:
        context = self._asset_context(document)
        workspace = Path(str(context["workspace_path"])).resolve()
        asset_version_id = str(uuid7())
        version = int(context["next_version"])
        relative_directory = Path("assets") / "panels" / document.panel_id / "versions" / (
            f"{version:04d}-{asset_version_id}"
        )
        final_directory = (workspace / relative_directory).resolve()
        if not final_directory.is_relative_to(workspace):
            raise ApplicationError("ASSET_PATH_INVALID", "素材目标路径不安全。", 500)
        staging_directory = (
            workspace / "assets" / ".staging" / f"{document.attempt_id}-{asset_version_id}"
        ).resolve()
        if not staging_directory.is_relative_to(workspace):
            raise ApplicationError("ASSET_PATH_INVALID", "素材临时路径不安全。", 500)
        staging_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        original_path = staging_directory / "original.png"
        provenance_path = staging_directory / "provenance.json"
        image_sha256 = hashlib.sha256(generated.png_bytes).hexdigest()
        provenance = {
            "schema_version": "1.0",
            "asset_version_id": asset_version_id,
            "asset_version": version,
            "project_id": document.project_id,
            "chapter_id": document.chapter_id,
            "panel_id": document.panel_id,
            "job_id": document.job_id,
            "item_id": document.item_id,
            "attempt_id": document.attempt_id,
            "correlation_id": document.correlation_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "spec_id": document.spec_id,
            "spec_sha256": spec_sha256,
            "storyboard_version_id": document.storyboard_version_id,
            "character_bible_version_id": document.character_bible_version_id,
            "style_bible_version_id": document.style_bible_version_id,
            "provider": document.provider,
            "provider_model_id": document.provider_model_id,
            "mapping_version": document.mapping_version,
            "contract_sha256": document.contract_sha256,
            "action": document.action,
            "prompt": document.prompt,
            "negative_prompt": document.negative_prompt,
            "width": generated.width,
            "height": generated.height,
            "steps": document.steps,
            "scale": document.scale,
            "sampler": document.sampler,
            "noise_schedule": document.noise_schedule,
            "requested_seed": document.seed,
            "response_seed": generated.seed,
            "references": [item.model_dump(mode="json") for item in document.references],
            "image_sha256": image_sha256,
            "recorded_cost_anlas": recorded_cost_anlas,
            "cost_record_status": (
                "provider_verified" if recorded_cost_anlas is not None else "not_reported"
            ),
            "credential_included": False,
        }
        try:
            write_synced(original_path, generated.png_bytes)
            write_synced(
                provenance_path,
                (canonical_json(provenance) + "\n").encode("utf-8"),
            )
            final_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(staging_directory, final_directory)
            fsync_directory(final_directory.parent)
        except Exception:
            # Keep any completed staging directory for startup reconciliation.
            raise

        original_relative_path = str(relative_directory / "original.png")
        provenance_relative_path = str(relative_directory / "provenance.json")
        try:
            with self.database.writer() as connection:
                connection.execute(
                    """
                    UPDATE asset_versions SET is_current = 0
                    WHERE project_id = ? AND panel_id = ? AND is_current = 1
                    """,
                    (document.project_id, document.panel_id),
                )
                connection.execute(
                    """
                    INSERT INTO asset_versions(
                        asset_version_id, project_id, panel_id, version,
                        parent_asset_version_id, job_id, item_id, attempt_id,
                        spec_id, status, original_relative_path,
                        provenance_relative_path, image_sha256, width, height,
                        seed, is_current
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        asset_version_id,
                        document.project_id,
                        document.panel_id,
                        version,
                        context["parent_asset_version_id"],
                        document.job_id,
                        document.item_id,
                        document.attempt_id,
                        document.spec_id,
                        original_relative_path,
                        provenance_relative_path,
                        image_sha256,
                        generated.width,
                        generated.height,
                        generated.seed,
                    ),
                )
                self.queue.complete_attempt_in_transaction(
                    connection,
                    document.attempt_id,
                    recorded_cost_anlas=recorded_cost_anlas,
                    asset_version_id=asset_version_id,
                )
                connection.execute(
                    """
                    INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
                    VALUES (?, ?, 'generation.asset_registered', ?)
                    """,
                    (
                        str(uuid7()),
                        document.project_id,
                        canonical_json(
                            {
                                "asset_version_id": asset_version_id,
                                "panel_id": document.panel_id,
                                "version": version,
                                "image_sha256": image_sha256,
                                "attempt_id": document.attempt_id,
                            }
                        ),
                    ),
                )
        except Exception:
            # The immutable final directory is deliberately retained for reconciliation.
            raise
        return self.get_asset(document.project_id, asset_version_id)

    def get_asset(self, project_id: str, asset_version_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM asset_versions
                WHERE project_id = ? AND asset_version_id = ?
                """,
                (project_id, asset_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("ASSET_VERSION_NOT_FOUND", "没有找到该素材版本。", 404)
        return self._asset_payload(row)

    def current_assets(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM asset_versions
                WHERE project_id = ? AND is_current = 1 AND status = 'ready'
                ORDER BY created_at, asset_version_id
                """,
                (project_id,),
            ).fetchall()
        return [self._asset_payload(row) for row in rows]

    def asset_content_path(self, project_id: str, asset_version_id: str) -> Path:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT av.original_relative_path, p.workspace_path
                FROM asset_versions av
                JOIN projects p ON p.project_id = av.project_id
                WHERE av.project_id = ? AND av.asset_version_id = ?
                """,
                (project_id, asset_version_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("ASSET_VERSION_NOT_FOUND", "没有找到该素材版本。", 404)
        workspace = Path(str(row["workspace_path"])).resolve()
        path = (workspace / str(row["original_relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError("ASSET_FILE_MISSING", "素材文件缺失或路径无效。", 409)
        return path

    def _asset_context(self, document: GenerationSpecDocument) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT p.workspace_path, gi.status AS item_status,
                       ga.status AS attempt_status
                FROM generation_attempts ga
                JOIN generation_job_items gi ON gi.item_id = ga.item_id
                JOIN generation_jobs gj ON gj.job_id = gi.job_id
                JOIN projects p ON p.project_id = gj.project_id
                WHERE ga.attempt_id = ? AND gi.item_id = ? AND gj.job_id = ?
                  AND gj.project_id = ? AND gi.panel_id = ?
                """,
                (
                    document.attempt_id,
                    document.item_id,
                    document.job_id,
                    document.project_id,
                    document.panel_id,
                ),
            ).fetchone()
            version_row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM asset_versions WHERE project_id = ? AND panel_id = ?
                """,
                (document.project_id, document.panel_id),
            ).fetchone()
            parent = connection.execute(
                """
                SELECT asset_version_id FROM asset_versions
                WHERE project_id = ? AND panel_id = ? AND is_current = 1
                """,
                (document.project_id, document.panel_id),
            ).fetchone()
        if row is None or row["item_status"] != "running" or row["attempt_status"] != "running":
            raise ApplicationError(
                "GENERATION_ATTEMPT_NOT_RUNNING", "生成尝试已失效，未登记素材。", 409
            )
        return {
            "workspace_path": str(row["workspace_path"]),
            "next_version": int(version_row["next_version"] if version_row else 1),
            "parent_asset_version_id": (
                str(parent["asset_version_id"]) if parent is not None else None
            ),
        }

    @staticmethod
    def _asset_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "asset_version_id": str(row["asset_version_id"]),
            "project_id": str(row["project_id"]),
            "panel_id": str(row["panel_id"]),
            "version": int(row["version"]),
            "parent_asset_version_id": row["parent_asset_version_id"],
            "job_id": str(row["job_id"]),
            "item_id": str(row["item_id"]),
            "attempt_id": str(row["attempt_id"]),
            "spec_id": str(row["spec_id"]),
            "status": str(row["status"]),
            "image_sha256": str(row["image_sha256"]),
            "width": int(row["width"]),
            "height": int(row["height"]),
            "seed": int(row["seed"]),
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
        }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_synced(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
