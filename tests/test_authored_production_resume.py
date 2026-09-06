import hashlib
from pathlib import Path

import httpx
import pytest

from scripts import produce_authored_manga as production


@pytest.mark.parametrize("status", ["ready", "needs_review"])
def test_resume_never_reposts_existing_attempt_or_erases_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    data = b"downloaded provider image bytes for transport test"
    digest = hashlib.sha256(data).hexdigest()
    manifest = tmp_path / "production.json"
    entry = {
        "global_page_number": 1,
        "generation_id": "attempt",
        "plan_sha256": "a" * 64,
        "status": "draft",
        "visual_review": "accepted",
        "image_sha256": digest,
    }
    production.save(
        manifest, {"project_id": "project", "chapters": [{"page_budget": 2, "pages": {"1": entry}}]}
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.method == "GET"
        if request.url.path == "/api/v1/vault":
            return httpx.Response(200, json={"unlocked": True})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=data)
        return httpx.Response(
            200,
            json={
                "status": status,
                "plan_sha256": "a" * 64,
                "cost_ceiling_anlas": 0,
                "external_requests_started": 2,
                "image_sha256": digest,
                "error_code": None,
            },
        )

    monkeypatch.setattr(
        production,
        "live_client",
        lambda _: httpx.Client(base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)),
    )
    if status == "needs_review":
        with pytest.raises(RuntimeError, match="no retry"):
            production.generate(manifest, 1, 1, 2, tmp_path / "unused")
        assert not (tmp_path / "originals").exists()
    else:
        production.generate(manifest, 1, 1, 1, tmp_path / "unused")
        restored = production.read(manifest)["chapters"][0]["pages"]["1"]
        assert restored["visual_review"] == "accepted"
        assert (tmp_path / "originals/001.png").read_bytes() == data
    assert all(method == "GET" for method, _ in calls)
