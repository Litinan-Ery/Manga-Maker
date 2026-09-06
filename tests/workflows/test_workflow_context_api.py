import hashlib
import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.workflows.book_production.contracts import ArtifactPointer, UnitReference


class DomainReader:
    def __init__(self) -> None:
        self.blocked = False
        self.ready = 0
        self.reviewed = 0
        self.source_valid = True

    def source_matches(self, project_id: str, source: ArtifactPointer) -> bool:
        return self.source_valid

    def inspect_unit(self, project_id: str, unit: UnitReference) -> dict[str, Any]:
        return {
            "status": "needs_review"
            if self.blocked
            else "ready"
            if unit.page_number <= self.ready
            else "draft",
            "generated": unit.page_number <= self.ready,
            "lettered": unit.page_number <= self.ready,
            "reviewed": unit.page_number <= self.reviewed,
            "blocked": self.blocked,
            "image_sha256": hashlib.sha256(str(unit.page_number).encode()).hexdigest(),
        }


@pytest.fixture
def workflow(
    client: TestClient, session_headers: dict[str, str]
) -> tuple[str, dict[str, Any], DomainReader]:
    project = client.post(
        "/api/v1/projects", headers=session_headers, json={"title": "100 页恢复"}
    ).json()["project_id"]
    reader = DomainReader()
    client.app.state.container.workflow_context.reader = reader
    task = {
        "objective": "完成 100 页，保持三个时代完整",
        "constraints": ["固定批准的零 Anlas 计划"],
        "acceptance_criteria": ["100 页逐页核验", "导出与重开"],
        "stage": "production",
        "next_action": "核对未完成页",
        "units": [{"unit_id": f"page-{n}", "page_number": n} for n in range(1, 101)],
    }
    response = client.post(
        f"/api/v1/projects/{project}/workflows", headers=session_headers, json={"snapshot": task}
    )
    assert response.status_code == 201, response.text
    run = response.json()["runtime"]["run_id"]
    return f"/api/v1/projects/{project}/workflows/{run}", task, reader


def claim(client: TestClient, base: str, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        base + "/leases", headers=headers, json={"writer_id": "test", "ttl": 600}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_api_auth_scope_and_evidence_paths(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, _, _ = workflow
    assert client.get(base + "/context").status_code == 401
    assert (
        client.post(
            base + "/recovery-plan",
            headers={
                "X-Manga-Maker-Session": session_headers["X-Manga-Maker-Session"],
            },
        ).status_code
        == 403
    )
    foreign = base.replace(base.split("/")[4], str(uuid4()))
    assert client.get(foreign + "/context", headers=session_headers).status_code == 404
    assert (
        client.get(
            base + "/evidence?artifact_id=../../private", headers=session_headers
        ).status_code
        == 422
    )
    assert client.get(base + "/evidence?limit=99999", headers=session_headers).status_code == 422


def test_progress_and_recovery_bundle_preserve_full_scope(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, task, reader = workflow
    reader.ready, reader.reviewed = 40, 20
    summary = client.get(base + "/context", headers=session_headers).json()
    assert summary["counts"] == {
        "total": 100,
        "generated": 40,
        "lettered": 40,
        "reviewed": 20,
        "needs_review": 0,
        "failed": 0,
    }
    assert summary["next_unit"] == "page-21"
    assert summary["export_approved"] is False
    lease = claim(client, base, session_headers)
    bundle = client.post(
        base + "/recovery-bundles",
        headers=session_headers,
        json={
            "lease": lease,
            "expected_revision": 1,
        },
    )
    assert bundle.status_code == 200, bundle.text
    data = bundle.json()
    assert data["token_upper_bound"] <= 8000
    assert data["host_context_replaced"] is False
    for key in ("objective", "constraints", "acceptance_criteria"):
        assert data["bundle"][key] == task[key]
    assert "units" not in data["bundle"]
    assert data["bundle"]["counts"]["total"] == 100


def test_changed_remote_state_and_unknown_attempt_prevent_resume(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, _, reader = workflow
    lease = claim(client, base, session_headers)
    plan = client.post(base + "/recovery-plan", headers=session_headers).json()
    reader.blocked = True
    response = client.post(
        base + "/resume",
        headers=session_headers,
        json={
            "lease": lease,
            "expected_revision": 1,
            "plan_hash": plan["plan_hash"],
        },
    )
    assert response.status_code == 409
    plan = client.post(base + "/recovery-plan", headers=session_headers).json()
    assert plan["external_requests_started"] == 0
    assert plan["can_resume"] is False
    response = client.post(
        base + "/resume",
        headers=session_headers,
        json={
            "lease": lease,
            "expected_revision": 1,
            "plan_hash": plan["plan_hash"],
        },
    )
    assert response.json()["error"]["code"] == "RECOVERY_REQUIRES_REVIEW"


def test_checkpoint_cannot_silently_shrink_goal_or_units(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, task, _ = workflow
    lease = claim(client, base, session_headers)
    for smaller in ({**task, "objective": "只完成一页"}, {**task, "units": task["units"][:1]}):
        response = client.post(
            base + "/checkpoints",
            headers=session_headers,
            json={
                "lease": lease,
                "expected_revision": 1,
                "snapshot": smaller,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CHECKPOINT_SCOPE_CHANGED"


def test_evidence_is_paginated_redacted_and_cross_run_safe(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, _, _ = workflow
    lease = claim(client, base, session_headers)
    response = client.post(
        base + "/evidence",
        headers=session_headers,
        json={
            "lease": lease,
            "expected_revision": 1,
            "items": [
                {
                    "id": n,
                    "status": "failed" if n == 99 else "ok",
                    "api_key": "never-expose",
                    "log": (
                        "https://local/#session=secret-session&csrf=secret-csrf "
                        "Bearer secret-bearer data:image/png;base64,c2VjcmV0"
                    ),
                    "detail": "x" * 4000,
                }
                for n in range(100)
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert "never-expose" not in response.text
    assert len(json.dumps(response.json(), ensure_ascii=False).encode()) <= 6000
    ref = response.json()["artifact_ref"]
    last = client.get(
        base + f"/evidence?artifact_id={ref}&cursor=99", headers=session_headers
    ).json()
    assert last["failure_count"] == 1
    assert last["items"][0]["failed"] is True
    chunks: list[str] = []
    offset = 0
    while True:
        response = client.get(
            base + f"/evidence/{ref}/items/99?offset={offset}", headers=session_headers
        )
        assert response.status_code == 200
        detail = response.json()
        assert len(json.dumps(detail, ensure_ascii=False).encode()) <= 2000
        chunks.append(detail["text"])
        if detail["next_offset"] is None:
            break
        offset = detail["next_offset"]
    recovered = json.loads("".join(chunks))
    assert recovered["detail"] == "x" * 4000
    assert recovered["status"] == "failed"
    for secret in ("never-expose", "secret-session", "secret-csrf", "secret-bearer", "c2VjcmV0"):
        assert secret not in "".join(chunks)


def test_refresh_preserves_review_and_rejects_duplicate_units(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, task, _ = workflow
    lease = claim(client, base, session_headers)
    evidence = client.post(
        base + "/evidence",
        headers=session_headers,
        json={"lease": lease, "expected_revision": 1, "items": [{"page": 1, "result": "accepted"}]},
    ).json()["artifact_ref"]
    task["units"][0]["review"] = {
        "result": "accepted",
        "scope": "panel/text",
        "rule_version": "1",
        "image_sha256": "a" * 64,
        "render_sha256": "b" * 64,
        "evidence_id": evidence,
    }
    saved = client.post(
        base + "/checkpoints",
        headers=session_headers,
        json={"lease": lease, "expected_revision": 1, "snapshot": task},
    )
    assert saved.status_code == 200
    revision = saved.json()["revision"]
    units = [
        {"unit_id": unit["unit_id"], "page_number": unit["page_number"]} for unit in task["units"]
    ]
    request = {
        "lease": lease,
        "expected_revision": revision,
        "units": units,
        "next_action": "继续核对",
    }
    refreshed = client.post(base + "/checkpoints/refresh", headers=session_headers, json=request)
    assert refreshed.status_code == 200, refreshed.text
    store = client.app.state.container.workflow_context.store
    checkpoint = store.checkpoint(base.split("/")[4], base.split("/")[6])
    assert str(checkpoint.snapshot.units[0].review.evidence_id) == evidence
    request["units"] = units + units[:1]
    assert (
        client.post(
            base + "/checkpoints/refresh", headers=session_headers, json=request
        ).status_code
        == 422
    )


def test_duplicate_usage_events_are_rejected_before_state_mutation(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, _, _ = workflow
    lease = claim(client, base, session_headers)
    response = client.post(
        base + "/observations",
        headers=session_headers,
        json={
            "lease": lease,
            "expected_revision": 1,
            "observation": {"window_id": "test"},
            "pending": [{"sequence": 1}, {"sequence": 1}],
        },
    )
    assert response.status_code == 422
    assert client.get(base + "/context", headers=session_headers).json()["runtime"]["revision"] == 1


def test_capture_from_real_domain_reads_frozen_plans_without_generation(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    from tests.test_generation_queue import prepare_generation_inputs

    project, chapter, _ = prepare_generation_inputs(client, session_headers)
    preview = client.post(
        f"/api/v1/projects/{project}/full-pages/preview",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "page_number": 1,
            "text_policy": "local",
            "seed": 12345,
            "cost_ceiling_anlas": 0,
        },
    )
    assert preview.status_code == 201
    result = client.post(
        f"/api/v1/projects/{project}/workflows/from-project",
        headers=session_headers,
        json={"objective": "核对当前完整冻结范围"},
    )
    assert result.status_code == 201, result.text
    summary = result.json()
    assert summary["counts"]["total"] == 1
    assert summary["counts"]["generated"] == 0
    assert summary["can_resume"]
    assert summary["capabilities"]["compaction"] is False


def test_100_page_mock_recovery_keeps_all_units_and_can_finish_checks(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
) -> None:
    base, task, reader = workflow
    lease = claim(client, base, session_headers)
    revision = 1
    for end in range(5, 101, 5):
        reader.ready = reader.reviewed = end
        task["current_unit"] = f"page-{end}"
        task["next_action"] = "整书验收" if end == 100 else f"继续第 {end + 1} 页"
        response = client.post(
            base + "/checkpoints",
            headers=session_headers,
            json={
                "lease": lease,
                "expected_revision": revision,
                "snapshot": task,
            },
        )
        assert response.status_code == 200, response.text
        revision = response.json()["revision"]
        if end in {25, 50, 75}:
            plan = client.post(base + "/recovery-plan", headers=session_headers).json()
            response = client.post(
                base + "/resume",
                headers=session_headers,
                json={
                    "lease": lease,
                    "expected_revision": revision,
                    "plan_hash": plan["plan_hash"],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["external_requests_started"] == 0
            revision = response.json()["runtime"]["revision"]
    summary = client.get(base + "/context", headers=session_headers).json()
    assert summary["counts"]["total"] == summary["counts"]["reviewed"] == 100
    assert summary["next_unit"] is None
    assert summary["export_approved"] is False
    seen = []
    cursor = 0
    while True:
        data = client.get(base + f"/evidence?cursor={cursor}", headers=session_headers).json()
        seen.extend(row["page_number"] for row in data["items"])
        if data["next_cursor"] is None:
            break
        cursor = data["next_cursor"]
    assert seen == list(range(1, 101))
