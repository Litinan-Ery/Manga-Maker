from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.platform.file_store.atomic import manifest_lock
from scripts import produce_authored_manga as production
from scripts.production_context import RemoteContextBatch, manifest_units
from tests.workflows.test_workflow_context_api import DomainReader, workflow

__all__ = ["workflow"]


def test_manifest_lock_rejects_a_second_process_and_releases_on_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    code = (
        "from pathlib import Path\n"
        "from backend.app.platform.file_store.atomic import manifest_lock\n"
        "import sys\nwith manifest_lock(Path(sys.argv[1])): pass"
    )
    with manifest_lock(manifest):
        child = subprocess.run([sys.executable, "-c", code, str(manifest)], capture_output=True)
        assert child.returncode != 0
        assert b"another producer owns this manifest" in child.stderr
    child = subprocess.run([sys.executable, "-c", code, str(manifest)], capture_output=True)
    assert child.returncode == 0


def test_batch_limit_stops_before_reading_manifest_or_opening_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(production, "live_client", lambda _: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="at most 5"):
        production.generate(tmp_path / "missing", 1, 1, 6, tmp_path / "unused")


def test_complete_scope_includes_unprepared_pages_and_atomic_current(tmp_path: Path) -> None:
    state = {
        "project_id": str(uuid4()),
        "chapters": [{"page_budget": 40, "pages": {}}, {"page_budget": 60, "pages": {}}],
    }
    manifest = tmp_path / "manifest.json"
    production.save(manifest, state)
    assert len(manifest_units(state)) == 100
    root = tmp_path / ".context" / "manifest.json"
    current = production.read(root / "CURRENT.json")
    snapshot = production.read(root / current["checkpoint"])
    assert (
        hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        == current["sha256"]
    )
    summary = production.read(root / "RECOVERY.json")
    assert summary["total_pages"] == 100
    assert summary["generated_reported"] == 0
    assert summary["requires_reconciliation"]
    assert len(json.dumps(summary).encode()) < 6000


def test_production_stops_before_generation_when_context_requires_handoff() -> None:
    paths = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/leases"):
            return httpx.Response(
                200,
                json={"revision": 1, "writer_id": "test", "fencing_token": 1, "expires_at": 999},
            )
        if request.url.path.endswith("/context"):
            return httpx.Response(
                200, json={"runtime": {"state": "handoff_required"}, "can_resume": True}
            )
        return httpx.Response(200, json={"released": True})

    with (
        httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)) as client,
        pytest.raises(RuntimeError, match="no generation started"),
        RemoteContextBatch(
            client, {"project_id": "p", "workflow_context": {"run_id": "r"}}
        ) as context,
    ):
        context.preflight()
    assert paths[-1].endswith("/leases/release")
    assert all(not path.endswith("/generate") for path in paths)


def test_100_page_runner_api_bridge_resumes_lost_response_without_duplicate_generation(
    client: TestClient,
    session_headers: dict[str, str],
    workflow: tuple[str, dict[str, Any], DomainReader],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, _, reader = workflow
    project, run = base.split("/")[4], base.split("/")[6]
    generations = {str(uuid4()): number for number in range(1, 101)}
    pages = {
        str(number): {"global_page_number": number, "generation_id": gen, "plan_sha256": "a" * 64}
        for gen, number in generations.items()
    }
    manifest = tmp_path / "manifest.json"
    production.save(
        manifest,
        {
            "project_id": project,
            "workflow_context": {"run_id": run},
            "chapters": [{"page_budget": 100, "pages": pages}],
        },
    )
    generated: set[int] = set()
    calls: list[int] = []
    lost_response = False

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal lost_response
        path = request.url.path
        if "/workflows/" in path:
            response = client.request(
                request.method,
                path,
                json=json.loads(request.content) if request.content else None,
                headers=session_headers,
            )
            assert response.status_code < 400, response.text
            return httpx.Response(response.status_code, json=response.json())
        if path == "/api/v1/vault":
            return httpx.Response(200, json={"unlocked": True})
        number = generations[path.split("/")[6]]
        content = f"provider transport fixture for page {number}".encode()
        if path.endswith("/generate"):
            assert number not in generated
            generated.add(number)
            calls.append(number)
            reader.ready = max(generated)
            if number == 26 and not lost_response:
                lost_response = True
                raise httpx.ReadTimeout("simulated response lost after server completed")
        if path.endswith("/content"):
            return httpx.Response(200, content=content)
        return httpx.Response(
            200,
            json={
                "status": "ready" if number in generated else "draft",
                "plan_sha256": "a" * 64,
                "cost_ceiling_anlas": 0,
                "external_requests_started": int(number in generated),
                "image_sha256": hashlib.sha256(content).hexdigest(),
                "error_code": None,
            },
        )

    monkeypatch.setattr(
        production,
        "live_client",
        lambda _: httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)),
    )
    for start in range(1, 101, 5):
        if start == 26:
            with pytest.raises(httpx.ReadTimeout):
                production.generate(manifest, 1, start, start + 4, tmp_path / "unused")
        production.generate(manifest, 1, start, start + 4, tmp_path / "unused")
    assert calls == list(range(1, 101))
    assert len(list((tmp_path / "originals").glob("*.png"))) == 100
    summary = client.get(base + "/context", headers=session_headers).json()
    assert summary["counts"]["total"] == summary["counts"]["generated"] == 100
    assert summary["counts"]["reviewed"] == 0
    assert not summary["export_approved"]
    assert summary["runtime"]["writer_id"] is None
    saved = client.app.state.container.workflow_context.store.checkpoint(project, run)
    assert len(saved.snapshot.units) == 100
    assert saved.snapshot.objective == "完成 100 页，保持三个时代完整"
