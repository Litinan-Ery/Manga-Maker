import json

import pytest
from pydantic import ValidationError

from backend.app.platform.context_budget import (
    ContextObservation,
    ContextPolicy,
    InputEvent,
    decide_budget,
    token_upper_bound,
)
from backend.app.platform.context_budget.results import bounded_envelope


def observation(tokens: int, **kwargs: object) -> ContextObservation:
    return ContextObservation.model_validate(
        {
            "window_id": "w1",
            "context_limit": 258400,
            "current_tokens": tokens,
            "measurement": "actual",
            **kwargs,
        }
    )


@pytest.mark.parametrize(
    ("tokens", "action"),
    [
        (30000, "continue"),
        (129200, "checkpoint"),
        (155040, "compact"),
        (180880, "handoff"),
    ],
)
def test_watermarks(tokens: int, action: str) -> None:
    assert decide_budget(observation(tokens)).action == action


def test_reserves_output_and_deduplicates_observed_events() -> None:
    result = decide_budget(
        observation(100000, counted_through=5),
        (
            InputEvent(sequence=5, text_tokens=100000),
            InputEvent(sequence=6, text_tokens=1000),
        ),
    )
    assert result.projected_tokens == 126840
    assert result.counted_through == 6
    assert decide_budget(observation(100000), planned_output=120000).action == "handoff"
    with pytest.raises(ValueError, match="unique"):
        decide_budget(observation(1), (InputEvent(sequence=1), InputEvent(sequence=1)))


def test_unknown_stale_and_small_windows_are_not_reported_as_safe() -> None:
    assert decide_budget(ContextObservation(window_id="w")).action == "handoff"
    assert decide_budget(observation(10, measurement="stale")).action == "checkpoint"
    assert decide_budget(observation(10, context_limit=8000)).action == "handoff"
    with pytest.raises(ValidationError):
        ContextPolicy(checkpoint_ratio=0.8)


def test_image_limits_and_unknown_image_tokens() -> None:
    assert (
        decide_budget(
            observation(30000), (InputEvent(sequence=1, images=3, image_tokens=3000),)
        ).reason
        == "IMAGE_BATCH_TOO_LARGE"
    )
    assert (
        decide_budget(
            observation(30000), (InputEvent(sequence=1, images=1, image_tokens=None),)
        ).reason
        == "IMAGE_USAGE_UNKNOWN"
    )
    assert (
        decide_budget(
            observation(30000, images_in_window=11),
            (InputEvent(sequence=1, images=1, image_tokens=1000),),
        ).action
        == "compact"
    )


def test_aggregate_envelope_reports_failure_even_beyond_truncation() -> None:
    items = [
        {"id": str(i), "status": "failed" if i == 99 else "ok", "detail": "错误证据" * 3000}
        for i in range(100)
    ]
    cursor, seen = 0, []
    while True:
        result = bounded_envelope(items, artifact_ref="evidence-1", cursor=cursor)
        assert token_upper_bound(json.dumps(result, ensure_ascii=False)) <= 6000
        assert result["failure_count"] == 1
        assert result["total_items"] == 100
        seen.extend(item["index"] for item in result["items"])
        if result["next_cursor"] is None:
            break
        cursor = result["next_cursor"]
    assert seen == list(range(100))


def test_small_results_share_one_budget() -> None:
    items = [{"id": i, "detail": "x" * 700} for i in range(40)]
    result = bounded_envelope(items, artifact_ref="results", limit=100)
    assert len(result["items"]) < 10
    assert result["next_cursor"] == len(result["items"])
    assert result["truncated"]
    assert token_upper_bound(json.dumps(result, ensure_ascii=False)) <= 6000


def test_evidence_end_cursor_terminates_and_nonstring_status_is_data() -> None:
    from backend.app.platform.context_budget.results import bounded_envelope

    items = [{"status": {"detail": "untrusted"}}]
    assert bounded_envelope(items, artifact_ref="evidence")["failure_count"] == 0
    result = bounded_envelope(items, artifact_ref="evidence", cursor=1)
    assert result["next_cursor"] is None
    assert result["items"] == []
