"""Register a complete production manifest, print a bounded summary, or export a handoff.

All commands are local/read-only with respect to image generation. No command approves
or starts provider requests. Use: python -m scripts.workflow_context MANIFEST ACTION.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.platform.file_store.atomic import atomic_json, manifest_lock
from backend.app.workflows.book_production.contracts import TaskSnapshot
from scripts.produce_authored_manga import live_client, read, save
from scripts.production_context import checked, checkpoint_manifest, manifest_units


def execute(
    manifest: Path, action: str, session: Path, objective: str | None, output: Path | None
) -> dict[str, Any]:
    with manifest_lock(manifest):
        state = read(manifest)
        if action == "summary" and not state.get("workflow_context"):
            return checkpoint_manifest(manifest, state)
        with live_client(session) as client:
            base = f"/api/v1/projects/{state['project_id']}/workflows"
            if action == "register":
                if state.get("workflow_context"):
                    raise ValueError(
                        "this manifest already has a workflow; reconcile its existing run"
                    )
                if not objective or not objective.strip():
                    raise ValueError("register requires --objective with the complete task goal")
                source = state.get("source")
                if (
                    source
                    and hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
                    != source["sha256"]
                ):
                    raise ValueError("original source changed")
                chapters = checked(
                    client.get(f"/api/v1/projects/{state['project_id']}/source/chapters")
                )["chapters"]
                source_refs = [
                    {
                        "kind": "source",
                        "artifact_id": chapter["chapter_id"],
                        "sha256": chapter["text_sha256"],
                    }
                    for chapter in chapters
                ]
                snapshot = TaskSnapshot.model_validate(
                    {
                        "objective": objective,
                        "constraints": [
                            "保留完整章节与页面预算",
                            "使用已批准的冻结计划和原有生成审批",
                        ]
                        + ([f"原始文件 SHA256: {source['sha256']}"] if source else []),
                        "acceptance_criteria": [
                            "全部计划页面逐页审查",
                            "整书连续性、导出与重开验收",
                        ],
                        "stage": "production",
                        "next_action": "核对下一页计划，每批最多 5 页",
                        "source_refs": source_refs,
                        "units": manifest_units(state),
                    }
                )
                result = checked(
                    client.post(base, json={"snapshot": snapshot.model_dump(mode="json")})
                )
                state["workflow_context"] = {"run_id": result["runtime"]["run_id"]}
                save(manifest, state)
                return result
            run = state.get("workflow_context", {}).get("run_id")
            if not run:
                raise ValueError(
                    "register the complete manifest before exporting a verified handoff"
                )
            base += f"/{run}"
            if action == "summary":
                return checked(client.get(base + "/context"))
            if action != "handoff":
                raise ValueError("unknown context command")
            lease = checked(
                client.post(base + "/leases", json={"writer_id": f"handoff-{uuid4()}", "ttl": 600})
            )
            try:
                result = checked(
                    client.post(
                        base + "/recovery-bundles",
                        json={"lease": lease, "expected_revision": lease["revision"]},
                    )
                )
                destination = (
                    output or manifest.parent / ".context" / manifest.name / "RECOVERY-BUNDLE.json"
                )
                atomic_json(destination, result["bundle"])
                return {
                    "handoff_file": str(destination.resolve()),
                    "artifact_id": result["artifact_id"],
                    "token_upper_bound": result["token_upper_bound"],
                    "host_context_replaced": False,
                }
            finally:
                checked(
                    client.post(
                        base + "/leases/release",
                        json={"lease": lease, "expected_revision": lease["revision"]},
                    )
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("action", choices=("register", "summary", "handoff"))
    parser.add_argument("--objective")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--session-file", type=Path, default=Path(".manga-maker/app-runtime/session.json")
    )
    args = parser.parse_args()
    result = execute(args.manifest, args.action, args.session_file, args.objective, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
