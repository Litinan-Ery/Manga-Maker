from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.database import Database
from backend.app.modules.lineage.contracts import (
    ArtifactVersionRefV1,
    InvalidateArtifactCommandV1,
    RegisterArtifactCommandV1,
    RegisterDependencyCommandV1,
)
from backend.app.modules.lineage.errors import (
    ArtifactConflictError,
    DependencyConflictError,
    DependencyCycleError,
    DependencyRuleError,
    InvalidationConflictError,
)
from backend.app.modules.lineage.sqlite import SQLiteLineageStore
from backend.app.shared_kernel import Sha256


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


class SequentialIdFactory:
    def __init__(self, start: int = 70_000) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def artifact(
    artifact_type: str,
    artifact_id: str,
    *,
    project_id: str = "project-lineage",
    version: int = 1,
) -> ArtifactVersionRefV1:
    return ArtifactVersionRefV1(
        project_id=project_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        version=version,
        content_sha256=str(
            Sha256.digest(
                f"{project_id}:{artifact_type}:{artifact_id}:{version}".encode()
            )
        ),
        schema_version="1.0",
    )


def store(tmp_path: Path, name: str = "lineage.db") -> tuple[Database, SQLiteLineageStore]:
    database = Database(tmp_path / name)
    database.migrate()
    return database, SQLiteLineageStore(database, FixedClock(), SequentialIdFactory())


def register_all(lineage: SQLiteLineageStore, *artifacts: ArtifactVersionRefV1) -> None:
    for ref in artifacts:
        assert lineage.register_artifact(RegisterArtifactCommandV1(artifact=ref)) == ref


def connect(
    lineage: SQLiteLineageStore,
    upstream: ArtifactVersionRefV1,
    downstream: ArtifactVersionRefV1,
    edge_type: str,
) -> None:
    lineage.register_dependency(
        RegisterDependencyCommandV1(
            upstream=upstream,
            downstream=downstream,
            edge_type=edge_type,
        )
    )


def panel_chain(
    lineage: SQLiteLineageStore, suffix: str
) -> tuple[ArtifactVersionRefV1, ...]:
    frame = artifact("frame", f"frame-{suffix}")
    prompt = artifact("prompt_plan", f"prompt-{suffix}")
    spec = artifact("generation_spec", f"spec-{suffix}")
    candidates = artifact("panel_candidate_set", f"candidates-{suffix}")
    review = artifact("review_decision", f"review-{suffix}")
    approval = artifact("page_approval", f"approval-{suffix}")
    register_all(lineage, frame, prompt, spec, candidates, review, approval)
    connect(lineage, frame, prompt, "frame_to_prompt")
    connect(lineage, prompt, spec, "prompt_to_generation_spec")
    connect(lineage, spec, candidates, "generation_spec_to_candidate_set")
    connect(lineage, candidates, review, "candidate_set_to_review")
    connect(lineage, review, approval, "review_to_page_approval")
    return frame, prompt, spec, candidates, review, approval


def test_frame_invalidation_returns_only_its_prompt_spec_review_and_approval_paths(
    tmp_path: Path,
) -> None:
    database, lineage = store(tmp_path)
    chain_a = panel_chain(lineage, "page-a-panel-1")
    chain_b = panel_chain(lineage, "page-b-panel-1")

    preview = lineage.impact_preview(chain_a[0])
    assert [impact.artifact.artifact_id for impact in preview] == [
        ref.artifact_id for ref in chain_a[1:]
    ]
    assert {impact.artifact.artifact_id for impact in preview}.isdisjoint(
        ref.artifact_id for ref in chain_b
    )
    assert [
        step.via_edge_type for step in preview[-1].path
    ] == [
        None,
        "frame_to_prompt",
        "prompt_to_generation_spec",
        "generation_spec_to_candidate_set",
        "candidate_set_to_review",
        "review_to_page_approval",
    ]

    command = InvalidateArtifactCommandV1(
        source_event_id=UUID(int=80_001),
        origin=chain_a[0],
        reason_code="FRAME_GEOMETRY_CHANGED",
    )
    invalidated = lineage.invalidate(command)
    replayed = lineage.invalidate(command)
    assert replayed == invalidated
    assert all(impact.artifact.is_stale for impact in invalidated.impacts)
    assert all(impact.marked_stale for impact in invalidated.impacts)

    with database.reader() as connection:
        stale_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT artifact_id FROM artifact_versions WHERE is_stale = 1"
            )
        }
        node_count = connection.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0]
        event_count = connection.execute("SELECT COUNT(*) FROM invalidation_events").fetchone()[0]
        impact_count = connection.execute("SELECT COUNT(*) FROM invalidation_impacts").fetchone()[0]
    assert stale_ids == {ref.artifact_id for ref in chain_a[1:]}
    assert node_count == 12
    assert event_count == 1
    assert impact_count == 5
    assert not lineage.impact_preview(chain_b[0])[0].artifact.is_stale

    with pytest.raises(InvalidationConflictError):
        lineage.invalidate(command.model_copy(update={"reason_code": "DIFFERENT_REASON"}))
    second_event = lineage.invalidate(
        command.model_copy(update={"source_event_id": UUID(int=80_002)})
    )
    assert len(second_event.impacts) == 5
    assert not any(impact.marked_stale for impact in second_event.impacts)
    with database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM invalidation_events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM invalidation_impacts").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0] == 12


def test_character_tag_and_storyboard_changes_have_minimal_independent_reachability(
    tmp_path: Path,
) -> None:
    _database, lineage = store(tmp_path, "minimal.db")
    storyboard_a = artifact("storyboard", "storyboard-a")
    storyboard_b = artifact("storyboard", "storyboard-b")
    layout_a = artifact("page_layout_draft", "layout-a")
    layout_b = artifact("page_layout_draft", "layout-b")
    tag_alice = artifact("character_tag_set", "tags-alice")
    tag_bob = artifact("character_tag_set", "tags-bob")
    prompt_a = artifact("prompt_plan", "prompt-a")
    prompt_b = artifact("prompt_plan", "prompt-b")
    spec_a = artifact("generation_spec", "spec-a")
    spec_b = artifact("generation_spec", "spec-b")
    register_all(
        lineage,
        storyboard_a,
        storyboard_b,
        layout_a,
        layout_b,
        tag_alice,
        tag_bob,
        prompt_a,
        prompt_b,
        spec_a,
        spec_b,
    )
    connect(lineage, storyboard_a, layout_a, "storyboard_to_layout")
    connect(lineage, storyboard_b, layout_b, "storyboard_to_layout")
    connect(lineage, layout_a, prompt_a, "layout_to_prompt")
    connect(lineage, layout_b, prompt_b, "layout_to_prompt")
    connect(lineage, tag_alice, prompt_a, "character_tags_to_prompt")
    connect(lineage, tag_bob, prompt_b, "character_tags_to_prompt")
    connect(lineage, prompt_a, spec_a, "prompt_to_generation_spec")
    connect(lineage, prompt_b, spec_b, "prompt_to_generation_spec")

    alice_impacts = lineage.impact_preview(tag_alice)
    assert {impact.artifact.artifact_id for impact in alice_impacts} == {
        "prompt-a",
        "spec-a",
    }
    storyboard_impacts = lineage.impact_preview(storyboard_a)
    assert {impact.artifact.artifact_id for impact in storyboard_impacts} == {
        "layout-a",
        "prompt-a",
        "spec-a",
    }
    assert {impact.artifact.artifact_id for impact in storyboard_impacts}.isdisjoint(
        {"layout-b", "prompt-b", "spec-b"}
    )


def test_artifact_dependency_guards_reject_conflicts_cross_project_and_cycles(
    tmp_path: Path,
) -> None:
    database, lineage = store(tmp_path, "guards.db")
    layout = artifact("page_layout_draft", "layout-guard")
    prompt = artifact("prompt_plan", "prompt-guard")
    register_all(lineage, layout, prompt)

    first = lineage.register_dependency(
        RegisterDependencyCommandV1(
            upstream=layout,
            downstream=prompt,
            edge_type="layout_to_prompt",
        )
    )
    assert lineage.register_dependency(
        RegisterDependencyCommandV1(
            upstream=layout,
            downstream=prompt,
            edge_type="layout_to_prompt",
        )
    ) == first
    assert lineage.dependencies_for("page_layout_draft", "layout-guard", 1) == (first,)

    with pytest.raises(DependencyRuleError):
        lineage.register_dependency(
            RegisterDependencyCommandV1(
                upstream=layout,
                downstream=prompt,
                edge_type="page_approval_to_export",
            )
        )
    with pytest.raises(ValidationError, match="same project"):
        RegisterDependencyCommandV1(
            upstream=layout,
            downstream=artifact(
                "prompt_plan", "other-project-prompt", project_id="project-other"
            ),
            edge_type="layout_to_prompt",
        )
    with pytest.raises(ArtifactConflictError, match="another project"):
        lineage.register_artifact(
            RegisterArtifactCommandV1(
                artifact=artifact(
                    "page_layout_draft",
                    "layout-guard",
                    project_id="project-other",
                )
            )
        )
    with pytest.raises(ArtifactConflictError, match="different content"):
        lineage.register_artifact(
            RegisterArtifactCommandV1(
                artifact=layout.model_copy(update={"content_sha256": "f" * 64})
            )
        )

    storyboard = artifact("storyboard", "storyboard-cycle")
    frame = artifact("frame", "frame-cycle")
    register_all(lineage, storyboard, frame)
    with database.writer() as connection:
        rows = {
            str(row["artifact_id"]): str(row["artifact_row_id"])
            for row in connection.execute(
                """
                SELECT artifact_row_id, artifact_id FROM artifact_versions
                WHERE artifact_id IN ('storyboard-cycle', 'frame-cycle')
                """
            )
        }
        connection.execute(
            """
            INSERT INTO artifact_dependencies(
                dependency_id, project_id, upstream_artifact_row_id,
                downstream_artifact_row_id, edge_type, created_at
            ) VALUES (?, ?, ?, ?, 'corrupt_back_edge', ?)
            """,
            (
                str(UUID(int=80_010)),
                storyboard.project_id,
                rows["frame-cycle"],
                rows["storyboard-cycle"],
                FixedClock().now().isoformat(),
            ),
        )
    with pytest.raises(DependencyCycleError) as cycle:
        lineage.register_dependency(
            RegisterDependencyCommandV1(
                upstream=storyboard,
                downstream=frame,
                edge_type="storyboard_to_layout",
            )
        )
    assert cycle.value.path == (
        "storyboard:storyboard-cycle:v1",
        "frame:frame-cycle:v1",
        "storyboard:storyboard-cycle:v1",
    )


def test_conflicting_seeded_edge_and_transaction_failure_do_not_partially_write(
    tmp_path: Path,
) -> None:
    database, lineage = store(tmp_path, "atomic.db")
    layout = artifact("page_layout_draft", "layout-conflict")
    prompt = artifact("prompt_plan", "prompt-conflict")
    register_all(lineage, layout, prompt)
    with database.writer() as connection:
        rows = {
            str(row["artifact_id"]): str(row["artifact_row_id"])
            for row in connection.execute(
                "SELECT artifact_row_id, artifact_id FROM artifact_versions"
            )
        }
        connection.execute(
            """
            INSERT INTO artifact_dependencies(
                dependency_id, project_id, upstream_artifact_row_id,
                downstream_artifact_row_id, edge_type, created_at
            ) VALUES (?, ?, ?, ?, 'corrupt_wrong_type', ?)
            """,
            (
                str(UUID(int=80_020)),
                layout.project_id,
                rows[layout.artifact_id],
                rows[prompt.artifact_id],
                FixedClock().now().isoformat(),
            ),
        )
    with pytest.raises(DependencyConflictError):
        lineage.register_dependency(
            RegisterDependencyCommandV1(
                upstream=layout,
                downstream=prompt,
                edge_type="layout_to_prompt",
            )
        )

    database2, lineage2 = store(tmp_path, "rollback.db")
    source = artifact("frame", "frame-rollback")
    downstream = artifact("prompt_plan", "prompt-rollback")
    with (
        pytest.raises(RuntimeError, match="injected lineage rollback"),
        database2.writer() as connection,
    ):
        bound = lineage2.bind(connection)
        bound.register_artifact(RegisterArtifactCommandV1(artifact=source))
        bound.register_artifact(RegisterArtifactCommandV1(artifact=downstream))
        bound.register_dependency(
            RegisterDependencyCommandV1(
                upstream=source,
                downstream=downstream,
                edge_type="frame_to_prompt",
            )
        )
        raise RuntimeError("injected lineage rollback")
    with database2.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifact_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifact_dependencies").fetchone()[0] == 0


def test_lineage_tables_store_only_refs_reasons_paths_and_hashes(tmp_path: Path) -> None:
    database, _lineage = store(tmp_path, "schema.db")
    with database.reader() as connection:
        columns = {
            table: {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for table in (
                "artifact_versions",
                "artifact_dependencies",
                "invalidation_events",
                "invalidation_impacts",
            )
        }
    forbidden = {
        "body",
        "content",
        "document_json",
        "image_bytes",
        "payload_json",
        "prompt",
        "token",
    }
    assert all(fields.isdisjoint(forbidden) for fields in columns.values())
    assert {"path_json", "path_sha256"}.issubset(columns["invalidation_impacts"])
    assert {"reason_code", "source_event_id"}.issubset(columns["invalidation_events"])
