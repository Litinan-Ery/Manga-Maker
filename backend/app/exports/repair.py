from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ..adaptation.models import StoryboardDocument
from ..generation.assets import canonical_json, fsync_directory, write_synced
from ..generation.models import CharacterTagSetRef, GenerationSpecDocument
from ..ids import uuid7
from ..modules.adaptation.contracts import StoryboardVersionRefV1
from ..modules.layout.contracts import (
    DimensionSelectionV1,
    LayoutValidationRequestV1,
    PageLayoutDraft,
)
from ..modules.layout.dimension_selector import dimension_selection_sha256
from ..modules.layout.domain import frame_content_sha256, layout_content_sha256
from ..modules.layout.validator import LayoutValidator
from ..modules.production.contracts import ProviderExecutionSpec
from ..modules.prompting.compiler import prompt_package_sha256, prompt_plan_sha256
from ..prompting.models import PromptBundleDocument, PromptPackageDocument
from ..shared_kernel import canonical_json_bytes, canonical_sha256


@dataclass(frozen=True, slots=True)
class _StoryboardState:
    storyboard_id: str
    storyboard_version_id: str
    version: int
    chapter_id: str
    document: dict[str, Any]
    content_sha256: str

    def ref(self) -> StoryboardVersionRefV1:
        return StoryboardVersionRefV1(
            storyboard_id=self.storyboard_id,
            storyboard_version_id=self.storyboard_version_id,
            version=self.version,
            content_sha256=self.content_sha256,
            approved=True,
        )


@dataclass(frozen=True, slots=True)
class _LayoutState:
    version_id: str
    layout_id: str
    chapter_id: str
    page_id: str
    version: int
    is_current: bool
    layout: PageLayoutDraft
    storyboard: StoryboardVersionRefV1 | None
    approved_panel_ids: tuple[UUID, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _DimensionState:
    selection_id: str
    frame_id: str
    selection: DimensionSelectionV1
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _ApprovalState:
    approval_id: str
    layout_version_id: str
    approval_sha256: str
    dimensions: tuple[_DimensionState, ...]


@dataclass(frozen=True, slots=True)
class _PromptState:
    version_id: str
    chapter_id: str
    document: PromptBundleDocument
    approval_hash: str | None
    snapshot_sha256: str


def repair_remapped_v15_records(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    workspace: Path,
) -> None:
    """Re-materialize every hash-bound v0.3 record after project-wide ID rebasing.

    The imported workspace is new and isolated. Database changes remain in the caller's
    transaction; if any file or contract check fails, the restore transaction rolls back
    and the caller moves the whole workspace to its recovery/orphan boundary.
    """

    storyboards = _repair_storyboards(connection, project_id)
    _repair_legacy_approval_hashes(connection, project_id)
    layouts = _repair_layout_versions(connection, project_id, workspace, storyboards)
    dimensions = _repair_dimensions(connection, project_id)
    approvals = _repair_layout_approvals(
        connection,
        project_id,
        workspace,
        layouts,
        dimensions,
        storyboards,
    )
    chapter_hashes = _chapter_layout_hashes(
        connection,
        project_id,
        layouts,
        approvals,
        storyboards,
    )
    prompts = _repair_prompts(
        connection,
        project_id,
        layouts,
        dimensions,
        approvals,
        chapter_hashes,
    )
    _repair_generation_history(connection, project_id, prompts)
    _repair_lineage(connection, project_id, storyboards, layouts, prompts)


def _repair_storyboards(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, _StoryboardState]:
    rows = connection.execute(
        """
        SELECT sv.*, s.storyboard_id AS root_storyboard_id, s.chapter_id
        FROM storyboard_versions sv
        JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
        WHERE s.project_id = ?
        ORDER BY sv.version
        """,
        (project_id,),
    ).fetchall()
    result: dict[str, _StoryboardState] = {}
    for row in rows:
        document = _object_json(str(row["document_json"]))
        document_json = canonical_json(document)
        source_fingerprint = _source_fingerprint(
            connection,
            project_id=project_id,
            chapter_id=str(row["chapter_id"]),
            beat_set_id=str(row["beat_set_id"]),
        )
        version_id = str(row["storyboard_version_id"])
        connection.execute(
            """
            UPDATE storyboard_versions
            SET source_fingerprint = ?, document_json = ?
            WHERE storyboard_version_id = ?
            """,
            (source_fingerprint, document_json, version_id),
        )
        approval_hash = hashlib.sha256(
            (document_json + source_fingerprint + str(row["page_budget"])).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            UPDATE storyboard_approvals SET approval_hash = ?
            WHERE storyboard_version_id = ?
            """,
            (approval_hash, version_id),
        )
        result[version_id] = _StoryboardState(
            storyboard_id=str(row["root_storyboard_id"]),
            storyboard_version_id=version_id,
            version=int(row["version"]),
            chapter_id=str(row["chapter_id"]),
            document=document,
            content_sha256=canonical_sha256(document),
        )
    return result


def _source_fingerprint(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    chapter_id: str,
    beat_set_id: str,
) -> str:
    row = connection.execute(
        """
        SELECT c.version AS chapter_version, c.start_offset, c.end_offset,
               sf.normalized_path, bs.version AS beat_set_version
        FROM source_chapters c
        JOIN source_chapter_sets cs ON cs.chapter_set_id = c.chapter_set_id
        JOIN source_files sf ON sf.source_file_id = cs.source_file_id
        JOIN story_beat_sets bs ON bs.chapter_id = c.chapter_id
        WHERE sf.project_id = ? AND c.chapter_id = ? AND bs.beat_set_id = ?
        """,
        (project_id, chapter_id, beat_set_id),
    ).fetchone()
    if row is None:
        raise ValueError("restored storyboard source is incomplete")
    source = Path(str(row["normalized_path"])).read_text(encoding="utf-8")
    chapter_text = source[int(row["start_offset"]) : int(row["end_offset"])]
    beats = connection.execute(
        """
        SELECT b.beat_id, b.anchor_id, a.excerpt_sha256
        FROM story_beats b
        JOIN source_anchors a ON a.anchor_id = b.anchor_id
        WHERE b.beat_set_id = ?
        ORDER BY b.ordinal
        """,
        (beat_set_id,),
    ).fetchall()
    payload = {
        "chapter_id": chapter_id,
        "chapter_version": int(row["chapter_version"]),
        "chapter_text_sha256": hashlib.sha256(chapter_text.encode("utf-8")).hexdigest(),
        "beat_set_id": beat_set_id,
        "beat_set_version": int(row["beat_set_version"]),
        "beats": [
            {
                "beat_id": str(beat["beat_id"]),
                "anchor_id": str(beat["anchor_id"]),
                "excerpt_sha256": str(beat["excerpt_sha256"]),
            }
            for beat in beats
        ],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _repair_legacy_approval_hashes(
    connection: sqlite3.Connection,
    project_id: str,
) -> None:
    for kind in ("character", "style"):
        rows = connection.execute(
            f"""
            SELECT v.{kind}_bible_version_id AS version_id,
                   v.storyboard_version_id, v.document_json
            FROM {kind}_bible_versions v
            JOIN {kind}_bibles b ON b.{kind}_bible_id = v.{kind}_bible_id
            WHERE b.project_id = ?
            """,
            (project_id,),
        ).fetchall()
        for row in rows:
            document_json = canonical_json(_object_json(str(row["document_json"])))
            connection.execute(
                f"UPDATE {kind}_bible_versions SET document_json = ? "
                f"WHERE {kind}_bible_version_id = ?",
                (document_json, str(row["version_id"])),
            )
            approval_hash = hashlib.sha256(
                (document_json + str(row["storyboard_version_id"])).encode("utf-8")
            ).hexdigest()
            connection.execute(
                f"UPDATE {kind}_bible_approvals SET approval_hash = ? "
                f"WHERE {kind}_bible_version_id = ?",
                (approval_hash, str(row["version_id"])),
            )

    tag_rows = connection.execute(
        """
        SELECT v.character_tag_bundle_version_id, v.storyboard_version_id,
               v.character_bible_version_id, v.style_bible_version_id,
               v.document_json
        FROM character_tag_bundle_versions v
        JOIN character_tag_bundles b
          ON b.character_tag_bundle_id = v.character_tag_bundle_id
        WHERE b.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for row in tag_rows:
        version_id = str(row["character_tag_bundle_version_id"])
        document_json = canonical_json(_object_json(str(row["document_json"])))
        connection.execute(
            """
            UPDATE character_tag_bundle_versions SET document_json = ?
            WHERE character_tag_bundle_version_id = ?
            """,
            (document_json, version_id),
        )
        approval_hash = _text_hash(
            "|".join(
                (
                    document_json,
                    str(row["storyboard_version_id"]),
                    str(row["character_bible_version_id"]),
                    str(row["style_bible_version_id"]),
                )
            )
        )
        connection.execute(
            """
            UPDATE character_tag_bundle_approvals SET approval_hash = ?
            WHERE character_tag_bundle_version_id = ?
            """,
            (approval_hash, version_id),
        )

    continuity_rows = connection.execute(
        """
        SELECT v.continuity_version_id, v.document_json
        FROM continuity_ledger_versions v
        JOIN continuity_ledgers l
          ON l.continuity_ledger_id = v.continuity_ledger_id
        WHERE l.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for row in continuity_rows:
        version_id = str(row["continuity_version_id"])
        document_json = canonical_json(_object_json(str(row["document_json"])))
        document_sha256 = _text_hash(document_json)
        connection.execute(
            """
            UPDATE continuity_ledger_versions
            SET document_json = ?, document_sha256 = ?
            WHERE continuity_version_id = ?
            """,
            (document_json, document_sha256, version_id),
        )
        approval_hash = _text_hash(document_json + version_id)
        connection.execute(
            """
            UPDATE continuity_approvals SET approval_hash = ?
            WHERE continuity_version_id = ?
            """,
            (approval_hash, version_id),
        )


def _repair_layout_versions(
    connection: sqlite3.Connection,
    project_id: str,
    workspace: Path,
    storyboards: dict[str, _StoryboardState],
) -> dict[str, _LayoutState]:
    rows = connection.execute(
        """
        SELECT * FROM page_layout_drafts
        WHERE project_id = ?
        ORDER BY chapter_id, page_id, version
        """,
        (project_id,),
    ).fetchall()
    result: dict[str, _LayoutState] = {}
    for row in rows:
        layout = PageLayoutDraft.model_validate_json(str(row["document_json"]))
        content_sha256 = layout_content_sha256(layout)
        layout = layout.model_copy(
            update={
                "content_sha256": content_sha256,
                "approved_content_sha256": (
                    content_sha256 if layout.approved_content_sha256 is not None else None
                ),
            }
        )
        storyboard = (
            storyboards[str(row["storyboard_version_id"])].ref()
            if row["storyboard_version_id"] is not None
            else None
        )
        approved_panel_ids = tuple(
            UUID(str(item)) for item in json.loads(str(row["approved_panel_ids_json"]))
        )
        document_json = canonical_json_bytes(layout).decode("utf-8")
        version_id = str(row["page_layout_draft_version_id"])
        file_payload = {
            "snapshot_schema_version": "1.0",
            "page_layout_draft_version_id": version_id,
            "project_id": project_id,
            "chapter_id": str(row["chapter_id"]),
            "revision": int(row["revision"]),
            "origin": str(row["origin"]),
            "storyboard": (storyboard.model_dump(mode="json") if storyboard is not None else None),
            "approved_panel_ids": [str(item) for item in approved_panel_ids],
            "legacy_page_version_id": row["legacy_page_version_id"],
            "layout": layout.model_dump(mode="json"),
            "created_at": _aware_utc_iso(str(row["created_at"])),
            "external_requests_started": 0,
        }
        snapshot_sha256 = _replace_canonical_snapshot(
            workspace,
            str(row["snapshot_relative_path"]),
            file_payload,
        )
        connection.execute(
            """
            UPDATE page_layout_drafts
            SET storyboard_content_sha256 = ?, document_json = ?,
                content_sha256 = ?, snapshot_sha256 = ?
            WHERE page_layout_draft_version_id = ?
            """,
            (
                storyboard.content_sha256 if storyboard is not None else None,
                document_json,
                content_sha256,
                snapshot_sha256,
                version_id,
            ),
        )
        result[version_id] = _LayoutState(
            version_id=version_id,
            layout_id=str(row["page_layout_draft_id"]),
            chapter_id=str(row["chapter_id"]),
            page_id=str(row["page_id"]),
            version=int(row["version"]),
            is_current=bool(row["is_current"]),
            layout=layout,
            storyboard=storyboard,
            approved_panel_ids=approved_panel_ids,
            content_sha256=content_sha256,
        )
    return result


def _repair_dimensions(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, _DimensionState]:
    rows = connection.execute(
        """
        SELECT d.*
        FROM dimension_selections d
        JOIN page_layout_drafts l
          ON l.page_layout_draft_version_id = d.page_layout_draft_version_id
        WHERE l.project_id = ?
        ORDER BY d.frame_id
        """,
        (project_id,),
    ).fetchall()
    result: dict[str, _DimensionState] = {}
    for row in rows:
        payload = _object_json(str(row["document_json"]))
        provisional = DimensionSelectionV1.model_validate(
            {
                **payload,
                "dimension_selection_id": str(row["dimension_selection_id"]),
                "content_sha256": "0" * 64,
            }
        )
        content_sha256 = dimension_selection_sha256(provisional)
        selection = provisional.model_copy(update={"content_sha256": content_sha256})
        document_json = canonical_json_bytes(
            selection.model_dump(
                mode="json",
                exclude={"dimension_selection_id", "content_sha256"},
            )
        ).decode("utf-8")
        selection_id = str(row["dimension_selection_id"])
        connection.execute(
            """
            UPDATE dimension_selections
            SET document_json = ?, content_sha256 = ?
            WHERE dimension_selection_id = ?
            """,
            (document_json, content_sha256, selection_id),
        )
        connection.execute(
            """
            UPDATE layout_approval_dimension_selections
            SET content_sha256 = ?
            WHERE dimension_selection_id = ?
            """,
            (content_sha256, selection_id),
        )
        result[selection_id] = _DimensionState(
            selection_id=selection_id,
            frame_id=str(row["frame_id"]),
            selection=selection,
            content_sha256=content_sha256,
        )
    return result


def _repair_layout_approvals(
    connection: sqlite3.Connection,
    project_id: str,
    workspace: Path,
    layouts: dict[str, _LayoutState],
    dimensions: dict[str, _DimensionState],
    storyboards: dict[str, _StoryboardState],
) -> dict[str, _ApprovalState]:
    rows = connection.execute(
        """
        SELECT * FROM layout_approvals
        WHERE project_id = ?
        ORDER BY created_at, approval_id
        """,
        (project_id,),
    ).fetchall()
    result: dict[str, _ApprovalState] = {}
    for row in rows:
        version_id = str(row["page_layout_draft_version_id"])
        layout = layouts[version_id]
        if layout.storyboard is None:
            raise ValueError("restored approved layout is missing its storyboard binding")
        storyboard = storyboards[str(row["storyboard_version_id"])].ref()
        if storyboard != layout.storyboard:
            raise ValueError("restored layout approval references a different storyboard")
        selection_rows = connection.execute(
            """
            SELECT dimension_selection_id, frame_id
            FROM layout_approval_dimension_selections
            WHERE approval_id = ?
            ORDER BY frame_id
            """,
            (str(row["approval_id"]),),
        ).fetchall()
        selected = tuple(dimensions[str(item["dimension_selection_id"])] for item in selection_rows)
        dimension_hashes = tuple(item.content_sha256 for item in selected)
        binding = {
            "binding_schema_version": "1.0",
            "project_id": project_id,
            "layout": {
                "page_layout_draft_id": layout.layout_id,
                "page_layout_draft_version_id": version_id,
                "version": layout.version,
                "content_sha256": layout.content_sha256,
            },
            "storyboard": storyboard.model_dump(mode="json"),
            "dimension_selection_sha256s": list(dimension_hashes),
        }
        approval_sha256 = canonical_sha256(binding)
        approval_id = str(row["approval_id"])
        file_payload = {
            "snapshot_schema_version": "1.0",
            "approval_id": approval_id,
            "binding": binding,
            "approval_sha256": approval_sha256,
            "created_at": str(row["created_at"]),
            "external_requests_started": 0,
        }
        snapshot_sha256 = _replace_canonical_snapshot(
            workspace,
            str(row["snapshot_relative_path"]),
            file_payload,
        )
        connection.execute(
            """
            UPDATE layout_approvals
            SET page_layout_draft_id = ?, layout_version = ?,
                layout_content_sha256 = ?, storyboard_id = ?,
                storyboard_version = ?, storyboard_content_sha256 = ?,
                approval_sha256 = ?, snapshot_sha256 = ?
            WHERE approval_id = ?
            """,
            (
                layout.layout_id,
                layout.version,
                layout.content_sha256,
                str(storyboard.storyboard_id),
                storyboard.version,
                storyboard.content_sha256,
                approval_sha256,
                snapshot_sha256,
                approval_id,
            ),
        )
        result[version_id] = _ApprovalState(
            approval_id=approval_id,
            layout_version_id=version_id,
            approval_sha256=approval_sha256,
            dimensions=selected,
        )
    return result


def _chapter_layout_hashes(
    connection: sqlite3.Connection,
    project_id: str,
    layouts: dict[str, _LayoutState],
    approvals: dict[str, _ApprovalState],
    storyboards: dict[str, _StoryboardState],
) -> dict[str, str]:
    del connection
    current_by_chapter: dict[str, dict[str, _LayoutState]] = {}
    for current_layout in layouts.values():
        if current_layout.is_current:
            current_by_chapter.setdefault(current_layout.chapter_id, {})[current_layout.page_id] = (
                current_layout
            )

    chapter_hashes: dict[str, str] = {}
    for chapter_id, by_page in current_by_chapter.items():
        storyboard_ids = {
            str(candidate.storyboard.storyboard_version_id)
            for candidate in by_page.values()
            if candidate.storyboard is not None
        }
        if len(storyboard_ids) != 1:
            continue
        storyboard = storyboards[next(iter(storyboard_ids))]
        document = StoryboardDocument.model_validate(storyboard.document)
        page_hashes: list[str] = []
        for page in document.pages:
            page_layout = by_page.get(str(page.page_id))
            if page_layout is None or page_layout.version_id not in approvals:
                raise ValueError("restored current layouts do not cover the storyboard")
            approval = approvals[page_layout.version_id]
            validation = LayoutValidator().validate(
                LayoutValidationRequestV1(
                    layout=page_layout.layout,
                    approved_panel_ids=page_layout.approved_panel_ids,
                )
            )
            if not validation.valid:
                raise ValueError("restored approved layout no longer validates")
            selected_by_frame = {item.frame_id: item for item in approval.dimensions}
            parent_ids = {
                frame.parent_frame_id
                for frame in page_layout.layout.frames
                if frame.parent_frame_id is not None
            }
            leaves = sorted(
                (frame for frame in page_layout.layout.frames if frame.frame_id not in parent_ids),
                key=lambda frame: frame.order or 0,
            )
            if set(selected_by_frame) != {str(frame.frame_id) for frame in leaves}:
                raise ValueError("restored dimension selections do not cover every frame")
            page_payload = {
                "page_layout_draft_version_id": page_layout.version_id,
                "layout_content_sha256": page_layout.content_sha256,
                "approval_sha256": approval.approval_sha256,
                "validation_rule_version": validation.rule_version,
                "frames": [
                    {
                        "frame_id": str(frame.frame_id),
                        "frame_content_sha256": frame_content_sha256(frame),
                        "dimension_selection_sha256": selected_by_frame[
                            str(frame.frame_id)
                        ].content_sha256,
                    }
                    for frame in leaves
                ],
            }
            page_hashes.append(canonical_sha256(page_payload))
        chapter_hashes[chapter_id] = canonical_sha256(
            {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "storyboard": storyboard.ref().model_dump(mode="json"),
                "pages": page_hashes,
            }
        )
    return chapter_hashes


def _repair_prompts(
    connection: sqlite3.Connection,
    project_id: str,
    layouts: dict[str, _LayoutState],
    dimensions: dict[str, _DimensionState],
    approvals: dict[str, _ApprovalState],
    chapter_hashes: dict[str, str],
) -> dict[str, _PromptState]:
    rows = connection.execute(
        """
        SELECT v.*, b.chapter_id, a.approval_hash, a.idempotency_key
        FROM prompt_bundle_versions v
        JOIN prompt_bundles b ON b.prompt_bundle_id = v.prompt_bundle_id
        LEFT JOIN prompt_bundle_approvals a
          ON a.prompt_bundle_version_id = v.prompt_bundle_version_id
        WHERE b.project_id = ?
        ORDER BY b.chapter_id, v.version
        """,
        (project_id,),
    ).fetchall()
    result: dict[str, _PromptState] = {}
    for row in rows:
        version_id = str(row["prompt_bundle_version_id"])
        chapter_id = str(row["chapter_id"])
        document = PromptBundleDocument.model_validate_json(str(row["document_json"]))
        repaired_packages: list[PromptPackageDocument] = []
        for package in document.packages:
            binding = package.layout_binding
            structured = package.structured_package
            if binding is None or structured is None:
                repaired_packages.append(package)
                continue
            layout = layouts[str(binding.page_layout_draft_version_id)]
            approval = approvals[layout.version_id]
            dimension = next(
                item for item in approval.dimensions if item.frame_id == str(binding.frame_id)
            )
            frame = next(item for item in layout.layout.frames if item.frame_id == binding.frame_id)
            repaired_binding = binding.model_copy(
                update={
                    "page_layout_draft_id": UUID(layout.layout_id),
                    "layout_version": layout.version,
                    "layout_content_sha256": layout.content_sha256,
                    "layout_approval_id": UUID(approval.approval_id),
                    "layout_approval_sha256": approval.approval_sha256,
                    "frame_content_sha256": frame_content_sha256(frame),
                    "dimension_selection_id": UUID(dimension.selection_id),
                    "dimension_selection_sha256": dimension.content_sha256,
                    "selected_width": dimension.selection.selected.width,
                    "selected_height": dimension.selection.selected.height,
                    "expected_crop_ratio": dimension.selection.expected_crop_ratio,
                    "dimension_rule_version": dimension.selection.rule_version,
                    "capability_snapshot_sha256": (dimension.selection.capability_snapshot_sha256),
                }
            )
            constraints = structured.prompt_plan.layout_constraints.model_copy(
                update={
                    "page_layout_draft_id": UUID(layout.layout_id),
                    "page_layout_draft_version": layout.version,
                    "frame_id": frame.frame_id,
                    "frame_sha256": frame_content_sha256(frame),
                }
            )
            provisional_plan = structured.prompt_plan.model_copy(
                update={"layout_constraints": constraints, "content_sha256": "0" * 64}
            )
            repaired_plan = provisional_plan.model_copy(
                update={"content_sha256": prompt_plan_sha256(provisional_plan)}
            )
            provisional_package = structured.model_copy(
                update={
                    "prompt_plan": repaired_plan,
                    "prompt_plan_sha256": repaired_plan.content_sha256,
                    "content_sha256": "0" * 64,
                    "approved_content_sha256": None,
                }
            )
            package_sha256 = prompt_package_sha256(provisional_package)
            repaired_structured = provisional_package.model_copy(
                update={
                    "content_sha256": package_sha256,
                    "approved_content_sha256": (
                        package_sha256 if structured.approved_content_sha256 is not None else None
                    ),
                }
            )
            repaired_packages.append(
                package.model_copy(
                    update={
                        "layout_binding": repaired_binding,
                        "structured_package": repaired_structured,
                    }
                )
            )
        repaired_document = document.model_copy(
            update={
                "layout_snapshot_sha256": chapter_hashes.get(
                    chapter_id, document.layout_snapshot_sha256
                ),
                "packages": repaired_packages,
            }
        )
        document_json = canonical_json(repaired_document.model_dump(mode="json"))
        approval_hash = (
            _text_hash(
                "|".join(
                    (
                        document_json,
                        str(row["storyboard_version_id"]),
                        str(row["character_bible_version_id"]),
                        str(row["style_bible_version_id"]),
                    )
                )
            )
            if row["approval_hash"] is not None
            else None
        )
        snapshot_sha256 = _prompt_snapshot_sha256(row, repaired_document)
        connection.execute(
            """
            UPDATE prompt_bundle_versions SET document_json = ?
            WHERE prompt_bundle_version_id = ?
            """,
            (document_json, version_id),
        )
        if approval_hash is not None:
            request_sha256 = (
                _text_hash(f"{version_id}|{snapshot_sha256}")
                if row["idempotency_key"] is not None
                else None
            )
            connection.execute(
                """
                UPDATE prompt_bundle_approvals
                SET approval_hash = ?, snapshot_sha256 = ?, request_sha256 = ?
                WHERE prompt_bundle_version_id = ?
                """,
                (approval_hash, snapshot_sha256, request_sha256, version_id),
            )
        result[version_id] = _PromptState(
            version_id=version_id,
            chapter_id=chapter_id,
            document=repaired_document,
            approval_hash=approval_hash,
            snapshot_sha256=snapshot_sha256,
        )
    return result


def _repair_generation_history(
    connection: sqlite3.Connection,
    project_id: str,
    prompts: dict[str, _PromptState],
) -> None:
    jobs = connection.execute(
        """
        SELECT * FROM generation_jobs
        WHERE project_id = ?
        ORDER BY created_at, job_id
        """,
        (project_id,),
    ).fetchall()
    repaired_items: dict[str, dict[str, Any]] = {}
    for job in jobs:
        prompt_version_id = str(job["prompt_bundle_version_id"] or "")
        prompt = prompts.get(prompt_version_id)
        if prompt is None:
            continue
        connection.execute(
            """
            UPDATE generation_jobs
            SET layout_snapshot_sha256 = ?, prompt_approval_hash = ?,
                prompt_snapshot_sha256 = ?
            WHERE job_id = ?
            """,
            (
                prompt.document.layout_snapshot_sha256 or "",
                prompt.approval_hash or "",
                prompt.snapshot_sha256,
                str(job["job_id"]),
            ),
        )
        packages_by_panel = {str(item.panel_id): item for item in prompt.document.packages}
        items = connection.execute(
            """
            SELECT * FROM generation_job_items
            WHERE job_id = ? ORDER BY ordinal
            """,
            (str(job["job_id"]),),
        ).fetchall()
        for item in items:
            package = packages_by_panel.get(str(item["panel_id"]))
            if (
                package is None
                or package.layout_binding is None
                or package.structured_package is None
            ):
                continue
            binding = package.layout_binding
            structured = package.structured_package
            plan = structured.prompt_plan
            execution = ProviderExecutionSpec.model_validate_json(
                str(item["provider_execution_spec_json"])
            )
            repaired_execution = execution.model_copy(
                update={
                    "prompt_plan_id": plan.prompt_plan_id,
                    "prompt_plan_version": plan.version,
                    "prompt_plan_sha256": plan.content_sha256,
                    "page_layout_draft_id": binding.page_layout_draft_id,
                    "page_layout_draft_version": binding.layout_version,
                    "page_layout_draft_sha256": binding.layout_content_sha256,
                }
            )
            execution_json = canonical_json(repaired_execution.model_dump(mode="json"))
            execution_sha256 = canonical_sha256(repaired_execution.model_dump(mode="json"))
            prompt_plan_json = canonical_json(plan.model_dump(mode="json"))
            connection.execute(
                """
                UPDATE generation_job_items
                SET page_layout_draft_id = ?, page_layout_draft_version_id = ?,
                    layout_version = ?, layout_content_sha256 = ?,
                    layout_approval_id = ?, layout_approval_sha256 = ?,
                    frame_id = ?, frame_content_sha256 = ?,
                    dimension_selection_id = ?, dimension_selection_sha256 = ?,
                    selected_width = ?, selected_height = ?, expected_crop_ratio = ?,
                    dimension_rule_version = ?, capability_snapshot_sha256 = ?,
                    prompt_plan_id = ?, prompt_plan_version = ?, prompt_plan_sha256 = ?,
                    prompt_plan_json = ?, prompt_package_sha256 = ?,
                    provider_execution_spec_json = ?, provider_execution_spec_sha256 = ?
                WHERE item_id = ?
                """,
                (
                    str(binding.page_layout_draft_id),
                    str(binding.page_layout_draft_version_id),
                    binding.layout_version,
                    binding.layout_content_sha256,
                    str(binding.layout_approval_id),
                    binding.layout_approval_sha256,
                    str(binding.frame_id),
                    binding.frame_content_sha256,
                    str(binding.dimension_selection_id),
                    binding.dimension_selection_sha256,
                    binding.selected_width,
                    binding.selected_height,
                    binding.expected_crop_ratio,
                    binding.dimension_rule_version,
                    binding.capability_snapshot_sha256,
                    str(plan.prompt_plan_id),
                    plan.version,
                    plan.content_sha256,
                    prompt_plan_json,
                    structured.content_sha256,
                    execution_json,
                    execution_sha256,
                    str(item["item_id"]),
                ),
            )
            repaired_items[str(item["item_id"])] = {
                "item_id": str(item["item_id"]),
                "panel_id": str(item["panel_id"]),
                "prompt_package_id": str(package.prompt_package_id),
                "prompt_package_sha256": structured.content_sha256,
                "prompt_plan_id": str(plan.prompt_plan_id),
                "prompt_plan_version": plan.version,
                "prompt_plan_sha256": plan.content_sha256,
                "prompt_plan": plan.model_dump(mode="json"),
                "provider_execution_spec_id": str(repaired_execution.provider_execution_spec_id),
                "provider_execution_spec": repaired_execution.model_dump(mode="json"),
                "provider_execution_spec_sha256": execution_sha256,
                "page_layout_draft_id": str(binding.page_layout_draft_id),
                "page_layout_draft_version_id": str(binding.page_layout_draft_version_id),
                "layout_version": binding.layout_version,
                "layout_content_sha256": binding.layout_content_sha256,
                "layout_approval_id": str(binding.layout_approval_id),
                "layout_approval_sha256": binding.layout_approval_sha256,
                "frame_id": str(binding.frame_id),
                "frame_content_sha256": binding.frame_content_sha256,
                "dimension_selection_id": str(binding.dimension_selection_id),
                "dimension_selection_sha256": binding.dimension_selection_sha256,
                "selected_width": binding.selected_width,
                "selected_height": binding.selected_height,
                "expected_crop_ratio": binding.expected_crop_ratio,
                "dimension_rule_version": binding.dimension_rule_version,
                "capability_snapshot_sha256": binding.capability_snapshot_sha256,
            }

    approvals = connection.execute(
        "SELECT * FROM generation_approvals WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    approval_hashes: dict[str, str] = {}
    for approval in approvals:
        approval_id = str(approval["generation_approval_id"])
        snapshot = _object_json(str(approval["snapshot_json"]))
        job = next(
            (row for row in jobs if str(row["generation_approval_id"] or "") == approval_id),
            None,
        )
        if job is not None:
            prompt = prompts.get(str(job["prompt_bundle_version_id"] or ""))
            if prompt is not None:
                snapshot["project_id"] = project_id
                snapshot["chapter_id"] = str(job["chapter_id"])
                snapshot["layout_snapshot_sha256"] = prompt.document.layout_snapshot_sha256
                snapshot["prompt_approval_hash"] = prompt.approval_hash
                snapshot["prompt_snapshot_sha256"] = prompt.snapshot_sha256
            item_rows = connection.execute(
                "SELECT item_id, panel_id FROM generation_job_items WHERE job_id = ?",
                (str(job["job_id"]),),
            ).fetchall()
            by_panel = {
                str(item["panel_id"]): repaired_items.get(str(item["item_id"]))
                for item in item_rows
            }
            target_key = "panels" if isinstance(snapshot.get("panels"), list) else "targets"
            targets = snapshot.get(target_key, [])
            if isinstance(targets, list):
                snapshot[target_key] = [
                    _merge_generation_target(target, by_panel)
                    for target in targets
                    if isinstance(target, dict)
                ]
        approval_sha256 = canonical_sha256(snapshot)
        approval_hashes[approval_id] = approval_sha256
        connection.execute(
            """
            UPDATE generation_approvals
            SET approval_sha256 = ?, snapshot_json = ?, state = 'stale'
            WHERE generation_approval_id = ?
            """,
            (approval_sha256, canonical_json(snapshot), approval_id),
        )
        connection.execute(
            """
            UPDATE generation_jobs SET generation_approval_sha256 = ?
            WHERE generation_approval_id = ?
            """,
            (approval_sha256, approval_id),
        )

    spec_rows = connection.execute(
        """
        SELECT s.*, i.*, j.prompt_bundle_version_id, j.generation_approval_id,
               j.layout_snapshot_sha256, j.prompt_approval_hash,
               j.prompt_snapshot_sha256, j.quality_rule_version,
               p.provider_execution_spec_id AS persisted_provider_spec_id,
               p.execution_spec_json AS persisted_execution_spec_json
        FROM generation_specs s
        JOIN generation_job_items i ON i.item_id = s.item_id
        JOIN generation_jobs j ON j.job_id = i.job_id
        LEFT JOIN provider_execution_specs p ON p.generation_spec_id = s.spec_id
        WHERE j.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for row in spec_rows:
        repaired = repaired_items.get(str(row["item_id"]))
        prompt = prompts.get(str(row["prompt_bundle_version_id"] or ""))
        if repaired is None or prompt is None:
            continue
        document = GenerationSpecDocument.model_validate_json(str(row["document_json"]))
        approval_id = str(row["generation_approval_id"] or "")
        refs = tuple(
            CharacterTagSetRef.model_validate(item)
            for item in json.loads(str(row["character_tag_set_refs_json"]))
        )
        repaired_document = document.model_copy(
            update={
                "project_id": project_id,
                "prompt_package_id": repaired["prompt_package_id"],
                "generation_approval_id": approval_id or None,
                "generation_approval_sha256": approval_hashes.get(approval_id),
                "prompt_approval_hash": prompt.approval_hash,
                "prompt_snapshot_sha256": prompt.snapshot_sha256,
                "prompt_plan_id": repaired["prompt_plan_id"],
                "prompt_plan_version": repaired["prompt_plan_version"],
                "prompt_plan_sha256": repaired["prompt_plan_sha256"],
                "prompt_package_sha256": repaired["prompt_package_sha256"],
                "character_tag_set_refs": list(refs),
                "approved_provider_execution_spec_sha256": repaired[
                    "provider_execution_spec_sha256"
                ],
                "layout_snapshot_sha256": prompt.document.layout_snapshot_sha256,
                "page_layout_draft_id": repaired["page_layout_draft_id"],
                "page_layout_draft_version_id": repaired["page_layout_draft_version_id"],
                "layout_version": repaired["layout_version"],
                "layout_content_sha256": repaired["layout_content_sha256"],
                "layout_approval_id": repaired["layout_approval_id"],
                "layout_approval_sha256": repaired["layout_approval_sha256"],
                "frame_id": repaired["frame_id"],
                "frame_content_sha256": repaired["frame_content_sha256"],
                "dimension_selection_id": repaired["dimension_selection_id"],
                "dimension_selection_sha256": repaired["dimension_selection_sha256"],
                "expected_crop_ratio": repaired["expected_crop_ratio"],
                "dimension_rule_version": repaired["dimension_rule_version"],
                "capability_snapshot_sha256": repaired["capability_snapshot_sha256"],
            }
        )
        serialized = canonical_json(repaired_document.model_dump(mode="json"))
        spec_sha256 = _text_hash(serialized)
        connection.execute(
            """
            UPDATE generation_specs SET document_json = ?, spec_sha256 = ?
            WHERE spec_id = ?
            """,
            (serialized, spec_sha256, str(row["spec_id"])),
        )
        if row["persisted_provider_spec_id"] is not None:
            persisted = ProviderExecutionSpec.model_validate_json(
                str(row["persisted_execution_spec_json"])
            )
            persisted = persisted.model_copy(
                update={
                    "generation_spec_id": UUID(str(row["spec_id"])),
                    "prompt_plan_id": UUID(str(repaired["prompt_plan_id"])),
                    "prompt_plan_version": int(repaired["prompt_plan_version"]),
                    "prompt_plan_sha256": str(repaired["prompt_plan_sha256"]),
                    "page_layout_draft_id": UUID(str(repaired["page_layout_draft_id"])),
                    "page_layout_draft_version": int(repaired["layout_version"]),
                    "page_layout_draft_sha256": str(repaired["layout_content_sha256"]),
                }
            )
            connection.execute(
                """
                UPDATE provider_execution_specs
                SET prompt_plan_id = ?, prompt_plan_version = ?,
                    prompt_plan_sha256 = ?, execution_spec_json = ?
                WHERE provider_execution_spec_id = ?
                """,
                (
                    str(persisted.prompt_plan_id),
                    persisted.prompt_plan_version,
                    persisted.prompt_plan_sha256,
                    canonical_json(persisted.model_dump(mode="json")),
                    str(row["persisted_provider_spec_id"]),
                ),
            )


def _merge_generation_target(
    target: dict[str, Any],
    by_panel: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    repaired = by_panel.get(str(target.get("panel_id")))
    if repaired is None:
        return target
    merged = dict(target)
    for key, value in repaired.items():
        if key != "item_id":
            merged[key] = value
    return merged


def _repair_lineage(
    connection: sqlite3.Connection,
    project_id: str,
    storyboards: dict[str, _StoryboardState],
    layouts: dict[str, _LayoutState],
    prompts: dict[str, _PromptState],
) -> None:
    content_by_identity: dict[tuple[str, str, int], str] = {}
    for storyboard in storyboards.values():
        content_by_identity[("storyboard", storyboard.storyboard_id, storyboard.version)] = (
            storyboard.content_sha256
        )
    for layout in layouts.values():
        content_by_identity[("page_layout_draft", layout.layout_id, layout.version)] = (
            layout.content_sha256
        )
        for frame in layout.layout.frames:
            content_by_identity[("frame", str(frame.frame_id), layout.version)] = (
                frame_content_sha256(frame)
            )
    for prompt in prompts.values():
        version_row = connection.execute(
            "SELECT version FROM prompt_bundle_versions WHERE prompt_bundle_version_id = ?",
            (prompt.version_id,),
        ).fetchone()
        if version_row is None:
            continue
        bundle_version = int(version_row["version"])
        for package in prompt.document.packages:
            content_by_identity[
                ("prompt_package", str(package.prompt_package_id), bundle_version)
            ] = canonical_sha256(package.model_dump(mode="json"))
            if package.structured_package is not None:
                plan = package.structured_package.prompt_plan
                content_by_identity[("prompt_plan", str(plan.prompt_plan_id), plan.version)] = (
                    plan.content_sha256
                )

    spec_rows = connection.execute(
        """
        SELECT s.spec_id, s.spec_sha256, p.provider_execution_spec_id,
               p.version AS provider_version, p.payload_sha256
        FROM generation_specs s
        JOIN generation_job_items i ON i.item_id = s.item_id
        JOIN generation_jobs j ON j.job_id = i.job_id
        LEFT JOIN provider_execution_specs p ON p.generation_spec_id = s.spec_id
        WHERE j.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for row in spec_rows:
        content_by_identity[("generation_spec", str(row["spec_id"]), 1)] = str(row["spec_sha256"])
        if row["provider_execution_spec_id"] is not None:
            content_by_identity[
                (
                    "provider_execution_spec",
                    str(row["provider_execution_spec_id"]),
                    int(row["provider_version"]),
                )
            ] = str(row["payload_sha256"])

    rows = connection.execute(
        "SELECT * FROM artifact_versions WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    for row in rows:
        identity = (
            str(row["artifact_type"]),
            str(row["artifact_id"]),
            int(row["version"]),
        )
        content_sha256 = content_by_identity.get(identity)
        if content_sha256 is not None:
            connection.execute(
                "UPDATE artifact_versions SET content_sha256 = ? WHERE artifact_row_id = ?",
                (content_sha256, str(row["artifact_row_id"])),
            )

    current_artifacts = {
        (
            str(row["artifact_type"]),
            str(row["artifact_id"]),
            int(row["version"]),
        ): str(row["content_sha256"])
        for row in connection.execute(
            "SELECT * FROM artifact_versions WHERE project_id = ?", (project_id,)
        ).fetchall()
    }
    impact_rows = connection.execute(
        """
        SELECT i.invalidation_impact_id, i.path_json
        FROM invalidation_impacts i
        JOIN invalidation_events e
          ON e.invalidation_event_id = i.invalidation_event_id
        WHERE e.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    for row in impact_rows:
        path = json.loads(str(row["path_json"]))
        if not isinstance(path, list):
            raise ValueError("restored invalidation impact path is invalid")
        for step in path:
            if not isinstance(step, dict) or not isinstance(step.get("artifact"), dict):
                raise ValueError("restored invalidation impact step is invalid")
            artifact = step["artifact"]
            artifact["project_id"] = project_id
            identity = (
                str(artifact.get("artifact_type")),
                str(artifact.get("artifact_id")),
                int(artifact.get("version", 0)),
            )
            if identity in current_artifacts:
                artifact["content_sha256"] = current_artifacts[identity]
        path_json = canonical_json_bytes(path).decode("utf-8")
        connection.execute(
            """
            UPDATE invalidation_impacts SET path_json = ?, path_sha256 = ?
            WHERE invalidation_impact_id = ?
            """,
            (path_json, canonical_sha256(path), str(row["invalidation_impact_id"])),
        )


def _prompt_snapshot_sha256(
    row: sqlite3.Row,
    document: PromptBundleDocument,
) -> str:
    return canonical_sha256(
        {
            "prompt_bundle_version_id": str(row["prompt_bundle_version_id"]),
            "version": int(row["version"]),
            "storyboard_version_id": str(row["storyboard_version_id"]),
            "character_bible_version_id": str(row["character_bible_version_id"]),
            "style_bible_version_id": str(row["style_bible_version_id"]),
            "character_tag_bundle_version_id": str(row["character_tag_bundle_version_id"]),
            "provider_model_id": str(row["provider_model_id"]),
            "layout_snapshot_sha256": document.layout_snapshot_sha256,
            "prompt_packages": [
                {
                    "prompt_package_id": str(package.prompt_package_id),
                    "content_sha256": (
                        package.structured_package.content_sha256
                        if package.structured_package is not None
                        else package.compiled_prompt_sha256
                    ),
                    "prompt_plan_sha256": (
                        package.structured_package.prompt_plan_sha256
                        if package.structured_package is not None
                        else None
                    ),
                }
                for package in document.packages
            ],
        }
    )


def _object_json(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("restored immutable document must be a JSON object")
    return value


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware_utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _replace_canonical_snapshot(
    workspace: Path,
    relative_path: str,
    payload: dict[str, Any],
) -> str:
    root = workspace.resolve()
    destination = (root / relative_path).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("restored snapshot path escapes the project workspace")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = canonical_json_bytes(payload)
    temporary = destination.with_name(f".{destination.name}.{uuid7()}.tmp")
    write_synced(temporary, content)
    os.replace(temporary, destination)
    fsync_directory(destination.parent)
    return hashlib.sha256(content).hexdigest()
