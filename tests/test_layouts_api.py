from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.bootstrap.dependencies import get_adaptation_facade
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.modules.adaptation.contracts import (
    StoryboardPageSnapshotV1,
    StoryboardVersionRefV1,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts" / "fixtures" / "v0.3"
PROJECT_ID = UUID("01900000-0000-7000-8000-000000009001")
CHAPTER_ID = UUID("01900000-0000-7000-8000-000000009002")


class StubAdaptationFacade:
    def __init__(self, page_id: str, panel_ids: tuple[str, ...]) -> None:
        self._page_id = page_id
        self._panel_ids = panel_ids

    def current_storyboard_ref(
        self,
        project_id: str,
        chapter_id: str,
    ) -> StoryboardVersionRefV1:
        del project_id, chapter_id
        return self.storyboard

    def storyboard_page(
        self,
        project_id: str,
        storyboard_version_id: str,
        page_id: str,
    ) -> StoryboardPageSnapshotV1:
        if (
            page_id != self._page_id
            or storyboard_version_id != self.storyboard.storyboard_version_id
        ):
            raise ValueError("storyboard version does not contain the requested page")
        return StoryboardPageSnapshotV1(
            project_id=project_id,
            chapter_id=CHAPTER_ID,
            page_id=page_id,
            storyboard=self.storyboard,
            panel_ids=self._panel_ids,
        )

    @property
    def storyboard(self) -> StoryboardVersionRefV1:
        return StoryboardVersionRefV1(
            storyboard_id="01900000-0000-7000-8000-000000009100",
            storyboard_version_id="01900000-0000-7000-8000-000000009101",
            version=1,
            content_sha256="b" * 64,
            approved=True,
        )


@pytest.fixture
def layout_api(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, str], Path]]:
    draft = layout_payload()
    panels = tuple(
        str(frame["panel_id"])
        for frame in draft["frames"]
        if frame["panel_id"] is not None
    )
    app: FastAPI = create_app(Settings(app_data_dir=tmp_path / "app-data", environment="test"))
    app.dependency_overrides[get_adaptation_facade] = lambda: StubAdaptationFacade(
        str(draft["page_id"]), panels
    )
    with TestClient(app) as client:
        workspace = tmp_path / "app-data" / "projects" / str(PROJECT_ID)
        workspace.mkdir(mode=0o700, parents=True)
        with client.app.state.database.writer() as connection:
            connection.execute(
                """
                INSERT INTO projects(project_id, title, workspace_path, workflow_version)
                VALUES (?, 'Layout API fixture', ?, 'v03')
                """,
                (str(PROJECT_ID), str(workspace)),
            )
        headers = {
            "X-Manga-Maker-Session": client.app.state.local_session.token,
            "X-CSRF-Token": client.app.state.local_session.csrf_token,
        }
        yield client, headers, tmp_path / "app-data" / "manga_maker.sqlite3"


def layout_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "page-layout-draft.json").read_text(encoding="utf-8"))


def capabilities_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "dimension-capabilities.json").read_text(encoding="utf-8"))


def create_body() -> dict[str, Any]:
    return {
        "chapter_id": str(CHAPTER_ID),
        "storyboard_version_id": "01900000-0000-7000-8000-000000009101",
        "draft": layout_payload(),
    }


def command_headers(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def create_layout(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/drafts",
        headers=command_headers(headers, "create-layout-1"),
        json=create_body(),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_layout_api_create_get_list_idempotency_openapi_and_zero_external_requests(
    layout_api: tuple[TestClient, dict[str, str], Path],
) -> None:
    client, headers, database_path = layout_api

    created = create_layout(client, headers)
    replay = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/drafts",
        headers=command_headers(headers, "create-layout-1"),
        json=create_body(),
    )
    assert replay.status_code == 201
    assert replay.json() == created
    assert created["external_requests_started"] == 0

    layout_id = created["layout"]["page_layout_draft_id"]
    current = client.get(f"/api/v1/projects/{PROJECT_ID}/layouts/drafts/{layout_id}")
    versions = client.get(
        f"/api/v1/projects/{PROJECT_ID}/layouts/drafts/{layout_id}/versions"
    )
    assert current.status_code == 200 and current.json() == created
    assert versions.status_code == 200 and versions.json() == [created]

    conflict_body = create_body()
    conflict_body["draft"]["reading_direction"] = "rtl_ttb"
    conflict = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/drafts",
        headers=command_headers(headers, "create-layout-1"),
        json=conflict_body,
    )
    assert conflict.status_code == 409
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM page_layout_drafts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM layout_command_receipts").fetchone()[0] == 1

    openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]
    expected_paths = {
        "/api/v1/projects/{project_id}/layouts/drafts",
        "/api/v1/projects/{project_id}/layouts/drafts/{page_layout_draft_id}",
        "/api/v1/projects/{project_id}/layouts/drafts/{page_layout_draft_id}/versions",
        "/api/v1/projects/{project_id}/layouts/{page_layout_draft_version_id}/revisions",
        "/api/v1/projects/{project_id}/layouts/{page_layout_draft_version_id}/validate",
        "/api/v1/projects/{project_id}/layouts/{page_layout_draft_version_id}/approve",
        "/api/v1/projects/{project_id}/layouts/{page_layout_draft_version_id}/impact",
    }
    assert expected_paths <= set(paths)
    create_parameters = paths[
        "/api/v1/projects/{project_id}/layouts/drafts"
    ]["post"]["parameters"]
    assert any(parameter["name"] == "Idempotency-Key" for parameter in create_parameters)


def test_two_concurrent_revision_writes_have_exactly_one_winner(
    layout_api: tuple[TestClient, dict[str, str], Path],
) -> None:
    client, headers, database_path = layout_api
    created = create_layout(client, headers)
    version_id = created["page_layout_draft_version_id"]
    draft = created["layout"]
    first_body = {
        "expected_revision": 1,
        "storyboard_version_id": "01900000-0000-7000-8000-000000009101",
        "draft": changed_layout(draft, focal_x=0.55),
    }
    second_body = {
        **first_body,
        "draft": changed_layout(draft, focal_x=0.65),
    }

    def submit(index: int) -> tuple[int, dict[str, Any]]:
        body = first_body if index == 1 else second_body
        response = client.post(
            f"/api/v1/projects/{PROJECT_ID}/layouts/{version_id}/revisions",
            headers=command_headers(headers, f"save-layout-{index}"),
            json=body,
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (1, 2)))

    assert sorted(status for status, _body in results) == [201, 409]
    losing = next(body for status, body in results if status == 409)
    assert losing["detail"] == {
        "code": "LAYOUT_REVISION_CONFLICT",
        "current_revision": 2,
    }
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM page_layout_drafts").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM page_layout_drafts WHERE is_current = 1"
        ).fetchone()[0] == 1


def test_validate_approve_impact_and_dimension_hash_gate(
    layout_api: tuple[TestClient, dict[str, str], Path],
) -> None:
    client, headers, database_path = layout_api
    created = create_layout(client, headers)
    version_id = created["page_layout_draft_version_id"]
    validate_body = {
        "expected_revision": 1,
        "layout_content_sha256": created["layout"]["content_sha256"],
        "storyboard_version_id": "01900000-0000-7000-8000-000000009101",
        "dimension_capabilities": capabilities_payload(),
        "target_pixels": 1_572_864,
        "max_crop_safe_risk": 0.01,
    }
    validated = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/{version_id}/validate",
        headers=headers,
        json=validate_body,
    )
    assert validated.status_code == 200, validated.text
    validation = validated.json()
    assert validation["valid"]
    assert validation["external_requests_started"] == 0
    assert all(outcome["status"] == "selected" for outcome in validation["dimension_outcomes"])

    selections = validation["dimension_outcomes"]
    tampered = json.loads(json.dumps(selections))
    tampered[0]["content_sha256"] = "f" * 64
    approve_body = {**validate_body, "dimension_selections": tampered}
    rejected = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/{version_id}/approve",
        headers=command_headers(headers, "approve-layout-bad"),
        json=approve_body,
    )
    assert rejected.status_code == 409

    approve_body["dimension_selections"] = selections
    approved = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/{version_id}/approve",
        headers=command_headers(headers, "approve-layout-1"),
        json=approve_body,
    )
    assert approved.status_code == 200, approved.text
    approval = approved.json()
    assert approval["state"] == "active"
    assert approval["external_requests_started"] == 0
    assert len(approval["dimension_selection_sha256s"]) == 2
    replay = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/{version_id}/approve",
        headers=command_headers(headers, "approve-layout-1"),
        json=approve_body,
    )
    assert replay.status_code == 200 and replay.json() == approval

    impact = client.get(
        f"/api/v1/projects/{PROJECT_ID}/layouts/{version_id}/impact",
        params={"layout_content_sha256": created["layout"]["content_sha256"]},
    )
    assert impact.status_code == 200
    assert impact.json()["impacts"] == []
    assert impact.json()["external_requests_started"] == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dimension_selections").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM layout_approvals").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM layout_approval_dimension_selections"
        ).fetchone()[0] == 2


def test_mutating_layout_routes_require_session_and_idempotency_headers(
    layout_api: tuple[TestClient, dict[str, str], Path],
) -> None:
    client, _headers, _database_path = layout_api
    missing_all = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/drafts",
        json=create_body(),
    )
    assert missing_all.status_code in {401, 422}
    idempotency_only = client.post(
        f"/api/v1/projects/{PROJECT_ID}/layouts/drafts",
        headers={"Idempotency-Key": "unauthorized-layout"},
        json=create_body(),
    )
    assert idempotency_only.status_code in {401, 403}


def changed_layout(payload: dict[str, Any], *, focal_x: float) -> dict[str, Any]:
    changed = json.loads(json.dumps(payload))
    leaf = next(frame for frame in changed["frames"] if frame["panel_id"] is not None)
    leaf["focal_point"]["x"] = focal_x
    return changed
