from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from ...database import Database
from ...errors import ApplicationError
from .contracts import Lease, RuntimeState, StoredCheckpoint, TaskSnapshot


def fail(code: str, message: str, status: int = 409) -> ApplicationError:
    return ApplicationError(code, message, status)


class CheckpointStore:
    """One SQLite writer transaction fences both state changes and artifact publication.

    Files published before a failed commit are unreferenced, never current. Existing
    checkpoints remain immutable and readable. Projections are derived on demand.
    """

    def __init__(
        self,
        database: Database,
        workspace: Callable[[str], Path],
        *,
        clock: Callable[[], float] = time.time,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.database, self.workspace, self.clock = database, workspace, clock
        self.fault = fault or (lambda _: None)

    def create(self, project_id: str) -> RuntimeState:
        self.workspace(project_id)
        run_id = str(uuid4())
        with self.database.writer() as connection:
            connection.execute(
                "INSERT INTO workflow_context_runs(run_id, project_id) VALUES (?, ?)",
                (run_id, project_id),
            )
        return self.get(project_id, run_id)

    def list_runs(self, project_id: str) -> list[RuntimeState]:
        self.workspace(project_id)
        with self.database.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_context_runs WHERE project_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 100",
                (project_id,),
            ).fetchall()
        return [self._state(row) for row in rows]

    def get(self, project_id: str, run_id: str) -> RuntimeState:
        with self.database.reader() as connection:
            return self._state(self._row(connection, project_id, run_id))

    @staticmethod
    def _state(row: sqlite3.Row) -> RuntimeState:
        values = dict(row)
        raw = values.pop("observation_json")
        values.pop("created_at")
        values["observation"] = json.loads(raw) if raw else None
        return RuntimeState.model_validate(values)

    @staticmethod
    def _row(connection: sqlite3.Connection, project_id: str, run_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM workflow_context_runs WHERE run_id = ? AND project_id = ?",
            (run_id, project_id),
        ).fetchone()
        if row is None:
            raise fail("WORKFLOW_CONTEXT_NOT_FOUND", "没有找到该项目的任务检查点。", 404)
        return cast(sqlite3.Row, row)

    def claim(self, project_id: str, run_id: str, writer_id: str, *, ttl: float = 60) -> Lease:
        if not writer_id or len(writer_id) > 80 or not 1 <= ttl <= 600:
            raise fail("INVALID_WORKFLOW_LEASE", "执行者或租约时长无效。", 422)
        with self.database.writer() as connection:
            row = self._row(connection, project_id, run_id)
            active = row["lease_expiry"] > self.clock()
            if active and row["writer_id"] != writer_id:
                raise fail("WORKFLOW_WRITER_CONFLICT", "另一执行者正在推进此任务，请先核对状态。")
            token = int(row["fencing_token"]) + int(not active)
            expiry = self.clock() + ttl
            connection.execute(
                "UPDATE workflow_context_runs SET writer_id=?, fencing_token=?, lease_expiry=? "
                "WHERE run_id=?",
                (writer_id, token, expiry, run_id),
            )
        return Lease(
            writer_id=writer_id, fencing_token=token, expires_at=expiry, revision=row["revision"]
        )

    def _guard(self, row: sqlite3.Row, lease: Lease, expected_revision: int) -> None:
        if (
            row["writer_id"] != lease.writer_id
            or row["fencing_token"] != lease.fencing_token
            or row["lease_expiry"] <= self.clock()
        ):
            raise fail("WORKFLOW_WRITER_CONFLICT", "执行权已过期或已转交，不能提交旧结果。")
        if row["revision"] != expected_revision:
            raise fail("RECOVERY_REVISION_CONFLICT", "任务进度已更新，请刷新后重试。")

    def release(self, project_id: str, run_id: str, lease: Lease) -> None:
        with self.database.writer() as connection:
            row = self._row(connection, project_id, run_id)
            self._guard(row, lease, row["revision"])
            connection.execute(
                "UPDATE workflow_context_runs SET writer_id=NULL, lease_expiry=0 WHERE run_id=?",
                (run_id,),
            )

    def _root(self, project_id: str, run_id: str) -> Path:
        UUID(run_id)
        workspace = self.workspace(project_id).resolve()
        root = workspace / "context" / run_id
        if not root.resolve().is_relative_to(workspace):
            raise fail("INVALID_EVIDENCE_REFERENCE", "检查点目录不属于当前工作区。", 422)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root

    def _publish(self, project_id: str, run_id: str, name: str, data: bytes) -> str:
        root = self._root(project_id, run_id)
        destination = root / name
        temporary = root / f".staging-{uuid4()}"
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self.fault("staged")
            # Unique artifact names: link is no-clobber, unlike replace.
            os.link(temporary, destination)
            temporary.unlink()
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.fault("published")
        except OSError as exc:
            raise fail("CHECKPOINT_WRITE_FAILED", "检查点未能写入，请检查本地存储。", 507) from exc
        finally:
            temporary.unlink(missing_ok=True)
        return hashlib.sha256(data).hexdigest()

    def _read(self, project_id: str, run_id: str, name: str, digest: str) -> bytes:
        root = self._root(project_id, run_id)
        path = root / name
        if (
            path.name != name
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise fail("CHECKPOINT_INVALID", "检查点引用无效。")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise fail("CHECKPOINT_INVALID", "检查点文件缺失，需核对最近有效版本。") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise fail("CHECKPOINT_INVALID", "检查点哈希不符，需核对最近有效版本。")
        return data

    def save(
        self,
        project_id: str,
        run_id: str,
        snapshot: TaskSnapshot,
        lease: Lease,
        expected_revision: int,
    ) -> StoredCheckpoint:
        checkpoint_id = uuid4()
        data = snapshot.model_dump_json().encode("utf-8")
        if len(data) > 1_000_000:
            raise fail("CHECKPOINT_TOO_LARGE", "检查点过大，请使用产物引用。", 422)
        with self.database.writer() as connection:
            row = self._row(connection, project_id, run_id)
            self._guard(row, lease, expected_revision)
            revision = expected_revision + 1
            name = f"checkpoint-{checkpoint_id}.json"
            digest = self._publish(project_id, run_id, name, data)
            connection.execute(
                "INSERT INTO workflow_context_checkpoints "
                "(checkpoint_id, run_id, revision, sha256, filename) VALUES (?, ?, ?, ?, ?)",
                (str(checkpoint_id), run_id, revision, digest, name),
            )
            connection.execute(
                "UPDATE workflow_context_runs SET latest_checkpoint_id=?, revision=? "
                "WHERE run_id=?",
                (str(checkpoint_id), revision, run_id),
            )
            self._event(connection, run_id, "checkpoint_committed", revision)
            self.fault("before_commit")
            self._guard(row, lease, expected_revision)
        return StoredCheckpoint(
            checkpoint_id=checkpoint_id, revision=revision, sha256=digest, snapshot=snapshot
        )

    def checkpoint(
        self, project_id: str, run_id: str, checkpoint_id: str | None = None
    ) -> StoredCheckpoint:
        state = self.get(project_id, run_id)
        identifier = checkpoint_id or str(state.latest_checkpoint_id or "")
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_context_checkpoints WHERE run_id=? AND checkpoint_id=?",
                (run_id, identifier),
            ).fetchone()
        if row is None:
            raise fail("CHECKPOINT_NOT_FOUND", "任务尚无该检查点。", 404)
        data = self._read(project_id, run_id, row["filename"], row["sha256"])
        try:
            snapshot = TaskSnapshot.model_validate_json(data)
        except ValueError as exc:
            raise fail("CHECKPOINT_INVALID", "检查点内容不符合当前契约。") from exc
        return StoredCheckpoint(
            checkpoint_id=UUID(identifier),
            revision=row["revision"],
            sha256=row["sha256"],
            snapshot=snapshot,
        )

    def valid_history(
        self, project_id: str, run_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("history limit must be 1-100")
        self.get(project_id, run_id)
        with self.database.reader() as connection:
            rows = connection.execute(
                "SELECT checkpoint_id, revision FROM workflow_context_checkpoints "
                "WHERE run_id=? ORDER BY revision DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                self.checkpoint(project_id, run_id, row["checkpoint_id"])
                valid = True
            except ApplicationError:
                valid = False
            result.append(
                {"checkpoint_id": row["checkpoint_id"], "revision": row["revision"], "valid": valid}
            )
        return result

    def transition(
        self,
        project_id: str,
        run_id: str,
        lease: Lease,
        expected_revision: int,
        *,
        event: str,
        **changes: Any,
    ) -> RuntimeState:
        allowed = {
            "state",
            "context_epoch",
            "compaction_attempts",
            "retry_at",
            "last_error",
            "observation",
        }
        if not changes or set(changes) - allowed:
            raise ValueError("invalid context state change")
        with self.database.writer() as connection:
            row = self._row(connection, project_id, run_id)
            self._guard(row, lease, expected_revision)
            values = self._state(row).model_dump()
            values.update(changes, revision=expected_revision + 1)
            validated = RuntimeState.model_validate(values)
            updates = {key: getattr(validated, key) for key in changes}
            if "observation" in updates:
                observation = updates.pop("observation")
                updates["observation_json"] = observation.model_dump_json() if observation else None
            updates["revision"] = validated.revision
            assignments = ", ".join(f"{key}=?" for key in updates)
            connection.execute(
                f"UPDATE workflow_context_runs SET {assignments} WHERE run_id=?",
                (*updates.values(), run_id),
            )
            self._event(connection, run_id, event, validated.revision)
        return self.get(project_id, run_id)

    @staticmethod
    def _event(connection: sqlite3.Connection, run_id: str, event: str, revision: int) -> None:
        connection.execute(
            "INSERT INTO workflow_context_events(run_id, kind, revision) VALUES (?, ?, ?)",
            (run_id, event, revision),
        )

    def write_artifact(
        self,
        project_id: str,
        run_id: str,
        kind: str,
        data: bytes,
        lease: Lease,
        expected_revision: int,
    ) -> str:
        if kind not in {"evidence", "recovery_bundle"} or len(data) > 16_000_000:
            raise fail("INVALID_EVIDENCE", "证据类型或大小不符合要求。", 422)
        artifact_id = str(uuid4())
        name = f"{kind}-{artifact_id}.json"
        with self.database.writer() as connection:
            row = self._row(connection, project_id, run_id)
            self._guard(row, lease, expected_revision)
            digest = self._publish(project_id, run_id, name, data)
            self._guard(row, lease, expected_revision)
            connection.execute(
                "INSERT INTO workflow_context_artifacts "
                "(artifact_id, run_id, kind, sha256, filename, byte_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, run_id, kind, digest, name, len(data)),
            )
        return artifact_id

    def artifact(self, project_id: str, run_id: str, artifact_id: str) -> bytes:
        self.get(project_id, run_id)
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_context_artifacts WHERE run_id=? AND artifact_id=?",
                (run_id, artifact_id),
            ).fetchone()
        if row is None:
            raise fail("INVALID_EVIDENCE_REFERENCE", "证据不属于当前任务。", 404)
        return self._read(project_id, run_id, row["filename"], row["sha256"])
