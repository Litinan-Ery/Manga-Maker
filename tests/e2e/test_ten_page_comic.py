from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from backend.app.acceptance.ten_page_story import (
    PAGE_COUNT,
    PROJECT_TITLE,
    TEXT_MODEL_BASE_URL,
    TEXT_MODEL_NAME,
    TenPageAcceptanceTextModel,
)
from backend.app.novelai.mock import MockNovelAIClient
from tests.test_bibles_api import (
    approve_complete_bibles,
    generate_bibles,
)
from tests.test_exports_api import download_file, export_preflight, export_request
from tests.test_generation_queue import (
    create_job,
    estimate_plan,
    prepare_prompting,
    transition,
)


def test_ten_page_comic_mock_pipeline_exports_auditable_artifacts(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    """A ten-page comic must survive the whole product path and artifact audit."""

    project_id, chapter_id = _prepare_ten_page_generation_inputs(
        client, session_headers
    )

    workflow = client.get(
        f"/api/v1/projects/{project_id}/prompting?chapter_id={chapter_id}"
    ).json()
    prompt_version = workflow["prompt_bundle"]
    inspector = client.get(
        f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
        f"{prompt_version['version_id']}/inspector",
        params={"snapshot_sha256": prompt_version["snapshot_sha256"]},
    )
    assert inspector.status_code == 200, inspector.text
    assert inspector.json()["generation_summary"] == {
        "panel_count": PAGE_COUNT,
        "candidate_count_per_panel": 1,
        "estimated_calls": PAGE_COUNT,
        "estimated_cost_upper_anlas": None,
        "cost_status": "requires_generation_estimate",
        "cost_notice": (
            "Prompt 审批不产生费用。保守成本上限在生成预估中按用户确认的"
            "每格上限计算。"
        ),
    }
    assert inspector.json()["external_requests_started"] == 0

    estimate = estimate_plan(client, session_headers, project_id, chapter_id)
    assert estimate["page_count"] == PAGE_COUNT
    assert estimate["panel_count"] == PAGE_COUNT
    assert estimate["estimated_calls"] == PAGE_COUNT
    assert estimate["estimated_cost_upper_anlas"] == PAGE_COUNT * 10
    assert len(estimate["panels"]) == PAGE_COUNT

    created = create_job(
        client,
        session_headers,
        project_id,
        chapter_id,
        estimate,
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["candidate_count_per_panel"] == 1
    assert len(job["items"]) == PAGE_COUNT
    assert all(len(item["provider_payload_sha256"]) == 64 for item in job["items"])
    assert all("provider_payload" not in item for item in job["items"])

    started = transition(client, session_headers, project_id, job, "start")
    provider = MockNovelAIClient()
    client.app.state.generation_executor.provider_factory = (
        lambda _configuration, _secret_reader: provider
    )
    asyncio.run(
        client.app.state.generation_executor.run_until_blocked(started["job_id"])
    )
    completed = client.app.state.generation_queue.get_job(project_id, started["job_id"])
    assert completed["status"] == "completed"
    assert completed["calls_started"] == PAGE_COUNT
    assert completed["calls_completed"] == PAGE_COUNT
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
    assert [page["document"]["page_number"] for page in pages] == list(
        range(1, PAGE_COUNT + 1)
    )
    assert len({page["render_sha256"] for page in pages}) == PAGE_COUNT
    for page in pages:
        content = client.get(
            f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions/"
            f"{page['page_version_id']}/content",
            headers=session_headers,
        )
        assert content.status_code == 200
        with Image.open(io.BytesIO(content.content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content.content)) as image:
            assert image.format == "PNG"
            assert image.size == (2048, 3072)

    plan = export_preflight(client, session_headers, project_id, chapter_id)
    assert plan["page_count"] == PAGE_COUNT
    assert [page["ordinal"] for page in plan["pages"]] == list(
        range(1, PAGE_COUNT + 1)
    )
    exported_response = client.post(
        f"/api/v1/projects/{project_id}/exports",
        headers=session_headers,
        json=export_request(plan),
    )
    assert exported_response.status_code == 201, exported_response.text
    exported = exported_response.json()
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
    assert len({payload for payload in png_payloads}) == PAGE_COUNT
    for payload in png_payloads:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            assert image.size == (2048, 3072)

    pdf = download_file(
        client, session_headers, project_id, exported, by_kind["pdf"][0]
    )
    assert pdf.startswith(b"%PDF-")
    assert pdf.count(b"/Type /Page\n") == PAGE_COUNT

    cbz = download_file(
        client, session_headers, project_id, exported, by_kind["cbz"][0]
    )
    with zipfile.ZipFile(io.BytesIO(cbz)) as archive:
        assert archive.namelist() == [
            "ComicInfo.xml",
            *[f"{page_number:03d}.png" for page_number in range(1, PAGE_COUNT + 1)],
        ]
        assert all(archive.read(f"{page_number:03d}.png") for page_number in range(1, 11))

    package = download_file(
        client,
        session_headers,
        project_id,
        exported,
        by_kind["engineering_package"][0],
    )
    assert b"unit-novelai-secret" not in package
    assert b"unit-text-model-secret" not in package
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("records.json"))
        assert manifest["credentials_included"] is False
        assert len(manifest["selected_pages"]) == PAGE_COUNT
        assert len(records["tables"]["comic_pages"]) == PAGE_COUNT
        assert len(records["tables"]["page_versions"]) == PAGE_COUNT
        assert len(records["tables"]["generation_specs"]) == PAGE_COUNT

    dry_run = client.post(
        "/api/v1/imports/preflight",
        headers=session_headers,
        files={
            "file": (
                "rainy-letter-ten-pages.manga-maker.zip",
                package,
                "application/zip",
            )
        },
    )
    assert dry_run.status_code == 201, dry_run.text
    assert dry_run.json()["writes_performed"] == 0
    assert provider.generation_calls == PAGE_COUNT


def _prepare_ten_page_generation_inputs(
    client: TestClient, headers: dict[str, str]
) -> tuple[str, str]:
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": PROJECT_TITLE}
    )
    assert project.status_code == 201, project.text
    project_id = str(project.json()["project_id"])
    chapter_text = (
        "第一章 雨夜来信\n"
        "林夏在祖母留下的旧屋里发现一封没有署名的信。"
        "她沿着十个线索穿过雨夜，在天亮时理解了家人留下的告别。\n"
    )
    preflight = client.post(
        f"/api/v1/projects/{project_id}/source/preflight",
        headers=headers,
        files={"file": ("rainy-letter.txt", chapter_text.encode(), "text/plain")},
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
        json={"master_password": "ten page acceptance password"},
    )
    assert created_vault.status_code == 201, created_vault.text
    text_secret = client.put(
        "/api/v1/vault/profiles/text-model",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "label": "十页验收文本模型",
            "secret": "unit-text-model-secret",
        },
    )
    assert text_secret.status_code == 200, text_secret.text
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
    client.app.state.adaptation.provider_factory = (
        lambda configuration, secret_reader: TenPageAcceptanceTextModel(
            configuration, secret_reader
        )
    )
    storyboard_response = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
        headers=headers,
        json={"chapter_id": chapter_id, "page_budget": PAGE_COUNT},
    )
    assert storyboard_response.status_code == 201, storyboard_response.text
    storyboard = storyboard_response.json()
    assert len(storyboard["document"]["pages"]) == PAGE_COUNT
    approved_storyboard = client.post(
        f"/api/v1/projects/{project_id}/adaptation/storyboards/"
        f"{storyboard['storyboard_version_id']}/approve",
        headers=headers,
    )
    assert approved_storyboard.status_code == 200, approved_storyboard.text

    bundle = generate_bibles(
        client, headers, project_id, storyboard["storyboard_version_id"]
    )
    approve_complete_bibles(client, headers, project_id, bundle)
    image_secret = client.put(
        "/api/v1/vault/profiles/novelai",
        headers=headers,
        json={
            "provider": "novelai",
            "label": "十页验收 NovelAI",
            "secret": "unit-novelai-secret",
        },
    )
    assert image_secret.status_code == 200, image_secret.text
    configured_image = client.put(
        f"/api/v1/projects/{project_id}/novelai/config",
        headers=headers,
        json={
            "provider_model_id": "nai-diffusion-4-5-full",
            "credential_profile_id": "novelai",
            "timeout_seconds": 20,
        },
    )
    assert configured_image.status_code == 200, configured_image.text
    client.app.state.novelai.provider_factory = (
        lambda configuration, _secret_reader: MockNovelAIClient(
            provider_model_id=configuration.provider_model_id
        )
    )
    connection = client.post(
        f"/api/v1/projects/{project_id}/novelai/connection-test", headers=headers
    )
    assert connection.status_code == 200, connection.text
    prepare_prompting(client, headers, project_id, chapter_id)
    return project_id, chapter_id
