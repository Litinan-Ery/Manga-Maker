import pytest

from backend.app.platform.context_budget import ContextObservation
from backend.app.workflows.book_production.coordinator import WorkflowContextCoordinator
from backend.app.workflows.book_production.host import HostCapabilities, HostFailure
from backend.app.workflows.book_production.store import CheckpointStore
from tests.workflows.test_context_checkpoints import context_store, snapshot

__all__ = ["context_store"]


class TestHost:
    __test__ = False

    def __init__(self, result: ContextObservation | Exception) -> None:
        self.result, self.calls = result, []

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(usage=True, compaction=True)

    def compact(self, *, checkpoint_id: str, request_id: str) -> ContextObservation:
        self.calls.append(request_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_failure_backoff_survives_restart_and_stops_at_two(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, clock = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer", ttl=600)
    store.save(project, run, snapshot(), lease, 0)
    host = TestHost(HostFailure())
    coordinator = WorkflowContextCoordinator(store, host=host)
    first = coordinator.compact(project, run, lease, 1)
    assert first.state == "backoff" and first.compaction_attempts == 1
    restarted = WorkflowContextCoordinator(store, host=host)
    assert restarted.compact(project, run, lease, first.revision) == first
    assert len(host.calls) == 1
    clock[0] += 31
    second = restarted.compact(project, run, lease, first.revision)
    assert second.state == "handoff_required" and second.compaction_attempts == 2
    for _ in range(3):
        second = restarted.compact(project, run, lease, second.revision)
    assert len(host.calls) == 2
    assert len(set(host.calls)) == 2


def test_unsupported_host_returns_manual_handoff_without_model_call(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer")
    store.save(project, run, snapshot(), lease, 0)
    coordinator = WorkflowContextCoordinator(store)
    state = coordinator.compact(project, run, lease, 1)
    assert state.state == "handoff_required"
    assert state.last_error == "HOST_CAPABILITY_UNAVAILABLE"
    assert state.compaction_attempts == 0


@pytest.mark.parametrize(
    "measurement,tokens", [("actual", 20000), ("stale", 20000), ("actual", 200000)]
)
def test_host_success_requires_new_small_actual_observation(
    context_store: tuple[CheckpointStore, str, list[float]],
    measurement: str,
    tokens: int,
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer")
    store.save(project, run, snapshot(), lease, 0)
    host = TestHost(
        ContextObservation.model_validate(
            {
                "window_id": "new",
                "measurement": measurement,
                "current_tokens": tokens,
                "context_limit": 258400,
            }
        )
    )
    state = WorkflowContextCoordinator(store, host=host).compact(project, run, lease, 1)
    assert state.state == (
        "rehydrating" if measurement == "actual" and tokens == 20000 else "handoff_required"
    )
    assert store.checkpoint(project, run).snapshot.objective == "完成完整 100 页漫画"


def test_process_loss_mid_compaction_is_not_blindly_replayed(
    context_store: tuple[CheckpointStore, str, list[float]],
) -> None:
    store, project, _ = context_store
    run = str(store.create(project).run_id)
    lease = store.claim(project, run, "writer")
    store.save(project, run, snapshot(), lease, 0)
    state = store.transition(
        project,
        run,
        lease,
        1,
        event="compaction_attempted",
        state="compacting",
        compaction_attempts=1,
    )
    host = TestHost(HostFailure())
    recovered = WorkflowContextCoordinator(store, host=host).compact(
        project,
        run,
        lease,
        state.revision,
    )
    assert recovered.state == "handoff_required"
    assert host.calls == []
