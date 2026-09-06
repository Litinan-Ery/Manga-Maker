"""Run UC-04 offline and retain inspectable requests, pages and exports.

Run as a module from the repository with development dependencies installed.
This script never sends requests to NovelAI or uses the user's credential vault.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.shared_kernel import canonical_sha256
from tests.e2e.test_storyboard_manga import SOURCE_TEXT, STORY_TITLE, run_manga_workflow
from tests.test_exports_api import download_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".manga-maker/storyboard-acceptance"))
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    output = args.output.expanduser().resolve() / stamp
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "case": "UC-04",
        "title": STORY_TITLE,
        "time_utc": stamp,
        "evidence": "offline mock; images are test fixtures, not NovelAI manga",
        "real_novelai_requests": 0,
        "model_visual_review": "not_run",
        "modes": {},
    }
    for mode in ("panel", "full_page"):
        directory = output / mode
        directory.mkdir()
        app = create_app(Settings(app_data_dir=directory / "app-data", environment="test"))
        with TestClient(app) as client:
            headers = {
                "X-Manga-Maker-Session": app.state.local_session.token,
                "X-CSRF-Token": app.state.local_session.csrf_token,
            }
            result = run_manga_workflow(client, headers, mode)
            (directory / "source.txt").write_text(SOURCE_TEXT, encoding="utf-8")
            (directory / "evidence.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            hashes = []
            for index, payload in enumerate(result["provider_payloads"], start=1):
                hashes.append(canonical_sha256(payload))
                (directory / f"prompt-{index:02d}.txt").write_text(
                    payload["input"]
                    + "\n\nUNDESIRED CONTENT:\n"
                    + payload["parameters"]["negative_prompt"]
                    + "\n",
                    encoding="utf-8",
                )
            exported_files = []
            for item in result["export"]["files"]:
                data = download_file(client, headers, result["project_id"], result["export"], item)
                target = directory / Path(item["filename"]).name
                target.write_bytes(data)
                exported_files.append({"file": target.name, "sha256": item["sha256"]})
            report["modes"][mode] = {
                "status": "passed",
                "page_panel_counts": [3, 4],
                "generation_calls": result["generation_calls"],
                "provider_payload_sha256": hashes,
                "export_files": exported_files,
            }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "README.md").write_text(
        f"# UC-04 {STORY_TITLE}\n\n"
        "本报告只验证本地产品链路。彩色测试图片不是 NovelAI 漫画成图。\n\n"
        "| 模式 | 页数/格数 | Mock 图像调用 | 结果 |\n|---|---|---|---|\n"
        "| 逐格拼页 | 2 页，3 + 4 格 | 7 | 通过 |\n"
        "| V5 整页 | 2 页，3 + 4 格 | 2 | 通过 |\n\n"
        "两种模式均检查 RTL、不等宽格框、分镜覆盖与四格式导出。"
        "完整来源、页面与实际 Mock 请求见各目录 evidence.json。"
        "请求哈希、调用计数和导出哈希见 report.json。\n\n"
        "真实 NovelAI 调用: 0。模型格数、画风、角色与文字视觉验收: 尚未执行。\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
