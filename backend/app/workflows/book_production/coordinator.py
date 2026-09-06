from __future__ import annotations

from dataclasses import asdict

from ...platform.context_budget import (
    BudgetDecision,
    ContextObservation,
    ContextPolicy,
    InputEvent,
    decide_budget,
)
from .contracts import Lease, RuntimeState
from .host import ContextHostPort, HostFailure, LocalHandoffHost
from .store import CheckpointStore


class WorkflowContextCoordinator:
    def __init__(
        self,
        store: CheckpointStore,
        *,
        host: ContextHostPort | None = None,
        policy: ContextPolicy | None = None,
    ) -> None:
        self.store = store
        self.host = host or LocalHandoffHost()
        self.policy = policy or ContextPolicy()

    def capabilities(self) -> dict[str, bool]:
        return asdict(self.host.capabilities())

    def observe(
        self,
        project: str,
        run: str,
        lease: Lease,
        revision: int,
        observation: ContextObservation,
        pending: tuple[InputEvent, ...] = (),
        planned_output: int = 0,
    ) -> tuple[RuntimeState, BudgetDecision]:
        self.store.checkpoint(project, run)
        decision = decide_budget(
            observation, pending, planned_output=planned_output, policy=self.policy
        )
        state = self.store.get(project, run)
        # A new report is evidence, not permission to clear a previous failure loop.
        next_state: str = state.state
        if state.state == "collecting":
            next_state = {
                "continue": "collecting",
                "checkpoint": "checkpointing",
                "compact": "checkpointing",
                "handoff": "handoff_required",
            }[decision.action]
        updated = self.store.transition(
            project,
            run,
            lease,
            revision,
            event="context_budget_observed",
            state=next_state,
            observation=observation,
            last_error=decision.reason if decision.action != "continue" else None,
        )
        return updated, decision

    def compact(self, project: str, run: str, lease: Lease, revision: int) -> RuntimeState:
        checkpoint = self.store.checkpoint(project, run)
        state = self.store.get(project, run)
        if state.revision != revision:
            # Delegate to the same CAS guard, without invoking the host.
            return self.store.transition(
                project, run, lease, revision, event="compaction_conflict", state=state.state
            )
        if not self.host.capabilities().compaction:
            return self.store.transition(
                project,
                run,
                lease,
                revision,
                event="handoff_required",
                state="handoff_required",
                last_error="HOST_CAPABILITY_UNAVAILABLE",
            )
        if state.state == "compacting" or state.compaction_attempts >= 2:
            return self.store.transition(
                project,
                run,
                lease,
                revision,
                event="handoff_required",
                state="handoff_required",
                last_error="CONTEXT_COMPACTION_FAILED",
            )
        if state.state == "handoff_required":
            return state
        if state.state == "backoff" and self.store.clock() < state.retry_at:
            return state
        started = self.store.transition(
            project,
            run,
            lease,
            revision,
            event="compaction_attempted",
            state="compacting",
            compaction_attempts=state.compaction_attempts + 1,
        )
        try:
            observed = self.host.compact(
                checkpoint_id=str(checkpoint.checkpoint_id),
                request_id=f"{run}:{state.context_epoch}:{started.compaction_attempts}",
            )
        except (HostFailure, TimeoutError, ConnectionError) as exc:
            retryable = not isinstance(exc, HostFailure) or exc.retryable
            exhausted = not retryable or started.compaction_attempts >= 2
            return self.store.transition(
                project,
                run,
                lease,
                started.revision,
                event="compaction_failed",
                state="handoff_required" if exhausted else "backoff",
                last_error="CONTEXT_COMPACTION_FAILED",
                retry_at=0 if exhausted else self.store.clock() + 30,
            )
        if (
            observed.measurement != "actual"
            or observed.current_tokens is None
            or observed.context_limit is None
            or observed.current_tokens >= self.policy.recovered_ratio * observed.context_limit
            or (state.observation is not None and observed.window_id == state.observation.window_id)
        ):
            return self.store.transition(
                project,
                run,
                lease,
                started.revision,
                event="handoff_required",
                state="handoff_required",
                last_error="CONTEXT_COMPACTION_UNVERIFIED",
            )
        return self.store.transition(
            project,
            run,
            lease,
            started.revision,
            event="compaction_succeeded",
            state="rehydrating",
            observation=observed,
            context_epoch=state.context_epoch + 1,
            compaction_attempts=0,
            retry_at=0,
            last_error=None,
        )
