from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts import workflow_context as cli
from scripts.produce_authored_manga import read, save
from tests.workflows.test_workflow_context_api import DomainReader, workflow

__all__ = ["workflow"]


def test_cli_registers_full_scope_exports_bounded_goal_and_never_generates(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, _, _ = workflow
    manifest = tmp_path / "manifest.json"
    save(
        manifest,
        {"project_id": base.split("/")[4], "chapters": [{"page_budget": 100, "pages": {}}]},
    )
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/source/chapters"):
            return httpx.Response(200, json={"chapters": []})
        assert "/workflows" in path
        response = client.request(
            request.method,
            path,
            json=json.loads(request.content) if request.content else None,
            headers=session_headers,
        )
        return httpx.Response(response.status_code, json=response.json())

    monkeypatch.setattr(
        cli,
        "live_client",
        lambda _: httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)),
    )
    session = tmp_path / "unused-session"
    result = cli.execute(manifest, "register", session, "完成全部 100 页并逐页验收", None)
    assert result["counts"]["total"] == 100
    assert read(manifest)["workflow_context"]["run_id"] == result["runtime"]["run_id"]
    assert cli.execute(manifest, "summary", session, None, None)["counts"]["generated"] == 0
    handoff = cli.execute(manifest, "handoff", session, None, None)
    bundle_file = Path(handoff["handoff_file"])
    bundle = read(bundle_file)
    assert bundle["objective"] == "完成全部 100 页并逐页验收"
    assert bundle["counts"]["total"] == 100
    assert not bundle["host_context_replaced"]
    assert handoff["token_upper_bound"] <= 8000
    assert bundle_file.stat().st_mode & 0o777 == 0o600
    assert not any(path.endswith("/generate") for path in calls)
    with pytest.raises(ValueError, match="already has a workflow"):
        cli.execute(manifest, "register", session, "不能替换原始范围", None)
