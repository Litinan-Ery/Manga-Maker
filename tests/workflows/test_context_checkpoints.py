from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.database import Database
from backend.app.errors import ApplicationError
from backend.app.projects import ProjectService
from backend.app.workflows.book_production.contracts import TaskSnapshot
from backend.app.workflows.book_production.store import CheckpointStore


@pytest.fixture
def context_store(tmp_path: Path) -> tuple[CheckpointStore, str, list[float]]:
    database = Database(tmp_path / "context.db")
    database.migrate()
    projects = ProjectService(database, tmp_path / "projects")
    project_id = projects.create("恢复测试").project_id
    clock = [1000.0]
    store = CheckpointStore(database, projects.workspace_path, clock=lambda: clock[0])
    return store, project_id, clock


def snapshot(action: str = "继续核对下一页") -> TaskSnapshot:
    return TaskSnapshot(
        objective="完成完整 100 页漫画",
        constraints=("保留全部章节",),
        acceptance_criteria=("全部页面通过逐页审查",),
        stage="review",
        next_action=action,
    )


def test_restart_reads_immutable_checkpoint_and_fences_stale_revision(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "first")
    saved = store.save(project, run, snapshot(), lease, 0)
    restarted = CheckpointStore(store.database, store.workspace, clock=store.clock)
    assert restarted.checkpoint(project, run) == saved
    with pytest.raises(ApplicationError) as error:
        store.save(project, run, snapshot("不能覆盖"), lease, 0)
    assert error.value.code == "RECOVERY_REVISION_CONFLICT"
    assert restarted.checkpoint(project, run).snapshot == snapshot()


def test_expired_writer_cannot_submit_after_takeover(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, clock = context_store
    run = str(store.create(project).run_id)
    old = store.claim(project, run, "old", ttl=10)
    clock[0] += 11
    new = store.claim(project, run, "new")
    assert new.fencing_token > old.fencing_token
    with pytest.raises(ApplicationError, match="执行权"):
        store.save(project, run, snapshot(), old, 0)
    store.save(project, run, snapshot(), new, 0)
    with pytest.raises(ApplicationError):
        store.release(project, run, old)


def test_only_one_of_two_writers_can_claim(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)

    def claim(writer: str) -> bool:
        other = CheckpointStore(Database(store.database.path), store.workspace, clock=store.clock)
        try:
            other.claim(project, run, writer)
            return True
        except ApplicationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(claim, ("a", "b"))) == 1


@pytest.mark.parametrize("point", ["staged", "published", "before_commit"])
def test_crash_never_publishes_partial_checkpoint(
    context_store: tuple[CheckpointStore, str, list[float]],
    point: str,
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer")
    first = store.save(project, run, snapshot(), lease, 0)

    def fault(at: str) -> None:
        if at == point:
            raise RuntimeError("simulated power loss")

    store.fault = fault
    with pytest.raises(RuntimeError, match="power loss"):
        store.save(project, run, snapshot("second"), lease, 1)
    assert store.checkpoint(project, run) == first
    assert store.get(project, run).revision == 1
    assert len(store.valid_history(project, run)) == 1


def test_corrupt_latest_reports_valid_older_checkpoint_without_silent_rollback(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer")
    first = store.save(project, run, snapshot(), lease, 0)
    second = store.save(project, run, snapshot("second"), lease, 1)
    path = store.workspace(project) / "context" / run / f"checkpoint-{second.checkpoint_id}.json"
    path.write_text("corrupt")
    with pytest.raises(ApplicationError) as error:
        store.checkpoint(project, run)
    assert error.value.code == "CHECKPOINT_INVALID"
    history = store.valid_history(project, run)
    assert [item["valid"] for item in history] == [False, True]
    assert store.checkpoint(project, run, str(first.checkpoint_id)) == first
    assert str(store.get(project, run).latest_checkpoint_id) == str(second.checkpoint_id)


def test_artifacts_are_scoped_hashed_and_symlink_safe(
    context_store: tuple[CheckpointStore, str, list[float]],
    tmp_path: Path,
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer")
    artifact = store.write_artifact(project, run, "evidence", b'[{"status":"ok"}]', lease, 0)
    assert store.artifact(project, run, artifact) == b'[{"status":"ok"}]'
    other = str(store.create(project).run_id)
    with pytest.raises(ApplicationError):
        store.artifact(project, other, artifact)
    with pytest.raises(ApplicationError):
        store.artifact(str(uuid4()), run, artifact)
    path = store.workspace(project) / "context" / run / f"evidence-{artifact}.json"
    path.unlink()
    secret = tmp_path / "private.txt"
    secret.write_text("must not read")
    path.symlink_to(secret)
    with pytest.raises(ApplicationError, match="引用无效"):
        store.artifact(project, run, artifact)


@pytest.mark.parametrize("artifact", [False, True])
def test_lease_expiring_during_file_publication_cannot_commit(
    context_store: tuple[CheckpointStore, str, list[float]],
    artifact: bool,
) -> None:
    store, project, clock = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer", ttl=1)

    def expire(point: str) -> None:
        if point == "published":
            clock[0] += 2

    store.fault = expire
    with pytest.raises(ApplicationError):
        if artifact:
            store.write_artifact(project, run, "evidence", b"[]", lease, 0)
        else:
            store.save(project, run, snapshot(), lease, 0)
    assert store.get(project, run).revision == 0
    assert store.get(project, run).latest_checkpoint_id is None
