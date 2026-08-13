from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.test_bibles_api import (
    approve_complete_bibles,
    generate_bibles,
    prepare_storyboard,
)
from tests.test_generation_queue import estimate_plan, prepare_generation_inputs


def test_three_field_text_model_configuration_encrypts_key_locally(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = client.post(
        "/api/v1/projects", headers=session_headers, json={"title": "三字段模型配置"}
    ).json()
    created = client.post(
        "/api/v1/vault",
        headers=session_headers,
        json={"master_password": "unit test master password"},
    )
    assert created.status_code == 201

    secret = "local-only-text-model-key"
    saved = client.put(
        f"/api/v1/projects/{project['project_id']}/adaptation/text-model",
        headers=session_headers,
        json={
            "provider_api_url": "https://models.example.test/v1",
            "model_name": "structured-manga-model",
            "api_key": secret,
        },
    )

    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["provider_api_url"] == "https://models.example.test/v1"
    assert payload["model_name"] == "structured-manga-model"
    assert payload["text_model_profile_id"] == project["project_id"]
    assert secret not in saved.text
    assert client.app.state.vault.get_secret(
        f"text-model-{project['project_id']}"
    ) == secret
    with client.app.state.database.reader() as connection:
        config = dict(connection.execute("SELECT * FROM text_model_configs").fetchone())
        audits = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM audit_events WHERE event_type = 'text_model.configuration_saved'"
            )
        ]
    assert secret not in json.dumps([config, audits], ensure_ascii=False)


def test_prompt_packages_inject_fixed_tags_and_freeze_all_versions(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    workflow = client.get(
        f"/api/v1/projects/{project_id}/prompting?chapter_id={chapter['chapter_id']}"
    ).json()

    assert workflow["generation_readiness"]["ready"] is True
    tag_version = workflow["character_tags"]
    prompt_version = workflow["prompt_bundle"]
    tag_set = tag_version["document"]["tag_sets"][0]
    package = prompt_version["document"]["packages"][0]
    assert workflow["generation_readiness"]["structured_prompt_ready"] is True
    assert prompt_version["compatibility"] == {
        "kind": "prompt_plan_v2",
        "access": "read_write",
        "regeneration_required": False,
        "eligible_for_new_job": True,
    }
    assert prompt_version["document"]["schema_version"] == "1.2"
    assert len(prompt_version["document"]["layout_snapshot_sha256"]) == 64
    repeated_approval = client.post(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
        f"{prompt_version['version_id']}/approve",
        headers={
            **session_headers,
            "Idempotency-Key": f"approve-prompt-{prompt_version['version_id']}",
        },
        json={"snapshot_sha256": prompt_version["snapshot_sha256"]},
    )
    assert repeated_approval.status_code == 200, repeated_approval.text
    with client.app.state.database.reader() as connection:
        approval_count = connection.execute(
            "SELECT COUNT(*) FROM prompt_bundle_approvals"
        ).fetchone()[0]
    assert approval_count == 1
    binding = package["layout_binding"]
    assert binding["selected_width"] > 0
    assert binding["selected_height"] > 0
    assert len(binding["frame_content_sha256"]) == 64
    assert len(binding["dimension_selection_sha256"]) == 64
    structured = package["structured_package"]
    assert structured["schema_version"] == "2.0"
    assert structured["prompt_plan_sha256"] == structured["prompt_plan"][
        "content_sha256"
    ]
    assert structured["prompt_plan"]["characters"][0]["action"]
    assert structured["prompt_plan"]["characters"][0]["center"] == {
        "x": 0.5,
        "y": 0.56,
    }
    block = package["character_blocks"][0]
    assert block["fixed_tags"] == tag_set["fixed_tags"]
    assert block["fixed_tags_sha256"] == tag_set["fixed_tags_sha256"]
    assert all(tag in package["compiled_prompt"] for tag in tag_set["fixed_tags"])
    assert hashlib.sha256(package["compiled_prompt"].encode()).hexdigest() == package[
        "compiled_prompt_sha256"
    ]

    estimate = estimate_plan(
        client, session_headers, project_id, str(chapter["chapter_id"])
    )
    assert estimate["character_tag_bundle_version_id"] == tag_version["version_id"]
    assert estimate["prompt_bundle_version_id"] == prompt_version["version_id"]
    assert estimate["text_model_config_revision"] == prompt_version["document"][
        "text_model_config_revision"
    ]
    assert estimate["panels"][0]["compiled_prompt"] == package["compiled_prompt"]

    changed = copy.deepcopy(tag_version["document"])
    changed["tag_sets"][0]["fixed_tags"].append("distinctive hair ribbon")
    revised = client.post(
        f"/api/v1/projects/{project_id}/prompting/character-tags/"
        f"{tag_version['version_id']}/revisions",
        headers=session_headers,
        json={"document": changed},
    )
    assert revised.status_code == 201, revised.text
    next_tag = revised.json()["document"]["tag_sets"][0]
    assert next_tag["fixed_tags_sha256"] != tag_set["fixed_tags_sha256"]

    blocked = client.post(
        f"/api/v1/projects/{project_id}/generation/estimate",
        headers=session_headers,
        json={
            "chapter_id": chapter["chapter_id"],
            "per_panel_cost_ceiling_anlas": 10,
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "GENERATION_PROMPTS_NOT_APPROVED"


def test_prompt_inspector_maps_current_snapshot_without_secrets_or_external_requests(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    workflow = client.get(
        f"/api/v1/projects/{project_id}/prompting?chapter_id={chapter['chapter_id']}"
    ).json()
    prompt_version = workflow["prompt_bundle"]

    inspected = client.get(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
        f"{prompt_version['version_id']}/inspector",
        params={"snapshot_sha256": prompt_version["snapshot_sha256"]},
    )

    assert inspected.status_code == 200, inspected.text
    payload = inspected.json()
    assert payload["snapshot_sha256"] == prompt_version["snapshot_sha256"]
    assert payload["external_requests_started"] == 0
    assert payload["redaction"] == {
        "credentials_included": False,
        "headers_included": False,
        "source_chapter_included": False,
        "base64_included": False,
    }
    assert payload["generation_summary"] == {
        "panel_count": 1,
        "candidate_count_per_panel": 1,
        "estimated_calls": 1,
        "estimated_cost_upper_anlas": None,
        "cost_status": "requires_generation_estimate",
        "cost_notice": (
            "Prompt 审批不产生费用。保守成本上限在生成预估中按用户确认的"
            "每格上限计算。"
        ),
    }
    panel = payload["panels"][0]
    plan_characters = panel["prompt_plan"]["characters"]
    mapped_characters = panel["provider_execution_spec"]["character_captions"]
    assert [character["character_id"] for character in plan_characters] == [
        character["character_id"] for character in mapped_characters
    ]
    assert [character["order"] for character in plan_characters] == [
        character["order"] for character in mapped_characters
    ]
    assert [character["center"] for character in plan_characters] == [
        character["center"] for character in mapped_characters
    ]
    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    assert "unit-novelai-secret" not in serialized
    assert "authorization" not in serialized
    assert "base64" not in serialized.replace('"base64_included": false', "")
    assert "director_reference_images" not in serialized

    stale = client.get(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
        f"{prompt_version['version_id']}/inspector",
        params={"snapshot_sha256": "0" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "PROMPT_INSPECTOR_SNAPSHOT_STALE"


def test_missing_layout_blocks_prompt_generation_before_credential_read(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, storyboard = prepare_storyboard(client, session_headers)
    bundle = generate_bibles(
        client,
        session_headers,
        project_id,
        storyboard["storyboard_version_id"],
    )
    approve_complete_bibles(client, session_headers, project_id, bundle)
    saved = client.put(
        "/api/v1/vault/profiles/novelai",
        headers=session_headers,
        json={
            "provider": "novelai",
            "label": "NovelAI",
            "secret": "unit-layout-gate-secret",
        },
    )
    assert saved.status_code == 200, saved.text
    configured = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=session_headers,
        json={
            "provider_model_id": "nai-diffusion-4-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 20,
        },
    )
    assert configured.status_code == 200, configured.text
    tags = client.post(
        f"/api/v1/projects/{project_id}/prompting/character-tags/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "confirmed_data_send": True},
    )
    assert tags.status_code == 201, tags.text
    approved = client.post(
        f"/api/v1/projects/{project_id}/prompting/character-tags/"
        f"{tags.json()['version_id']}/approve",
        headers=session_headers,
    )
    assert approved.status_code == 200, approved.text

    secret_reads = 0
    original_get_secret = client.app.state.vault.get_secret

    def counted_get_secret(profile_id: str) -> str:
        nonlocal secret_reads
        secret_reads += 1
        return original_get_secret(profile_id)

    client.app.state.vault.get_secret = counted_get_secret
    secret_reads = 0
    response = client.post(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/generate",
        headers=session_headers,
        json={"chapter_id": chapter["chapter_id"], "confirmed_data_send": True},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAYOUT_NOT_READY"
    assert secret_reads == 0


def test_manual_prompting_revisions_preserve_stable_artifact_ids(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project_id, chapter, _bundle = prepare_generation_inputs(client, session_headers)
    workflow = client.get(
        f"/api/v1/projects/{project_id}/prompting?chapter_id={chapter['chapter_id']}"
    ).json()

    tag_version = workflow["character_tags"]
    changed_tags = copy.deepcopy(tag_version["document"])
    changed_tags["tag_sets"][0]["tag_set_id"] = str(uuid4())
    tag_revision = client.post(
        f"/api/v1/projects/{project_id}/prompting/character-tags/"
        f"{tag_version['version_id']}/revisions",
        headers=session_headers,
        json={"document": changed_tags},
    )
    assert tag_revision.status_code == 422
    assert tag_revision.json()["error"]["code"] == "INVALID_MODEL_ARTIFACT_IDS"

    prompt_version = workflow["prompt_bundle"]
    prompt_document = prompt_version["document"]
    changed_prompt = {
        "schema_version": "1.0",
        "storyboard_version_id": prompt_document["storyboard_version_id"],
        "character_tag_bundle_version_id": prompt_document[
            "character_tag_bundle_version_id"
        ],
        "packages": [
            {
                "prompt_package_id": package["prompt_package_id"],
                "panel_id": package["panel_id"],
                "base_visual_tags": package["base_visual_tags"],
                "character_blocks": [
                        {
                            "character_id": block["character_id"],
                            "tag_set_id": block["tag_set_id"],
                            "variable_tags": block["variable_tags"],
                            "negative_tags": next(
                                character["negative_tags"]
                                for character in package["structured_package"][
                                    "prompt_plan"
                                ]["characters"]
                                if character["character_id"] == block["character_id"]
                            ),
                            "action": next(
                                character["action"]
                                for character in package["structured_package"][
                                    "prompt_plan"
                                ]["characters"]
                                if character["character_id"] == block["character_id"]
                            ),
                            "order": next(
                                character["order"]
                                for character in package["structured_package"][
                                    "prompt_plan"
                                ]["characters"]
                                if character["character_id"] == block["character_id"]
                            ),
                            "center": next(
                                character["center"]
                                for character in package["structured_package"][
                                    "prompt_plan"
                                ]["characters"]
                                if character["character_id"] == block["character_id"]
                            ),
                        }
                    for block in package["character_blocks"]
                ],
                "style_tags": package["style_tags"],
                "negative_tags": package["negative_tags"],
                "relationship_action": package["structured_package"]["prompt_plan"][
                    "base"
                ]["relationship_action"],
                "continuity_tags": package["structured_package"]["prompt_plan"][
                    "continuity_tags"
                ],
            }
            for package in prompt_document["packages"]
        ],
    }
    changed_prompt["packages"][0]["prompt_package_id"] = str(uuid4())
    prompt_revision = client.post(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
        f"{prompt_version['version_id']}/revisions",
        headers=session_headers,
        json={"document": changed_prompt},
    )
    assert prompt_revision.status_code == 422
    assert prompt_revision.json()["error"]["code"] == "INVALID_MODEL_ARTIFACT_IDS"
