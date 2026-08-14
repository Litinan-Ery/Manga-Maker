from __future__ import annotations

import json
import sqlite3
from collections import deque
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID

from ...shared_kernel import Clock, IdFactory, canonical_json_bytes, canonical_sha256
from .contracts import (
    ArtifactDependencySnapshotV1,
    ArtifactImpactV1,
    ArtifactVersionRefV1,
    DependencyEdgeType,
    ImpactPathStepV1,
    InvalidateArtifactCommandV1,
    InvalidationResultV1,
    RegisterArtifactCommandV1,
    RegisterDependencyCommandV1,
)
from .errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    DependencyConflictError,
    DependencyCycleError,
    DependencyRuleError,
    InvalidationConflictError,
)
from .rules import edge_is_allowed


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lineage timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


@dataclass(frozen=True, slots=True)
class _ArtifactNode:
    row_id: str
    ref: ArtifactVersionRefV1


@dataclass(frozen=True, slots=True)
class _DependencyEdge:
    dependency_id: UUID
    upstream_row_id: str
    downstream_row_id: str
    edge_type: DependencyEdgeType


class SQLiteLineageAdapter:
    """Transaction-bound DAG adapter; callers own commit and rollback."""

    def __init__(self, connection: sqlite3.Connection, clock: Clock, id_factory: IdFactory) -> None:
        self._connection = connection
        self._clock = clock
        self._id_factory = id_factory

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        self._connection.execute("SAVEPOINT lineage_operation")
        try:
            yield
        except Exception:
            self._connection.execute("ROLLBACK TO lineage_operation")
            self._connection.execute("RELEASE lineage_operation")
            raise
        else:
            self._connection.execute("RELEASE lineage_operation")

    def register_artifact(
        self, command: RegisterArtifactCommandV1
    ) -> ArtifactVersionRefV1:
        artifact = command.artifact
        with self._atomic():
            same_identity = self._connection.execute(
                """
                SELECT project_id FROM artifact_versions
                WHERE artifact_type = ? AND artifact_id = ? LIMIT 1
                """,
                (artifact.artifact_type, artifact.artifact_id),
            ).fetchone()
            if (
                same_identity is not None
                and str(same_identity["project_id"]) != artifact.project_id
            ):
                raise ArtifactConflictError(
                    "artifact identity is already owned by another project"
                )
            existing = self._artifact_row(artifact)
            if existing is not None:
                current = self._artifact_ref(existing)
                if (
                    current.content_sha256 != artifact.content_sha256
                    or current.schema_version != artifact.schema_version
                ):
                    raise ArtifactConflictError(
                        "artifact version is already registered with different content"
                    )
                return current
            self._connection.execute(
                """
                INSERT INTO artifact_versions(
                    artifact_row_id, project_id, artifact_type, artifact_id, version,
                    content_sha256, schema_version, is_stale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    str(self._id_factory.new()),
                    artifact.project_id,
                    artifact.artifact_type,
                    artifact.artifact_id,
                    artifact.version,
                    artifact.content_sha256,
                    artifact.schema_version,
                    _iso(self._clock.now()),
                ),
            )
            row = self._artifact_row(artifact)
            assert row is not None
            return self._artifact_ref(row)

    def register_dependency(
        self, command: RegisterDependencyCommandV1
    ) -> ArtifactDependencySnapshotV1:
        with self._atomic():
            upstream = self._require_artifact(command.upstream)
            downstream = self._require_artifact(command.downstream)
            if upstream.ref.project_id != downstream.ref.project_id:
                raise DependencyRuleError("dependency cannot cross projects")
            if not edge_is_allowed(
                command.edge_type,
                upstream.ref.artifact_type,
                downstream.ref.artifact_type,
            ):
                raise DependencyRuleError(
                    f"{command.edge_type} does not allow "
                    f"{upstream.ref.artifact_type} -> {downstream.ref.artifact_type}"
                )
            existing = self._connection.execute(
                """
                SELECT * FROM artifact_dependencies
                WHERE upstream_artifact_row_id = ? AND downstream_artifact_row_id = ?
                """,
                (upstream.row_id, downstream.row_id),
            ).fetchone()
            if existing is not None:
                if str(existing["edge_type"]) != command.edge_type:
                    raise DependencyConflictError(
                        "artifact pair is already registered with another edge type"
                    )
                return self._dependency_snapshot(existing, upstream.ref, downstream.ref)

            existing_path = self._path_between(
                upstream.ref.project_id,
                start_row_id=downstream.row_id,
                target_row_id=upstream.row_id,
            )
            if existing_path is not None:
                cycle_labels = (
                    self._label(upstream.ref),
                    *(self._label(node.ref) for node in existing_path),
                )
                raise DependencyCycleError(cycle_labels)

            dependency_id = self._id_factory.new()
            self._connection.execute(
                """
                INSERT INTO artifact_dependencies(
                    dependency_id, project_id, upstream_artifact_row_id,
                    downstream_artifact_row_id, edge_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(dependency_id),
                    upstream.ref.project_id,
                    upstream.row_id,
                    downstream.row_id,
                    command.edge_type,
                    _iso(self._clock.now()),
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM artifact_dependencies WHERE dependency_id = ?",
                (str(dependency_id),),
            ).fetchone()
            assert row is not None
            return self._dependency_snapshot(row, upstream.ref, downstream.ref)

    def dependencies_for(
        self, artifact_type: str, artifact_id: str, version: int
    ) -> tuple[ArtifactDependencySnapshotV1, ...]:
        artifact_rows = self._connection.execute(
            """
            SELECT * FROM artifact_versions
            WHERE artifact_type = ? AND artifact_id = ? AND version = ?
            """,
            (artifact_type, artifact_id, version),
        ).fetchall()
        if len(artifact_rows) != 1:
            raise ArtifactNotFoundError("artifact version was not found or is ambiguous")
        upstream = _ArtifactNode(
            str(artifact_rows[0]["artifact_row_id"]), self._artifact_ref(artifact_rows[0])
        )
        rows = self._connection.execute(
            """
            SELECT d.*, downstream.*
            FROM artifact_dependencies d
            JOIN artifact_versions downstream
              ON downstream.artifact_row_id = d.downstream_artifact_row_id
            WHERE d.upstream_artifact_row_id = ?
            ORDER BY downstream.artifact_type, downstream.artifact_id,
                     downstream.version, d.edge_type
            """,
            (upstream.row_id,),
        ).fetchall()
        return tuple(
            self._dependency_snapshot(row, upstream.ref, self._artifact_ref(row))
            for row in rows
        )

    def impact_preview(self, origin: ArtifactVersionRefV1) -> tuple[ArtifactImpactV1, ...]:
        origin_node = self._require_artifact(origin)
        return self._impact_paths(origin_node)

    def invalidate(self, command: InvalidateArtifactCommandV1) -> InvalidationResultV1:
        with self._atomic():
            origin = self._require_artifact(command.origin)
            existing = self._connection.execute(
                """
                SELECT * FROM invalidation_events
                WHERE project_id = ? AND source_event_id = ?
                """,
                (command.origin.project_id, str(command.source_event_id)),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["origin_artifact_row_id"]) != origin.row_id
                    or str(existing["reason_code"]) != command.reason_code
                ):
                    raise InvalidationConflictError(
                        "source event id is already bound to another invalidation"
                    )
                return self._invalidation_result(existing, origin.ref)

            preview = self._impact_paths(origin)
            invalidation_event_id = self._id_factory.new()
            self._connection.execute(
                """
                INSERT INTO invalidation_events(
                    invalidation_event_id, project_id, source_event_id,
                    origin_artifact_row_id, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(invalidation_event_id),
                    origin.ref.project_id,
                    str(command.source_event_id),
                    origin.row_id,
                    command.reason_code,
                    _iso(self._clock.now()),
                ),
            )
            for impact in preview:
                artifact_node = self._require_artifact(impact.artifact)
                was_stale = artifact_node.ref.is_stale
                self._connection.execute(
                    "UPDATE artifact_versions SET is_stale = 1 WHERE artifact_row_id = ?",
                    (artifact_node.row_id,),
                )
                final_path = tuple(
                    ImpactPathStepV1(
                        artifact=step.artifact.model_copy(
                            update={"is_stale": index > 0 or step.artifact.is_stale}
                        ),
                        via_edge_type=step.via_edge_type,
                    )
                    for index, step in enumerate(impact.path)
                )
                final_impact = ArtifactImpactV1(
                    artifact=impact.artifact.model_copy(update={"is_stale": True}),
                    path=final_path,
                    marked_stale=not was_stale,
                )
                path_payload = [step.model_dump(mode="json") for step in final_impact.path]
                self._connection.execute(
                    """
                    INSERT INTO invalidation_impacts(
                        invalidation_impact_id, invalidation_event_id, artifact_row_id,
                        path_json, path_sha256, marked_stale
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(self._id_factory.new()),
                        str(invalidation_event_id),
                        artifact_node.row_id,
                        canonical_json_bytes(path_payload).decode("utf-8"),
                        canonical_sha256(path_payload),
                        int(not was_stale),
                    ),
                )
            row = self._connection.execute(
                "SELECT * FROM invalidation_events WHERE invalidation_event_id = ?",
                (str(invalidation_event_id),),
            ).fetchone()
            assert row is not None
            return self._invalidation_result(row, origin.ref)

    def _impact_paths(self, origin: _ArtifactNode) -> tuple[ArtifactImpactV1, ...]:
        nodes, adjacency = self._graph(origin.ref.project_id)
        paths: dict[str, tuple[ImpactPathStepV1, ...]] = {
            origin.row_id: (ImpactPathStepV1(artifact=origin.ref),)
        }
        queue: deque[str] = deque([origin.row_id])
        while queue:
            current = queue.popleft()
            for edge in adjacency.get(current, ()):
                downstream_id = edge.downstream_row_id
                if downstream_id in paths:
                    continue
                paths[downstream_id] = (
                    *paths[current],
                    ImpactPathStepV1(
                        artifact=nodes[downstream_id].ref,
                        via_edge_type=edge.edge_type,
                    ),
                )
                queue.append(downstream_id)
        ordered = sorted(
            (row_id for row_id in paths if row_id != origin.row_id),
            key=lambda row_id: (
                len(paths[row_id]),
                nodes[row_id].ref.artifact_type,
                nodes[row_id].ref.artifact_id,
                nodes[row_id].ref.version,
            ),
        )
        return tuple(
            ArtifactImpactV1(
                artifact=nodes[row_id].ref,
                path=paths[row_id],
                marked_stale=not nodes[row_id].ref.is_stale,
            )
            for row_id in ordered
        )

    def _path_between(
        self, project_id: str, *, start_row_id: str, target_row_id: str
    ) -> tuple[_ArtifactNode, ...] | None:
        nodes, adjacency = self._graph(project_id)
        parents: dict[str, str | None] = {start_row_id: None}
        queue: deque[str] = deque([start_row_id])
        while queue:
            current = queue.popleft()
            if current == target_row_id:
                path_ids: list[str] = []
                cursor: str | None = current
                while cursor is not None:
                    path_ids.append(cursor)
                    cursor = parents[cursor]
                return tuple(nodes[row_id] for row_id in reversed(path_ids))
            for edge in adjacency.get(current, ()):
                if edge.downstream_row_id not in parents:
                    parents[edge.downstream_row_id] = current
                    queue.append(edge.downstream_row_id)
        return None

    def _graph(
        self, project_id: str
    ) -> tuple[dict[str, _ArtifactNode], dict[str, tuple[_DependencyEdge, ...]]]:
        artifact_rows = self._connection.execute(
            "SELECT * FROM artifact_versions WHERE project_id = ?", (project_id,)
        ).fetchall()
        nodes = {
            str(row["artifact_row_id"]): _ArtifactNode(
                str(row["artifact_row_id"]), self._artifact_ref(row)
            )
            for row in artifact_rows
        }
        edges: dict[str, list[_DependencyEdge]] = {}
        for row in self._connection.execute(
            """
            SELECT * FROM artifact_dependencies WHERE project_id = ?
            """,
            (project_id,),
        ):
            edge = _DependencyEdge(
                dependency_id=UUID(str(row["dependency_id"])),
                upstream_row_id=str(row["upstream_artifact_row_id"]),
                downstream_row_id=str(row["downstream_artifact_row_id"]),
                edge_type=cast(DependencyEdgeType, str(row["edge_type"])),
            )
            edges.setdefault(edge.upstream_row_id, []).append(edge)
        adjacency = {
            upstream: tuple(
                sorted(
                    values,
                    key=lambda edge: (
                        nodes[edge.downstream_row_id].ref.artifact_type,
                        nodes[edge.downstream_row_id].ref.artifact_id,
                        nodes[edge.downstream_row_id].ref.version,
                        edge.edge_type,
                    ),
                )
            )
            for upstream, values in edges.items()
        }
        return nodes, adjacency

    def _require_artifact(self, ref: ArtifactVersionRefV1) -> _ArtifactNode:
        row = self._artifact_row(ref)
        if row is None:
            raise ArtifactNotFoundError(
                f"artifact {self._label(ref)} is not registered in project {ref.project_id}"
            )
        current = self._artifact_ref(row)
        if (
            current.content_sha256 != ref.content_sha256
            or current.schema_version != ref.schema_version
        ):
            raise ArtifactConflictError(
                f"artifact {self._label(ref)} hash or schema version does not match"
            )
        return _ArtifactNode(str(row["artifact_row_id"]), current)

    def _artifact_row(self, ref: ArtifactVersionRefV1) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT * FROM artifact_versions
                WHERE project_id = ? AND artifact_type = ? AND artifact_id = ? AND version = ?
                """,
                (ref.project_id, ref.artifact_type, ref.artifact_id, ref.version),
            ).fetchone(),
        )

    @staticmethod
    def _artifact_ref(row: sqlite3.Row) -> ArtifactVersionRefV1:
        return ArtifactVersionRefV1(
            project_id=str(row["project_id"]),
            artifact_type=str(row["artifact_type"]),
            artifact_id=str(row["artifact_id"]),
            version=int(row["version"]),
            content_sha256=str(row["content_sha256"]),
            schema_version=str(row["schema_version"]),
            is_stale=bool(row["is_stale"]),
        )

    @staticmethod
    def _label(ref: ArtifactVersionRefV1) -> str:
        return f"{ref.artifact_type}:{ref.artifact_id}:v{ref.version}"

    @staticmethod
    def _dependency_snapshot(
        row: sqlite3.Row,
        upstream: ArtifactVersionRefV1,
        downstream: ArtifactVersionRefV1,
    ) -> ArtifactDependencySnapshotV1:
        return ArtifactDependencySnapshotV1(
            dependency_id=UUID(str(row["dependency_id"])),
            project_id=str(row["project_id"]),
            upstream=upstream,
            downstream=downstream,
            edge_type=str(row["edge_type"]),
        )

    def _invalidation_result(
        self, event_row: sqlite3.Row, origin: ArtifactVersionRefV1
    ) -> InvalidationResultV1:
        impacts: list[ArtifactImpactV1] = []
        for row in self._connection.execute(
            """
            SELECT i.*, a.* FROM invalidation_impacts i
            JOIN artifact_versions a ON a.artifact_row_id = i.artifact_row_id
            WHERE i.invalidation_event_id = ?
            ORDER BY json_array_length(i.path_json), a.artifact_type,
                     a.artifact_id, a.version
            """,
            (str(event_row["invalidation_event_id"]),),
        ):
            path_payload = json.loads(str(row["path_json"]))
            if canonical_sha256(path_payload) != str(row["path_sha256"]):
                raise InvalidationConflictError("stored invalidation path hash is invalid")
            path = tuple(ImpactPathStepV1.model_validate(step) for step in path_payload)
            impacts.append(
                ArtifactImpactV1(
                    artifact=path[-1].artifact,
                    path=path,
                    marked_stale=bool(row["marked_stale"]),
                )
            )
        return InvalidationResultV1(
            invalidation_event_id=UUID(str(event_row["invalidation_event_id"])),
            source_event_id=UUID(str(event_row["source_event_id"])),
            project_id=str(event_row["project_id"]),
            origin=origin,
            reason_code=str(event_row["reason_code"]),
            impacts=tuple(impacts),
        )


class LineageDatabase(Protocol):
    def reader(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def writer(self) -> AbstractContextManager[sqlite3.Connection]: ...


class SQLiteLineageStore:
    def __init__(self, database: LineageDatabase, clock: Clock, id_factory: IdFactory) -> None:
        self._database = database
        self._clock = clock
        self._id_factory = id_factory

    def bind(self, connection: sqlite3.Connection) -> SQLiteLineageAdapter:
        return SQLiteLineageAdapter(connection, self._clock, self._id_factory)

    def register_artifact(
        self, command: RegisterArtifactCommandV1
    ) -> ArtifactVersionRefV1:
        with self._database.writer() as connection:
            return self.bind(connection).register_artifact(command)

    def register_dependency(
        self, command: RegisterDependencyCommandV1
    ) -> ArtifactDependencySnapshotV1:
        with self._database.writer() as connection:
            return self.bind(connection).register_dependency(command)

    def dependencies_for(
        self, artifact_type: str, artifact_id: str, version: int
    ) -> tuple[ArtifactDependencySnapshotV1, ...]:
        with self._database.reader() as connection:
            return self.bind(connection).dependencies_for(artifact_type, artifact_id, version)

    def impact_preview(self, origin: ArtifactVersionRefV1) -> tuple[ArtifactImpactV1, ...]:
        with self._database.reader() as connection:
            return self.bind(connection).impact_preview(origin)

    def invalidate(self, command: InvalidateArtifactCommandV1) -> InvalidationResultV1:
        with self._database.writer() as connection:
            return self.bind(connection).invalidate(command)
