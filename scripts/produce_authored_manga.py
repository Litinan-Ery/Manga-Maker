"""Prepare authored chapters locally, then execute frozen full-page plans through the live app.

No test providers, credentials or synthetic image fallbacks are used. The manifest and
chapter JSON files live in the user's private production directory. Run as a module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid5

import httpx
from fastapi.testclient import TestClient

from backend.app.adaptation.models import StoryboardDocument
from backend.app.bootstrap.application import create_application
from backend.app.config import Settings
from backend.app.fullpages.models import FullPageOptions
from backend.app.platform.file_store.atomic import atomic_json, manifest_lock
from scripts.production_context import RemoteContextBatch, checkpoint_manifest


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: Any) -> None:
    atomic_json(path, data)
    if isinstance(data, dict) and "chapters" in data and "project_id" in data:
        checkpoint_manifest(path, data)


def live_client(session_file: Path) -> httpx.Client:
    url = urlsplit(read(session_file)["url"])
    if url.hostname != "127.0.0.1":
        raise ValueError("production requires the local Manga Maker app")
    fragment = parse_qs(url.fragment)
    return httpx.Client(
        base_url=f"{url.scheme}://{url.netloc}",
        headers={
            "X-Manga-Maker-Session": fragment["session"][0],
            "X-CSRF-Token": fragment["csrf"][0],
        },
        timeout=180,
    )


def checked(response: httpx.Response) -> Any:
    if response.is_error:
        # Local application errors are redacted; never print a provider response or headers.
        raise RuntimeError(f"Manga Maker returned {response.status_code}: {response.text[:1500]}")
    return response.json()


def uid(scope: str, name: str) -> str:
    return str(uuid5(UUID(scope), name))


def storyboard_document(
    part: dict[str, Any], source: dict[str, Any], beats: dict[str, Any], authored: list[Any]
) -> dict[str, Any]:
    count = part["page_budget"]
    if len(authored) != count:
        raise ValueError(f"expected {count} authored pages, received {len(authored)}")
    groups: list[list[Any]] = [[] for _ in range(count)]
    length = part["end_offset"] - part["start_offset"]
    for beat in beats["beats"]:
        index = min(
            count - 1, int((beat["start_offset"] + beat["end_offset"]) / 2 / length * count)
        )
        groups[index].append(beat)
    pages, scenes, resolutions = [], [], []
    for index, (authored_page, group) in enumerate(zip(authored, groups, strict=True), 1):
        if not group or len(authored_page["panels"]) not in range(3, 7):
            raise ValueError("authored page must have source coverage and 3-6 panels")
        scene_id = uid(part["chapter_id"], f"scene-{index}")
        scene = {
            "scene_id": scene_id,
            "order": index,
            "title": authored_page["title"],
            "location": authored_page.get("location", "原文对应场景"),
            "time_of_day": "按分格画面描述",
            "summary": authored_page["title"],
            "beat_ids": [b["beat_id"] for b in group],
        }
        scenes.append(scene)
        resolutions.extend(
            {
                "beat_id": b["beat_id"],
                "status": "condensed",
                "page_numbers": [index],
                "reason": "按来源时序压缩为本页核心事件。对话和背景有所取舍，非逐句呈现。",
            }
            for b in group
        )
        panels = []
        for n, (names, shot, visual, caption) in enumerate(authored_page["panels"]):
            start = n * len(group) // len(authored_page["panels"])
            end = (n + 1) * len(group) // len(authored_page["panels"])
            anchors = [b["anchor_id"] for b in group[start:end]] or [group[-1]["anchor_id"]]
            if len(anchors) > 50:
                raise ValueError("page source interval needs a finer editorial partition")
            panels.append(
                {
                    "panel_id": uid(part["chapter_id"], f"page-{index}-panel-{n + 1}"),
                    "order": n + 1,
                    "purpose": caption,
                    "shot": shot,
                    "characters": names.split("|") if names else [],
                    "dialogue": [],
                    "narration": [caption],
                    "sfx": [],
                    "visual_prompt": visual,
                    "negative_prompt": (
                        "color, watermark, logo, photorealistic, modern casual clothing"
                    ),
                    "source_anchor_ids": anchors,
                }
            )
        pages.append(
            {
                "page_id": uid(part["chapter_id"], f"page-{index}"),
                "page_number": index,
                "page_type": "standard",
                "turning_point": authored_page["title"],
                "scene_ids": [scene_id],
                "panels": panels,
            }
        )
    document = {
        "schema_version": "1.1",
        "storyboard_id": uid(part["chapter_id"], "storyboard"),
        "chapter_version": source["request"]["chapter_version"],
        "beat_resolutions": resolutions,
        "scenes": scenes,
        "pages": pages,
    }
    return StoryboardDocument.model_validate(document).model_dump(mode="json")


def bible_documents(
    project: str, storyboard: str, designs: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    characters = []
    for name, (english, age, face, hair, body, outfit, feature, tags) in designs.items():
        characters.append(
            {
                "character_id": uid(project, name),
                "name": name,
                "aliases": [english],
                "narrative_role": "小说中的叙事角色，外观缺项由本地漫画设计补全",
                "age_range": age,
                "face_shape": f"{english}: {face}",
                "hair": hair,
                "body_type": body,
                "outfit": outfit.split("; "),
                "signature_features": [feature],
                "forbidden_changes": ["different face", "different clothing within the same scene"],
                "variable_features": ["visible aging when specified in the panel"],
                "expression_range": ["serious", "thoughtful", "afraid", "relieved"],
                "positive_prompt_fragment": ", ".join(tags),
                "negative_prompt_fragment": "different identity, extra limbs",
            }
        )
    character_doc = {
        "schema_version": "1.0",
        "character_bible_id": uid(project, f"characters-{storyboard}"),
        "storyboard_version_id": storyboard,
        "characters": characters,
        "notes": "Codex 本地编写。人物命运按用户提供原文，未明确的脸型与发型是漫画设计选择。",
    }
    style = {
        "schema_version": "1.0",
        "style_bible_id": uid(project, f"style-{storyboard}"),
        "storyboard_version_id": storyboard,
        "summary": "日式黑白科幻历史漫画，荒漠修道院与文明循环",
        "line_art": "fine expressive ink lines and strong black silhouettes",
        "screentone": "controlled gray screentone and hatching",
        "lighting": "dramatic natural light",
        "background_density": "readable medieval stone architecture and desolate landscapes",
        "whitespace": "clean white gutters with quiet areas for local captions",
        "camera_language": "distinct sequential actions and varied wide medium close-up shots",
        "positive_prompt_fragment": (
            "seinen manga, monochrome, fine ink lines, screentone, "
            "mature realistic proportions, cinematic chiaroscuro"
        ),
        "negative_prompt_fragment": "color, photorealistic, 3d, chibi, watermark, logo, text",
        "prohibited_elements": ["彩色", "水印", "模型生成文字", "Q版人物"],
    }
    return character_doc, style


def prepare(manifest: Path, part_number: int, session_file: Path) -> None:
    with manifest_lock(manifest):
        _prepare(manifest, part_number, session_file)


def _prepare(manifest: Path, part_number: int, session_file: Path) -> None:
    state = read(manifest)
    part = state["chapters"][part_number - 1]
    root = manifest.parent
    authored_path = root / f"part-{part_number}-authored.json"
    designs_path = root / f"part-{part_number}-designs.json"
    authored_hash = hashlib.sha256(
        authored_path.read_bytes() + designs_path.read_bytes()
    ).hexdigest()
    if part.get("authored_sha256") not in (None, authored_hash):
        raise ValueError("authored inputs changed; review and replan explicitly before resuming")
    app = create_application(Settings())
    container = app.state.container
    project = state["project_id"]
    prefix = f"/api/v1/projects/{project}"
    local = TestClient(
        app
    )  # No lifespan startup: do not interrupt the live service or its unlocked vault.
    local.headers.update(
        {
            "X-Manga-Maker-Session": container.local_session.token,
            "X-CSRF-Token": container.local_session.csrf_token,
        }
    )
    with live_client(session_file) as live:
        existing = live.get(prefix + "/novelai/config")
        if existing.status_code == 404:
            checked(
                live.put(
                    prefix + "/novelai/config",
                    json={
                        "provider_model_id": "nai-diffusion-5-full",
                        "credential_profile_id": "novelai",
                        "timeout_seconds": 120,
                    },
                )
            )
        else:
            config = checked(existing)
            if config["provider_model_id"] != "nai-diffusion-5-full":
                raise ValueError("project must use the explicitly selected NovelAI V5 Full model")
    source = read(root / f"part-{part_number}-source.json")
    beats = read(root / f"part-{part_number}-beats.json")
    document = storyboard_document(part, source, beats, read(authored_path))
    save(root / f"part-{part_number}-storyboard.json", document)
    if "storyboard_version_id" not in part:
        imported = checked(
            local.post(
                prefix + "/adaptation/storyboards/import",
                json={
                    "chapter_id": part["chapter_id"],
                    "page_budget": part["page_budget"],
                    "expected_source_fingerprint": part["source_fingerprint"],
                    "document": document,
                    "source_note": "Codex 根据用户提供的小说在本地编写漫画脚本。外部文本调用为零。",
                },
            )
        )
        part["storyboard_version_id"] = imported["storyboard_version_id"]
        part["authored_sha256"] = authored_hash
        save(manifest, state)
    version = part["storyboard_version_id"]
    checked(local.post(prefix + f"/adaptation/storyboards/{version}/approve"))
    designs = read(designs_path)
    characters, style = bible_documents(project, version, designs)
    if "bible_versions" not in part:
        bundle = checked(
            local.post(
                prefix + "/bibles/import",
                json={
                    "storyboard_version_id": version,
                    "character_bible": characters,
                    "style_bible": style,
                    "source_note": "本地角色设计与统一日式黑白漫画画风。",
                },
            )
        )
        part["bible_versions"] = {
            kind: bundle[f"{kind}_bible"]["version_id"] for kind in ("character", "style")
        }
        save(manifest, state)
    for kind, bible_version in part["bible_versions"].items():
        checked(local.post(prefix + f"/bibles/{kind}/{bible_version}/approve"))
    if "tag_version_id" not in part:
        tags = {
            "schema_version": "1.0",
            "storyboard_version_id": version,
            "character_bible_version_id": part["bible_versions"]["character"],
            "style_bible_version_id": part["bible_versions"]["style"],
            "tag_sets": [
                {
                    "tag_set_id": uid(project, f"tags-{version}-{name}"),
                    "character_id": uid(project, name),
                    "character_name": name,
                    "fixed_tags": design[-1],
                    "negative_tags": ["extra limbs"],
                    "rationale": "同一场景中保持已批准角色外观",
                }
                for name, design in designs.items()
            ],
        }
        imported_tags = checked(
            local.post(
                prefix + "/prompting/character-tags/import",
                json={
                    "chapter_id": part["chapter_id"],
                    "document": tags,
                    "source_note": "从本地批准的角色设计编写固定 tags。",
                },
            )
        )
        part["tag_version_id"] = imported_tags["version_id"]
        save(manifest, state)
    checked(local.post(prefix + f"/prompting/character-tags/{part['tag_version_id']}/approve"))
    capabilities = read(Path("contracts/fixtures/v0.3/dimension-capabilities.json"))
    part.setdefault("pages", {})
    for page in document["pages"]:
        n = page["page_number"]
        entry = part["pages"].setdefault(
            str(n), {"global_page_number": part["global_page_start"] + n - 1}
        )
        if "layout_version_id" not in entry:
            layout = make_layout(page, project)
            created = checked(
                local.post(
                    prefix + "/layouts/drafts",
                    headers={"Idempotency-Key": f"authored-create-{page['page_id']}"},
                    json={
                        "chapter_id": part["chapter_id"],
                        "storyboard_version_id": version,
                        "draft": layout,
                    },
                )
            )
            entry["layout_version_id"] = created["page_layout_draft_version_id"]
            save(manifest, state)
        current = checked(local.get(prefix + f"/layouts/drafts/{uid(page['page_id'], 'layout')}"))
        validation_body = {
            "expected_revision": current["revision"],
            "layout_content_sha256": current["layout"]["content_sha256"],
            "storyboard_version_id": version,
            "dimension_capabilities": capabilities,
            "target_pixels": 1_048_576,
            "max_crop_safe_risk": 1.0,
        }
        valid = checked(
            local.post(
                prefix + f"/layouts/{entry['layout_version_id']}/validate", json=validation_body
            )
        )
        if not valid["valid"]:
            raise ValueError(f"page {n} layout invalid: {valid}")
        checked(
            local.post(
                prefix + f"/layouts/{entry['layout_version_id']}/approve",
                headers={"Idempotency-Key": f"authored-approve-{page['page_id']}"},
                json={**validation_body, "dimension_selections": valid["dimension_outcomes"]},
            )
        )
    # The app freezes a complete approved chapter layout, so finish every layout before previews.
    for page in document["pages"]:
        n = page["page_number"]
        entry = part["pages"][str(n)]
        if "generation_id" not in entry:
            preview = container.full_pages.preview(
                project,
                FullPageOptions(
                    chapter_id=part["chapter_id"],
                    page_number=n,
                    text_policy="local",
                    seed=100_000 + entry["global_page_number"],
                    cost_ceiling_anlas=0,
                ),
            )
            save(root / "previews" / f"{entry['global_page_number']:03d}.json", preview)
            entry.update(
                {
                    "generation_id": preview["generation_id"],
                    "plan_sha256": preview["plan_sha256"],
                    "status": "draft",
                }
            )
            save(manifest, state)
        print(f"prepared page {entry['global_page_number']:03d}", flush=True)
    part["status"] = "prepared"
    state["status"] = "production_prepared"
    save(manifest, state)
    local.close()


def make_layout(page: dict[str, Any], project: str) -> dict[str, Any]:
    page_id = page["page_id"]
    if len(page["panels"]) != 3:
        raise ValueError("this production template requires three panels")
    # Two alternating clear RTL topologies. Full-page model geometry still needs visual review.
    if page["page_number"] % 2:
        rectangles = [(0.52, 0.03, 0.45, 0.44), (0.03, 0.03, 0.45, 0.44), (0.03, 0.51, 0.94, 0.46)]
    else:
        rectangles = [(0.03, 0.03, 0.94, 0.44), (0.52, 0.51, 0.45, 0.46), (0.03, 0.51, 0.45, 0.46)]
    root_frame_id = uid(page_id, "root-frame")
    frames = [
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
        }
    ]
    for panel, (x, y, width, height) in zip(page["panels"], rectangles, strict=True):
        frames.append(
            {
                "frame_id": uid(panel["panel_id"], "frame"),
                "parent_frame_id": root_frame_id,
                "panel_id": panel["panel_id"],
                "order": panel["order"],
                "rect": {"x": x, "y": y, "width": width, "height": height},
                "aspect_ratio": width * 2048 / (height * 3072),
                "shot_scale": panel["shot"],
                "focal_point": {"x": 0.5, "y": 0.5},
                "character_positions": [
                    {
                        "character_id": uid(project, name),
                        "center": {"x": (i + 1) / (len(panel["characters"]) + 1), "y": 0.5},
                        "prominence": "primary" if i == 0 else "secondary",
                    }
                    for i, name in enumerate(panel["characters"])
                ],
                "text_safe_zones": [
                    {
                        "zone_id": uid(panel["panel_id"], "caption"),
                        "kind": "narration",
                        "rect": {"x": 0.05, "y": 0.76, "width": 0.90, "height": 0.21},
                    }
                ],
                "crop_safe_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
            }
        )
    return {
        "schema_version": "1.0",
        "page_layout_draft_id": uid(page_id, "layout"),
        "version": 1,
        "page_id": page_id,
        "page_profile": "print_portrait_2_3",
        "canvas": {"width": 2048, "height": 3072},
        "reading_direction": "rtl_ttb",
        "frames": frames,
        "content_sha256": "0" * 64,
        "approved_content_sha256": None,
    }


def generate(manifest: Path, part_number: int, start: int, end: int, session_file: Path) -> None:
    if end - start + 1 > 5:
        raise ValueError("one production batch may contain at most 5 pages")
    with manifest_lock(manifest):
        _generate(manifest, part_number, start, end, session_file)


def _generate(manifest: Path, part_number: int, start: int, end: int, session_file: Path) -> None:
    state = read(manifest)
    if (
        state.get("source")
        and hashlib.sha256(Path(state["source"]["path"]).read_bytes()).hexdigest()
        != state["source"]["sha256"]
    ):
        raise ValueError("original source changed")
    part = state["chapters"][part_number - 1]
    if not (1 <= start <= end <= part["page_budget"]):
        raise ValueError("page range is outside the prepared chapter")
    root = manifest.parent
    checkpoint_manifest(manifest, state)
    with live_client(session_file) as live, RemoteContextBatch(live, state) as context:
        vault = checked(live.get("/api/v1/vault"))
        if not vault["unlocked"]:
            raise ValueError("unlock the local app before image generation")
        for n in range(start, end + 1):
            entry = part["pages"][str(n)]
            context.checkpoint(f"核对第 {entry['global_page_number']} 页冻结计划与生成状态")
            context.preflight()
            path = f"/api/v1/projects/{state['project_id']}/full-pages/{entry['generation_id']}"
            plan = checked(live.get(path))
            if plan["plan_sha256"] != entry["plan_sha256"] or plan["cost_ceiling_anlas"] != 0:
                raise ValueError("frozen plan or approved zero-Anlas ceiling changed")
            if plan["status"] == "draft":
                plan = checked(
                    live.post(
                        path + "/generate",
                        json={"confirmed": True, "plan_sha256": entry["plan_sha256"]},
                    )
                )
            entry["status"] = plan["status"]
            entry["external_requests_started"] = plan["external_requests_started"]
            save(root / "results" / f"{entry['global_page_number']:03d}.json", plan)
            save(manifest, state)
            context.checkpoint(f"核对第 {entry['global_page_number']} 页生成结果")
            if plan["status"] != "ready":
                raise RuntimeError(
                    f"page {entry['global_page_number']} stopped: "
                    f"{plan['status']} {plan['error_code']}; no retry"
                )
            response = live.get(path + "/content")
            response.raise_for_status()
            digest = hashlib.sha256(response.content).hexdigest()
            if digest != plan["image_sha256"]:
                raise ValueError("downloaded image hash mismatch")
            if entry.get("image_sha256") not in (None, digest):
                raise ValueError("previously downloaded image changed; review the new attempt")
            destination = root / "originals" / f"{entry['global_page_number']:03d}.png"
            if (
                destination.exists()
                and hashlib.sha256(destination.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("existing original differs; preserve it for review")
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            destination.write_bytes(response.content)
            entry["image_sha256"] = digest
            entry.setdefault("visual_review", "pending")
            save(manifest, state)
            context.checkpoint(f"逐页审查第 {entry['global_page_number']} 页，核对下一页")
            print(
                f"generated page {entry['global_page_number']:03d} {digest[:12]} "
                f"(visual review {entry['visual_review']})",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--part", type=int, required=True)
    parser.add_argument(
        "--session-file", type=Path, default=Path(".manga-maker/app-runtime/session.json")
    )
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=1)
    args = parser.parse_args()
    state = read(args.manifest)
    if (
        hashlib.sha256(Path(state["source"]["path"]).read_bytes()).hexdigest()
        != state["source"]["sha256"]
    ):
        raise ValueError("original source changed")
    if args.generate:
        if not args.confirmed:
            parser.error(
                "generation requires explicit --confirmed after reviewing the frozen plans"
            )
        generate(args.manifest, args.part, args.start, args.end, args.session_file)
    else:
        prepare(args.manifest, args.part, args.session_file)


if __name__ == "__main__":
    main()
