from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .policy import token_upper_bound


def bounded_envelope(
    items: Sequence[dict[str, Any]],
    *,
    artifact_ref: str,
    cursor: int = 0,
    limit: int = 20,
    token_budget: int = 6000,
    single_item_budget: int = 2000,
) -> dict[str, Any]:
    """Page already-sanitized results; keep every item addressable, including failures.

    The caller persists the complete sanitized artifact before calling this function.
    All envelope overhead counts against the aggregate conservative token bound.
    """
    if not 0 <= cursor <= len(items) or not 1 <= limit <= 100:
        raise ValueError("invalid result page")
    if not 512 <= token_budget <= 6000 or not 128 <= single_item_budget <= 2000:
        raise ValueError("invalid result budget")
    if not artifact_ref or len(artifact_ref) > 160:
        raise ValueError("invalid artifact reference")
    failures = sum(item.get("status") in ("failed", "error") for item in items)
    encoded = json.dumps(list(items), ensure_ascii=False, separators=(",", ":"))
    result: dict[str, Any] = {
        "items": [],
        "total_items": len(items),
        "failure_count": failures,
        "artifact_ref": artifact_ref,
        "failure_index_ref": artifact_ref,
        "original_bytes": len(encoded.encode("utf-8")),
        "returned_range": [cursor, cursor],
        "truncated": cursor > 0 or bool(items),
        "next_cursor": cursor if cursor < len(items) else None,
        "measurement": "utf8_byte_upper_bound",
    }
    selected: list[dict[str, Any]] = []
    for index in range(cursor, min(len(items), cursor + limit)):
        item = items[index]
        if token_upper_bound(json.dumps(item, ensure_ascii=False)) > single_item_budget:
            item = {
                "index": index,
                "truncated": True,
                "detail_ref": artifact_ref,
                "failed": item.get("status") in ("failed", "error"),
            }
        candidate = [*selected, item]
        next_cursor = index + 1 if index + 1 < len(items) else None
        result.update(
            items=candidate,
            returned_range=[cursor, index + 1],
            truncated=cursor > 0
            or next_cursor is not None
            or any(row.get("truncated", False) for row in candidate),
            next_cursor=next_cursor,
        )
        if token_upper_bound(json.dumps(result, ensure_ascii=False)) > token_budget:
            result.update(
                items=selected, returned_range=[cursor, index], truncated=True, next_cursor=index
            )
            break
        selected = candidate
    if token_upper_bound(json.dumps(result, ensure_ascii=False)) > token_budget:
        raise ValueError("result metadata exceeds budget")
    if result["next_cursor"] == cursor and cursor < len(items):
        raise ValueError("budget cannot fit one addressable result")
    return result
