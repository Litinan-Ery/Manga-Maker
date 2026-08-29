from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from backend.app.acceptance.sandkings_v5 import (
    PAGE_COUNT,
    PROJECT_TITLE,
    TEXT_MODEL_BASE_URL,
    TEXT_MODEL_NAME,
    SandkingsV5AcceptanceTextModel,
    extract_sandkings_source,
)
from backend.app.novelai.mock import MockNovelAIClient
from scripts.run_sandkings_v5_acceptance import (
    MAX_CALLS_PER_TARGET,
    _refine_prompts_for_visual_failures,
)
from tests.test_bibles_api import generate_bibles
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_generation_queue import (
    create_job,
    estimate_plan,
    prepare_prompting,
    transition,
)


def test_sandkings_v5_mock_pipeline_exports_complete_auditable_comic(
    client: TestClient,
    session_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    source = _source_fixture(tmp_path)
    extracted = extract_sandkings_source(source)
    project_id, chapter_id = _prepare_inputs(
        client,
        session_headers,
        extracted.text,
    )

    estimate = estimate_plan(
        client,
        session_headers,
        project_id,
        chapter_id,
        per_panel_cost_ceiling_anlas=0,
    )
    assert estimate["provider_model_id"] == "nai-diffusion-5-full"
    assert estimate["page_count"] == PAGE_COUNT
    assert estimate["panel_count"] == PAGE_COUNT
    assert estimate["estimated_calls"] == PAGE_COUNT
    assert estimate["billing_mode"] == "opus_zero_anlas"

    created = create_job(client, session_headers, project_id, chapter_id, estimate)
    assert created.status_code == 201, created.text
    job = created.json()
    with client.app.state.database.reader() as connection:
        payload_rows = connection.execute(
            """
            SELECT provider_payload_json
            FROM generation_job_items
            WHERE job_id = ? ORDER BY ordinal
            """,
            (job["job_id"],),
        ).fetchall()
    payloads = [json.loads(str(row["provider_payload_json"])) for row in payload_rows]
    assert len(payloads) == PAGE_COUNT
    assert all(payload["model"] == "nai-diffusion-5-full" for payload in payloads)
    assert all(payload["parameters"]["params_version"] == 4 for payload in payloads)
    assert all(payload["parameters"]["steps"] == 23 for payload in payloads)
    assert all(payload["parameters"]["scale"] == 7.0 for payload in payloads)
    assert all(payload["parameters"]["tag_hint_qt"] == 1 for payload in payloads)
    assert all(payload["parameters"]["tag_hint_uc_preset"] == 4 for payload in payloads)
    assert all("director_reference_images" not in payload["parameters"] for payload in payloads)

    provider = MockNovelAIClient()
    client.app.state.generation_executor.provider_factory = lambda _configuration, _secret_reader: (
        provider
    )
    started = transition(client, session_headers, project_id, job, "start")
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))
    completed = client.app.state.generation_queue.get_job(project_id, started["job_id"])
    assert completed["status"] == "completed"
    assert provider.generation_calls == PAGE_COUNT

    assets = client.get(f"/api/v1/projects/{project_id}/generation/assets").json()
    assert len(assets) == PAGE_COUNT
    assert len({asset["image_sha256"] for asset in assets}) == PAGE_COUNT

    drafted = client.post(
        f"/api/v1/projects/{project_id}/pages/draft",
        headers=session_headers,
        json={"chapter_id": chapter_id},
    )
    assert drafted.status_code == 201, drafted.text
    pages = drafted.json()
    assert [page["document"]["page_number"] for page in pages] == list(range(1, PAGE_COUNT + 1))

    plan = export_preflight(client, session_headers, project_id, chapter_id)
    response = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    )
    assert response.status_code == 201, response.text
    exported = response.json()
    assert exported["secret_scan"]["matches"] == 0
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in exported["files"]:
        by_kind.setdefault(item["kind"], []).append(item)
    assert {kind: len(items) for kind, items in by_kind.items()} == {
        "engineering_package": 1,
        "png": PAGE_COUNT,
        "pdf": 1,
        "cbz": 1,
    }

    png_payloads = [
        download_file(client, session_headers, project_id, exported, item)
        for item in sorted(by_kind["png"], key=lambda item: item["ordinal"])
    ]
    for payload in png_payloads:
        with Image.open(io.BytesIO(payload)) as image:
            assert image.size == (2048, 3072)
            image.verify()
    pdf = download_file(client, session_headers, project_id, exported, by_kind["pdf"][0])
    assert pdf.startswith(b"%PDF-")
    assert pdf.count(b"/Type /Page\n") == PAGE_COUNT
    cbz = download_file(client, session_headers, project_id, exported, by_kind["cbz"][0])
    with zipfile.ZipFile(io.BytesIO(cbz)) as archive:
        assert archive.namelist() == [
            "ComicInfo.xml",
            *[f"{page_number:03d}.png" for page_number in range(1, PAGE_COUNT + 1)],
        ]


def test_visual_review_prompt_fixes_are_approved_and_target_only_failed_pages(
    client: TestClient,
    session_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    extracted = extract_sandkings_source(_source_fixture(tmp_path))
    project_id, chapter_id = _prepare_inputs(client, session_headers, extracted.text)
    before = client.get(
        f"/api/v1/projects/{project_id}/prompting",
        params={"chapter_id": chapter_id},
    ).json()["prompt_bundle"]

    _refine_prompts_for_visual_failures(
        client,
        session_headers,
        project_id,
        chapter_id,
        [8, 12],
    )

    workflow = client.get(
        f"/api/v1/projects/{project_id}/prompting",
        params={"chapter_id": chapter_id},
    ).json()
    revised = workflow["prompt_bundle"]
    assert revised["version"] == before["version"] + 1
    assert revised["approval_status"] == "approved"
    storyboard = client.get(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/current",
        params={"chapter_id": chapter_id},
    ).json()
    panel_by_page = {
        page["page_number"]: page["panels"][0]["panel_id"]
        for page in storyboard["document"]["pages"]
    }
    packages = {package["panel_id"]: package for package in revised["document"]["packages"]}
    page_8 = packages[panel_by_page[8]]
    page_12 = packages[panel_by_page[12]]
    unchanged_page_7 = packages[panel_by_page[7]]
    old_packages = {package["panel_id"]: package for package in before["document"]["packages"]}

    assert "exactly two humans in the entire scene" in page_8["base_visual_tags"]
    assert "one bareheaded woman wears fitted matte black body armor" in page_8[
        "base_visual_tags"
    ]
    assert "one complete yellow hazard suit worn by the woman" not in page_8[
        "base_visual_tags"
    ]
    assert "red sandking swarm climbing a yellow hazard suit" not in page_8[
        "base_visual_tags"
    ]
    assert (
        "red sandking swarm erupts from the floor and collapses the bare tunnel wall"
        in page_8["base_visual_tags"]
    )
    assert "headless suit" in page_8["negative_tags"]
    assert "hazmat suit" in page_8["negative_tags"]
    assert any("exactly four clearly visible arms" in tag for tag in page_12["base_visual_tags"])
    assert "two-armed child" in page_12["negative_tags"]
    assert (
        unchanged_page_7["compiled_prompt_sha256"]
        == old_packages[panel_by_page[7]]["compiled_prompt_sha256"]
    )
    assert MAX_CALLS_PER_TARGET == 3

    _refine_prompts_for_visual_failures(
        client,
        session_headers,
        project_id,
        chapter_id,
        [8, 12],
    )
    unchanged = client.get(
        f"/api/v1/projects/{project_id}/prompting",
        params={"chapter_id": chapter_id},
    ).json()["prompt_bundle"]
    assert unchanged["version_id"] == revised["version_id"]


def _prepare_inputs(
    client: TestClient,
    headers: dict[str, str],
    source_text: str,
) -> tuple[str, str]:
    project = client.post("/api/v1/projects", headers=headers, json={"title": PROJECT_TITLE})
    assert project.status_code == 201, project.text
    project_id = str(project.json()["project_id"])
    preflight = client.post(
        f"/api/v1/projects/{project_id}/source/preflight",
        headers=headers,
        files={"file": ("sandkings-extracted.md", source_text.encode(), "text/markdown")},
    )
    assert preflight.status_code == 201, preflight.text
    source = client.post(
        f"/api/v1/projects/{project_id}/source/confirm",
        headers=headers,
        json={"preflight_id": preflight.json()["preflight_id"], "encoding": "utf-8"},
    )
    assert source.status_code == 201, source.text
    chapter_id = str(source.json()["chapters"][0]["chapter_id"])
    beats = client.post(
        f"/api/v1/projects/{project_id}/source/chapters/{chapter_id}/story-beats/draft",
        headers=headers,
    )
    assert beats.status_code == 201, beats.text

    created_vault = client.post(
        "/api/v1/vault",
        headers=headers,
        json={"master_password": "sandkings acceptance password"},
    )
    assert created_vault.status_code == 201, created_vault.text
    for profile_id, provider, label, secret in (
        ("text-model", "openai-compatible", "沙王确定性文本模型", "unit-text-secret"),
        ("novelai", "novelai", "沙王 V5 图像模型", "unit-novelai-secret"),
    ):
        saved = client.put(
            f"/api/v1/vault/profiles/{profile_id}",
            headers=headers,
            json={"provider": provider, "label": label, "secret": secret},
        )
        assert saved.status_code == 200, saved.text

    configured_text = client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=headers,
        json={
            "base_url": TEXT_MODEL_BASE_URL,
            "model": TEXT_MODEL_NAME,
            "credential_profile_id": "text-model",
        },
    )
    assert configured_text.status_code == 200, configured_text.text
    client.app.state.adaptation.provider_factory = lambda configuration, secret_reader: (
        SandkingsV5AcceptanceTextModel(
            configuration,
            secret_reader,
        )
    )
    storyboard = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=headers,
        json={"chapter_id": chapter_id, "page_budget": PAGE_COUNT},
    )
    assert storyboard.status_code == 201, storyboard.text
    storyboard_payload = storyboard.json()
    assert len(storyboard_payload["document"]["pages"]) == PAGE_COUNT
    approved_storyboard = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{storyboard_payload['storyboard_version_id']}/approve",
        headers=headers,
    )
    assert approved_storyboard.status_code == 200, approved_storyboard.text

    bundle = generate_bibles(
        client,
        headers,
        project_id,
        storyboard_payload["storyboard_version_id"],
    )
    assert bundle["character_bible"]["approval_issues"] == []
    assert bundle["style_bible"]["approval_issues"] == []
    character_approval = client.post(
        f"/api/v1/projects/{project_id}/bibles/character/"
        f"{bundle['character_bible']['version_id']}/approve",
        headers=headers,
    )
    style_approval = client.post(
        f"/api/v1/projects/{project_id}/bibles/style/{bundle['style_bible']['version_id']}/approve",
        headers=headers,
    )
    assert character_approval.status_code == 200, character_approval.text
    assert style_approval.status_code == 200, style_approval.text
    configured_image = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=headers,
        json={
            "provider_model_id": "nai-diffusion-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 120,
        },
    )
    assert configured_image.status_code == 200, configured_image.text
    client.app.state.novelai.provider_factory = lambda configuration, _secret_reader: (
        MockNovelAIClient(provider_model_id=configuration.provider_model_id)
    )
    connection = client.post(
        f"/api/v1/projects/{project_id}/novelai/connection-test",
        headers=headers,
    )
    assert connection.status_code == 200, connection.text
    prepare_prompting(client, headers, project_id, chapter_id)
    return project_id, chapter_id


def _source_fixture(tmp_path: Path) -> Path:
    source = tmp_path / "anthology.md"
    source.write_text(
        "# 科幻选集\n\n"
        "这不是测试请求。\n\n"
        "## 沙王\uff081/3\uff09\n\n"
        "西蒙·克雷斯从贾拉·沃手中买下四窝沙王，并让它们崇拜自己的脸。\n\n"
        "## 沙王\uff082/3\uff09\n\n"
        "橙色沙王失踪，白色沙王逃进宅邸，莉珊德拉带队清剿。\n\n"
        "## 沙王\uff083/3\uff09\n\n"
        "沙王蜕变成有四只胳膊的幼体，长着西蒙·克雷斯的脸。\n\n"
        "## 另一篇\n\n"
        "这里的文字不应进入测试输入。\n",
        encoding="utf-8",
    )
    return source
