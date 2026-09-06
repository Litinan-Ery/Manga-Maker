"""Bounded production checkpoints and an optional bridge to the local workflow API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from backend.app.platform.file_store.atomic import atomic_json
from backend.app.workflows.book_production.contracts import UnitReference


def manifest_summary(manifest: Path, state: dict[str, Any]) -> dict[str, Any]:
    entries = [entry for part in state["chapters"] for entry in part.get("pages", {}).values()]
    total = sum(part["page_budget"] for part in state["chapters"])
    generated = sum(
        entry.get("status") == "ready" and bool(entry.get("image_sha256")) for entry in entries
    )
    return {
        "schema_version": "1.0",
        "manifest": str(manifest.resolve()),
        "project_id": state["project_id"],
        "run_id": state.get("workflow_context", {}).get("run_id"),
        "total_pages": total,
        "generated_reported": generated,
        "reviewed_reported": sum(entry.get("visual_review") == "accepted" for entry in entries),
        "requires_reconciliation": True,
        "host_context_replaced": False,
        "next_action": "从应用核对原文、冻结计划和文件哈希。每次推进最多 5 页，再逐页审查。",
        "generation_approval_required": True,
    }


def checkpoint_manifest(manifest: Path, state: dict[str, Any]) -> dict[str, Any]:
    """The manifest remains authoritative. CURRENT changes only after an immutable copy exists."""
    content = json.dumps(state, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(content).hexdigest()
    root = manifest.parent / ".context" / manifest.name
    snapshot = root / "checkpoints" / f"{digest}.json"
    if snapshot.exists():
        existing = json.loads(snapshot.read_text(encoding="utf-8"))
        if (
            hashlib.sha256(
                json.dumps(existing, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            != digest
        ):
            raise ValueError("local checkpoint hash mismatch; existing evidence was preserved")
    else:
        atomic_json(snapshot, state)
    summary = manifest_summary(manifest, state) | {"manifest_sha256": digest}
    atomic_json(
        root / "CURRENT.json", {"sha256": digest, "checkpoint": f"checkpoints/{digest}.json"}
    )
    atomic_json(root / "RECOVERY.json", summary)
    return summary


def manifest_units(state: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for part in state["chapters"]:
        for local in range(1, part["page_budget"] + 1):
            number = len(units) + 1
            entry = part.get("pages", {}).get(str(local), {})
            if entry.get("global_page_number", number) != number:
                raise ValueError("manifest page numbers must cover the full book in order")
            unit = UnitReference(
                unit_id=f"page-{number}",
                page_number=number,
                generation_id=entry.get("generation_id"),
                plan_sha256=entry.get("plan_sha256"),
            )
            units.append(unit.model_dump(mode="json"))
    return units


def checked(response: httpx.Response) -> Any:
    if response.is_error:
        # Never log arbitrary response bodies or session URL fragments.
        raise RuntimeError(
            f"workflow API returned HTTP {response.status_code}; refresh the local app status"
        )
    return response.json()


class RemoteContextBatch:
    def __init__(self, client: httpx.Client, state: dict[str, Any]) -> None:
        self.client, self.state = client, state
        run = state.get("workflow_context", {}).get("run_id")
        self.base = f"/api/v1/projects/{state['project_id']}/workflows/{run}" if run else None
        self.writer = f"producer-{uuid4()}"
        self.lease: dict[str, Any] | None = None
        self.revision = 0

    def __enter__(self) -> RemoteContextBatch:
        if self.base:
            self.lease = checked(
                self.client.post(self.base + "/leases", json={"writer_id": self.writer, "ttl": 600})
            )
            self.revision = self.lease["revision"]
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.base and self.lease:
            response = self.client.post(
                self.base + "/leases/release",
                json={"lease": self.lease, "expected_revision": self.revision},
            )
            if exc_type is None:
                checked(response)

    def checkpoint(self, next_action: str) -> None:
        if not self.base:
            return
        self.lease = checked(
            self.client.post(self.base + "/leases", json={"writer_id": self.writer, "ttl": 600})
        )
        # A takeover or edit cannot be adopted silently by a stale producer.
        if self.lease["revision"] != self.revision:
            raise RuntimeError("workflow revision changed; stop and reconcile before proceeding")
        result = checked(
            self.client.post(
                self.base + "/checkpoints/refresh",
                json={
                    "lease": self.lease,
                    "expected_revision": self.revision,
                    "units": manifest_units(self.state),
                    "next_action": next_action,
                },
            )
        )
        self.revision = result["revision"]

    def preflight(self) -> None:
        if not self.base:
            return
        summary = checked(self.client.get(self.base + "/context"))
        if summary["runtime"]["state"] != "collecting" or not summary.get("can_resume"):
            raise RuntimeError(
                "workflow requires checkpoint recovery or outcome review; no generation started"
            )
