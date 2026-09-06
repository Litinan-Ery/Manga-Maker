"""Translate approved visual geometry into provider-visible language, without IDs."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from ..layout.contracts import FrameSpec, NormalizedRect

SHOT_TAGS = {
    "extreme_close_up": "extreme close-up",
    "close_up": "close-up",
    "medium": "medium shot",
    "full": "full body",
    "wide": "wide shot",
    "establishing": "establishing shot",
}
SHOT_ALIASES = frozenset(
    (
        *SHOT_TAGS.values(),
        "close up",
        "extreme close up",
        "full shot",
        "full-body",
        "medium close-up",
        "cowboy shot",
        "upper body",
    )
)


def without_camera_tags(tags: Sequence[str]) -> tuple[str, ...]:
    return tuple(tag for tag in tags if " ".join(tag.casefold().split()) not in SHOT_ALIASES)


def composition_prompt(frame: FrameSpec, character_order: Sequence[UUID]) -> str:
    sentences = [
        f"A single manga panel framed as {SHOT_TAGS[frame.shot_scale]}.",
        f"Place the main visual focus at {percent(frame.focal_point.x)} of the image width "
        f"from the left and {percent(frame.focal_point.y)} of the image height from the top.",
        f"Keep essential visual details inside {region(frame.crop_safe_rect)} "
        "so they survive cropping.",
    ]
    positions = {position.character_id: position for position in frame.character_positions}
    for index, character_id in enumerate(character_order, start=1):
        position = positions[character_id]
        sentences.append(
            f"Character {index} has {position.prominence} prominence, centered at "
            f"{percent(position.center.x)} from the left "
            f"and {percent(position.center.y)} from the top."
        )
    for zone in frame.text_safe_zones:
        sentences.append(
            f"Reserve {region(zone.rect)} for later lettering ({zone.kind}). "
            "Keep this area light and visually quiet, with faces and important objects outside it."
        )
    return "\n".join(sentences)


def region(rect: NormalizedRect) -> str:
    return (
        f"the rectangle from {percent(rect.x)} to {percent(rect.x + rect.width)} horizontally "
        f"and {percent(rect.y)} to {percent(rect.y + rect.height)} vertically"
    )


def percent(value: float) -> str:
    return f"{value * 100:.2f}".rstrip("0").rstrip(".") + "%"
