from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from ...shared_kernel import Clock, IdFactory, canonical_json_bytes, canonical_sha256
from ..adaptation.contracts import StoryboardVersionRefV1
from .contracts import (
    ApprovedChapterLayoutSnapshotV1,
    ApprovedFrameSnapshotV1,
    ApprovedPageLayoutSnapshotV1,
    ApproveLayoutCommandV1,
    CreateLayoutDraftCommandV1,
    DimensionSelectionV1,
    ImportLegacyLayoutCommandV1,
    LayoutApprovalStaleReason,
    LayoutApprovalV1,
    LayoutOrigin,
    LayoutPageRequirementV1,
    LayoutValidationRequestV1,
    LayoutVersionSnapshotV1,
    PageLayoutDraft,
    SaveLayoutDraftCommandV1,
)
from .dimension_selector import dimension_selection_sha256
from .domain import (
    frame_content_sha256,
    layout_content_sha256,
    materialize_layout_version,
    require_exact_panel_coverage,
)
from .errors import (
    LayoutApprovalConflictError,
    LayoutGenerationGateError,
    LayoutIdempotencyConflictError,
    LayoutIdentityConflictError,
    LayoutNotFoundError,
    LayoutRevisionConflictError,
    LayoutSnapshotIntegrityError,
    LayoutStoryboardBindingError,
)
from .snapshots import LayoutWorkspaceSnapshotStore
from .validator import LayoutValidator


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("layout timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


class LayoutDatabase(Protocol):
    def reader(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def writer(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SQLiteLayoutStore:
    """SQLite index plus append-only canonical workspace snapshots for layouts."""

    def __init__(
        self,
        database: LayoutDatabase,
        projects_dir: Path,
        *,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._database = database
        self._snapshots = LayoutWorkspaceSnapshotStore(projects_dir)
        self._clock = clock
        self._id_factory = id_factory

    def create_draft(
        self,
        command: CreateLayoutDraftCommandV1,
        *,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> LayoutVersionSnapshotV1:
        layout = materialize_layout_version(
            command.draft,
            page_layout_draft_id=command.draft.page_layout_draft_id,
            version=1,
        )
        panel_ids = self._normalized_panel_ids(command.approved_panel_ids)
        require_exact_panel_coverage(layout, panel_ids)
        with self._database.writer() as connection:
            replay = self._idempotent_version(
                connection,
                project_id=command.project_id,
                command_kind="create_layout_draft",
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return self._layout_snapshot_from_row(replay)
            self._require_new_identity_and_page(
                connection,
                command.project_id,
                layout.page_layout_draft_id,
                layout.page_id,
            )
            row = self._insert_version(
                connection,
                project_id=command.project_id,
                chapter_id=command.chapter_id,
                origin="planned",
                storyboard=command.storyboard,
                approved_panel_ids=panel_ids,
                legacy_page_version_id=None,
                layout=layout,
            )
            self._record_receipt(
                connection,
                project_id=command.project_id,
                command_kind="create_layout_draft",
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                resource_type="layout_version",
                resource_id=str(row["page_layout_draft_version_id"]),
            )
            return self._layout_snapshot_from_row(row)

    def import_legacy(self, command: ImportLegacyLayoutCommandV1) -> LayoutVersionSnapshotV1:
        layout = materialize_layout_version(
            command.draft,
            page_layout_draft_id=command.draft.page_layout_draft_id,
            version=1,
        )
        panel_ids = self._normalized_panel_ids(command.panel_ids)
        require_exact_panel_coverage(layout, panel_ids)
        with self._database.writer() as connection:
            self._require_new_identity_and_page(
                connection,
                command.project_id,
                layout.page_layout_draft_id,
                layout.page_id,
            )
            row = self._insert_version(
                connection,
                project_id=command.project_id,
                chapter_id=command.chapter_id,
                origin="imported_legacy",
                storyboard=None,
                approved_panel_ids=panel_ids,
                legacy_page_version_id=command.legacy_page_version_id,
                layout=layout,
            )
            return self._layout_snapshot_from_row(row)

    def save_draft(
        self,
        command: SaveLayoutDraftCommandV1,
        *,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> LayoutVersionSnapshotV1:
        with self._database.writer() as connection:
            replay = self._idempotent_version(
                connection,
                project_id=command.project_id,
                command_kind="save_layout_draft",
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return self._layout_snapshot_from_row(replay)
            current = self._current_row(
                connection,
                command.project_id,
                command.page_layout_draft_id,
            )
            if current is None:
                raise LayoutNotFoundError("page layout draft was not found")
            current_revision = int(current["revision"])
            if command.expected_revision != current_revision:
                raise LayoutRevisionConflictError(current_revision)
            if command.draft.page_layout_draft_id != command.page_layout_draft_id:
                raise LayoutIdentityConflictError("saved draft id does not match the command")
            if str(command.draft.page_id) != str(current["page_id"]):
                raise LayoutIdentityConflictError("a layout revision cannot change its page id")

            next_version = current_revision + 1
            layout = materialize_layout_version(
                command.draft,
                page_layout_draft_id=command.page_layout_draft_id,
                version=next_version,
            )
            panel_ids = self._normalized_panel_ids(command.approved_panel_ids)
            require_exact_panel_coverage(layout, panel_ids)
            if self._same_version_content(current, layout, command.storyboard, panel_ids):
                self._record_receipt(
                    connection,
                    project_id=command.project_id,
                    command_kind="save_layout_draft",
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    resource_type="layout_version",
                    resource_id=str(current["page_layout_draft_version_id"]),
                )
                return self._layout_snapshot_from_row(current)

            connection.execute(
                """
                UPDATE page_layout_drafts SET is_current = 0
                WHERE page_layout_draft_version_id = ? AND is_current = 1
                """,
                (str(current["page_layout_draft_version_id"]),),
            )
            row = self._insert_version(
                connection,
                project_id=command.project_id,
                chapter_id=UUID(str(current["chapter_id"])),
                origin=cast(LayoutOrigin, str(current["origin"])),
                storyboard=command.storyboard,
                approved_panel_ids=panel_ids,
                legacy_page_version_id=(
                    str(current["legacy_page_version_id"])
                    if current["legacy_page_version_id"] is not None
                    else None
                ),
                layout=layout,
            )
            self._record_receipt(
                connection,
                project_id=command.project_id,
                command_kind="save_layout_draft",
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                resource_type="layout_version",
                resource_id=str(row["page_layout_draft_version_id"]),
            )
            return self._layout_snapshot_from_row(row)

    def approve_layout(
        self,
        command: ApproveLayoutCommandV1,
        *,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> LayoutApprovalV1:
        with self._database.writer() as connection:
            replay = self._idempotent_approval(
                connection,
                project_id=command.project_id,
                command_kind="approve_layout",
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return self._approval_from_row(connection, replay)
            current = self._current_row(
                connection,
                command.project_id,
                command.page_layout_draft_id,
            )
            if current is None:
                raise LayoutNotFoundError("page layout draft was not found")
            current_revision = int(current["revision"])
            if command.expected_revision != current_revision:
                raise LayoutRevisionConflictError(current_revision)
            if str(current["page_layout_draft_version_id"]) != str(
                command.page_layout_draft_version_id
            ):
                raise LayoutApprovalConflictError("only the current layout version can be approved")
            if str(current["content_sha256"]) != command.layout_content_sha256:
                raise LayoutApprovalConflictError("layout content hash does not match")
            current_storyboard = self._storyboard_from_row(current)
            if current_storyboard is None:
                raise LayoutStoryboardBindingError(
                    "legacy layout must first be saved against an approved storyboard"
                )
            if current_storyboard != command.storyboard:
                raise LayoutStoryboardBindingError("storyboard approval binding does not match")

            existing = connection.execute(
                """
                SELECT * FROM layout_approvals
                WHERE page_layout_draft_version_id = ?
                """,
                (str(command.page_layout_draft_version_id),),
            ).fetchone()
            if existing is not None:
                approval = self._approval_from_row(connection, existing)
                expected_hashes = self._dimension_hashes(command.dimension_selections)
                if approval.dimension_selection_sha256s != expected_hashes:
                    raise LayoutApprovalConflictError(
                        "layout version is already approved with other dimensions"
                    )
                self._record_receipt(
                    connection,
                    project_id=command.project_id,
                    command_kind="approve_layout",
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    resource_type="layout_approval",
                    resource_id=str(existing["approval_id"]),
                )
                return approval

            approval_id = self._id_factory.new()
            created_at = _utc(self._clock.now())
            self._persist_dimension_selections(
                connection,
                command.page_layout_draft_version_id,
                command.dimension_selections,
                created_at,
            )
            dimension_hashes = self._dimension_hashes(command.dimension_selections)
            binding = self._approval_binding(
                approval_id=None,
                project_id=command.project_id,
                page_layout_draft_id=command.page_layout_draft_id,
                page_layout_draft_version_id=command.page_layout_draft_version_id,
                layout_version=current_revision,
                layout_content_sha256=command.layout_content_sha256,
                storyboard=command.storyboard,
                dimension_selection_sha256s=dimension_hashes,
            )
            approval_sha256 = canonical_sha256(binding)
            relative_path = self._snapshots.approval_relative_path(
                command.page_layout_draft_id,
                current_revision,
                approval_id,
            )
            file_payload = {
                "snapshot_schema_version": "1.0",
                "approval_id": str(approval_id),
                "binding": binding,
                "approval_sha256": approval_sha256,
                "created_at": _iso(created_at),
                "external_requests_started": 0,
            }
            snapshot_sha256 = self._snapshots.write(
                command.project_id,
                relative_path,
                file_payload,
            )
            connection.execute(
                """
                INSERT INTO layout_approvals(
                    approval_id, project_id, page_layout_draft_id,
                    page_layout_draft_version_id, layout_version,
                    layout_content_sha256, storyboard_id, storyboard_version_id,
                    storyboard_version, storyboard_content_sha256,
                    approval_sha256, snapshot_relative_path, snapshot_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(approval_id),
                    str(command.project_id),
                    str(command.page_layout_draft_id),
                    str(command.page_layout_draft_version_id),
                    current_revision,
                    command.layout_content_sha256,
                    command.storyboard.storyboard_id,
                    command.storyboard.storyboard_version_id,
                    command.storyboard.version,
                    command.storyboard.content_sha256,
                    approval_sha256,
                    relative_path,
                    snapshot_sha256,
                    _iso(created_at),
                ),
            )
            row = connection.execute(
                "SELECT * FROM layout_approvals WHERE approval_id = ?",
                (str(approval_id),),
            ).fetchone()
            assert row is not None
            for selection in command.dimension_selections:
                connection.execute(
                    """
                    INSERT INTO layout_approval_dimension_selections(
                        approval_id, dimension_selection_id, frame_id, content_sha256
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(approval_id),
                        str(selection.dimension_selection_id),
                        str(selection.frame_id),
                        selection.content_sha256,
                    ),
                )
            self._record_receipt(
                connection,
                project_id=command.project_id,
                command_kind="approve_layout",
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                resource_type="layout_approval",
                resource_id=str(approval_id),
            )
            return self._approval_from_row(connection, row)

    def get_layout(self, layout_id: str, version: int) -> PageLayoutDraft:
        with self._database.reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM page_layout_drafts
                WHERE page_layout_draft_id = ? AND version = ?
                """,
                (layout_id, version),
            ).fetchall()
            if len(rows) != 1:
                raise LayoutNotFoundError("page layout version was not found or is ambiguous")
            return self._layout_snapshot_from_row(rows[0]).layout

    def get_version(
        self,
        project_id: UUID,
        page_layout_draft_version_id: UUID,
    ) -> LayoutVersionSnapshotV1:
        with self._database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM page_layout_drafts
                WHERE project_id = ? AND page_layout_draft_version_id = ?
                """,
                (str(project_id), str(page_layout_draft_version_id)),
            ).fetchone()
            if row is None:
                raise LayoutNotFoundError("page layout version was not found")
            return self._layout_snapshot_from_row(row)

    def current_layout(
        self,
        project_id: UUID,
        page_layout_draft_id: UUID,
    ) -> LayoutVersionSnapshotV1:
        with self._database.reader() as connection:
            row = self._current_row(connection, project_id, page_layout_draft_id)
            if row is None:
                raise LayoutNotFoundError("page layout draft was not found")
            return self._layout_snapshot_from_row(row)

    def list_layout_versions(
        self,
        project_id: UUID,
        page_layout_draft_id: UUID,
    ) -> tuple[LayoutVersionSnapshotV1, ...]:
        with self._database.reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM page_layout_drafts
                WHERE project_id = ? AND page_layout_draft_id = ?
                ORDER BY version
                """,
                (str(project_id), str(page_layout_draft_id)),
            ).fetchall()
            return tuple(self._layout_snapshot_from_row(row) for row in rows)

    def list_current_layouts(
        self,
        project_id: UUID,
        chapter_id: UUID,
    ) -> tuple[LayoutVersionSnapshotV1, ...]:
        with self._database.reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM page_layout_drafts
                WHERE project_id = ? AND chapter_id = ? AND is_current = 1
                ORDER BY page_id
                """,
                (str(project_id), str(chapter_id)),
            ).fetchall()
            return tuple(self._layout_snapshot_from_row(row) for row in rows)

    def get_approval(self, project_id: UUID, approval_id: UUID) -> LayoutApprovalV1:
        with self._database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM layout_approvals
                WHERE project_id = ? AND approval_id = ?
                """,
                (str(project_id), str(approval_id)),
            ).fetchone()
            if row is None:
                raise LayoutNotFoundError("layout approval was not found")
            return self._approval_from_row(connection, row)

    def approval_for_version(
        self,
        project_id: UUID,
        page_layout_draft_version_id: UUID,
    ) -> LayoutApprovalV1 | None:
        with self._database.reader() as connection:
            row = connection.execute(
                """
                SELECT * FROM layout_approvals
                WHERE project_id = ? AND page_layout_draft_version_id = ?
                """,
                (str(project_id), str(page_layout_draft_version_id)),
            ).fetchone()
            return self._approval_from_row(connection, row) if row is not None else None

    def approved_chapter_snapshot(
        self,
        project_id: UUID,
        chapter_id: UUID,
        storyboard: StoryboardVersionRefV1,
        pages: tuple[LayoutPageRequirementV1, ...],
    ) -> ApprovedChapterLayoutSnapshotV1:
        """Fail closed unless every storyboard page has one active approved layout."""

        if not storyboard.approved:
            raise LayoutGenerationGateError("storyboard is not approved")
        if not pages or len({page.page_id for page in pages}) != len(pages):
            raise LayoutGenerationGateError("storyboard page requirements are invalid")
        with self._database.reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM page_layout_drafts
                WHERE project_id = ? AND chapter_id = ? AND is_current = 1
                """,
                (str(project_id), str(chapter_id)),
            ).fetchall()
            rows_by_page = {UUID(str(row["page_id"])): row for row in rows}
            expected_page_ids = {page.page_id for page in pages}
            if len(rows_by_page) != len(rows) or set(rows_by_page) != expected_page_ids:
                raise LayoutGenerationGateError(
                    "current layouts must cover every storyboard page exactly once"
                )

            approved_pages: list[ApprovedPageLayoutSnapshotV1] = []
            for requirement in pages:
                row = rows_by_page[requirement.page_id]
                version = self._layout_snapshot_from_row(row)
                if version.origin != "planned" or version.storyboard != storyboard:
                    raise LayoutGenerationGateError(
                        "layout is not bound to the approved storyboard"
                    )
                if set(version.approved_panel_ids) != set(requirement.panel_ids):
                    raise LayoutGenerationGateError(
                        "layout panel coverage differs from the approved storyboard"
                    )
                approval_row = connection.execute(
                    """
                    SELECT * FROM layout_approvals
                    WHERE project_id = ? AND page_layout_draft_version_id = ?
                    """,
                    (str(project_id), str(version.page_layout_draft_version_id)),
                ).fetchone()
                if approval_row is None:
                    raise LayoutGenerationGateError("layout approval is missing")
                approval = self._approval_from_row(connection, approval_row)
                if approval.state != "active":
                    raise LayoutGenerationGateError("layout approval is stale")

                validation = LayoutValidator().validate(
                    LayoutValidationRequestV1(
                        layout=version.layout,
                        approved_panel_ids=version.approved_panel_ids,
                    )
                )
                if not validation.valid:
                    raise LayoutGenerationGateError("approved layout is no longer valid")
                selections = self._dimension_selections_for_approval(
                    connection,
                    approval.approval_id,
                )
                selections_by_frame = {item.frame_id: item for item in selections}
                parent_ids = {
                    frame.parent_frame_id
                    for frame in version.layout.frames
                    if frame.parent_frame_id is not None
                }
                leaf_frames = sorted(
                    (
                        frame
                        for frame in version.layout.frames
                        if frame.frame_id not in parent_ids
                    ),
                    key=lambda frame: frame.order or 0,
                )
                if (
                    len(selections_by_frame) != len(selections)
                    or set(selections_by_frame)
                    != {frame.frame_id for frame in leaf_frames}
                ):
                    raise LayoutGenerationGateError(
                        "approved layout dimensions do not cover every frame"
                    )
                frames = tuple(
                    ApprovedFrameSnapshotV1(
                        frame=frame,
                        frame_content_sha256=frame_content_sha256(frame),
                        dimension_selection=selections_by_frame[frame.frame_id],
                    )
                    for frame in leaf_frames
                )
                page_payload = {
                    "page_layout_draft_version_id": str(
                        version.page_layout_draft_version_id
                    ),
                    "layout_content_sha256": version.layout.content_sha256,
                    "approval_sha256": approval.approval_sha256,
                    "validation_rule_version": validation.rule_version,
                    "frames": [
                        {
                            "frame_id": str(frame.frame.frame_id),
                            "frame_content_sha256": frame.frame_content_sha256,
                            "dimension_selection_sha256": (
                                frame.dimension_selection.content_sha256
                            ),
                        }
                        for frame in frames
                    ],
                }
                approved_pages.append(
                    ApprovedPageLayoutSnapshotV1(
                        version=version,
                        approval=approval,
                        frames=frames,
                        validation_rule_version=validation.rule_version,
                        content_sha256=canonical_sha256(page_payload),
                    )
                )

        chapter_payload = {
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "storyboard": storyboard.model_dump(mode="json"),
            "pages": [page.content_sha256 for page in approved_pages],
        }
        return ApprovedChapterLayoutSnapshotV1(
            project_id=project_id,
            chapter_id=chapter_id,
            storyboard=storyboard,
            pages=tuple(approved_pages),
            content_sha256=canonical_sha256(chapter_payload),
        )

    @staticmethod
    def _dimension_selections_for_approval(
        connection: sqlite3.Connection,
        approval_id: UUID,
    ) -> tuple[DimensionSelectionV1, ...]:
        rows = connection.execute(
            """
            SELECT ds.dimension_selection_id, ds.document_json,
                   ds.content_sha256, ads.frame_id
            FROM layout_approval_dimension_selections ads
            JOIN dimension_selections ds
              ON ds.dimension_selection_id = ads.dimension_selection_id
            WHERE ads.approval_id = ?
            ORDER BY ads.frame_id
            """,
            (str(approval_id),),
        ).fetchall()
        selections: list[DimensionSelectionV1] = []
        for row in rows:
            try:
                payload = json.loads(str(row["document_json"]))
                selection = DimensionSelectionV1.model_validate(
                    {
                        **payload,
                        "dimension_selection_id": str(row["dimension_selection_id"]),
                        "content_sha256": str(row["content_sha256"]),
                    }
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise LayoutSnapshotIntegrityError(
                    "stored dimension selection is invalid"
                ) from exc
            if (
                str(selection.frame_id) != str(row["frame_id"])
                or dimension_selection_sha256(selection) != selection.content_sha256
            ):
                raise LayoutSnapshotIntegrityError(
                    "stored dimension selection identity or hash is invalid"
                )
            selections.append(selection)
        return tuple(selections)

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: UUID,
        chapter_id: UUID,
        origin: LayoutOrigin,
        storyboard: StoryboardVersionRefV1 | None,
        approved_panel_ids: tuple[UUID, ...],
        legacy_page_version_id: str | None,
        layout: PageLayoutDraft,
    ) -> sqlite3.Row:
        version_id = self._id_factory.new()
        created_at = _utc(self._clock.now())
        relative_path = self._snapshots.layout_relative_path(
            layout.page_layout_draft_id,
            layout.version,
            version_id,
        )
        file_payload = self._layout_file_payload(
            version_id=version_id,
            project_id=project_id,
            chapter_id=chapter_id,
            origin=origin,
            storyboard=storyboard,
            approved_panel_ids=approved_panel_ids,
            legacy_page_version_id=legacy_page_version_id,
            layout=layout,
            created_at=created_at,
        )
        snapshot_sha256 = self._snapshots.write(project_id, relative_path, file_payload)
        source = self._storyboard_columns(storyboard)
        document_json = canonical_json_bytes(layout).decode("utf-8")
        approved_panel_ids_json = canonical_json_bytes(
            [str(panel_id) for panel_id in approved_panel_ids]
        ).decode("utf-8")
        connection.execute(
            """
            INSERT INTO page_layout_drafts(
                page_layout_draft_version_id, page_layout_draft_id, project_id,
                chapter_id, page_id, version, revision, origin,
                storyboard_id, storyboard_version_id, storyboard_version,
                storyboard_content_sha256, approved_panel_ids_json,
                legacy_page_version_id, document_json, content_sha256,
                snapshot_relative_path, snapshot_sha256, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                str(version_id),
                str(layout.page_layout_draft_id),
                str(project_id),
                str(chapter_id),
                str(layout.page_id),
                layout.version,
                layout.version,
                origin,
                source[0],
                source[1],
                source[2],
                source[3],
                approved_panel_ids_json,
                legacy_page_version_id,
                document_json,
                layout.content_sha256,
                relative_path,
                snapshot_sha256,
                _iso(created_at),
            ),
        )
        row = connection.execute(
            """
            SELECT * FROM page_layout_drafts
            WHERE page_layout_draft_version_id = ?
            """,
            (str(version_id),),
        ).fetchone()
        assert row is not None
        return cast(sqlite3.Row, row)

    def _layout_snapshot_from_row(self, row: sqlite3.Row) -> LayoutVersionSnapshotV1:
        document_json = str(row["document_json"])
        try:
            layout = PageLayoutDraft.model_validate_json(document_json)
        except ValueError as exc:
            raise LayoutSnapshotIntegrityError("stored layout document is invalid") from exc
        if canonical_json_bytes(layout).decode("utf-8") != document_json:
            raise LayoutSnapshotIntegrityError("stored layout document is not canonical JSON")
        if layout_content_sha256(layout) != str(row["content_sha256"]):
            raise LayoutSnapshotIntegrityError("stored layout content hash is invalid")
        if layout.content_sha256 != str(row["content_sha256"]):
            raise LayoutSnapshotIntegrityError("layout document and index hashes disagree")
        if (
            str(layout.page_layout_draft_id) != str(row["page_layout_draft_id"])
            or layout.version != int(row["version"])
            or str(layout.page_id) != str(row["page_id"])
        ):
            raise LayoutSnapshotIntegrityError("layout document identity does not match its index")
        try:
            panel_payload = json.loads(str(row["approved_panel_ids_json"]))
            if not isinstance(panel_payload, list):
                raise TypeError
            panel_ids = tuple(UUID(str(item)) for item in panel_payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LayoutSnapshotIntegrityError("stored approved panel ids are invalid") from exc
        storyboard = self._storyboard_from_row(row)
        snapshot = LayoutVersionSnapshotV1(
            page_layout_draft_version_id=UUID(str(row["page_layout_draft_version_id"])),
            project_id=UUID(str(row["project_id"])),
            chapter_id=UUID(str(row["chapter_id"])),
            revision=int(row["revision"]),
            origin=str(row["origin"]),
            storyboard=storyboard,
            approved_panel_ids=panel_ids,
            legacy_page_version_id=(
                str(row["legacy_page_version_id"])
                if row["legacy_page_version_id"] is not None
                else None
            ),
            layout=layout,
            snapshot_sha256=str(row["snapshot_sha256"]),
            created_at=str(row["created_at"]),
        )
        expected = self._layout_file_payload(
            version_id=snapshot.page_layout_draft_version_id,
            project_id=snapshot.project_id,
            chapter_id=snapshot.chapter_id,
            origin=snapshot.origin,
            storyboard=snapshot.storyboard,
            approved_panel_ids=snapshot.approved_panel_ids,
            legacy_page_version_id=snapshot.legacy_page_version_id,
            layout=snapshot.layout,
            created_at=snapshot.created_at,
        )
        actual = self._snapshots.read(
            snapshot.project_id,
            str(row["snapshot_relative_path"]),
            snapshot.snapshot_sha256,
        )
        if actual != expected:
            raise LayoutSnapshotIntegrityError("workspace and database layout snapshots disagree")
        return snapshot

    def _approval_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> LayoutApprovalV1:
        project_id = UUID(str(row["project_id"]))
        layout_id = UUID(str(row["page_layout_draft_id"]))
        version_id = UUID(str(row["page_layout_draft_version_id"]))
        storyboard = StoryboardVersionRefV1(
            storyboard_id=str(row["storyboard_id"]),
            storyboard_version_id=str(row["storyboard_version_id"]),
            version=int(row["storyboard_version"]),
            content_sha256=str(row["storyboard_content_sha256"]),
            approved=True,
        )
        dimension_rows = connection.execute(
            """
            SELECT ads.content_sha256, ds.document_json
            FROM layout_approval_dimension_selections ads
            JOIN dimension_selections ds
              ON ds.dimension_selection_id = ads.dimension_selection_id
            WHERE ads.approval_id = ?
            ORDER BY ads.frame_id
            """,
            (str(row["approval_id"]),),
        ).fetchall()
        dimension_hashes = tuple(str(item["content_sha256"]) for item in dimension_rows)
        for item in dimension_rows:
            document = json.loads(str(item["document_json"]))
            if canonical_sha256(document) != str(item["content_sha256"]):
                raise LayoutSnapshotIntegrityError("stored dimension selection hash is invalid")
        binding = self._approval_binding(
            approval_id=None,
            project_id=project_id,
            page_layout_draft_id=layout_id,
            page_layout_draft_version_id=version_id,
            layout_version=int(row["layout_version"]),
            layout_content_sha256=str(row["layout_content_sha256"]),
            storyboard=storyboard,
            dimension_selection_sha256s=dimension_hashes,
        )
        approval_sha256 = canonical_sha256(binding)
        if approval_sha256 != str(row["approval_sha256"]):
            raise LayoutSnapshotIntegrityError("stored layout approval hash is invalid")
        file_payload = {
            "snapshot_schema_version": "1.0",
            "approval_id": str(row["approval_id"]),
            "binding": binding,
            "approval_sha256": approval_sha256,
            "created_at": str(row["created_at"]),
            "external_requests_started": 0,
        }
        actual = self._snapshots.read(
            project_id,
            str(row["snapshot_relative_path"]),
            str(row["snapshot_sha256"]),
        )
        if actual != file_payload:
            raise LayoutSnapshotIntegrityError("workspace and database approvals disagree")

        current = self._current_row(connection, project_id, layout_id)
        if current is None:
            raise LayoutSnapshotIntegrityError("approved layout no longer has a current version")
        stale_reasons: list[LayoutApprovalStaleReason] = []
        if str(current["page_layout_draft_version_id"]) != str(version_id):
            stale_reasons.append("layout_version_superseded")
        if str(current["content_sha256"]) != str(row["layout_content_sha256"]):
            stale_reasons.append("layout_content_changed")
        current_storyboard = self._storyboard_from_row(current)
        if current_storyboard != storyboard:
            stale_reasons.append("storyboard_binding_changed")
        return LayoutApprovalV1(
            approval_id=UUID(str(row["approval_id"])),
            project_id=project_id,
            page_layout_draft_id=layout_id,
            page_layout_draft_version_id=version_id,
            layout_version=int(row["layout_version"]),
            layout_content_sha256=str(row["layout_content_sha256"]),
            storyboard=storyboard,
            dimension_selection_sha256s=dimension_hashes,
            approval_sha256=approval_sha256,
            state="stale" if stale_reasons else "active",
            stale_reasons=tuple(stale_reasons),
            created_at=str(row["created_at"]),
        )

    def _require_new_identity_and_page(
        self,
        connection: sqlite3.Connection,
        project_id: UUID,
        page_layout_draft_id: UUID,
        page_id: UUID,
    ) -> None:
        identity = connection.execute(
            """
            SELECT project_id FROM page_layout_drafts
            WHERE page_layout_draft_id = ? LIMIT 1
            """,
            (str(page_layout_draft_id),),
        ).fetchone()
        if identity is not None:
            raise LayoutIdentityConflictError("page layout draft id already exists")
        page = connection.execute(
            """
            SELECT page_layout_draft_id FROM page_layout_drafts
            WHERE project_id = ? AND page_id = ? AND is_current = 1
            """,
            (str(project_id), str(page_id)),
        ).fetchone()
        if page is not None:
            raise LayoutIdentityConflictError("page already has a current layout draft")

    @staticmethod
    def _normalized_panel_ids(panel_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return tuple(sorted(panel_ids, key=str))

    @staticmethod
    def _storyboard_columns(
        storyboard: StoryboardVersionRefV1 | None,
    ) -> tuple[str | None, str | None, int | None, str | None]:
        if storyboard is None:
            return None, None, None, None
        return (
            storyboard.storyboard_id,
            storyboard.storyboard_version_id,
            storyboard.version,
            storyboard.content_sha256,
        )

    @staticmethod
    def _storyboard_from_row(row: sqlite3.Row) -> StoryboardVersionRefV1 | None:
        if row["storyboard_version_id"] is None:
            return None
        return StoryboardVersionRefV1(
            storyboard_id=str(row["storyboard_id"]),
            storyboard_version_id=str(row["storyboard_version_id"]),
            version=int(row["storyboard_version"]),
            content_sha256=str(row["storyboard_content_sha256"]),
            approved=True,
        )

    def _same_version_content(
        self,
        current: sqlite3.Row,
        layout: PageLayoutDraft,
        storyboard: StoryboardVersionRefV1,
        panel_ids: tuple[UUID, ...],
    ) -> bool:
        current_panels = tuple(
            UUID(str(item)) for item in json.loads(str(current["approved_panel_ids_json"]))
        )
        return (
            str(current["content_sha256"]) == layout.content_sha256
            and self._storyboard_from_row(current) == storyboard
            and current_panels == panel_ids
        )

    @staticmethod
    def _current_row(
        connection: sqlite3.Connection,
        project_id: UUID,
        page_layout_draft_id: UUID,
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """
                SELECT * FROM page_layout_drafts
                WHERE project_id = ? AND page_layout_draft_id = ? AND is_current = 1
                """,
                (str(project_id), str(page_layout_draft_id)),
            ).fetchone(),
        )

    @staticmethod
    def _layout_file_payload(
        *,
        version_id: UUID,
        project_id: UUID,
        chapter_id: UUID,
        origin: LayoutOrigin,
        storyboard: StoryboardVersionRefV1 | None,
        approved_panel_ids: tuple[UUID, ...],
        legacy_page_version_id: str | None,
        layout: PageLayoutDraft,
        created_at: datetime,
    ) -> dict[str, Any]:
        return {
            "snapshot_schema_version": "1.0",
            "page_layout_draft_version_id": str(version_id),
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "revision": layout.version,
            "origin": origin,
            "storyboard": (
                storyboard.model_dump(mode="json") if storyboard is not None else None
            ),
            "approved_panel_ids": [str(panel_id) for panel_id in approved_panel_ids],
            "legacy_page_version_id": legacy_page_version_id,
            "layout": layout.model_dump(mode="json"),
            "created_at": _iso(created_at),
            "external_requests_started": 0,
        }

    @staticmethod
    def _approval_binding(
        *,
        approval_id: UUID | None,
        project_id: UUID,
        page_layout_draft_id: UUID,
        page_layout_draft_version_id: UUID,
        layout_version: int,
        layout_content_sha256: str,
        storyboard: StoryboardVersionRefV1,
        dimension_selection_sha256s: tuple[str, ...],
    ) -> dict[str, Any]:
        del approval_id  # Deliberately excluded: the hash covers the semantic binding.
        return {
            "binding_schema_version": "1.0",
            "project_id": str(project_id),
            "layout": {
                "page_layout_draft_id": str(page_layout_draft_id),
                "page_layout_draft_version_id": str(page_layout_draft_version_id),
                "version": layout_version,
                "content_sha256": layout_content_sha256,
            },
            "storyboard": storyboard.model_dump(mode="json"),
            "dimension_selection_sha256s": list(dimension_selection_sha256s),
        }

    @staticmethod
    def _dimension_hashes(
        selections: tuple[Any, ...],
    ) -> tuple[str, ...]:
        return tuple(
            selection.content_sha256
            for selection in sorted(selections, key=lambda item: str(item.frame_id))
        )

    @staticmethod
    def _persist_dimension_selections(
        connection: sqlite3.Connection,
        page_layout_draft_version_id: UUID,
        selections: tuple[Any, ...],
        created_at: datetime,
    ) -> None:
        for selection in selections:
            if dimension_selection_sha256(selection) != selection.content_sha256:
                raise LayoutApprovalConflictError(
                    "dimension selection content hash does not match"
                )
            document = selection.model_dump(
                mode="json",
                exclude={"content_sha256", "dimension_selection_id"},
            )
            existing = connection.execute(
                """
                SELECT document_json, content_sha256 FROM dimension_selections
                WHERE dimension_selection_id = ?
                """,
                (str(selection.dimension_selection_id),),
            ).fetchone()
            document_json = canonical_json_bytes(document).decode("utf-8")
            if existing is not None:
                if (
                    str(existing["document_json"]) != document_json
                    or str(existing["content_sha256"]) != selection.content_sha256
                ):
                    raise LayoutApprovalConflictError(
                        "dimension selection id is bound to different content"
                    )
                continue
            connection.execute(
                """
                INSERT INTO dimension_selections(
                    dimension_selection_id, page_layout_draft_version_id, frame_id,
                    capability_snapshot_id, capability_snapshot_sha256, rule_version,
                    selected_width, selected_height, expected_crop_ratio,
                    document_json, content_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(selection.dimension_selection_id),
                    str(page_layout_draft_version_id),
                    str(selection.frame_id),
                    selection.capability_snapshot_id,
                    selection.capability_snapshot_sha256,
                    selection.rule_version,
                    selection.selected.width,
                    selection.selected.height,
                    selection.expected_crop_ratio,
                    document_json,
                    selection.content_sha256,
                    _iso(created_at),
                ),
            )

    def _idempotent_version(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: UUID,
        command_kind: str,
        idempotency_key: str | None,
        request_sha256: str | None,
    ) -> sqlite3.Row | None:
        resource_id = self._idempotent_resource(
            connection,
            project_id=project_id,
            command_kind=command_kind,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            resource_type="layout_version",
        )
        if resource_id is None:
            return None
        row = connection.execute(
            "SELECT * FROM page_layout_drafts WHERE page_layout_draft_version_id = ?",
            (resource_id,),
        ).fetchone()
        if row is None:
            raise LayoutSnapshotIntegrityError("layout command receipt points to missing version")
        return cast(sqlite3.Row, row)

    def _idempotent_approval(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: UUID,
        command_kind: str,
        idempotency_key: str | None,
        request_sha256: str | None,
    ) -> sqlite3.Row | None:
        resource_id = self._idempotent_resource(
            connection,
            project_id=project_id,
            command_kind=command_kind,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            resource_type="layout_approval",
        )
        if resource_id is None:
            return None
        row = connection.execute(
            "SELECT * FROM layout_approvals WHERE approval_id = ?",
            (resource_id,),
        ).fetchone()
        if row is None:
            raise LayoutSnapshotIntegrityError("layout command receipt points to missing approval")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _idempotent_resource(
        connection: sqlite3.Connection,
        *,
        project_id: UUID,
        command_kind: str,
        idempotency_key: str | None,
        request_sha256: str | None,
        resource_type: str,
    ) -> str | None:
        if idempotency_key is None and request_sha256 is None:
            return None
        if idempotency_key is None or request_sha256 is None:
            raise ValueError("idempotency key and request hash must be supplied together")
        row = connection.execute(
            """
            SELECT * FROM layout_command_receipts
            WHERE project_id = ? AND idempotency_key = ?
            """,
            (str(project_id), idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["command_kind"]) != command_kind
            or str(row["request_sha256"]) != request_sha256
            or str(row["resource_type"]) != resource_type
        ):
            raise LayoutIdempotencyConflictError(
                "idempotency key is already bound to another layout command"
            )
        return str(row["resource_id"])

    def _record_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: UUID,
        command_kind: str,
        idempotency_key: str | None,
        request_sha256: str | None,
        resource_type: str,
        resource_id: str,
    ) -> None:
        if idempotency_key is None and request_sha256 is None:
            return
        if idempotency_key is None or request_sha256 is None:
            raise ValueError("idempotency key and request hash must be supplied together")
        connection.execute(
            """
            INSERT INTO layout_command_receipts(
                receipt_id, project_id, command_kind, idempotency_key,
                request_sha256, resource_type, resource_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(self._id_factory.new()),
                str(project_id),
                command_kind,
                idempotency_key,
                request_sha256,
                resource_type,
                resource_id,
                _iso(self._clock.now()),
            ),
        )
