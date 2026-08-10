from __future__ import annotations

import asyncio
import json
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from tests.test_generation_queue import transition
from tests.test_pages_api import prepare_page


def test_panel_reroll_creates_asset_and_page_versions_then_restores_without_calls(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    panel = page["document"]["panels"][0]
    calls_before = provider.generation_calls

    estimate = estimate_revision(
        client,
        session_headers,
        project_id,
        {
            "operation": "panel_reroll",
            "page_id": page["page_id"],
            "panel_id": panel["panel_id"],
            "per_panel_cost_ceiling_anlas": 9,
        },
    )
    assert estimate["panel_count"] == 1
    assert estimate["targets"][0]["parent_asset_version_id"] == panel["asset_version_id"]
    assert estimate["external_request_created"] is False
    assert provider.generation_calls == calls_before

    job = create_revision_job(client, session_headers, project_id, estimate).json()
    assert job["operation_kind"] == "panel_reroll"
    assert job["target_page_version_id"] == page["page_version_id"]
    assert job["external_requests_started"] == 0
    started = transition(client, session_headers, project_id, job, "start")
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    completed = client.app.state.generation_queue.get_job(project_id, started["job_id"])
    assert completed["status"] == "completed"
    assert completed["result_page_version_id"]
    assert provider.generation_calls == calls_before + 1
    current = client.get(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/current"
    ).json()
    assert current["version"] == 2
    assert current["source_job_id"] == completed["job_id"]
    next_asset_id = current["document"]["panels"][0]["asset_version_id"]
    assert next_asset_id != panel["asset_version_id"]

    asset_versions = client.get(
        f"/api/v1/projects/{project_id}/generation/assets/panels/"
        f"{panel['panel_id']}/versions"
    ).json()
    assert [asset["version"] for asset in asset_versions] == [2, 1]
    assert asset_versions[0]["parent_asset_version_id"] == panel["asset_version_id"]
    page_versions = client.get(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions"
    ).json()
    assert [version["version"] for version in page_versions] == [2, 1]

    restored_asset = client.post(
        f"/api/v1/projects/{project_id}/generation/assets/"
        f"{panel['asset_version_id']}/activate",
        headers=session_headers,
        json={
            "panel_id": panel["panel_id"],
            "expected_current_asset_version_id": next_asset_id,
        },
    )
    assert restored_asset.status_code == 200
    restored_page = client.post(
        f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions/"
        f"{page['page_version_id']}/activate",
        headers=session_headers,
        json={"expected_revision": current["page_revision"]},
    )
    assert restored_page.status_code == 200
    assert restored_page.json()["page_version_id"] == page["page_version_id"]
    assert provider.generation_calls == calls_before + 1
    assert len(
        client.get(
            f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions"
        ).json()
    ) == 2


def test_inpaint_freezes_mask_and_preserves_unmasked_mock_pixels(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    panel = page["document"]["panels"][0]
    parent_content = asset_content(
        client, session_headers, project_id, panel["asset_version_id"]
    )
    uploaded = upload_mask(
        client,
        session_headers,
        project_id,
        panel["panel_id"],
        panel["asset_version_id"],
        mask_bytes(832, 1216, box=(100, 100, 300, 300)),
    )
    assert uploaded.status_code == 201, uploaded.text
    mask = uploaded.json()
    assert mask["selected_pixel_count"] == 201 * 201
    assert mask["external_requests_started"] == 0

    estimate = estimate_revision(
        client,
        session_headers,
        project_id,
        {
            "operation": "inpaint",
            "page_id": page["page_id"],
            "panel_id": panel["panel_id"],
            "mask_asset_id": mask["mask_asset_id"],
            "edit_prompt": "修正右手手指，保持其余构图",
            "inpaint_strength": 0.65,
            "per_panel_cost_ceiling_anlas": 12,
        },
    )
    assert estimate["provider_model_id"].endswith("-inpainting")
    job_response = create_revision_job(client, session_headers, project_id, estimate)
    assert job_response.status_code == 201, job_response.text
    started = transition(
        client, session_headers, project_id, job_response.json(), "start"
    )
    asyncio.run(client.app.state.generation_executor.run_until_blocked(started["job_id"]))

    completed = client.app.state.generation_queue.get_job(project_id, started["job_id"])
    assert completed["status"] == "completed"
    assert provider.generation_calls == 2
    current_asset = client.get(
        f"/api/v1/projects/{project_id}/generation/assets"
    ).json()[0]
    child_content = asset_content(
        client, session_headers, project_id, current_asset["asset_version_id"]
    )
    with Image.open(BytesIO(parent_content)) as parent_image, Image.open(
        BytesIO(child_content)
    ) as child_image:
        assert child_image.getpixel((0, 0)) == parent_image.getpixel((0, 0))
        assert child_image.getpixel((200, 200)) != parent_image.getpixel((200, 200))

    with client.app.state.database.reader() as connection:
        row = connection.execute(
            """
            SELECT gs.document_json FROM generation_specs gs
            JOIN generation_job_items gi ON gi.item_id = gs.item_id
            WHERE gi.job_id = ?
            """,
            (started["job_id"],),
        ).fetchone()
    spec = json.loads(str(row["document_json"]))
    assert spec["action"] == "inpaint"
    assert spec["schema_version"] == "1.2"
    assert spec["parent_asset_version_id"] == panel["asset_version_id"]
    assert spec["mask_asset_id"] == mask["mask_asset_id"]
    assert spec["mask_sha256"] == mask["sha256"]
    assert spec["inpaint_strength"] == 0.65


def test_mask_validation_and_page_reroll_scope_fail_closed(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    prepared, provider, page = prepare_page(client, session_headers)
    project_id = prepared["project_id"]
    panel = page["document"]["panels"][0]
    endpoint_args = (project_id, panel["panel_id"], panel["asset_version_id"])

    empty = upload_mask(
        client,
        session_headers,
        *endpoint_args,
        mask_bytes(832, 1216),
    )
    full = upload_mask(
        client,
        session_headers,
        *endpoint_args,
        mask_bytes(832, 1216, full=True),
    )
    mismatch = upload_mask(
        client,
        session_headers,
        *endpoint_args,
        mask_bytes(640, 640, box=(10, 10, 20, 20)),
    )
    assert empty.json()["error"]["code"] == "MASK_EMPTY"
    assert full.json()["error"]["code"] == "MASK_SELECTS_ENTIRE_IMAGE"
    assert mismatch.json()["error"]["code"] == "MASK_DIMENSIONS_MISMATCH"

    estimate = estimate_revision(
        client,
        session_headers,
        project_id,
        {
            "operation": "page_reroll",
            "page_id": page["page_id"],
            "per_panel_cost_ceiling_anlas": 7,
        },
    )
    assert estimate["panel_count"] == len(page["document"]["panels"])
    assert [target["panel_id"] for target in estimate["targets"]] == [
        panel["panel_id"] for panel in page["document"]["panels"]
    ]
    stale = create_revision_job(
        client,
        session_headers,
        project_id,
        {**estimate, "plan_fingerprint": "f" * 64},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "GENERATION_PLAN_STALE"
    assert provider.generation_calls == 1


def estimate_revision(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/generation/revisions/estimate",
        headers=headers,
        json=request,
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_revision_job(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    estimate: dict[str, Any],
):
    target = estimate["targets"][0]
    return client.post(
        f"/api/v1/projects/{project_id}/generation/revisions/jobs",
        headers=headers,
        json={
            "operation": estimate["operation"],
            "page_id": estimate["page_id"],
            "panel_id": (
                target["panel_id"] if estimate["operation"] != "page_reroll" else None
            ),
            "mask_asset_id": target["mask_asset_id"],
            "edit_prompt": target["edit_prompt"],
            "inpaint_strength": target["inpaint_strength"],
            "per_panel_cost_ceiling_anlas": target["cost_ceiling_anlas"],
            "plan_fingerprint": estimate["plan_fingerprint"],
            "max_calls": estimate["panel_count"],
            "max_cost_anlas": estimate["estimated_cost_upper_anlas"],
            "confirmed": True,
        },
    )


def upload_mask(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    panel_id: str,
    parent_asset_version_id: str,
    content: bytes,
):
    return client.post(
        f"/api/v1/projects/{project_id}/generation/masks",
        headers=headers,
        data={
            "panel_id": panel_id,
            "parent_asset_version_id": parent_asset_version_id,
        },
        files={"mask": ("mask.png", content, "image/png")},
    )


def asset_content(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    asset_version_id: str,
) -> bytes:
    response = client.get(
        f"/api/v1/projects/{project_id}/generation/assets/{asset_version_id}/content",
        headers=headers,
    )
    assert response.status_code == 200
    return response.content


def mask_bytes(
    width: int,
    height: int,
    *,
    box: tuple[int, int, int, int] | None = None,
    full: bool = False,
) -> bytes:
    image = Image.new("L", (width, height), color=255 if full else 0)
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill=255)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
