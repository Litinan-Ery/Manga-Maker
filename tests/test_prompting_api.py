from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient

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
        "schema_version": prompt_document["schema_version"],
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
                    }
                    for block in package["character_blocks"]
                ],
                "style_tags": package["style_tags"],
                "negative_tags": package["negative_tags"],
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
