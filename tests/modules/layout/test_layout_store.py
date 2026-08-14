from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.database import Database
from backend.app.modules.adaptation.contracts import StoryboardVersionRefV1
from backend.app.modules.layout.contracts import (
    ApproveLayoutCommandV1,
    CreateLayoutDraftCommandV1,
    ImportLegacyLayoutCommandV1,
    NormalizedPoint,
    PageLayoutDraft,
    SaveLayoutDraftCommandV1,
)
from backend.app.modules.layout.domain import layout_content_sha256, layout_leaf_panel_ids
from backend.app.modules.layout.errors import (
    LayoutIdempotencyConflictError,
    LayoutPanelCoverageError,
    LayoutRevisionConflictError,
    LayoutSnapshotIntegrityError,
    LayoutStoryboardBindingError,
)
from backend.app.modules.layout.sqlite import SQLiteLayoutStore

ROOT = Path(__file__).resolve().parents[3]
LAYOUT_FIXTURE = ROOT / "contracts" / "fixtures" / "v0.3" / "page-layout-draft.json"
PROJECT_ID = UUID("01900000-0000-7000-8000-000000009001")
CHAPTER_ID = UUID("01900000-0000-7000-8000-000000009002")


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class SequentialIdFactory:
    def __init__(self, start: int = 90_000) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def layout_fixture() -> PageLayoutDraft:
    return PageLayoutDraft.model_validate_json(LAYOUT_FIXTURE.read_text(encoding="utf-8"))


def storyboard(version: int = 1, content: str = "b") -> StoryboardVersionRefV1:
    return StoryboardVersionRefV1(
        storyboard_id="01900000-0000-7000-8000-000000009100",
        storyboard_version_id=f"01900000-0000-7000-8000-{version:012d}",
        version=version,
        content_sha256=content * 64,
        approved=True,
    )


def store(tmp_path: Path) -> tuple[Database, SQLiteLayoutStore, Path]:
    database = Database(tmp_path / "layout.db")
    database.migrate()
    projects_dir = tmp_path / "projects"
    return (
        database,
        SQLiteLayoutStore(
            database,
            projects_dir,
            clock=FixedClock(),
            id_factory=SequentialIdFactory(),
        ),
        projects_dir,
    )


def create_command(draft: PageLayoutDraft | None = None) -> CreateLayoutDraftCommandV1:
    document = draft or layout_fixture()
    return CreateLayoutDraftCommandV1(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        storyboard=storyboard(),
        approved_panel_ids=layout_leaf_panel_ids(document),
        draft=document,
    )


def changed_layout(layout: PageLayoutDraft, *, x: float) -> PageLayoutDraft:
    leaf = next(frame for frame in layout.frames if frame.panel_id is not None)
    changed_leaf = leaf.model_copy(update={"focal_point": NormalizedPoint(x=x, y=0.5)})
    frames = [changed_leaf if frame.frame_id == leaf.frame_id else frame for frame in layout.frames]
    return layout.model_copy(update={"frames": frames})


def test_create_round_trips_full_layout_and_writes_canonical_provider_neutral_snapshot(
    tmp_path: Path,
) -> None:
    database, layouts, projects_dir = store(tmp_path)
    source = layout_fixture()

    created = layouts.create_draft(create_command(source))

    assert created.revision == 1
    assert created.origin == "planned"
    assert created.storyboard == storyboard()
    assert created.approved_panel_ids == tuple(sorted(layout_leaf_panel_ids(source), key=str))
    assert created.layout.content_sha256 == layout_content_sha256(created.layout)
    assert created.layout.content_sha256 != source.content_sha256
    assert created.layout.approved_content_sha256 is None
    assert layouts.get_layout(str(source.page_layout_draft_id), 1) == created.layout
    assert layouts.get_version(PROJECT_ID, created.page_layout_draft_version_id) == created
    assert layouts.current_layout(PROJECT_ID, source.page_layout_draft_id) == created
    assert layouts.list_layout_versions(PROJECT_ID, source.page_layout_draft_id) == (created,)
    assert created.external_requests_started == 0

    with database.reader() as connection:
        row = connection.execute("SELECT * FROM page_layout_drafts").fetchone()
        assert row is not None
        relative_path = str(row["snapshot_relative_path"])
        document = json.loads(str(row["document_json"]))
        table_columns = {
            str(item["name"])
            for table in ("page_layout_drafts", "layout_approvals", "dimension_selections")
            for item in connection.execute(f"PRAGMA table_info({table})")
        }
    snapshot_path = projects_dir / str(PROJECT_ID) / relative_path
    assert snapshot_path.is_file()
    assert snapshot_path.read_bytes().decode("utf-8").startswith("{")
    assert document == created.layout.model_dump(mode="json")
    forbidden = ("novelai", "provider", "api_token", "image_token", "authorization")
    assert not any(term in column.lower() for term in forbidden for column in table_columns)
    assert not any(term in snapshot_path.read_text().lower() for term in forbidden)


def test_storyboard_panel_coverage_is_exact_before_any_state_is_written(tmp_path: Path) -> None:
    database, layouts, projects_dir = store(tmp_path)
    document = layout_fixture()
    approved = layout_leaf_panel_ids(document)
    missing_one = create_command(document).model_copy(update={"approved_panel_ids": approved[:1]})

    with pytest.raises(LayoutPanelCoverageError) as raised:
        layouts.create_draft(missing_one)

    assert raised.value.unexpected == (approved[1],)
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM page_layout_drafts").fetchone()[0] == 0
    assert not (projects_dir / str(PROJECT_ID)).exists()


def test_save_is_append_only_idempotent_and_optimistically_locked(tmp_path: Path) -> None:
    database, layouts, projects_dir = store(tmp_path)
    first = layouts.create_draft(create_command())
    panels = layout_leaf_panel_ids(first.layout)
    with database.reader() as connection:
        first_path = str(
            connection.execute(
                "SELECT snapshot_relative_path FROM page_layout_drafts WHERE version = 1"
            ).fetchone()[0]
        )
    immutable_path = projects_dir / str(PROJECT_ID) / first_path
    immutable_bytes = immutable_path.read_bytes()

    reordered = first.layout.model_copy(update={"frames": list(reversed(first.layout.frames))})
    replayed = layouts.save_draft(
        SaveLayoutDraftCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=first.layout.page_layout_draft_id,
            expected_revision=1,
            storyboard=storyboard(),
            approved_panel_ids=panels,
            draft=reordered,
        )
    )
    assert replayed == first

    second = layouts.save_draft(
        SaveLayoutDraftCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=first.layout.page_layout_draft_id,
            expected_revision=1,
            storyboard=storyboard(),
            approved_panel_ids=panels,
            draft=changed_layout(first.layout, x=0.6),
        )
    )
    assert second.revision == 2
    assert second.page_layout_draft_version_id != first.page_layout_draft_version_id
    assert second.layout.content_sha256 != first.layout.content_sha256
    assert immutable_path.read_bytes() == immutable_bytes
    assert layouts.get_layout(str(first.layout.page_layout_draft_id), 1) == first.layout

    with pytest.raises(LayoutRevisionConflictError) as raised:
        layouts.save_draft(
            SaveLayoutDraftCommandV1(
                project_id=PROJECT_ID,
                page_layout_draft_id=first.layout.page_layout_draft_id,
                expected_revision=1,
                storyboard=storyboard(),
                approved_panel_ids=panels,
                draft=changed_layout(second.layout, x=0.7),
            )
        )
    assert raised.value.current_revision == 2
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM page_layout_drafts").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM page_layout_drafts WHERE is_current = 1"
        ).fetchone()[0] == 1


def test_approval_binds_exact_hashes_and_old_decisions_remain_queryable_as_stale(
    tmp_path: Path,
) -> None:
    database, layouts, _projects_dir = store(tmp_path)
    first = layouts.create_draft(create_command())
    approve_first = ApproveLayoutCommandV1(
        project_id=PROJECT_ID,
        page_layout_draft_id=first.layout.page_layout_draft_id,
        page_layout_draft_version_id=first.page_layout_draft_version_id,
        expected_revision=1,
        layout_content_sha256=first.layout.content_sha256,
        storyboard=storyboard(),
    )
    first_approval = layouts.approve_layout(approve_first)
    assert first_approval.state == "active"
    assert layouts.approve_layout(approve_first) == first_approval

    panels = layout_leaf_panel_ids(first.layout)
    second = layouts.save_draft(
        SaveLayoutDraftCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=first.layout.page_layout_draft_id,
            expected_revision=1,
            storyboard=storyboard(),
            approved_panel_ids=panels,
            draft=changed_layout(first.layout, x=0.6),
        )
    )
    stale_first = layouts.get_approval(PROJECT_ID, first_approval.approval_id)
    assert stale_first.state == "stale"
    assert stale_first.stale_reasons == (
        "layout_version_superseded",
        "layout_content_changed",
    )

    second_approval = layouts.approve_layout(
        ApproveLayoutCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=second.layout.page_layout_draft_id,
            page_layout_draft_version_id=second.page_layout_draft_version_id,
            expected_revision=2,
            layout_content_sha256=second.layout.content_sha256,
            storyboard=storyboard(),
        )
    )
    third = layouts.save_draft(
        SaveLayoutDraftCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=second.layout.page_layout_draft_id,
            expected_revision=2,
            storyboard=storyboard(version=2, content="c"),
            approved_panel_ids=panels,
            draft=second.layout,
        )
    )
    assert third.layout.content_sha256 == second.layout.content_sha256
    stale_second = layouts.get_approval(PROJECT_ID, second_approval.approval_id)
    assert stale_second.stale_reasons == (
        "layout_version_superseded",
        "storyboard_binding_changed",
    )
    assert layouts.approval_for_version(PROJECT_ID, first.page_layout_draft_version_id)
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM layout_approvals").fetchone()[0] == 2


def test_legacy_import_is_a_draft_only_until_rebound_to_an_approved_storyboard(
    tmp_path: Path,
) -> None:
    _database, layouts, _projects_dir = store(tmp_path)
    document = layout_fixture()
    imported = layouts.import_legacy(
        ImportLegacyLayoutCommandV1(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            legacy_page_version_id="legacy-page-v16-42",
            panel_ids=layout_leaf_panel_ids(document),
            draft=document,
        )
    )
    assert imported.origin == "imported_legacy"
    assert imported.storyboard is None
    assert imported.legacy_page_version_id == "legacy-page-v16-42"
    assert layouts.approval_for_version(PROJECT_ID, imported.page_layout_draft_version_id) is None

    with pytest.raises(LayoutStoryboardBindingError):
        layouts.approve_layout(
            ApproveLayoutCommandV1(
                project_id=PROJECT_ID,
                page_layout_draft_id=imported.layout.page_layout_draft_id,
                page_layout_draft_version_id=imported.page_layout_draft_version_id,
                expected_revision=1,
                layout_content_sha256=imported.layout.content_sha256,
                storyboard=storyboard(),
            )
        )

    rebound = layouts.save_draft(
        SaveLayoutDraftCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=imported.layout.page_layout_draft_id,
            expected_revision=1,
            storyboard=storyboard(),
            approved_panel_ids=layout_leaf_panel_ids(imported.layout),
            draft=imported.layout,
        )
    )
    approval = layouts.approve_layout(
        ApproveLayoutCommandV1(
            project_id=PROJECT_ID,
            page_layout_draft_id=rebound.layout.page_layout_draft_id,
            page_layout_draft_version_id=rebound.page_layout_draft_version_id,
            expected_revision=2,
            layout_content_sha256=rebound.layout.content_sha256,
            storyboard=storyboard(),
        )
    )
    assert approval.state == "active"


def test_snapshot_tampering_fails_closed(tmp_path: Path) -> None:
    database, layouts, projects_dir = store(tmp_path)
    created = layouts.create_draft(create_command())
    with database.reader() as connection:
        relative_path = str(
            connection.execute(
                "SELECT snapshot_relative_path FROM page_layout_drafts"
            ).fetchone()[0]
        )
    path = projects_dir / str(PROJECT_ID) / relative_path
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(LayoutSnapshotIntegrityError, match="hash"):
        layouts.get_version(PROJECT_ID, created.page_layout_draft_version_id)


def test_repository_idempotency_receipt_replays_before_revision_check(tmp_path: Path) -> None:
    database, layouts, _projects_dir = store(tmp_path)
    first = layouts.create_draft(
        create_command(),
        idempotency_key="create-command",
        request_sha256="1" * 64,
    )
    assert layouts.create_draft(
        create_command(),
        idempotency_key="create-command",
        request_sha256="1" * 64,
    ) == first
    changed = changed_layout(first.layout, x=0.6)
    command = SaveLayoutDraftCommandV1(
        project_id=PROJECT_ID,
        page_layout_draft_id=first.layout.page_layout_draft_id,
        expected_revision=1,
        storyboard=storyboard(),
        approved_panel_ids=layout_leaf_panel_ids(first.layout),
        draft=changed,
    )
    second = layouts.save_draft(
        command,
        idempotency_key="save-command",
        request_sha256="2" * 64,
    )
    assert layouts.save_draft(
        command,
        idempotency_key="save-command",
        request_sha256="2" * 64,
    ) == second
    with pytest.raises(LayoutIdempotencyConflictError):
        layouts.save_draft(
            command,
            idempotency_key="save-command",
            request_sha256="3" * 64,
        )
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM page_layout_drafts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM layout_command_receipts").fetchone()[0] == 2
