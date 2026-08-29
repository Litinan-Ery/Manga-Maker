from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image, ImageDraw
from pydantic import SecretStr

from backend.app.acceptance.sandkings_v5 import (
    PAGE_BEATS,
    PAGE_COUNT,
    PROJECT_TITLE,
    TEXT_MODEL_BASE_URL,
    TEXT_MODEL_NAME,
    SandkingsV5AcceptanceTextModel,
    extract_sandkings_source,
)
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.novelai.contracts import CONTRACT_SHA256, MAPPING_VERSION
from backend.app.vault import CredentialVault, VaultAuthenticationError

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(
    "/Users/hna/Downloads/书/真名实姓——英美最佳中篇科幻小说选_精华_20260809_155952.md"
)
DEFAULT_APP_DATA = Path.home() / "Library" / "Application Support" / "Manga Maker"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "workspace" / "acceptance" / "sandkings-v5"
DIMENSION_CAPABILITIES = json.loads(
    (REPO_ROOT / "contracts" / "fixtures" / "v0.3" / "dimension-capabilities.json").read_text(
        encoding="utf-8"
    )
)
UNLOCK_TIMEOUT_SECONDS = 10 * 60
MAX_CALLS_PER_TARGET = 3


class AcceptanceFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the real NovelAI V5 Sandkings acceptance production."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--app-data", type=Path, default=DEFAULT_APP_DATA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--novelai-profile", default="novelai")
    parser.add_argument("--resume-project")
    parser.add_argument(
        "--reroll-pages",
        default="",
        help="Comma-separated one-based page numbers to reroll before exporting.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement that this run may call NovelAI.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.confirm:
        raise AcceptanceFailure("Refusing to call NovelAI without --confirm")
    reroll_pages = _parse_pages(args.reroll_pages)
    settings = Settings(
        app_data_dir=args.app_data.expanduser().resolve(),
        environment="acceptance",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        state = _app_state(client)
        headers = {
            "X-Manga-Maker-Session": state.local_session.token,
            "X-CSRF-Token": state.local_session.csrf_token,
        }
        profile_id = _unlock_and_select_profile(
            client,
            headers,
            requested_profile=args.novelai_profile,
        )
        if args.resume_project:
            project_id = args.resume_project
            chapter_id = _single_chapter_id(client, project_id)
            connection = _configure_and_test_v5(
                client,
                headers,
                project_id,
                profile_id,
            )
            _refine_prompts_for_visual_failures(
                client,
                headers,
                project_id,
                chapter_id,
                reroll_pages,
            )
            _reroll_pages(
                client,
                headers,
                project_id,
                chapter_id,
                reroll_pages,
            )
            source_manifest = (
                extract_sandkings_source(args.source).manifest()
                if args.source.is_file()
                else _source_manifest_from_latest(args.output_root, project_id)
            )
        else:
            if reroll_pages:
                raise AcceptanceFailure("--reroll-pages requires --resume-project")
            extracted = extract_sandkings_source(args.source)
            project_id = _create_project(client, headers)
            connection = _configure_and_test_v5(
                client,
                headers,
                project_id,
                profile_id,
            )
            chapter_id = _prepare_story_pipeline(
                client,
                headers,
                project_id,
                extracted.text,
            )
            _generate_initial_comic(client, headers, project_id, chapter_id)
            source_manifest = extracted.manifest()

        jobs = cast(
            list[dict[str, Any]],
            _ok(
                client.get(f"/api/v1/projects/{project_id}/generation/jobs"),
                200,
                "list complete generation history",
            ).json(),
        )

        output_dir = _new_output_directory(args.output_root, len(reroll_pages))
        summary = _export_and_audit(
            client,
            headers,
            project_id,
            chapter_id,
            output_dir,
            source_manifest=source_manifest,
            connection=connection,
            jobs=jobs,
            rerolled_pages=reroll_pages,
        )
        _write_latest(args.output_root, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _parse_pages(raw: str) -> list[int]:
    if not raw.strip():
        return []
    try:
        pages = sorted({int(item.strip()) for item in raw.split(",")})
    except ValueError as exc:
        raise AcceptanceFailure("reroll pages must be comma-separated integers") from exc
    if any(page < 1 or page > PAGE_COUNT for page in pages):
        raise AcceptanceFailure(f"reroll pages must be between 1 and {PAGE_COUNT}")
    return pages


def _unlock_and_select_profile(
    client: TestClient,
    headers: dict[str, str],
    *,
    requested_profile: str,
) -> str:
    status = _ok(client.get("/api/v1/vault"), 200, "read vault status").json()
    if not status["configured"]:
        raise AcceptanceFailure("Manga Maker credential vault is not configured")
    if status["unlocked"]:
        unlocked = status
    else:
        _unlock_vault_in_browser(_app_state(client).vault)
        unlocked = _ok(client.get("/api/v1/vault"), 200, "read unlocked vault").json()
        if not unlocked["unlocked"]:
            raise AcceptanceFailure("credential vault remained locked after browser unlock")
    novelai_profiles = [
        profile for profile in unlocked["profiles"] if profile["provider"] == "novelai"
    ]
    selected = next(
        (profile for profile in novelai_profiles if profile["profile_id"] == requested_profile),
        None,
    )
    if selected is None and len(novelai_profiles) == 1:
        selected = novelai_profiles[0]
    if selected is None:
        available = ", ".join(profile["profile_id"] for profile in novelai_profiles) or "none"
        raise AcceptanceFailure(
            f"NovelAI credential profile {requested_profile!r} not found; available: {available}"
        )
    print(f"Using local NovelAI profile: {selected['profile_id']} ({selected['label']})")
    return str(selected["profile_id"])


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _unlock_page(*, action_path: str, error: str | None = None) -> str:
    error_html = (
        '<p class="error" role="alert">主密码错误或凭证库已损坏，请重试。</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Manga Maker · 解锁本地凭证库</title>
  <style>
    :root {{ color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }}
    body {{ min-height: 100vh; margin: 0; display: grid; place-items: center;
      background: #111; color: #f4f0e8; }}
    main {{ width: min(480px, calc(100vw - 48px)); padding: 36px; border: 1px solid #49443b;
      border-radius: 18px; background: #1d1b18; box-shadow: 0 24px 80px #0008; }}
    h1 {{ margin: 0 0 12px; font-size: 28px; }}
    p {{ color: #c9c2b5; line-height: 1.65; }}
    label {{ display: grid; gap: 10px; margin-top: 24px; font-weight: 650; }}
    input {{ box-sizing: border-box; width: 100%; padding: 13px 14px; border: 1px solid #665f52;
      border-radius: 10px; background: #0e0d0c; color: white; font: inherit; }}
    button {{ width: 100%; margin-top: 16px; padding: 13px; border: 0; border-radius: 10px;
      background: #d67535; color: #140c06; font: inherit; font-weight: 750; cursor: pointer; }}
    .error {{ color: #ff9d8a; }}
    small {{ display: block; margin-top: 18px; color: #928b80; line-height: 1.55; }}
  </style>
</head>
<body>
  <main>
    <h1>解锁 Manga Maker</h1>
    <p>输入本机凭证库主密码后，沙王的 12 页 NovelAI V5 验收生成会自动继续。</p>
    {error_html}
    <form method="post" action="{action_path}" autocomplete="off">
      <label>主密码
        <input name="master_password" type="password" minlength="10" maxlength="1024"
          autocomplete="current-password" autofocus required>
      </label>
      <button type="submit">解锁并继续生成</button>
    </form>
    <small>此页面只监听本机回环地址，使用一次性随机路径，不加载外部资源。密码不会写入项目、日志或命令行。</small>
  </main>
</body>
</html>"""


def _unlock_success_page() -> str:
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Manga Maker · 已解锁</title><style>
:root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
body{min-height:100vh;margin:0;display:grid;place-items:center;background:#111;color:#f4f0e8}
main{max-width:520px;padding:40px;text-align:center}h1{color:#ef9a60}p{color:#c9c2b5;line-height:1.7}
</style></head><body><main><h1>凭证库已解锁</h1>
<p>沙王的 NovelAI V5 生成已经继续。此页现在可以关闭。</p>
</main></body></html>"""


def _build_unlock_portal(
    vault: CredentialVault,
    *,
    one_time_token: str,
    unlocked_event: threading.Event,
) -> FastAPI:
    portal = FastAPI(
        title="Manga Maker local vault unlock",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    action_path = f"/unlock/{one_time_token}"

    @portal.get(action_path, response_class=HTMLResponse)
    def unlock_form() -> HTMLResponse:
        return HTMLResponse(
            _unlock_page(action_path=action_path),
            headers=_unlock_security_headers(),
        )

    @portal.post(action_path, response_class=HTMLResponse)
    def unlock_submit(
        master_password: Annotated[SecretStr, Form(min_length=10, max_length=1024)],
    ) -> HTMLResponse:
        try:
            vault.unlock(master_password.get_secret_value())
        except VaultAuthenticationError:
            return HTMLResponse(
                _unlock_page(action_path=action_path, error="authentication-failed"),
                status_code=401,
                headers=_unlock_security_headers(),
            )
        unlocked_event.set()
        return HTMLResponse(_unlock_success_page(), headers=_unlock_security_headers())

    return portal


def _unlock_security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _unlock_vault_in_browser(vault: CredentialVault) -> None:
    one_time_token = secrets.token_urlsafe(32)
    unlocked_event = threading.Event()
    port = _available_loopback_port()
    portal = _build_unlock_portal(
        vault,
        one_time_token=one_time_token,
        unlocked_event=unlocked_event,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            portal,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="sandkings-vault-unlock", daemon=True)
    thread.start()
    deadline = time.monotonic() + UNLOCK_TIMEOUT_SECONDS
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise AcceptanceFailure("failed to start the loopback vault unlock page")
    url = f"http://127.0.0.1:{port}/unlock/{one_time_token}"
    print(f"Unlock Manga Maker in your browser: {url}", flush=True)
    webbrowser.open(url)
    try:
        remaining = max(0.0, deadline - time.monotonic())
        if not unlocked_event.wait(timeout=remaining):
            raise AcceptanceFailure("timed out waiting for the local vault to be unlocked")
        time.sleep(0.2)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _create_project(client: TestClient, headers: dict[str, str]) -> str:
    response = _ok(
        client.post("/api/v1/projects", headers=headers, json={"title": PROJECT_TITLE}),
        201,
        "create acceptance project",
    )
    project_id = str(response.json()["project_id"])
    print(f"Created acceptance project {project_id}")
    return project_id


def _configure_and_test_v5(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    profile_id: str,
) -> dict[str, Any]:
    _ok(
        client.put(
            f"/api/v1/projects/{project_id}/novelai/config",
            headers=headers,
            json={
                "provider_model_id": "nai-diffusion-5-full",
                "credential_profile_id": profile_id,
                "timeout_seconds": 180,
            },
        ),
        200,
        "save V5 configuration",
    )
    result = cast(
        dict[str, Any],
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/novelai/connection-test",
                headers=headers,
            ),
            200,
            "verify NovelAI V5 connection and allowance",
        ).json(),
    )
    if result["provider_model_id"] != "nai-diffusion-5-full":
        raise AcceptanceFailure("connection test returned a model other than V5 Full")
    if result["generated_images"] != 0:
        raise AcceptanceFailure("connection test unexpectedly generated an image")
    if result["zero_anlas_ready"] is not True:
        raise AcceptanceFailure(
            "NovelAI V5 Opus allowance is not available; paid generation was not authorized"
        )
    usage = result["subscription"].get("usage_percent")
    print(f"NovelAI V5 connection verified; reported usage allowance: {usage}%")
    return result


def _prepare_story_pipeline(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    source_text: str,
) -> str:
    preflight = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/source/preflight",
            headers=headers,
            files={
                "file": (
                    "sandkings-extracted.md",
                    source_text.encode("utf-8"),
                    "text/markdown",
                )
            },
        ),
        201,
        "preflight extracted Sandkings source",
    ).json()
    source = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/source/confirm",
            headers=headers,
            json={"preflight_id": preflight["preflight_id"], "encoding": "utf-8"},
        ),
        201,
        "import extracted Sandkings source",
    ).json()
    chapters = source["chapters"]
    if len(chapters) != 1:
        raise AcceptanceFailure("Sandkings extraction must import as exactly one chapter")
    chapter_id = str(chapters[0]["chapter_id"])
    _ok(
        client.post(
            f"/api/v1/projects/{project_id}/source/chapters/{chapter_id}/story-beats/draft",
            headers=headers,
        ),
        201,
        "draft source-anchored story beats",
    )

    temporary_profile = f"sandkings-local-{project_id}"[:64]
    _ok(
        client.put(
            f"/api/v1/vault/profiles/{temporary_profile}",
            headers=headers,
            json={
                "provider": "openai-compatible",
                "label": "沙王本地确定性编剧",
                "secret": secrets.token_urlsafe(32),
            },
        ),
        200,
        "create temporary local authoring profile",
    )
    try:
        _ok(
            client.put(
                f"/api/v1/projects/{project_id}/adaptation/text-model",
                headers=headers,
                json={
                    "remark_name": "沙王 V5 确定性验收编剧",
                    "url": TEXT_MODEL_BASE_URL,
                    "request_model": TEXT_MODEL_NAME,
                    "credential_profile_id": temporary_profile,
                    "timeout_seconds": 60,
                    "temperature": 0,
                },
            ),
            200,
            "configure deterministic local authoring",
        )
        _app_state(client).adaptation.provider_factory = lambda configuration, secret_reader: (
            SandkingsV5AcceptanceTextModel(
                configuration,
                secret_reader,
            )
        )
        storyboard = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/adaptation/storyboards/generate",
                headers=headers,
                json={"chapter_id": chapter_id, "page_budget": PAGE_COUNT},
            ),
            201,
            "generate 12-page storyboard",
        ).json()
        if len(storyboard["document"]["pages"]) != PAGE_COUNT:
            raise AcceptanceFailure("storyboard did not contain exactly 12 pages")
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/adaptation/storyboards/"
                f"{storyboard['storyboard_version_id']}/approve",
                headers=headers,
            ),
            200,
            "approve Sandkings storyboard",
        )
        bibles = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/bibles/generate",
                headers=headers,
                json={
                    "storyboard_version_id": storyboard["storyboard_version_id"],
                    "confirmed_data_send": True,
                },
            ),
            201,
            "generate character and style bibles",
        ).json()
        for kind, key in (("character", "character_bible"), ("style", "style_bible")):
            if bibles[key]["approval_issues"]:
                raise AcceptanceFailure(f"generated {kind} bible is not approvable")
            _ok(
                client.post(
                    f"/api/v1/projects/{project_id}/bibles/{kind}/"
                    f"{bibles[key]['version_id']}/approve",
                    headers=headers,
                ),
                200,
                f"approve {kind} bible",
            )
        _ensure_approved_layout(client, headers, project_id, chapter_id)
        tags = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/prompting/character-tags/generate",
                headers=headers,
                json={"chapter_id": chapter_id, "confirmed_data_send": True},
            ),
            201,
            "generate fixed character tags",
        ).json()
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/prompting/character-tags/"
                f"{tags['version_id']}/approve",
                headers=headers,
            ),
            200,
            "approve fixed character tags",
        )
        prompts = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/prompting/prompt-bundles/generate",
                headers=headers,
                json={"chapter_id": chapter_id, "confirmed_data_send": True},
            ),
            201,
            "generate V5 prompt bundle",
        ).json()
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
                f"{prompts['version_id']}/approve",
                headers={
                    **headers,
                    "Idempotency-Key": f"sandkings-prompt-{prompts['version_id']}",
                },
                json={"snapshot_sha256": prompts["snapshot_sha256"]},
            ),
            200,
            "approve V5 prompt bundle",
        )
    finally:
        client.delete(
            f"/api/v1/vault/profiles/{temporary_profile}",
            headers=headers,
        )
    return chapter_id


def _ensure_approved_layout(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
) -> None:
    storyboard = _ok(
        client.get(
            f"/api/v1/projects/{project_id}/adaptation/storyboards/current",
            params={"chapter_id": chapter_id},
        ),
        200,
        "load approved storyboard for layouts",
    ).json()
    bibles = _ok(
        client.get(
            f"/api/v1/projects/{project_id}/bibles",
            params={"chapter_id": chapter_id},
        ),
        200,
        "load character bible for layouts",
    ).json()
    character_ids = {
        alias.casefold(): character["character_id"]
        for character in bibles["character_bible"]["document"]["characters"]
        for alias in (character["name"], *character["aliases"])
    }
    for page in storyboard["document"]["pages"]:
        panel = page["panels"][0]
        layout_id = str(uuid4())
        root_frame_id = str(uuid4())
        positions = [
            {
                "character_id": character_ids[name.casefold()],
                "center": {"x": (index + 1) / (len(panel["characters"]) + 1), "y": 0.56},
                "prominence": "primary" if index == 0 else "secondary",
            }
            for index, name in enumerate(panel["characters"])
        ]
        draft = {
            "schema_version": "1.0",
            "page_layout_draft_id": layout_id,
            "version": 1,
            "page_id": page["page_id"],
            "page_profile": "print_portrait_2_3",
            "canvas": {"width": 2048, "height": 3072},
            "reading_direction": "ltr_ttb",
            "frames": [
                {
                    "frame_id": root_frame_id,
                    "parent_frame_id": None,
                    "panel_id": None,
                    "order": None,
                    "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "aspect_ratio": 2 / 3,
                    "shot_scale": "establishing",
                    "focal_point": {"x": 0.5, "y": 0.5},
                    "character_positions": [],
                    "text_safe_zones": [],
                    "crop_safe_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                },
                {
                    "frame_id": str(uuid4()),
                    "parent_frame_id": root_frame_id,
                    "panel_id": panel["panel_id"],
                    "order": 1,
                    "rect": {"x": 0.04, "y": 0.04, "width": 0.92, "height": 0.92},
                    "aspect_ratio": 2 / 3,
                    "shot_scale": "wide",
                    "focal_point": {"x": 0.5, "y": 0.5},
                    "character_positions": positions,
                    "text_safe_zones": [],
                    "crop_safe_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                },
            ],
            "content_sha256": "0" * 64,
            "approved_content_sha256": None,
        }
        created = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/layouts/drafts",
                headers={
                    **headers,
                    "Idempotency-Key": f"sandkings-layout-create-{page['page_id']}",
                },
                json={
                    "chapter_id": chapter_id,
                    "storyboard_version_id": storyboard["storyboard_version_id"],
                    "draft": draft,
                },
            ),
            201,
            f"create layout for page {page['page_number']}",
        ).json()
        validation_body = {
            "expected_revision": created["revision"],
            "layout_content_sha256": created["layout"]["content_sha256"],
            "storyboard_version_id": storyboard["storyboard_version_id"],
            "dimension_capabilities": DIMENSION_CAPABILITIES,
            "target_pixels": 1_572_864,
            "max_crop_safe_risk": 1.0,
        }
        validated = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/layouts/"
                f"{created['page_layout_draft_version_id']}/validate",
                headers=headers,
                json=validation_body,
            ),
            200,
            f"validate layout for page {page['page_number']}",
        ).json()
        if not validated["valid"]:
            raise AcceptanceFailure(f"layout validation failed for page {page['page_number']}")
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/layouts/"
                f"{created['page_layout_draft_version_id']}/approve",
                headers={
                    **headers,
                    "Idempotency-Key": f"sandkings-layout-approve-{page['page_id']}",
                },
                json={
                    **validation_body,
                    "dimension_selections": validated["dimension_outcomes"],
                },
            ),
            200,
            f"approve layout for page {page['page_number']}",
        )


def _generate_initial_comic(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
) -> dict[str, Any]:
    estimate = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/generation/estimate",
            headers=headers,
            json={"chapter_id": chapter_id, "per_panel_cost_ceiling_anlas": 0},
        ),
        200,
        "estimate zero-Anlas V5 production",
    ).json()
    if (
        estimate["provider_model_id"] != "nai-diffusion-5-full"
        or estimate["panel_count"] != PAGE_COUNT
        or estimate["billing_mode"] != "opus_zero_anlas"
        or estimate["estimated_cost_upper_anlas"] != 0
    ):
        raise AcceptanceFailure("generation estimate is outside the frozen V5 acceptance scope")
    created = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/generation/jobs",
            headers={
                **headers,
                "Idempotency-Key": f"sandkings-v5-{estimate['plan_fingerprint']}",
            },
            json={
                "chapter_id": chapter_id,
                "per_panel_cost_ceiling_anlas": 0,
                "plan_fingerprint": estimate["plan_fingerprint"],
                "max_calls": estimate["panel_count"] * MAX_CALLS_PER_TARGET,
                "max_cost_anlas": 0,
                "confirmed": True,
            },
        ),
        201,
        "create bounded V5 generation job",
    ).json()
    started = _transition(client, headers, project_id, created, "start")
    print(f"Generating {PAGE_COUNT} real NovelAI V5 images serially...")
    state = _app_state(client)
    asyncio.run(state.generation_executor.run_until_blocked(started["job_id"]))
    completed = cast(
        dict[str, Any],
        state.generation_queue.get_job(project_id, started["job_id"]),
    )
    _require_completed_job(completed)
    pages = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/pages/draft",
            headers=headers,
            json={"chapter_id": chapter_id},
        ),
        201,
        "compose 12 comic pages",
    ).json()
    if len(pages) != PAGE_COUNT:
        raise AcceptanceFailure("page compositor did not produce exactly 12 pages")
    _enable_color_pages(client, headers, project_id, pages)
    return completed


def _enable_color_pages(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    pages: list[dict[str, Any]],
) -> None:
    for page in pages:
        document = copy.deepcopy(page["document"])
        document["schema_version"] = "2.0"
        document["color_mode"] = "color"
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/pages/{page['page_id']}/versions",
                headers=headers,
                json={
                    "expected_revision": page["page_revision"],
                    "document": document,
                },
            ),
            201,
            f"preserve limited color on page {page['page_number']}",
        )


def _reroll_pages(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
    page_numbers: list[int],
) -> list[dict[str, Any]]:
    if not page_numbers:
        return []
    jobs: list[dict[str, Any]] = []
    for page_number in page_numbers:
        pages = _ok(
            client.get(
                f"/api/v1/projects/{project_id}/pages",
                params={"chapter_id": chapter_id},
            ),
            200,
            "list current pages for reroll",
        ).json()
        page = next((item for item in pages if item["page_number"] == page_number), None)
        if page is None or len(page["document"]["panels"]) != 1:
            raise AcceptanceFailure(f"page {page_number} is missing or not a one-panel page")
        panel = page["document"]["panels"][0]
        estimate = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/generation/revisions/estimate",
                headers=headers,
                json={
                    "operation": "panel_reroll",
                    "page_id": page["page_id"],
                    "panel_id": panel["panel_id"],
                    "per_panel_cost_ceiling_anlas": 0,
                },
            ),
            200,
            f"estimate zero-Anlas reroll for page {page_number}",
        ).json()
        target = estimate["targets"][0]
        created = _ok(
            client.post(
                f"/api/v1/projects/{project_id}/generation/revisions/jobs",
                headers={
                    **headers,
                    "Idempotency-Key": f"sandkings-reroll-{estimate['plan_fingerprint']}",
                },
                json={
                    "operation": "panel_reroll",
                    "page_id": page["page_id"],
                    "panel_id": panel["panel_id"],
                    "mask_asset_id": None,
                    "edit_prompt": None,
                    "inpaint_strength": None,
                    "per_panel_cost_ceiling_anlas": 0,
                    "plan_fingerprint": estimate["plan_fingerprint"],
                    "max_calls": MAX_CALLS_PER_TARGET,
                    "max_cost_anlas": 0,
                    "confirmed": True,
                },
            ),
            201,
            f"create reroll job for page {page_number}",
        ).json()
        if target["parent_asset_version_id"] != panel["asset_version_id"]:
            raise AcceptanceFailure("reroll did not freeze the current parent asset")
        started = _transition(client, headers, project_id, created, "start")
        print(f"Rerolling page {page_number} with real NovelAI V5...")
        state = _app_state(client)
        asyncio.run(state.generation_executor.run_until_blocked(started["job_id"]))
        completed = cast(
            dict[str, Any],
            state.generation_queue.get_job(project_id, started["job_id"]),
        )
        _require_completed_job(completed)
        jobs.append(completed)
    return jobs


def _refine_prompts_for_visual_failures(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
    page_numbers: list[int],
) -> None:
    supported = {8, 12}
    selected = supported.intersection(page_numbers)
    if not selected:
        return
    storyboard = _ok(
        client.get(
            f"/api/v1/projects/{project_id}/adaptation/storyboards/current",
            params={"chapter_id": chapter_id},
        ),
        200,
        "load storyboard for visual-review prompt fixes",
    ).json()
    panel_by_page = {
        int(page["page_number"]): str(page["panels"][0]["panel_id"])
        for page in storyboard["document"]["pages"]
        if len(page["panels"]) == 1
    }
    workflow = _ok(
        client.get(
            f"/api/v1/projects/{project_id}/prompting",
            params={"chapter_id": chapter_id},
        ),
        200,
        "load current prompt bundle for visual-review fixes",
    ).json()
    current = workflow["prompt_bundle"]
    document = current["document"]
    draft = {
        "schema_version": "1.0",
        "storyboard_version_id": document["storyboard_version_id"],
        "character_tag_bundle_version_id": document["character_tag_bundle_version_id"],
        "packages": [_prompt_draft_package(package) for package in document["packages"]],
    }
    packages = {str(package["panel_id"]): package for package in draft["packages"]}
    changed = False
    if 8 in selected:
        package = packages[panel_by_page[8]]
        changed |= _replace_tag(
            package["base_visual_tags"],
            "industrial extermination team inside alien manor",
            "exactly one adult man and exactly one adult woman inside the alien manor",
        )
        changed |= _replace_tag(
            package["base_visual_tags"],
            "red sandking swarm climbing a yellow hazard suit",
            "red sandking swarm erupts from the floor and collapses the bare tunnel wall",
        )
        changed |= _replace_tag(
            package["base_visual_tags"],
            "both humans fully visible with complete heads and bodies",
            "only the two adults are shown from head to boots with no overlap",
        )
        changed |= _replace_tag(
            package["base_visual_tags"],
            "one complete yellow hazard suit worn by the woman",
            "one bareheaded woman wears fitted matte black body armor",
        )
        changed |= _replace_tag(
            package["base_visual_tags"],
            "no background workers or empty clothing",
            "plain tunnel contains no spare clothing or helmets or human shaped props",
        )
        changed |= _append_tags(
            package["base_visual_tags"],
            [
                "exactly two humans in the entire scene",
                "only the two adults are shown from head to boots with no overlap",
                "one bareheaded woman wears fitted matte black body armor",
                "one bareheaded man wears a dark red coat",
                "only the woman carries a flamethrower",
                "plain tunnel contains no spare clothing or helmets or human shaped props",
            ],
        )
        changed |= _append_tags(
            package["negative_tags"],
            [
                "headless person",
                "headless suit",
                "empty clothing",
                "floating body",
                "duplicate worker",
                "background human",
                "hazmat suit",
                "yellow protective clothing",
                "vacant helmet",
                "third figure",
                "human shaped prop",
                "cropped head",
            ],
        )
        relationship = (
            "one bareheaded man in a red coat stands beside one bareheaded woman in black "
            "armor; exactly two complete human silhouettes and no third figure"
        )
        if package["relationship_action"] != relationship:
            package["relationship_action"] = relationship
            changed = True
    if 12 in selected:
        package = packages[panel_by_page[12]]
        changed |= _replace_tag(
            package["base_visual_tags"],
            "circle of small orange four-armed humanoid children",
            "three small orange alien children each with exactly four clearly visible arms",
        )
        changed |= _append_tags(
            package["base_visual_tags"],
            [
                "four hands visible on every orange child",
                "two separate pairs of arms attached to every child",
                "all four arms spread apart and unobstructed",
                "three orange four-armed children carry the captured man together",
                "no crowd",
            ],
        )
        changed |= _append_tags(
            package["negative_tags"],
            [
                "two-armed child",
                "missing arms",
                "hidden arms",
                "fused arms",
                "crowd",
            ],
        )
    if not changed:
        print("Visual-review prompt fixes are already current")
        return
    revised = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
            f"{current['version_id']}/revisions",
            headers=headers,
            json={"document": draft},
        ),
        201,
        "revise prompts from visual review",
    ).json()
    _ok(
        client.post(
            f"/api/v1/projects/{project_id}/prompting/prompt-bundles/"
            f"{revised['version_id']}/approve",
            headers={
                **headers,
                "Idempotency-Key": f"sandkings-visual-fix-{revised['version_id']}",
            },
            json={"snapshot_sha256": revised["snapshot_sha256"]},
        ),
        200,
        "approve visual-review prompt fixes",
    )
    print(f"Approved visual-review prompt fixes for pages {sorted(selected)}")


def _prompt_draft_package(package: dict[str, Any]) -> dict[str, Any]:
    structured = package["structured_package"]["prompt_plan"]
    characters = {
        str(character["character_id"]): character for character in structured["characters"]
    }
    return {
        "prompt_package_id": package["prompt_package_id"],
        "panel_id": package["panel_id"],
        "base_visual_tags": list(package["base_visual_tags"]),
        "character_blocks": [
            {
                "character_id": block["character_id"],
                "tag_set_id": block["tag_set_id"],
                "variable_tags": list(block["variable_tags"]),
                "negative_tags": list(characters[str(block["character_id"])]["negative_tags"]),
                "action": characters[str(block["character_id"])]["action"],
                "order": characters[str(block["character_id"])]["order"],
                "center": characters[str(block["character_id"])]["center"],
            }
            for block in package["character_blocks"]
        ],
        "style_tags": list(package["style_tags"]),
        "negative_tags": list(package["negative_tags"]),
        "relationship_action": structured["base"]["relationship_action"],
        "continuity_tags": list(structured["continuity_tags"]),
    }


def _append_tags(target: list[str], additions: list[str]) -> bool:
    existing = {item.casefold() for item in target}
    changed = False
    for addition in additions:
        if addition.casefold() not in existing:
            target.append(addition)
            existing.add(addition.casefold())
            changed = True
    return changed


def _replace_tag(target: list[str], old: str, new: str) -> bool:
    try:
        index = next(
            index for index, value in enumerate(target) if value.casefold() == old.casefold()
        )
    except StopIteration:
        return _append_tags(target, [new])
    if target[index] == new:
        return False
    target[index] = new
    return True


def _transition(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    job: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _ok(
            client.post(
                f"/api/v1/projects/{project_id}/generation/jobs/{job['job_id']}/{action}",
                headers=headers,
                json={"expected_revision": job["revision"]},
            ),
            200,
            f"{action} generation job",
        ).json(),
    )


def _require_completed_job(job: dict[str, Any]) -> None:
    if job["status"] != "completed":
        errors = [item.get("last_error_code") for item in job["items"]]
        raise AcceptanceFailure(f"NovelAI job {job['job_id']} ended as {job['status']}: {errors}")
    if job["max_cost_anlas"] != 0 or job["allocated_cost_anlas"] != 0:
        raise AcceptanceFailure("zero-Anlas job recorded a nonzero local allocation")


def _export_and_audit(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    chapter_id: str,
    output_dir: Path,
    *,
    source_manifest: dict[str, Any],
    connection: dict[str, Any],
    jobs: list[dict[str, Any]],
    rerolled_pages: list[int],
) -> dict[str, Any]:
    pages = _ok(
        client.get(
            f"/api/v1/projects/{project_id}/pages",
            params={"chapter_id": chapter_id},
        ),
        200,
        "list composed pages",
    ).json()
    if len(pages) != PAGE_COUNT or [page["page_number"] for page in pages] != list(
        range(1, PAGE_COUNT + 1)
    ):
        raise AcceptanceFailure("current page set is incomplete")
    if any(page["document"]["color_mode"] != "color" for page in pages):
        raise AcceptanceFailure("current comic pages do not preserve the V5 limited color art")

    preflight = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/exports/preflight",
            headers=headers,
            json={"chapter_id": chapter_id},
        ),
        200,
        "preflight complete comic export",
    ).json()
    exported = _ok(
        client.post(
            f"/api/v1/projects/{project_id}/exports",
            headers=headers,
            json={
                "chapter_id": chapter_id,
                "page_version_ids": [item["page_version_id"] for item in preflight["pages"]],
                "plan_fingerprint": preflight["plan_fingerprint"],
                "confirmed": True,
            },
        ),
        201,
        "export complete comic",
    ).json()
    if exported["secret_scan"]["matches"] != 0:
        raise AcceptanceFailure("export secret scan reported a match")

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(mode=0o700)
    output_records: list[dict[str, Any]] = []
    page_paths: list[Path] = []
    kind_extensions = {
        "engineering_package": ".manga-maker.zip",
        "pdf": ".pdf",
        "cbz": ".cbz",
    }
    for item in exported["files"]:
        response = _ok(
            client.get(
                f"/api/v1/projects/{project_id}/exports/{exported['export_revision_id']}"
                f"/files/{item['export_file_id']}",
                headers=headers,
            ),
            200,
            f"download {item['kind']} export",
        )
        payload = response.content
        digest = hashlib.sha256(payload).hexdigest()
        if digest != item["sha256"]:
            raise AcceptanceFailure(f"downloaded {item['kind']} hash mismatch")
        if item["kind"] == "png":
            path = pages_dir / f"{int(item['ordinal']):03d}.png"
            page_paths.append(path)
        else:
            path = output_dir / f"sandkings-v5{kind_extensions[item['kind']]}"
        path.write_bytes(payload)
        output_records.append(
            {
                "kind": item["kind"],
                "ordinal": item["ordinal"],
                "path": str(path.resolve()),
                "sha256": digest,
                "bytes": len(payload),
            }
        )

    _validate_export_files(output_records, page_paths)
    contact_sheet = _create_contact_sheet(page_paths, output_dir / "contact-sheet.png")
    assets = _ok(
        client.get(f"/api/v1/projects/{project_id}/generation/assets"),
        200,
        "list generated assets",
    ).json()
    current_asset_ids = {
        panel["asset_version_id"] for page in pages for panel in page["document"]["panels"]
    }
    current_assets = [asset for asset in assets if asset["asset_version_id"] in current_asset_ids]
    if len(current_assets) != PAGE_COUNT:
        raise AcceptanceFailure("current page assets are incomplete")

    payload_audit = _payload_audit(client, current_asset_ids)
    if payload_audit["model_id"] != "nai-diffusion-5-full":
        raise AcceptanceFailure("a current asset was not generated by NovelAI V5 Full")
    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "technical_complete_visual_review_pending",
        "project_id": project_id,
        "chapter_id": chapter_id,
        "output_dir": str(output_dir.resolve()),
        "source": source_manifest,
        "provider": {
            "model_id": "nai-diffusion-5-full",
            "mapping_version": MAPPING_VERSION,
            "contract_sha256": CONTRACT_SHA256,
            "connection_test": connection,
        },
        "jobs": [
            {
                "job_id": job["job_id"],
                "operation_kind": job["operation_kind"],
                "status": job["status"],
                "calls_started": job["calls_started"],
                "verification_calls_started": job["verification_calls_started"],
                "max_calls": job["max_calls"],
                "max_cost_anlas": job["max_cost_anlas"],
                "allocated_cost_anlas": job["allocated_cost_anlas"],
            }
            for job in jobs
        ],
        "rerolled_pages": rerolled_pages,
        "current_pages": [
            {
                "page_number": page["page_number"],
                "page_id": page["page_id"],
                "page_version_id": page["page_version_id"],
                "render_sha256": page["render_sha256"],
            }
            for page in pages
        ],
        "current_assets": [
            {
                "asset_version_id": asset["asset_version_id"],
                "panel_id": asset["panel_id"],
                "image_sha256": asset["image_sha256"],
                "seed": asset["seed"],
                "parent_asset_version_id": asset["parent_asset_version_id"],
            }
            for asset in current_assets
        ],
        "provider_payload_audit": payload_audit,
        "outputs": output_records,
        "contact_sheet": {
            "path": str(contact_sheet.resolve()),
            "sha256": _sha256_path(contact_sheet),
        },
        "technical_checks": {
            "page_count": PAGE_COUNT,
            "all_pages_2048x3072": True,
            "pdf_page_count": PAGE_COUNT,
            "cbz_page_count": PAGE_COUNT,
            "all_current_assets_v5_full": True,
            "all_payloads_v5_defaults": True,
            "unique_current_asset_hashes": len({asset["image_sha256"] for asset in current_assets})
            == PAGE_COUNT,
            "export_secret_matches": 0,
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review_path = output_dir / "visual-review.md"
    review_path.write_text(_review_template(page_paths), encoding="utf-8")
    summary = {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "output_dir": str(output_dir.resolve()),
        "manifest": str(manifest_path.resolve()),
        "contact_sheet": str(contact_sheet.resolve()),
        "visual_review": str(review_path.resolve()),
        "pdf": next(item["path"] for item in output_records if item["kind"] == "pdf"),
        "cbz": next(item["path"] for item in output_records if item["kind"] == "cbz"),
        "source": source_manifest,
    }
    return summary


def _payload_audit(client: TestClient, current_asset_ids: set[str]) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in current_asset_ids)
    with _app_state(client).database.reader() as connection:
        rows = connection.execute(
            f"""
            SELECT av.asset_version_id, pes.payload_json
            FROM asset_versions av
            JOIN generation_specs gs ON gs.spec_id = av.spec_id
            JOIN provider_execution_specs pes ON pes.generation_spec_id = gs.spec_id
            WHERE av.asset_version_id IN ({placeholders})
            ORDER BY av.asset_version_id
            """,
            tuple(sorted(current_asset_ids)),
        ).fetchall()
    payloads = [json.loads(str(row["payload_json"])) for row in rows]
    if len(payloads) != PAGE_COUNT:
        raise AcceptanceFailure("provider payload audit did not cover every current asset")
    for payload in payloads:
        parameters = payload["parameters"]
        if (
            payload["action"] != "generate"
            or payload["model"] != "nai-diffusion-5-full"
            or parameters["steps"] != 23
            or parameters["scale"] != 7.0
            or parameters["params_version"] != 4
            or parameters["tag_hint_qt"] != 1
            or parameters["tag_hint_uc_preset"] != 4
            or "director_reference_images" in parameters
            or "image" in parameters
            or "mask" in parameters
        ):
            raise AcceptanceFailure("a current provider payload is outside V5 defaults")
    return {
        "payload_count": len(payloads),
        "model_id": "nai-diffusion-5-full",
        "steps": 23,
        "scale": 7.0,
        "params_version": 4,
        "tag_hint_qt": 1,
        "tag_hint_uc_preset": 4,
        "contains_reference_or_base_images": False,
    }


def _validate_export_files(
    records: list[dict[str, Any]],
    page_paths: list[Path],
) -> None:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["kind"]] = counts.get(record["kind"], 0) + 1
    if counts != {"engineering_package": 1, "png": PAGE_COUNT, "pdf": 1, "cbz": 1}:
        raise AcceptanceFailure(f"unexpected export file set: {counts}")
    for path in page_paths:
        with Image.open(path) as image:
            if image.size != (2048, 3072):
                raise AcceptanceFailure(f"unexpected page dimensions for {path.name}")
            image.verify()
    pdf_record = next(record for record in records if record["kind"] == "pdf")
    pdf = Path(pdf_record["path"]).read_bytes()
    if not pdf.startswith(b"%PDF-") or pdf.count(b"/Type /Page\n") != PAGE_COUNT:
        raise AcceptanceFailure("PDF export is incomplete")
    cbz_record = next(record for record in records if record["kind"] == "cbz")
    with zipfile.ZipFile(cbz_record["path"]) as archive:
        expected = [
            "ComicInfo.xml",
            *[f"{number:03d}.png" for number in range(1, PAGE_COUNT + 1)],
        ]
        if archive.namelist() != expected:
            raise AcceptanceFailure("CBZ export is incomplete")


def _create_contact_sheet(page_paths: list[Path], target: Path) -> Path:
    columns = 4
    thumb_width = 384
    thumb_height = 576
    label_height = 40
    rows = (len(page_paths) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + label_height)),
        "#161616",
    )
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(page_paths):
        with Image.open(path) as source:
            thumb = source.convert("RGB").resize(
                (thumb_width, thumb_height),
                Image.Resampling.LANCZOS,
            )
        x = (index % columns) * thumb_width
        y = (index // columns) * (thumb_height + label_height)
        sheet.paste(thumb, (x, y))
        draw.text((x + 12, y + thumb_height + 10), f"PAGE {index + 1:02d}", fill="white")
    sheet.save(target, format="PNG", optimize=False, compress_level=6)
    return target


def _review_template(page_paths: list[Path]) -> str:
    lines = [
        "# 沙王 NovelAI V5 视觉审片",
        "",
        "状态: 待 Codex 逐页视觉审片; 技术完整性见 `manifest.json`。",
        "",
        "审片标准: 叙事事件可辨、主角设计连续、无破坏性随机文字、构图不遮挡旁白、",
        "四臂幼体与沙王母题可辨。未通过的页面必须以零 Anlas reroll 生成新版本后重审。",
        "",
    ]
    for index, (beat, page_path) in enumerate(zip(PAGE_BEATS, page_paths, strict=True), start=1):
        lines.extend(
            [
                f"## 第 {index} 页",
                "",
                f"- 目标: {beat.turning_point}",
                f"- 文件: `{page_path.name}`",
                "- 结论: 待审",
                "- 问题:",
                "",
            ]
        )
    return "\n".join(lines)


def _new_output_directory(output_root: Path, reroll_count: int) -> Path:
    output_root = output_root.expanduser().resolve()
    suffix = f"reroll-{reroll_count}" if reroll_count else "initial"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return output_root / f"{stamp}-{suffix}"


def _write_latest(output_root: Path, summary: dict[str, Any]) -> None:
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (output_root / "latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _source_manifest_from_latest(output_root: Path, project_id: str) -> dict[str, Any]:
    latest_path = output_root.expanduser().resolve() / "latest.json"
    if latest_path.is_file():
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        if latest.get("project_id") == project_id and isinstance(latest.get("source"), dict):
            return dict(latest["source"])
    return {"source_path": "existing-project", "source_sha256": "unknown"}


def _single_chapter_id(client: TestClient, project_id: str) -> str:
    response = _ok(
        client.get(f"/api/v1/projects/{project_id}/source/chapters"),
        200,
        "load project chapters",
    ).json()
    chapters = response["chapters"]
    if len(chapters) != 1:
        raise AcceptanceFailure("acceptance project must contain exactly one chapter")
    return str(chapters[0]["chapter_id"])


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _app_state(client: TestClient) -> Any:
    return cast(Any, client.app).state


def _ok(response: Response, expected_status: int, operation: str) -> Response:
    if response.status_code != expected_status:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"status_code": response.status_code, "body": response.text[:500]}
        raise AcceptanceFailure(f"Failed to {operation}: {payload}")
    return response


def _fatal(exc: BaseException) -> NoReturn:
    print(f"Sandkings V5 acceptance failed: {exc}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceFailure, OSError, ValueError) as error:
        _fatal(error)
