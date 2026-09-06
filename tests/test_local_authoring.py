from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.adaptation.models import StoryboardRequest
from tests.test_adaptation_api import create_project_and_source, storyboard_document


def authored_source(client: TestClient, headers: dict[str, str]) -> tuple[str, dict[str, Any]]:
    project, chapter, _ = create_project_and_source(client, headers)
    prefix = f"/api/v1/projects/{project}/adaptation/storyboards"
    source = client.get(
        prefix + "/source", params={"chapter_id": chapter["chapter_id"], "page_budget": 1}
    )
    assert source.status_code == 200, source.text
    body = source.json()
    return project, {
        "chapter_id": chapter["chapter_id"],
        "page_budget": 1,
        "expected_source_fingerprint": body["source_fingerprint"],
        "source_note": "UC-100-02 local authored fixture",
        "document": storyboard_document(
            StoryboardRequest.model_validate(body["request"]), "represented"
        ).model_dump(mode="json"),
    }


@pytest.mark.parametrize("problem", ["anchor", "chapter", "fingerprint", "page_policy"])
def test_local_storyboard_import_rejects_invalid_or_stale_sources(
    client: TestClient,
    session_headers: dict[str, str],
    problem: str,
) -> None:
    project, body = authored_source(client, session_headers)
    if problem == "anchor":
        body["document"]["pages"][0]["panels"][0]["source_anchor_ids"] = [str(uuid4())]
    elif problem == "chapter":
        body["document"]["chapter_version"] += 1
    elif problem == "fingerprint":
        body["expected_source_fingerprint"] = "0" * 64
    else:
        body["document"]["pages"][0]["page_type"] = "standard"
    result = client.post(
        f"/api/v1/projects/{project}/adaptation/storyboards/import",
        headers=session_headers,
        json=body,
    )
    assert result.status_code in (409, 422), result.text
    with client.app.state.database.reader() as db:
        assert db.execute("SELECT COUNT(*) FROM storyboard_versions").fetchone()[0] == 0


def test_local_authored_sources_reach_approved_tags_without_text_model(
    client: TestClient,
    session_headers: dict[str, str],
) -> None:
    project, body = authored_source(client, session_headers)
    prefix = f"/api/v1/projects/{project}"
    imported = client.post(
        prefix + "/adaptation/storyboards/import", headers=session_headers, json=body
    )
    assert imported.status_code == 201, imported.text
    storyboard = imported.json()
    version = storyboard["storyboard_version_id"]
    assert storyboard["provenance"]["change_type"] == "local_import"
    assert storyboard["provenance"]["external_requests_started"] == 0
    assert (
        client.post(
            prefix + f"/adaptation/storyboards/{version}/approve", headers=session_headers
        ).status_code
        == 200
    )
    character = {
        "character_id": str(uuid4()),
        "name": "林夏",
        "narrative_role": "主角",
        "age_range": "adult",
        "face_shape": "oval face",
        "hair": "short black hair",
        "body_type": "slender",
        "outfit": ["white shirt"],
        "signature_features": ["dark eyes"],
        "forbidden_changes": ["different hairstyle"],
        "expression_range": ["alert"],
        "positive_prompt_fragment": "adult woman",
        "negative_prompt_fragment": "long hair",
    }
    characters = {
        "schema_version": "1.0",
        "character_bible_id": str(uuid4()),
        "storyboard_version_id": version,
        "characters": [character],
    }
    style = {
        key: "black and white ink"
        for key in (
            "summary",
            "line_art",
            "screentone",
            "lighting",
            "background_density",
            "whitespace",
            "camera_language",
            "positive_prompt_fragment",
            "negative_prompt_fragment",
        )
    }
    style.update(
        {
            "schema_version": "1.0",
            "style_bible_id": str(uuid4()),
            "storyboard_version_id": version,
            "prohibited_elements": ["watermark"],
        }
    )
    bible_body = {
        "storyboard_version_id": version,
        "character_bible": characters,
        "style_bible": style,
        "source_note": "authored fixture",
    }
    invalid = deepcopy(bible_body)
    invalid["character_bible"]["characters"][0]["reference_asset_ids"] = [str(uuid4())]
    rejected = client.post(prefix + "/bibles/import", headers=session_headers, json=invalid)
    assert rejected.status_code == 422
    bible = client.post(prefix + "/bibles/import", headers=session_headers, json=bible_body)
    assert bible.status_code == 201, bible.text
    bundle = bible.json()
    for kind, field, id_field in (
        ("character", "character_bible", "version_id"),
        ("style", "style_bible", "version_id"),
    ):
        response = client.post(
            prefix + f"/bibles/{kind}/{bundle[field][id_field]}/approve", headers=session_headers
        )
        assert response.status_code == 200, response.text
    # Configure only an image profile, then lock the vault. Importing tags must not read it.
    client.post(
        "/api/v1/vault",
        headers=session_headers,
        json={"master_password": "local authoring test password"},
    )
    client.put(
        "/api/v1/vault/profiles/novelai",
        headers=session_headers,
        json={"provider": "novelai", "label": "test", "secret": "never-sent-test-key"},
    )
    configured = client.put(
        prefix + "/novelai/config",
        headers=session_headers,
        json={
            "provider_model_id": "nai-diffusion-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 120,
        },
    )
    assert configured.status_code == 200
    client.post("/api/v1/vault/lock", headers=session_headers)
    tag_body = {
        "chapter_id": body["chapter_id"],
        "source_note": "authored fixture",
        "document": {
            "schema_version": "1.0",
            "storyboard_version_id": version,
            "character_bible_version_id": bundle["character_bible"]["version_id"],
            "style_bible_version_id": bundle["style_bible"]["version_id"],
            "tag_sets": [
                {
                    "tag_set_id": str(uuid4()),
                    "character_id": character["character_id"],
                    "character_name": "林夏",
                    "fixed_tags": ["adult woman", "short black hair"],
                    "negative_tags": ["long hair"],
                    "rationale": "stable appearance",
                }
            ],
        },
    }
    invalid_tags = deepcopy(tag_body)
    invalid_tags["document"]["character_bible_version_id"] = str(uuid4())
    assert (
        client.post(
            prefix + "/prompting/character-tags/import", headers=session_headers, json=invalid_tags
        ).status_code
        == 422
    )
    tags = client.post(
        prefix + "/prompting/character-tags/import", headers=session_headers, json=tag_body
    )
    assert tags.status_code == 201, tags.text
    tag = tags.json()
    assert tag["provenance"]["external_requests_started"] == 0
    approved = client.post(
        prefix + f"/prompting/character-tags/{tag['version_id']}/approve",
        headers=session_headers,
    )
    assert approved.status_code == 200, approved.text
    with client.app.state.database.reader() as db:
        assert db.execute("SELECT COUNT(*) FROM text_model_configs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM full_page_generations").fetchone()[0] == 0
