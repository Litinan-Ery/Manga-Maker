from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.errors import ApplicationError
from backend.app.novelai.mock import MockNovelAIClient
from tests.test_bibles_api import (
    approve_complete_bibles,
    generate_bibles,
    prepare_storyboard,
)

ROOT = Path(__file__).resolve().parents[1]
DIMENSION_CAPABILITIES = json.loads(
    (ROOT / "contracts" / "fixtures" / "v0.3" / "dimension-capabilities.json").read_text(
        encoding="utf-8"
    )
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
    assert len(estimate["layout_snapshot_sha256"]) == 64
    assert estimate["panels"][0]["selected_width"] == 832
    assert estimate["panels"][0]["selected_height"] == 1216
    assert len(estimate["panels"][0]["frame_content_sha256"]) == 64

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
    assert job["layout_snapshot_sha256"] == estimate["layout_snapshot_sha256"]
    assert len(job["generation_approval_id"]) > 0
    assert len(job["generation_approval_sha256"]) == 64
    assert len(job["prompt_approval_hash"]) == 64
    assert len(job["prompt_snapshot_sha256"]) == 64
    assert job["candidate_count_per_panel"] == 1
    assert job["quality_rule_version"] == "quality-rules-v1"
    assert job["items"][0]["dimension_selection_id"] == estimate["panels"][0][
        "dimension_selection_id"
    ]
    assert len(job["items"][0]["prompt_plan_id"]) > 0
    assert job["items"][0]["prompt_plan_version"] >= 1
    assert len(job["items"][0]["prompt_plan_sha256"]) == 64
    assert len(job["items"][0]["prompt_package_sha256"]) == 64
    assert job["items"][0]["character_tag_set_refs"]
    assert len(job["items"][0]["provider_execution_spec_id"]) > 0
    assert len(job["items"][0]["provider_execution_spec_sha256"]) == 64
    assert len(job["items"][0]["provider_payload_sha256"]) == 64
    assert job["items"][0]["provider_seed"] >= 0
    assert job["items"][0]["candidate_count"] == 1
    assert "provider_payload" not in job["items"][0]
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
    assert duplicate.status_code == 201
    assert duplicate.json()["job_id"] == job["job_id"]
    with client.app.state.database.reader() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_approvals").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM generation_jobs").fetchone()[0] == 1


def test_zero_anlas_plan_freezes_official_limits_and_zero_hard_cap(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    response = client.post(
        f"/api/v1/projects/{project_id}/generation/estimate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"]},
    )
    assert response.status_code == 200, response.text
    estimate = response.json()

    assert estimate["billing_mode"] == "opus_zero_anlas"
    assert estimate["cost_basis"] == "opus_zero_anlas_official_limits_v1"
    assert estimate["per_panel_cost_ceiling_anlas"] == 0
    assert estimate["estimated_cost_upper_anlas"] == 0
    assert estimate["candidate_count_per_panel"] == 1
    assert estimate["panels"][0]["selected_width"] == 832
    assert estimate["panels"][0]["selected_height"] == 1216

    created = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs",
        headers={**session_headers, "Idempotency-Key": "zero-anlas-job"},
        json={
            "chapter_id": chapter["chapter_id"],
            "per_panel_cost_ceiling_anlas": 0,
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": estimate["estimated_calls"],
            "max_cost_anlas": 0,
            "confirmed": True,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["max_cost_anlas"] == 0
    assert created.json()["cost_basis"] == "opus_zero_anlas_official_limits_v1"

    widened = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs",
        headers={**session_headers, "Idempotency-Key": "zero-anlas-widened"},
        json={
            "chapter_id": chapter["chapter_id"],
            "per_panel_cost_ceiling_anlas": 0,
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": estimate["estimated_calls"],
            "max_cost_anlas": 1,
            "confirmed": True,
        },
    )
    assert widened.status_code == 422
    assert widened.json()["error"]["code"] == "GENERATION_ZERO_ANLAS_LIMIT_INVALID"


def test_concurrent_idempotent_job_creation_returns_the_same_job(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    estimate = estimate_plan(client, session_headers, project_id, chapter["chapter_id"])
    service = client.app.state.generation_queue
    barrier = threading.Barrier(2)

    def create() -> dict[str, Any]:
        barrier.wait()
        return service.create_job(
            project_id,
            chapter["chapter_id"],
            plan_fingerprint=estimate["plan_fingerprint"],
            per_panel_cost_ceiling_anlas=estimate["per_panel_cost_ceiling_anlas"],
            max_calls=estimate["panel_count"],
            max_cost_anlas=estimate["estimated_cost_upper_anlas"],
            confirmed=True,
            idempotency_key="concurrent-create-job",
            request_sha256="a" * 64,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))

    assert results[0]["job_id"] == results[1]["job_id"]
    with client.app.state.database.reader() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM generation_approvals WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM generation_jobs WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0] == 1


def test_plan_change_and_stale_revision_fail_closed(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    estimate = estimate_plan(client, session_headers, project_id, chapter["chapter_id"])

    stale = client.post(
        f"/api/v1/projects/{project_id}/generation/jobs",
        headers={**session_headers, "Idempotency-Key": "stale-plan-job"},
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


def test_legacy_flat_prompt_is_readable_but_cannot_create_a_v03_job(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    with client.app.state.database.writer() as connection:
        row = connection.execute(
            """
            SELECT pbv.prompt_bundle_version_id, pbv.document_json
            FROM prompt_bundle_versions pbv
            JOIN prompt_bundles pb ON pb.prompt_bundle_id = pbv.prompt_bundle_id
            WHERE pb.project_id = ? AND pb.chapter_id = ? AND pbv.is_current = 1
            """,
            (project_id, chapter["chapter_id"]),
        ).fetchone()
        document = json.loads(str(row["document_json"]))
        document["schema_version"] = "1.1"
        for package in document["packages"]:
            package.pop("structured_package")
        connection.execute(
            "UPDATE prompt_bundle_versions SET document_json = ? "
            "WHERE prompt_bundle_version_id = ?",
            (
                json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                row["prompt_bundle_version_id"],
            ),
        )

    workflow_response = client.get(
        f"/api/v1/projects/{project_id}/prompting",
        params={"chapter_id": chapter["chapter_id"]},
    )
    assert workflow_response.status_code == 200, workflow_response.text
    workflow = workflow_response.json()
    assert workflow["prompt_bundle"]["compatibility"] == {
        "kind": "legacy_flat_prompt",
        "access": "read_only",
        "regeneration_required": True,
        "eligible_for_new_job": False,
    }
    assert workflow["prompt_bundle"]["document"]["packages"][0]["compiled_prompt"]
    assert workflow["generation_readiness"]["ready"] is False
    assert workflow["generation_readiness"]["structured_prompt_ready"] is False

    response = client.post(
        f"/api/v1/projects/{project_id}/generation/estimate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "per_panel_cost_ceiling_anlas": 10},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GENERATION_STRUCTURED_PROMPT_REQUIRED"


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


def test_zero_anlas_verification_respects_image_and_verification_call_limit(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(
        client, session_headers, title_suffix="核验上限"
    )
    estimate = estimate_plan(
        client,
        session_headers,
        project_id,
        chapter["chapter_id"],
        per_panel_cost_ceiling_anlas=0,
    )
    created = create_job(
        client, session_headers, project_id, chapter["chapter_id"], estimate
    ).json()
    service = client.app.state.generation_queue
    started = transition(client, session_headers, project_id, created, "start")
    attempt = service.claim_next(started["job_id"])
    assert attempt is not None

    with client.app.state.database.writer() as connection:
        connection.execute(
            "UPDATE generation_jobs SET calls_started = max_calls WHERE job_id = ?",
            (started["job_id"],),
        )

    assert service.mark_verification_request_started(attempt["attempt_id"]) is False
    blocked = service.get_job(project_id, started["job_id"])
    assert blocked["status"] == "needs_review"
    assert blocked["verification_calls_started"] == 0
    assert blocked["external_requests_started"] == blocked["max_calls"]
    assert blocked["items"][0]["last_error_code"] == "CALL_LIMIT_REACHED"


def test_zero_anlas_image_request_requires_completed_subscription_verification(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(
        client, session_headers, title_suffix="核验先于出图"
    )
    estimate = estimate_plan(
        client,
        session_headers,
        project_id,
        chapter["chapter_id"],
        per_panel_cost_ceiling_anlas=0,
    )
    created = create_job(
        client, session_headers, project_id, chapter["chapter_id"], estimate
    ).json()
    service = client.app.state.generation_queue
    started = transition(client, session_headers, project_id, created, "start")
    attempt = service.claim_next(started["job_id"])
    assert attempt is not None

    with pytest.raises(ApplicationError) as exc_info:
        service.mark_provider_request_started(attempt["attempt_id"])

    assert exc_info.value.code == "GENERATION_VERIFICATION_REQUIRED"
    blocked = service.get_job(project_id, started["job_id"])
    assert blocked["calls_started"] == 0
    assert blocked["verification_calls_started"] == 0


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
    assert settled["unverified_cost_calls"] == 1


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
    assert reviewed["unverified_cost_calls"] == 1
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
    prepare_prompting(
        client,
        headers,
        project_id,
        str(chapter["chapter_id"]),
    )
    return project_id, chapter, ready_bundle


def prepare_prompting(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
) -> dict[str, Any]:
    ensure_approved_layout(client, headers, project_id, chapter_id)
    generated_tags = client.post(
        f"/api/v1/projects/{project_id}/prompting/character-tags/generate",
        headers=headers,
        json={"chapter_id": chapter_id, "confirmed_data_send": True},
    )
    assert generated_tags.status_code == 201, generated_tags.text
    tag_version_id = generated_tags.json()["version_id"]
    approved_tags = client.post(
        f"/api/v1/projects/{project_id}/prompting/character-tags/"
        f"{tag_version_id}/approve",
        headers=headers,
    )
    assert approved_tags.status_code == 200, approved_tags.text
    generated_prompts = client.post(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/generate",
        headers=headers,
        json={"chapter_id": chapter_id, "confirmed_data_send": True},
    )
    assert generated_prompts.status_code == 201, generated_prompts.text
    prompt_version_id = generated_prompts.json()["version_id"]
    snapshot_sha256 = generated_prompts.json()["snapshot_sha256"]
    approved_prompts = client.post(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
        f"{prompt_version_id}/approve",
        headers={**headers, "Idempotency-Key": f"approve-prompt-{prompt_version_id}"},
        json={"snapshot_sha256": snapshot_sha256},
    )
    assert approved_prompts.status_code == 200, approved_prompts.text
    return client.get(
        f"/api/v1/projects/{project_id}/prompting?chapter_id={chapter_id}"
    ).json()


def ensure_approved_layout(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
) -> list[dict[str, Any]]:
    """Create and approve one provider-neutral layout for every storyboard page."""

    existing = client.get(
        f"/api/v1/projects/{project_id}/layouts",
        params={"chapter_id": chapter_id},
    )
    assert existing.status_code == 200, existing.text
    if existing.json():
        return list(existing.json())

    storyboard_response = client.get(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/current",
        params={"chapter_id": chapter_id},
    )
    assert storyboard_response.status_code == 200, storyboard_response.text
    storyboard = storyboard_response.json()
    assert storyboard["approval_status"] == "approved"
    bible_response = client.get(
        f"/api/v1/projects/{project_id}/bibles",
        params={"chapter_id": chapter_id},
    )
    assert bible_response.status_code == 200, bible_response.text
    character_bible = bible_response.json()["character_bible"]["document"]
    character_ids = {
        alias.casefold(): character["character_id"]
        for character in character_bible["characters"]
        for alias in [character["name"], *character["aliases"]]
    }

    approved_layouts: list[dict[str, Any]] = []
    for page in storyboard["document"]["pages"]:
        layout_id = str(uuid4())
        root_frame_id = str(uuid4())
        panel_count = len(page["panels"])
        gutter = 0.02
        leaf_height = (0.92 - gutter * (panel_count - 1)) / panel_count
        frames: list[dict[str, Any]] = [
            {
                "frame_id": root_frame_id,
                "parent_frame_id": None,
                "panel_id": None,
                "order": None,
                "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                "aspect_ratio": 2 / 3,
                "shot_scale": "establishing",
                "focal_point": {"x": 0.5, "y": 0.5},
                "character_positions": [],
                "text_safe_zones": [],
                "crop_safe_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
            }
        ]
        for index, panel in enumerate(page["panels"], start=1):
            leaf_y = 0.04 + (index - 1) * (leaf_height + gutter)
            panel_character_ids = [
                character_ids[name.casefold()] for name in panel["characters"]
            ]
            position_count = len(panel_character_ids)
            character_positions = [
                {
                    "character_id": character_id,
                    "center": {
                        "x": (position_index + 1) / (position_count + 1),
                        "y": 0.56,
                    },
                    "prominence": "primary" if position_index == 0 else "secondary",
                }
                for position_index, character_id in enumerate(panel_character_ids)
            ]
            frames.append(
                {
                    "frame_id": str(uuid4()),
                    "parent_frame_id": root_frame_id,
                    "panel_id": panel["panel_id"],
                    "order": index,
                    "rect": {
                        "x": 0.04,
                        "y": leaf_y,
                        "width": 0.92,
                        "height": leaf_height,
                    },
                    "aspect_ratio": (0.92 * 2048) / (leaf_height * 3072),
                    "shot_scale": "medium",
                    "focal_point": {"x": 0.5, "y": 0.5},
                    "character_positions": character_positions,
                    "text_safe_zones": [],
                    "crop_safe_rect": {
                        "x": 0,
                        "y": 0,
                        "width": 1,
                        "height": 1,
                    },
                }
            )
        draft = {
            "schema_version": "1.0",
            "page_layout_draft_id": layout_id,
            "version": 1,
            "page_id": page["page_id"],
            "page_profile": "print_portrait_2_3",
            "canvas": {"width": 2048, "height": 3072},
            "reading_direction": "ltr_ttb",
            "frames": frames,
            "content_sha256": "0" * 64,
            "approved_content_sha256": None,
        }
        created_response = client.post(
            f"/api/v1/projects/{project_id}/layouts/drafts",
            headers={**headers, "Idempotency-Key": f"test-layout-create-{page['page_id']}"},
            json={
                "chapter_id": chapter_id,
                "storyboard_version_id": storyboard["storyboard_version_id"],
                "draft": draft,
            },
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        validation_body = {
            "expected_revision": created["revision"],
            "layout_content_sha256": created["layout"]["content_sha256"],
            "storyboard_version_id": storyboard["storyboard_version_id"],
            "dimension_capabilities": DIMENSION_CAPABILITIES,
            "target_pixels": 1_572_864,
            "max_crop_safe_risk": 1.0,
        }
        validated_response = client.post(
            f"/api/v1/projects/{project_id}/layouts/"
            f"{created['page_layout_draft_version_id']}/validate",
            headers=headers,
            json=validation_body,
        )
        assert validated_response.status_code == 200, validated_response.text
        validated = validated_response.json()
        assert validated["valid"], validated
        approved_response = client.post(
            f"/api/v1/projects/{project_id}/layouts/"
            f"{created['page_layout_draft_version_id']}/approve",
            headers={
                **headers,
                "Idempotency-Key": f"test-layout-approve-{page['page_id']}",
            },
            json={
                **validation_body,
                "dimension_selections": validated["dimension_outcomes"],
            },
        )
        assert approved_response.status_code == 200, approved_response.text
        approved_layouts.append(created)
    return approved_layouts


def estimate_plan(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
    *,
    per_panel_cost_ceiling_anlas: int = 10,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/generation/estimate",
        headers=headers,
        json={
            "chapter_id": chapter_id,
            "per_panel_cost_ceiling_anlas": per_panel_cost_ceiling_anlas,
        },
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
        headers={
            **headers,
            "Idempotency-Key": f"create-job-{estimate['plan_fingerprint']}-{confirmed}",
        },
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
