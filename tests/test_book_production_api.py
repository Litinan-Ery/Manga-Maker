from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.novelai.mock import MockNovelAIClient
from tests.test_continuity_api import prepare_two_approved_chapters
from tests.test_generation_queue import prepare_prompting, transition


def test_whole_book_zero_anlas_plan_cannot_widen_cost_limit(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapters = prepare_book_project(client, session_headers)
    estimated = client.post(
        f"/api/v1/projects/{project_id}/book-production/estimate",
        headers=session_headers,
        json={"per_panel_cost_ceiling_anlas": 0},
    )
    assert estimated.status_code == 200, estimated.text
    estimate = estimated.json()
    assert estimate["billing_mode"] == "opus_zero_anlas"
    assert estimate["cost_basis"] == "opus_zero_anlas_official_limits_v1"
    assert estimate["estimated_cost_upper_anlas"] == 0
    assert estimate["estimated_verification_calls"] == estimate["estimated_calls"]
    assert estimate["estimated_external_requests"] == estimate["estimated_calls"] * 2

    widened = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans",
        headers=session_headers,
        json={
            "per_panel_cost_ceiling_anlas": 0,
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": estimate["estimated_calls"],
            "max_cost_anlas": 1,
            "confirmed": True,
        },
    )
    assert widened.status_code == 422
    assert widened.json()["error"]["code"] == "BOOK_ZERO_ANLAS_LIMIT_INVALID"


def test_whole_book_budget_requires_per_chapter_approval_and_runs_one_job_at_a_time(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapters = prepare_book_project(client, session_headers)
    estimate = estimate_book(client, session_headers, project_id)
    assert estimate["chapter_count"] == 2
    assert estimate["estimated_page_count"] == 2
    assert estimate["estimated_panel_count"] == 2
    assert estimate["estimated_calls"] == 2
    assert estimate["estimated_cost_upper_anlas"] == 20
    assert estimate["external_request_created"] is False

    denied = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans",
        headers=session_headers,
        json=book_plan_request(estimate, confirmed=False),
    )
    assert denied.status_code == 422
    created = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans",
        headers=session_headers,
        json=book_plan_request(estimate),
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["status"] == "awaiting_approval"
    assert [chapter["status"] for chapter in plan["chapters"]] == [
        "awaiting_approval",
        "awaiting_approval",
    ]
    assert sum(chapter["max_calls"] for chapter in plan["chapters"]) <= plan["max_calls"]
    assert sum(chapter["max_cost_anlas"] for chapter in plan["chapters"]) <= plan[
        "max_cost_anlas"
    ]

    for chapter in list(plan["chapters"]):
        plan = approve_book_chapter(
            client,
            session_headers,
            project_id,
            plan,
            chapter["book_chapter_plan_id"],
        )
    assert plan["status"] == "ready"
    plan = transition_book(client, session_headers, project_id, plan, "start")
    assert plan["status"] == "active"

    plan = transition_book(client, session_headers, project_id, plan, "advance")
    assert [chapter["status"] for chapter in plan["chapters"]] == [
        "job_created",
        "approved",
    ]
    first_job_id = plan["chapters"][0]["generation_job_id"]
    assert first_job_id
    same_plan = transition_book(client, session_headers, project_id, plan, "advance")
    assert same_plan["chapters"][0]["generation_job_id"] == first_job_id
    assert len(client.app.state.generation_queue.list_jobs(project_id)) == 1

    provider = MockNovelAIClient()
    client.app.state.generation_executor.provider_factory = (
        lambda _configuration, _secret_reader: provider
    )
    first_job = client.app.state.generation_queue.get_job(project_id, first_job_id)
    started = transition(client, session_headers, project_id, first_job, "start")
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))
    assert provider.generation_calls == 1

    refreshed = client.get(
        f"/api/v1/projects/{project_id}/book-production/plans/current"
    ).json()
    plan = transition_book(client, session_headers, project_id, refreshed, "advance")
    assert [chapter["status"] for chapter in plan["chapters"]] == [
        "completed",
        "job_created",
    ]
    assert len(client.app.state.generation_queue.list_jobs(project_id)) == 2
    second_job = client.app.state.generation_queue.get_job(
        project_id, plan["chapters"][1]["generation_job_id"]
    )
    second_started = transition(client, session_headers, project_id, second_job, "start")
    asyncio.run(
        client.app.state.generation_executor.run_until_blocked(second_started["job_id"])
    )
    assert provider.generation_calls == 2

    current = client.get(
        f"/api/v1/projects/{project_id}/book-production/plans/current"
    ).json()
    completed = transition_book(client, session_headers, project_id, current, "advance")
    assert completed["status"] == "completed"
    assert [chapter["status"] for chapter in completed["chapters"]] == [
        "completed",
        "completed",
    ]
    assert completed["calls_started"] == 2
    assert completed["calls_completed"] == 2
    assert completed["external_requests_started"] == 2


def test_whole_book_pause_and_resume_also_controls_the_linked_local_job(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapters = prepare_book_project(client, session_headers)
    plan = create_ready_book_plan(client, session_headers, project_id)
    plan = transition_book(client, session_headers, project_id, plan, "start")
    plan = transition_book(client, session_headers, project_id, plan, "advance")
    job_id = plan["chapters"][0]["generation_job_id"]

    paused = transition_book(client, session_headers, project_id, plan, "pause")
    assert paused["status"] == "paused"
    assert paused["chapters"][0]["status"] == "paused"
    assert client.app.state.generation_queue.get_job(project_id, job_id)["status"] == "paused"
    assert paused["calls_started"] == 0

    resumed = transition_book(client, session_headers, project_id, paused, "resume")
    assert resumed["status"] == "active"
    assert resumed["chapters"][0]["status"] == "running"
    assert client.app.state.generation_queue.get_job(project_id, job_id)["status"] == "running"
    assert resumed["calls_started"] == 0


def test_restart_never_advances_book_and_unknown_chapter_cost_needs_review(
    app_data_dir: Path,
) -> None:
    settings = Settings(app_data_dir=app_data_dir, environment="test")
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        headers = session_headers_for(first)
        project_id, _chapters = prepare_book_project(first, headers)
        plan = create_ready_book_plan(first, headers, project_id)
        plan = transition_book(first, headers, project_id, plan, "start")
        plan = transition_book(first, headers, project_id, plan, "advance")
        job_id = plan["chapters"][0]["generation_job_id"]
        job = first.app.state.generation_queue.get_job(project_id, job_id)
        started = transition(first, headers, project_id, job, "start")
        attempt = first.app.state.generation_queue.claim_next(started["job_id"])
        assert attempt is not None
        assert first.app.state.generation_queue.mark_provider_request_started(
            attempt["attempt_id"]
        )

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        report = second.get("/api/v1/system/recovery").json()
        assert report["queue_recovery"]["needs_review"] == 1
        assert report["book_recovery"] == {
            "book_plans_paused": 0,
            "book_plans_needs_review": 1,
        }
        plan = second.get(
            f"/api/v1/projects/{project_id}/book-production/plans/current"
        ).json()
        assert plan["status"] == "needs_review"
        assert plan["chapters"][0]["status"] == "needs_review"
        assert plan["chapters"][1]["status"] == "approved"
        assert len(second.app.state.generation_queue.list_jobs(project_id)) == 1
        assert plan["calls_started"] == 1
        assert plan["unverified_cost_calls"] == 1


def test_chapter_retry_preserves_lifecycle_call_cost_and_unknown_cost_limits(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, _chapters = prepare_book_project(client, session_headers)
    plan = create_ready_book_plan(client, session_headers, project_id)
    plan = transition_book(client, session_headers, project_id, plan, "start")
    plan = transition_book(client, session_headers, project_id, plan, "advance")
    chapter_plan_id = plan["chapters"][0]["book_chapter_plan_id"]
    first_job_id = plan["chapters"][0]["generation_job_id"]
    queue = client.app.state.generation_queue

    first_job = queue.get_job(project_id, first_job_id)
    first_started = transition(
        client, session_headers, project_id, first_job, "start"
    )
    first_attempt = queue.claim_next(first_started["job_id"])
    assert first_attempt is not None
    assert queue.mark_provider_request_started(first_attempt["attempt_id"])
    queue.requeue_attempt(first_attempt["attempt_id"], error_code="PROVIDER_TEMPORARY")
    queue.mark_job_needs_review(first_job_id, "TEST_RETRY_REQUIRED")

    plan = transition_book(client, session_headers, project_id, plan, "advance")
    assert plan["status"] == "needs_review"
    assert plan["calls_started"] == 1
    assert plan["allocated_cost_anlas"] == 10
    assert plan["unverified_cost_calls"] == 1

    retry = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans/"
        f"{plan['book_plan_id']}/chapters/{chapter_plan_id}/retry",
        headers=session_headers,
        json={"expected_revision": plan["revision"]},
    )
    assert retry.status_code == 200, retry.text
    plan = retry.json()
    assert plan["status"] == "paused"
    assert plan["chapters"][0]["generation_job_id"] is None
    assert plan["calls_started"] == 1
    assert plan["allocated_cost_anlas"] == 10
    assert plan["unverified_cost_calls"] == 1

    plan = transition_book(client, session_headers, project_id, plan, "resume")
    plan = transition_book(client, session_headers, project_id, plan, "advance")
    second_job_id = plan["chapters"][0]["generation_job_id"]
    assert second_job_id != first_job_id
    second_job = queue.get_job(project_id, second_job_id)
    assert second_job["max_calls"] == 2
    assert second_job["max_cost_anlas"] == 20
    second_started = transition(
        client, session_headers, project_id, second_job, "start"
    )

    second_attempt = queue.claim_next(second_started["job_id"])
    assert second_attempt is not None
    assert queue.mark_provider_request_started(second_attempt["attempt_id"])
    queue.requeue_attempt(second_attempt["attempt_id"], error_code="PROVIDER_TEMPORARY")
    final_attempt = queue.claim_next(second_started["job_id"])
    assert final_attempt is not None
    assert queue.mark_provider_request_started(final_attempt["attempt_id"])
    queue.fail_attempt(
        final_attempt["attempt_id"],
        error_code="UNKNOWN_PROVIDER_OUTCOME",
        outcome_unknown=True,
    )

    plan = transition_book(client, session_headers, project_id, plan, "advance")
    assert plan["status"] == "needs_review"
    assert plan["calls_started"] == 3
    assert plan["allocated_cost_anlas"] == 30
    assert plan["unverified_cost_calls"] == 3
    assert plan["chapters"][0]["calls_started"] == 3

    exhausted = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans/"
        f"{plan['book_plan_id']}/chapters/{chapter_plan_id}/retry",
        headers=session_headers,
        json={"expected_revision": plan["revision"]},
    )
    assert exhausted.status_code == 409, exhausted.text
    assert exhausted.json()["error"]["code"] == "BOOK_CHAPTER_RETRY_BUDGET_EXHAUSTED"
    current = client.get(
        f"/api/v1/projects/{project_id}/book-production/plans/current"
    ).json()
    assert current["calls_started"] == 3
    assert current["allocated_cost_anlas"] == 30
    assert current["unverified_cost_calls"] == 3
    assert current["chapters"][0]["generation_job_id"] == second_job_id


def prepare_book_project(
    client: TestClient, headers: dict[str, str]
) -> tuple[str, list[dict[str, Any]]]:
    project_id, chapters = prepare_two_approved_chapters(client, headers)
    continuity_endpoint = f"/api/v1/projects/{project_id}/continuity"
    for chapter in chapters:
        ledger = client.post(
            f"{continuity_endpoint}/draft",
            headers=headers,
            json={"chapter_id": chapter["chapter_id"]},
        ).json()
        approved = client.post(
            f"{continuity_endpoint}/{ledger['continuity_version_id']}/approve",
            headers=headers,
        )
        assert approved.status_code == 200, approved.text

    saved = client.put(
        "/api/v1/vault/profiles/novelai",
        headers=headers,
        json={
            "provider": "novelai",
            "label": "NovelAI",
            "secret": "unit-book-novelai-secret",
        },
    )
    assert saved.status_code == 200
    configured = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=headers,
        json={
            "provider_model_id": "nai-diffusion-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 20,
        },
    )
    assert configured.status_code == 200
    client.app.state.novelai.provider_factory = (
        lambda configuration, _secret_reader: MockNovelAIClient(
            provider_model_id=configuration.provider_model_id
        )
    )
    tested = client.post(
        f"/api/v1/projects/{project_id}/novelai/connection-test",
        headers=headers,
    )
    assert tested.status_code == 200
    for chapter in chapters:
        prepare_prompting(
            client,
            headers,
            project_id,
            str(chapter["chapter_id"]),
        )
    return project_id, chapters


def estimate_book(
    client: TestClient, headers: dict[str, str], project_id: str
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/book-production/estimate",
        headers=headers,
        json={"per_panel_cost_ceiling_anlas": 10},
    )
    assert response.status_code == 200, response.text
    return response.json()


def book_plan_request(
    estimate: dict[str, Any], *, confirmed: bool = True
) -> dict[str, Any]:
    return {
        "per_panel_cost_ceiling_anlas": 10,
        "plan_fingerprint": estimate["plan_fingerprint"],
        "max_calls": estimate["estimated_calls"] * 3,
        "max_cost_anlas": estimate["estimated_cost_upper_anlas"] * 3,
        "confirmed": confirmed,
    }


def create_ready_book_plan(
    client: TestClient, headers: dict[str, str], project_id: str
) -> dict[str, Any]:
    estimate = estimate_book(client, headers, project_id)
    response = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans",
        headers=headers,
        json=book_plan_request(estimate),
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    for chapter in list(plan["chapters"]):
        plan = approve_book_chapter(
            client,
            headers,
            project_id,
            plan,
            chapter["book_chapter_plan_id"],
        )
    return plan


def approve_book_chapter(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    plan: dict[str, Any],
    chapter_plan_id: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans/"
        f"{plan['book_plan_id']}/chapters/{chapter_plan_id}/approve",
        headers=headers,
        json={"expected_revision": plan["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def transition_book(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    plan: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/book-production/plans/"
        f"{plan['book_plan_id']}/{action}",
        headers=headers,
        json={"expected_revision": plan["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def session_headers_for(client: TestClient) -> dict[str, str]:
    return {
        "X-Manga-Maker-Session": client.app.state.local_session.token,
        "X-CSRF-Token": client.app.state.local_session.csrf_token,
    }
