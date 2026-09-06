from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ...errors import ApplicationError
from ...platform.context_budget import token_upper_bound
from ...platform.context_budget.results import bounded_envelope
from ...safety import redact_sensitive
from .contracts import Lease, TaskSnapshot, UnitReference
from .coordinator import WorkflowContextCoordinator
from .ports import ContextDomainReader
from .store import CheckpointStore, fail


class WorkflowContextService:
    def __init__(
        self,
        store: CheckpointStore,
        reader: ContextDomainReader,
        coordinator: WorkflowContextCoordinator | None = None,
    ) -> None:
        self.store, self.reader = store, reader
        self.coordinator = coordinator or WorkflowContextCoordinator(store)

    def refresh_units(
        self,
        project: str,
        run: str,
        lease: Lease,
        revision: int,
        units: tuple[UnitReference, ...],
        next_action: str,
    ) -> dict[str, Any]:
        prior = self.store.checkpoint(project, run).snapshot
        previous = {unit.unit_id: unit for unit in prior.units}
        merged: list[UnitReference] = []
        for unit in units:
            old = previous.get(unit.unit_id)
            if old is None or old.page_number != unit.page_number:
                raise fail("CHECKPOINT_SCOPE_CHANGED", "生产清单与检查点页码范围不一致。")
            if old.generation_id and (
                old.generation_id != unit.generation_id or old.plan_sha256 != unit.plan_sha256
            ):
                raise fail("GENERATION_PLAN_CHANGED", "已有页面的冻结计划发生变化，请明确重审。")
            if old.page_id and unit.page_id and old.page_id != unit.page_id:
                raise fail("GENERATION_PAGE_MISMATCH", "已有页面引用发生变化。")
            merged.append(
                unit.model_copy(
                    update={"review": old.review, "page_id": old.page_id or unit.page_id}
                )
            )
        snapshot = TaskSnapshot.model_validate(
            prior.model_dump() | {"units": tuple(merged), "next_action": next_action}
        )
        return self.save(project, run, snapshot, lease, revision)

    def save(
        self, project: str, run: str, snapshot: TaskSnapshot, lease: Lease, revision: int
    ) -> dict[str, Any]:
        state = self.store.get(project, run)
        if state.latest_checkpoint_id is not None:
            prior = self.store.checkpoint(project, run).snapshot
            preserved = ("objective", "constraints", "acceptance_criteria", "source_refs")
            if any(getattr(prior, key) != getattr(snapshot, key) for key in preserved):
                raise fail("CHECKPOINT_SCOPE_CHANGED", "目标或限制发生变化，请建立新的明确任务。")
            if prior.units and {(u.unit_id, u.page_number) for u in prior.units} != {
                (u.unit_id, u.page_number) for u in snapshot.units
            }:
                raise fail("CHECKPOINT_SCOPE_CHANGED", "检查点不能丢失或替换既有页码范围。")
        for evidence in snapshot.check_refs:
            self.store.artifact(project, run, str(evidence))
        for unit in snapshot.units:
            if unit.review is not None and unit.review.result == "accepted":
                if unit.review.evidence_id is None:
                    raise fail(
                        "REVIEW_EVIDENCE_REQUIRED", "接受记录必须引用当前任务的审查证据。", 422
                    )
                self.store.artifact(project, run, str(unit.review.evidence_id))
        saved = self.store.save(project, run, snapshot, lease, revision)
        return {
            "checkpoint_id": str(saved.checkpoint_id),
            "sha256": saved.sha256,
            "revision": saved.revision,
        }

    def reconcile(self, project: str, run: str) -> dict[str, Any]:
        state = self.store.get(project, run)
        checkpoint = self.store.checkpoint(project, run)
        snapshot = checkpoint.snapshot
        problems: list[dict[str, Any]] = []
        for evidence in snapshot.check_refs:
            try:
                self.store.artifact(project, run, str(evidence))
            except ApplicationError:
                problems.append({"code": "CHECK_EVIDENCE_INVALID", "id": str(evidence)})
        for source in snapshot.source_refs:
            if source.kind == "evidence":
                try:
                    data = self.store.artifact(project, run, str(source.artifact_id))
                    valid = hashlib.sha256(data).hexdigest() == source.sha256
                except ApplicationError:
                    valid = False
            else:
                valid = self.reader.source_matches(project, source)
            if not valid:
                problems.append({"code": "SOURCE_VERSION_CHANGED", "id": str(source.artifact_id)})
        units: list[dict[str, Any]] = []
        counts = {
            "total": len(snapshot.units),
            "generated": 0,
            "lettered": 0,
            "reviewed": 0,
            "needs_review": 0,
            "failed": 0,
        }
        for unit in sorted(snapshot.units, key=lambda item: item.page_number):
            item = self.reader.inspect_unit(project, unit)
            if unit.review is not None and unit.review.result == "accepted":
                try:
                    if unit.review.evidence_id is None:
                        raise fail("REVIEW_EVIDENCE_INVALID", "审查记录缺少证据。")
                    self.store.artifact(project, run, str(unit.review.evidence_id))
                except ApplicationError:
                    item.update(reviewed=False, blocked=True, code="REVIEW_EVIDENCE_INVALID")
            item.update(unit_id=unit.unit_id, page_number=unit.page_number)
            for key in ("generated", "lettered", "reviewed"):
                counts[key] += int(bool(item.get(key)))
            if item.get("blocked"):
                problems.append(
                    {"code": item.get("code", "UNIT_NEEDS_REVIEW"), "unit_id": unit.unit_id}
                )
            if item.get("status") == "needs_review":
                counts["needs_review"] += 1
            if item.get("status") == "failed":
                counts["failed"] += 1
            units.append(item)
        next_unit = next((item["unit_id"] for item in units if not item.get("reviewed")), None)
        # A workflow report never supplies a PageApproval or claims final export completion.
        basis = {
            "checkpoint_sha256": checkpoint.sha256,
            "revision": state.revision,
            "units": units,
            "problems": problems,
        }
        plan_hash = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
        return {
            "run_id": run,
            "revision": state.revision,
            "counts": counts,
            "checkpoint_id": str(checkpoint.checkpoint_id),
            "checkpoint_sha256": checkpoint.sha256,
            "units": units,
            "problems": problems,
            "can_resume": not problems,
            "next_unit": next_unit,
            "plan_hash": plan_hash,
            "external_requests_started": 0,
            "export_approved": False,
        }

    def summary(self, project: str, run: str) -> dict[str, Any]:
        state = self.store.get(project, run)
        result: dict[str, Any] = {
            "runtime": state.model_dump(mode="json"),
            "capabilities": self.coordinator.capabilities(),
            "history": self.store.valid_history(project, run, limit=5),
        }
        try:
            plan = self.reconcile(project, run)
            snapshot = self.store.checkpoint(project, run).snapshot
            result.update(
                objective=snapshot.objective[:240],
                stage=snapshot.stage,
                next_action=snapshot.next_action[:240],
                counts=plan["counts"],
                next_unit=plan["next_unit"],
                can_resume=plan["can_resume"],
                problem_count=len(plan["problems"]),
                problems=plan["problems"][:5],
                plan_hash=plan["plan_hash"],
                export_approved=False,
            )
        except ApplicationError as exc:
            result.update(can_resume=False, error_code=exc.code, error_message=exc.message)
        return result

    def evidence(
        self,
        project: str,
        run: str,
        artifact: str | None = None,
        *,
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        if artifact is None:
            items = self.reconcile(project, run)["units"]
            reference = f"workflow:{run}:units"
        else:
            data = json.loads(self.store.artifact(project, run, artifact))
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise fail("INVALID_EVIDENCE_REFERENCE", "该产物不是分页证据列表。", 422)
            items, reference = data, artifact
        try:
            return bounded_envelope(items, artifact_ref=reference, cursor=cursor, limit=limit)
        except ValueError as exc:
            raise fail("INVALID_EVIDENCE_PAGE", "证据分页参数或预算无效。", 422) from exc

    def record_evidence(
        self, project: str, run: str, items: list[dict[str, Any]], lease: Lease, revision: int
    ) -> dict[str, Any]:
        safe = sanitize_evidence(redact_sensitive(items))
        data = json.dumps(safe, ensure_ascii=False).encode("utf-8")
        reference = self.store.write_artifact(project, run, "evidence", data, lease, revision)
        return bounded_envelope(safe, artifact_ref=reference)

    def evidence_detail(
        self, project: str, run: str, artifact: str, index: int, offset: int = 0
    ) -> dict[str, Any]:
        data = json.loads(self.store.artifact(project, run, artifact))
        if not isinstance(data, list) or not 0 <= index < len(data):
            raise fail("INVALID_EVIDENCE_PAGE", "证据条目不存在。", 404)
        text = json.dumps(data[index], ensure_ascii=False)
        if not 0 <= offset <= len(text):
            raise fail("INVALID_EVIDENCE_PAGE", "证据范围无效。", 422)
        end = min(len(text), offset + 300)
        return {
            "artifact_ref": artifact,
            "index": index,
            "offset": offset,
            "total_chars": len(text),
            "text": text[offset:end],
            "next_offset": end if end < len(text) else None,
        }

    def bundle(self, project: str, run: str, lease: Lease, revision: int) -> dict[str, Any]:
        checkpoint = self.store.checkpoint(project, run)
        snapshot = checkpoint.snapshot
        plan = self.reconcile(project, run)
        # Preserve all goal/constraint text. Detailed units remain addressable by checkpoint ID.
        body = {
            "schema_version": "1.0",
            "project_id": project,
            "run_id": run,
            "checkpoint_id": str(checkpoint.checkpoint_id),
            "checkpoint_sha256": checkpoint.sha256,
            "objective": snapshot.objective,
            "constraints": snapshot.constraints,
            "acceptance_criteria": snapshot.acceptance_criteria,
            "stage": snapshot.stage,
            "next_action": snapshot.next_action,
            "open_questions": snapshot.open_questions,
            "counts": plan["counts"],
            "next_unit": plan["next_unit"],
            "problem_count": len(plan["problems"]),
            "problems": plan["problems"][:5],
            "expected_revision": plan["revision"],
            "recovery_plan_hash": plan["plan_hash"],
            "evidence_endpoint": f"/api/v1/projects/{project}/workflows/{run}/evidence",
            "requires_reconciliation": True,
            "creates_generation_approval": False,
            "host_context_replaced": False,
        }
        text = json.dumps(body, ensure_ascii=False, indent=2)
        if token_upper_bound(text) > 8000:
            raise fail(
                "RECOVERY_BUNDLE_TOO_LARGE",
                "目标与限制超出交接预算，请明确精简，不能静默删除。",
                422,
            )
        reference = self.store.write_artifact(
            project, run, "recovery_bundle", text.encode(), lease, revision
        )
        return {
            "artifact_id": reference,
            "bundle": body,
            "token_upper_bound": token_upper_bound(text),
            "host_context_replaced": False,
        }

    def resume(
        self, project: str, run: str, lease: Lease, revision: int, plan_hash: str
    ) -> dict[str, Any]:
        plan = self.reconcile(project, run)
        if plan["revision"] != revision or plan["plan_hash"] != plan_hash:
            raise fail("RECOVERY_REVISION_CONFLICT", "恢复计划已过期，请重新核对当前状态。")
        if not plan["can_resume"]:
            raise fail("RECOVERY_REQUIRES_REVIEW", "来源、页面或在途请求需要先核对，未发起生成。")
        state = self.store.get(project, run)
        if state.state in {"compacting", "backoff"}:
            raise fail("CONTEXT_COMPACTION_PENDING", "上下文操作尚未结束，请先核对或交接。")
        # Local workflow readiness only; the external host's history has not been replaced.
        updated = self.store.transition(
            project,
            run,
            lease,
            revision,
            event="recovery_reconciled",
            state="collecting",
            last_error=None,
        )
        return {
            "runtime": updated.model_dump(mode="json"),
            "next_unit": plan["next_unit"],
            "external_requests_started": 0,
            "host_context_replaced": False,
            "generation_approval_required": True,
        }


def sanitize_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_evidence(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(?:data:image/[^;]+;base64,)[A-Za-z0-9+/=]+", "[IMAGE_OMITTED]", value)
        value = re.sub(r"(?i)[#?&](?:session|csrf)=[^\s\"&]+", "[REDACTED]", value)
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value
