from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import FastAPI

app = FastAPI(title="Manga Maker manual QA text-model mock")


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": "manga-qa-model", "object": "model"}],
    }


@app.post("/v1/chat/completions")
def create_chat_completion(body: dict[str, Any]) -> dict[str, Any]:
    request = _storyboard_request(body)
    beats = request["story_beats"]
    scene_id = str(uuid.uuid4())
    page_id = str(uuid.uuid4())
    storyboard = {
        "schema_version": "1.0",
        "storyboard_id": str(uuid.uuid4()),
        "chapter_version": request["chapter_version"],
        "beat_resolutions": [
            {
                "beat_id": beat["beat_id"],
                "status": "represented",
                "reason": None,
                "page_numbers": [1],
            }
            for beat in beats
        ],
        "scenes": [
            {
                "scene_id": scene_id,
                "order": 1,
                "title": "雨夜车站",
                "location": "老车站",
                "time_of_day": "夜晚",
                "summary": "林夏追查红伞帽少女留下的线索。",
                "beat_ids": [beat["beat_id"] for beat in beats],
            }
        ],
        "pages": [
            {
                "page_id": page_id,
                "page_number": 1,
                "turning_point": "怀表揭示异常时间",
                "scene_ids": [scene_id],
                "panels": [
                    {
                        "panel_id": str(uuid.uuid4()),
                        "order": index,
                        "purpose": f"呈现来源节拍 {index}",
                        "shot": "medium shot",
                        "characters": ["林夏"],
                        "dialogue": [],
                        "narration": [beat["excerpt"][:80]],
                        "sfx": ["沙沙"],
                        "visual_prompt": (
                            "black and white manga, rainy old station, "
                            "detective Lin Xia, cinematic lighting, no text"
                        ),
                        "negative_prompt": "color, watermark, text, letters, logo",
                        "source_anchor_ids": [beat["anchor_id"]],
                    }
                    for index, beat in enumerate(beats, start=1)
                ],
            }
        ],
    }
    content = json.dumps(storyboard, ensure_ascii=False)
    return {
        "id": "manga-maker-manual-qa",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 80},
    }


def _storyboard_request(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("missing OpenAI-compatible messages")
    user_content = messages[-1].get("content")
    payload = json.loads(user_content)
    request = payload.get("request")
    if not isinstance(request, dict):
        raise ValueError("missing storyboard request")
    return request
