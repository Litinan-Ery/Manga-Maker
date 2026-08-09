from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.app.novelai.mock import MockNovelAIClient
from tests.test_bibles_api import (
    approve_complete_bibles,
    generate_bibles,
    prepare_storyboard,
)


def test_estimate_and_job_freeze_exact_approved_versions_and_limits(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    estimate = estimate_plan(client, session_headers, project_id, chapter["chapter_id"])

    assert estimate["page_count"] == 1
    assert estimate["panel_count"] == 1
    assert estimate["estimated_calls"] == 1
    assert estimate["estimated_cost_upper_anlas"] == 10
    assert estimate["external_request_created"] is False
    assert len(estimate["plan_fingerprint"]) == 64

    missing_confirmation = create_job(
        client,
        session_headers,
        project_id,
        chapter["chapter_id"],
        estimate,
        confirmed=False,
    )
    assert missing_confirmation.status_code == 422

    created = create_job(
        client,
        session_headers,
        project_id,
        chapter["chapter_id"],
        estimate,
    )
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "queued"
    assert job["storyboard_version_id"] == estimate["storyboard_version_id"]
    assert job["character_bible_version_id"] == estimate["character_bible_version_id"]
    assert job["style_bible_version_id"] == estimate["style_bible_version_id"]
    assert job["novelai_config_revision"] == estimate["novelai_config_revision"]
    assert job["external_requests_started"] == 0
    assert [item["panel_id"] for item in job["items"]] == [
        panel["panel_id"] for panel in estimate["panels"]
    ]

    duplicate = create_job(
        client,
        session_headers,
        project_id,
        chapter["chapter_id"],
        estimate,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "GENERATION_JOB_ALREADY_ACTIVE"


def test_plan_change_and_stale_revision_fail_closed(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    estimate = estimate_plan(client, session_headers, project_id, chapter["chapter_id"])

    stale = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "per_panel_cost_ceiling_anlas": 11,
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": 1,
            "max_cost_anlas": 11,
            "confirmed": True,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "GENERATION_PLAN_STALE"

    job = create_job(
        client,
        session_headers,
        project_id,
        chapter["chapter_id"],
        estimate,
    ).json()
    started = transition(client, session_headers, project_id, job, "start")
    assert started["status"] == "running"
    conflict = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs/{job['job_id']}/pause",
        headers=session_headers,
        json={"expected_revision": job["revision"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "GENERATION_JOB_REVISION_CONFLICT"


def test_stale_novelai_contract_blocks_generation_plan(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    with client.app.state.database.writer() as connection:
        connection.execute(
            "UPDATE novelai_configs SET contract_sha256 = ? WHERE project_id = ?",
            ("b" * 64, project_id),
        )

    response = client.post(
        f"/api/v1/projects/{project_id}/generation/estimate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "per_panel_cost_ceiling_anlas": 10},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOVELAI_CONTRACT_STALE"


def test_worker_claim_is_globally_serial_and_pause_stops_new_claims(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    first = prepare_job(client, session_headers, title_suffix="一")
    second = prepare_job(client, session_headers, title_suffix="二")
    service = client.app.state.generation_queue

    first_started = transition(
        client, session_headers, first["project_id"], first["job"], "start"
    )
    second_started = transition(
        client, session_headers, second["project_id"], second["job"], "start"
    )
    first_attempt = service.claim_next(first_started["job_id"])
    assert first_attempt is not None
    assert service.mark_provider_request_started(first_attempt["attempt_id"]) is True
    assert service.claim_next(second_started["job_id"]) is None

    paused = transition(
        client, session_headers, first["project_id"], service.get_job(
            first["project_id"], first_started["job_id"]
        ), "pause"
    )
    assert paused["status"] == "paused"
    assert paused["items"][0]["status"] == "running"
    assert service.claim_next(first_started["job_id"]) is None

    service.complete_attempt(first_attempt["attempt_id"], recorded_cost_anlas=4)
    still_paused = service.get_job(first["project_id"], first_started["job_id"])
    assert still_paused["status"] == "paused"
    assert still_paused["items"][0]["status"] == "completed"

    second_attempt = service.claim_next(second_started["job_id"])
    assert second_attempt is not None
    assert service.mark_provider_request_started(second_attempt["attempt_id"]) is True
    service.complete_attempt(second_attempt["attempt_id"], recorded_cost_anlas=3)
    completed = service.get_job(second["project_id"], second_started["job_id"])
    assert completed["status"] == "completed"
    assert completed["calls_started"] == 1
    assert completed["recorded_cost_anlas"] == 3


def test_pause_between_claim_and_send_releases_prepared_attempt_without_a_call(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="发送前暂停")
    service = client.app.state.generation_queue
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    attempt = service.claim_next(started["job_id"])
    assert attempt is not None
    current = service.get_job(prepared["project_id"], started["job_id"])
    paused = transition(
        client, session_headers, prepared["project_id"], current, "pause"
    )

    assert service.mark_provider_request_started(attempt["attempt_id"]) is False
    released = service.get_job(prepared["project_id"], started["job_id"])
    assert released["status"] == "paused"
    assert released["items"][0]["status"] == "queued"
    assert released["items"][0]["active_attempt_id"] is None
    assert released["calls_started"] == 0
    assert released["allocated_cost_anlas"] == 0
    assert paused["revision"] < released["revision"]


def test_cancel_during_definite_temporary_failure_does_not_requeue_item(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="在途取消")
    service = client.app.state.generation_queue
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    attempt = service.claim_next(started["job_id"])
    assert attempt is not None
    assert service.mark_provider_request_started(attempt["attempt_id"]) is True
    current = service.get_job(prepared["project_id"], started["job_id"])
    canceled = transition(
        client, session_headers, prepared["project_id"], current, "cancel"
    )

    service.requeue_attempt(attempt["attempt_id"], error_code="PROVIDER_TEMPORARY")
    settled = service.get_job(prepared["project_id"], started["job_id"])
    assert canceled["status"] == "canceled"
    assert settled["status"] == "canceled"
    assert settled["items"][0]["status"] == "canceled"
    assert settled["items"][0]["active_attempt_id"] is None


def test_cancel_preserves_in_flight_settlement_and_restart_requires_review(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="恢复")
    service = client.app.state.generation_queue
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    attempt = service.claim_next(started["job_id"])
    assert attempt is not None
    assert service.mark_provider_request_started(attempt["attempt_id"]) is True

    reconciliation = service.reconcile_startup()
    assert reconciliation == {"needs_review": 1, "paused": 0}
    reviewed = service.get_job(prepared["project_id"], started["job_id"])
    assert reviewed["status"] == "needs_review"
    assert reviewed["items"][0]["status"] == "needs_review"
    assert service.claim_next(started["job_id"]) is None

    canceled = transition(
        client, session_headers, prepared["project_id"], reviewed, "cancel"
    )
    assert canceled["status"] == "canceled"
    assert canceled["calls_started"] == 1


def test_recorded_cost_overrun_stops_queue_for_review(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared = prepare_job(client, session_headers, title_suffix="成本")
    service = client.app.state.generation_queue
    started = transition(
        client, session_headers, prepared["project_id"], prepared["job"], "start"
    )
    attempt = service.claim_next(started["job_id"])
    assert attempt is not None
    assert service.mark_provider_request_started(attempt["attempt_id"]) is True
    service.complete_attempt(attempt["attempt_id"], recorded_cost_anlas=11)

    job = service.get_job(prepared["project_id"], started["job_id"])
    assert job["recorded_cost_anlas"] == 11
    assert job["max_cost_anlas"] == 10
    assert job["status"] == "needs_review"
    assert service.claim_next(started["job_id"]) is None


def prepare_generation_inputs(
    client: TestClient, headers: dict[str, str], *, title_suffix: str = ""
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    project_id, chapter, storyboard = prepare_storyboard(client, headers)
    if title_suffix:
        with client.app.state.database.writer() as connection:
            connection.execute(
                "UPDATE projects SET title = title || ? WHERE project_id = ?",
                (title_suffix, project_id),
            )
    bundle = generate_bibles(
        client, headers, project_id, storyboard["storyboard_version_id"]
    )
    ready_bundle = approve_complete_bibles(client, headers, project_id, bundle)
    novelai_secret = client.put(
        "/api/v1/vault/profiles/novelai",
        headers=headers,
        json={
            "provider": "novelai",
            "label": "NovelAI",
            "secret": "unit-novelai-secret",
        },
    )
    assert novelai_secret.status_code == 200
    configured = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=headers,
        json={
            "provider_model_id": "nai-diffusion-4-5-full",
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
    return project_id, chapter, ready_bundle


def estimate_plan(
    client: TestClient, headers: dict[str, str], project_id: str, chapter_id: str
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/generation/estimate",
        headers=headers,
        json={"chapter_id": chapter_id, "per_panel_cost_ceiling_anlas": 10},
    )
    assert response.status_code == 200
    return response.json()


def create_job(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
    estimate: dict[str, Any],
    *,
    confirmed: bool = True,
):
    return client.post(
        f"/api/v1/projects/{project_id}/generation/jobs",
        headers=headers,
        json={
            "chapter_id": chapter_id,
            "per_panel_cost_ceiling_anlas": estimate["per_panel_cost_ceiling_anlas"],
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": estimate["panel_count"],
            "max_cost_anlas": estimate["estimated_cost_upper_anlas"],
            "confirmed": confirmed,
        },
    )


def prepare_job(
    client: TestClient, headers: dict[str, str], *, title_suffix: str
) -> dict[str, Any]:
    project_id, chapter, _bundle = prepare_generation_inputs(
        client, headers, title_suffix=title_suffix
    )
    estimate = estimate_plan(client, headers, project_id, chapter["chapter_id"])
    response = create_job(
        client, headers, project_id, chapter["chapter_id"], estimate
    )
    assert response.status_code == 201
    return {"project_id": project_id, "chapter": chapter, "job": response.json()}


def transition(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    job: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs/{job['job_id']}/{action}",
        headers=headers,
        json={"expected_revision": job["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()
