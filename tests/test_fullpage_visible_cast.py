import json

from fastapi.testclient import TestClient

from backend.app.shared_kernel import canonical_sha256
from tests.test_generation_queue import prepare_generation_inputs


def test_object_closeups_omit_offscreen_cast_and_preserve_legacy_plan(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project, chapter, _ = prepare_generation_inputs(client, session_headers)
    base = f"/api/v1/projects/{project}/full-pages"
    source = client.get(base + "/sources", params={"chapter_id": chapter["chapter_id"]}).json()
    panels = source["pages"][0]["panels"]
    options = {"chapter_id": chapter["chapter_id"], "page_number": 1, "seed": 42}
    original = client.post(base + "/preview", headers=session_headers, json=options).json()
    edited = client.post(
        base + "/preview",
        headers=session_headers,
        json={
            **options,
            "panel_characters": {p["panel_id"]: [] for p in panels},
            "panel_descriptions": {
                p["panel_id"]: "A closed book on a wooden table. No people." for p in panels
            },
        },
    )
    assert edited.status_code == 201, edited.text
    result = edited.json()
    assert "Character reference for" not in result["provider_payload"]["input"]
    assert result["provider_payload_sha256"] != original["provider_payload_sha256"]
    assert result["external_requests_started"] == 0
    assert "characters" in panels[0]
    for invalid in [{"foreign-panel": []}, {panels[0]["panel_id"]: ["stranger"]}]:
        denied = client.post(
            base + "/preview",
            headers=session_headers,
            json={**options, "panel_characters": invalid},
        )
        assert denied.status_code == 422
    with client.app.state.database.writer() as db:
        row = db.execute(
            "SELECT plan_json FROM full_page_generations WHERE generation_id = ?",
            (original["generation_id"],),
        ).fetchone()
        legacy = json.loads(row[0])
        legacy["options"].pop("panel_characters", None)
        digest = canonical_sha256(legacy)
        db.execute(
            "UPDATE full_page_generations SET plan_json = ?, plan_sha256 = ? "
            "WHERE generation_id = ?",
            (json.dumps(legacy), digest, original["generation_id"]),
        )
    reopened = client.get(base + "/" + original["generation_id"])
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["plan_sha256"] == digest
    assert reopened.json()["provider_payload"] == original["provider_payload"]
