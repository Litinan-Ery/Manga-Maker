from __future__ import annotations

from uuid import UUID, uuid4

from backend.app.modules.layout.public import FrameSpec
from backend.app.prompting.service import (
    _bind_layout_character_slots,
    _layout_character_slot_id,
)


def test_layout_character_slot_id_matches_react_editor_encoding() -> None:
    assert _layout_character_slot_id("林夏") == UUID(
        "e69e97e5-a48f-7000-8000-000000000000"
    )


def test_pre_bible_layout_slots_bind_to_approved_character_ids() -> None:
    lin_xia_id = uuid4()
    grandmother_id = uuid4()
    frame = _frame(
        [
            _layout_character_slot_id("祖母"),
            _layout_character_slot_id("林夏"),
        ]
    )

    rebound = _bind_layout_character_slots(
        frame,
        character_names=["林夏", "祖母"],
        aliases={"林夏": str(lin_xia_id), "祖母": str(grandmother_id)},
    )

    assert [position.character_id for position in rebound.character_positions] == [
        grandmother_id,
        lin_xia_id,
    ]
    assert [position.center for position in rebound.character_positions] == [
        position.center for position in frame.character_positions
    ]


def test_unknown_layout_character_ids_still_fail_closed_in_compiler() -> None:
    frame = _frame([uuid4()])

    rebound = _bind_layout_character_slots(
        frame,
        character_names=["林夏"],
        aliases={"林夏": str(uuid4())},
    )

    assert rebound is frame


def _frame(character_ids: list[UUID]) -> FrameSpec:
    return FrameSpec.model_validate(
        {
            "frame_id": str(uuid4()),
            "parent_frame_id": str(uuid4()),
            "panel_id": str(uuid4()),
            "order": 1,
            "rect": {"x": 0.03, "y": 0.03, "width": 0.94, "height": 0.94},
            "aspect_ratio": 2 / 3,
            "shot_scale": "medium",
            "focal_point": {"x": 0.5, "y": 0.5},
            "character_positions": [
                {
                    "character_id": str(character_id),
                    "center": {"x": (index + 1) / (len(character_ids) + 1), "y": 0.58},
                    "prominence": "primary" if index == 0 else "secondary",
                }
                for index, character_id in enumerate(character_ids)
            ],
            "text_safe_zones": [],
            "crop_safe_rect": {"x": 0.06, "y": 0.06, "width": 0.88, "height": 0.88},
        }
    )
