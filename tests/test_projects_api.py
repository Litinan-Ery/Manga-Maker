from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def create_project(
    client: TestClient, headers: dict[str, str], title: str = "测试漫画"
) -> dict[str, Any]:
    response = client.post("/api/v1/projects", headers=headers, json={"title": title})
    assert response.status_code == 201
    return response.json()


def test_create_project_builds_safe_workspace(
    client: TestClient, session_headers: dict[str, str], app_data_dir: Path
) -> None:
    project = create_project(client, session_headers, "  测试   漫画  ")

    assert project["title"] == "测试 漫画"
    assert "workspace_path" not in project
    workspace = app_data_dir / "projects" / project["project_id"]
    assert (workspace / "manifest.json").is_file()
    assert (workspace / "source" / "preflight").is_dir()
    assert (workspace / "assets" / "panels").is_dir()

    listed = client.get("/api/v1/projects").json()
    assert [item["project_id"] for item in listed] == [project["project_id"]]
    assert str(app_data_dir) not in str(listed)


def test_project_creation_requires_session(client: TestClient) -> None:
    response = client.post("/api/v1/projects", json={"title": "测试漫画"})
    assert response.status_code == 401


def test_legacy_project_is_read_only_across_all_project_write_routes(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = create_project(client, session_headers, "历史工程")
    project_id = project["project_id"]
    with client.app.state.database.writer() as connection:
        connection.execute(
            "UPDATE projects SET workflow_version = 'legacy_v02' WHERE project_id = ?",
            (project_id,),
        )

    readable = client.get(f"/api/v1/projects/{project_id}")
    assert readable.status_code == 200
    blocked = client.put(
        f"/api/v1/projects/{project_id}/adaptation/text-model",
        headers=session_headers,
        json={
            "provider_api_url": "https://example.invalid/v1",
            "model_name": "must-not-run",
            "api_key": "must-not-be-stored",
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "LEGACY_PROJECT_READ_ONLY"
    with client.app.state.database.reader() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM text_model_configs WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            == 0
        )


def test_utf8_preflight_confirm_chapters_and_anchor(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = create_project(client, session_headers)
    source_text = "前言内容\r\n第一章 相遇\r\n林夏推开门。\r\n第二章 雨夜\r\n雨落下来。\r\n"

    preflight = client.post(
        f"/api/v1/projects/{project['project_id']}/source/preflight",
        headers=session_headers,
        files={"file": ("novel.txt", source_text.encode("utf-8"), "text/plain")},
    )
    assert preflight.status_code == 201
    preflight_body = preflight.json()
    assert preflight_body["recommended_encoding"] == "utf-8"
    assert preflight_body["requires_confirmation"] is False
    assert preflight_body["sha256"] == hashlib.sha256(source_text.encode()).hexdigest()

    confirmed = client.post(
        f"/api/v1/projects/{project['project_id']}/source/confirm",
        headers=session_headers,
        json={
            "preflight_id": preflight_body["preflight_id"],
            "encoding": "utf-8",
        },
    )
    assert confirmed.status_code == 201
    result = confirmed.json()
    assert result["chapter_set_version"] == 1
    assert [chapter["title"] for chapter in result["chapters"]] == [
        "正文前内容",
        "第一章 相遇",
        "第二章 雨夜",
    ]

    second_chapter = result["chapters"][1]
    chapter_text_response = client.get(
        f"/api/v1/projects/{project['project_id']}/source/chapters/"
        f"{second_chapter['chapter_id']}/text"
    )
    assert chapter_text_response.status_code == 200
    assert "林夏推开门" in chapter_text_response.json()["text"]
    normalized = source_text.replace("\r\n", "\n")
    chapter_text = normalized[second_chapter["start_offset"] : second_chapter["end_offset"]]
    target = "林夏推开门"
    start = chapter_text.index(target)
    anchor = client.post(
        f"/api/v1/projects/{project['project_id']}/source/anchors",
        headers=session_headers,
        json={
            "chapter_id": second_chapter["chapter_id"],
            "start_offset": start,
            "end_offset": start + len(target),
        },
    )
    assert anchor.status_code == 201
    assert anchor.json()["excerpt"] == target
    assert anchor.json()["excerpt_sha256"] == hashlib.sha256(target.encode()).hexdigest()

    drafted_beats = client.post(
        f"/api/v1/projects/{project['project_id']}/source/chapters/"
        f"{second_chapter['chapter_id']}/story-beats/draft",
        headers=session_headers,
    )
    assert drafted_beats.status_code == 201
    beat_body = drafted_beats.json()
    assert beat_body["beat_set_version"] == 1
    assert [beat["resolution_status"] for beat in beat_body["beats"]] == [
        "unresolved",
        "unresolved",
    ]
    assert beat_body["beats"][1]["source_excerpt"] == "林夏推开门。"
    assert (
        beat_body["beats"][1]["excerpt_sha256"]
        == hashlib.sha256("林夏推开门。".encode()).hexdigest()
    )

    listed_beats = client.get(
        f"/api/v1/projects/{project['project_id']}/source/chapters/"
        f"{second_chapter['chapter_id']}/story-beats"
    )
    assert listed_beats.status_code == 200
    assert listed_beats.json()["beats"] == beat_body["beats"]


def test_gb18030_requires_confirmation_and_decodes_losslessly(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = create_project(client, session_headers)
    source_text = "第一章 测试\n这是中文正文。\n"
    content = source_text.encode("gb18030")

    preflight = client.post(
        f"/api/v1/projects/{project['project_id']}/source/preflight",
        headers=session_headers,
        files={"file": ("gb.txt", content, "text/plain")},
    )
    assert preflight.status_code == 201
    body = preflight.json()
    assert any(item["encoding"] == "gb18030" for item in body["candidates"])
    assert body["requires_confirmation"] is True

    bad_confirm = client.post(
        f"/api/v1/projects/{project['project_id']}/source/confirm",
        headers=session_headers,
        json={"preflight_id": body["preflight_id"], "encoding": "utf-8"},
    )
    assert bad_confirm.status_code == 422

    confirmed = client.post(
        f"/api/v1/projects/{project['project_id']}/source/confirm",
        headers=session_headers,
        json={"preflight_id": body["preflight_id"], "encoding": "gb18030"},
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["chapters"][0]["title"] == "第一章 测试"


def test_manual_chapter_boundaries_create_new_immutable_set(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = create_project(client, session_headers)
    text = "甲段。\n乙段。\n"
    preflight = client.post(
        f"/api/v1/projects/{project['project_id']}/source/preflight",
        headers=session_headers,
        files={"file": ("plain.txt", text.encode(), "text/plain")},
    ).json()
    confirmed = client.post(
        f"/api/v1/projects/{project['project_id']}/source/confirm",
        headers=session_headers,
        json={"preflight_id": preflight["preflight_id"], "encoding": "utf-8"},
    ).json()

    split_at = text.index("乙")
    replaced = client.put(
        f"/api/v1/projects/{project['project_id']}/source/chapters",
        headers=session_headers,
        json={
            "source_file_id": confirmed["source_file_id"],
            "chapters": [
                {"title": "甲", "start_offset": 0, "end_offset": split_at},
                {"title": "乙", "start_offset": split_at, "end_offset": len(text)},
            ],
        },
    )
    assert replaced.status_code == 200
    assert replaced.json()["chapter_set_version"] == 2
    assert replaced.json()["chapter_set_id"] != confirmed["chapter_set_id"]

    current = client.get(f"/api/v1/projects/{project['project_id']}/source/chapters").json()
    assert current["chapter_set_version"] == 2
    assert [chapter["title"] for chapter in current["chapters"]] == ["甲", "乙"]

    old_chapter = confirmed["chapters"][0]
    stale_text = client.get(
        f"/api/v1/projects/{project['project_id']}/source/chapters/{old_chapter['chapter_id']}/text"
    )
    assert stale_text.status_code == 404


def test_chapter_boundaries_must_cover_source_without_gaps(
    client: TestClient, session_headers: dict[str, str]
) -> None:
    project = create_project(client, session_headers)
    text = "完整正文"
    preflight = client.post(
        f"/api/v1/projects/{project['project_id']}/source/preflight",
        headers=session_headers,
        files={"file": ("plain.txt", text.encode(), "text/plain")},
    ).json()
    confirmed = client.post(
        f"/api/v1/projects/{project['project_id']}/source/confirm",
        headers=session_headers,
        json={"preflight_id": preflight["preflight_id"], "encoding": "utf-8"},
    ).json()

    response = client.put(
        f"/api/v1/projects/{project['project_id']}/source/chapters",
        headers=session_headers,
        json={
            "source_file_id": confirmed["source_file_id"],
            "chapters": [{"title": "缺失", "start_offset": 1, "end_offset": len(text)}],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CHAPTER_BOUNDARIES"
