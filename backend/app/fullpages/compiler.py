from __future__ import annotations

import json
import re
from itertools import pairwise
from typing import Any, cast
from uuid import UUID, uuid5

from ..adaptation.models import StoryboardDocument
from ..bibles.models import CharacterBibleDocument, StyleBibleDocument
from ..errors import ApplicationError
from ..modules.layout.public import ApprovedChapterLayoutSnapshotV1
from ..modules.production.adapters.novelai import NovelAIPayload, require_frozen_novelai_payload
from ..modules.production.contracts import ProviderExecutionSpec
from ..modules.prompting.composition import SHOT_TAGS, percent, region
from ..novelai.contracts import CONTRACT_SHA256
from ..pages.layout import compose_layout_document, pixel_frames
from ..pages.models import PageImageSource, PageLayoutSource, PixelRect
from ..prompting.models import CharacterTagBundleDocument
from ..shared_kernel import canonical_sha256
from .models import FullPageOptions, FullPagePlan

FULL_PAGE_MAPPING_VERSION = "novelai-v5-full-page-2026-09-06.4"
POSITIVE = ["manga", "comic", "monochrome", "greyscale", "screentone", "ink lineart"]
NEGATIVE = [
    "lowres",
    "bad anatomy",
    "bad hands",
    "jpeg artifacts",
    "watermark",
    "logo",
    "color",
    "panel number",
    "page number",
]
PANEL_CONFLICTS = {
    "manga",
    "anime",
    "comic",
    "panel border",
    "panel borders",
    "comic panel",
    "multiple views",
    "multiple scenes",
    "screentone",
    "halftone",
    "negative space",
}
TEXT_NEGATIVES = {"text", "letters", "speech bubble", "speech bubbles", "caption", "captions"}
TEXT_POSITIVES = {"no text", "no letters", "no speech bubbles", "no speech bubble", "no captions"}
SINGLE_POSITIVES = {"single full-page comic illustration", "single panel", "single manga panel"}


def input_fingerprint(inputs: dict[str, Any]) -> str:
    return canonical_sha256(
        {
            key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            for key, value in inputs.items()
        }
    )


def compile_full_page(
    project_id: str,
    generation_id: str,
    options: FullPageOptions,
    inputs: dict[str, Any],
    configuration_revision: int,
) -> FullPagePlan:
    storyboard = cast(StoryboardDocument, inputs["storyboard"])
    style = cast(StyleBibleDocument, inputs["style"])
    characters = cast(CharacterBibleDocument, inputs["characters"])
    tags = cast(CharacterTagBundleDocument, inputs["tags"])
    layouts = cast(ApprovedChapterLayoutSnapshotV1, inputs["layout"])
    page = next(
        (page for page in storyboard.pages if page.page_number == options.page_number), None
    )
    if page is None:
        raise ApplicationError("FULL_PAGE_NOT_FOUND", "当前分镜中没有这一页。", 404)
    approved = next(item for item in layouts.pages if item.version.layout.page_id == page.page_id)
    layout = approved.version.layout
    page_ids = {str(panel.panel_id) for panel in page.panels}
    if (
        set(options.panel_descriptions) | set(options.panel_texts) | set(options.panel_characters)
    ) - page_ids:
        raise ApplicationError("FULL_PAGE_PANEL_UNKNOWN", "画面或文字覆盖包含其他页的格。", 422)
    visible_cast = {
        str(panel.panel_id): options.panel_characters.get(str(panel.panel_id), panel.characters)
        for panel in page.panels
    }
    for panel in page.panels:
        selected_names = visible_cast[str(panel.panel_id)]
        if len(set(selected_names)) != len(selected_names) or set(selected_names) - set(
            panel.characters
        ):
            raise ApplicationError(
                "FULL_PAGE_CHARACTER_UNKNOWN", "出镜角色必须是不重复的本格已批准角色。", 422
            )
    if options.text_policy == "local" and options.panel_texts:
        raise ApplicationError("FULL_PAGE_LOCAL_TEXT_EDIT", "本地文字请在页面编辑器中修改。", 422)

    # A Japanese manga mode cannot silently inherit an explicitly incompatible art direction.
    style_positive = split_tags(style.positive_prompt_fragment)
    style_negative = split_tags(style.negative_prompt_fragment)
    if any(fold(tag) in {"manga", "anime", "comic"} for tag in style_negative):
        raise ApplicationError(
            "FULL_PAGE_STYLE_CONFLICT", "批准风格排斥日式漫画，请先修改风格板并重新批准。", 409
        )
    removed = []
    positive = list(POSITIVE)
    for tag in style_positive:
        if fold(tag) in SINGLE_POSITIVES | TEXT_POSITIVES:
            removed.append(tag)
        else:
            positive.append(tag)
    negative = list(NEGATIVE)
    for tag in style_negative:
        if fold(tag) in PANEL_CONFLICTS | TEXT_NEGATIVES:
            removed.append(tag)
        else:
            negative.append(tag)
    if options.text_policy == "local":
        positive.append("no text")
        negative.extend(sorted(TEXT_NEGATIVES))
    else:
        positive.extend(["text", "speech bubble"])
    positive, negative = unique(positive), unique(negative)

    direction = {
        "rtl_ttb": "right to left, then top to bottom",
        "ltr_ttb": "left to right, then top to bottom",
        "ttb": "top to bottom",
    }[layout.reading_direction]
    narrative = [
        f"One complete Japanese manga page containing exactly {len(page.panels)} distinct panels. "
        f"Read {direction}. Draw crisp panel borders separated by clean white gutters. "
        "Keep all gutters empty, without labels or numbering.",
        "The same named character keeps the same face, hair and clothing across all panels.",
    ]
    by_id = {str(panel.panel_id): panel for panel in page.panels}
    rectangles = pixel_frames(layout)
    leaves = sorted(
        (frame for frame in layout.frames if frame.panel_id), key=lambda f: f.order or 0
    )
    topology, locations = layout_language([rectangles[frame.frame_id] for frame in leaves])
    narrative.insert(1, topology + " One instant per panel; do not add extra inset panels.")
    names = {profile.name: profile for profile in characters.characters}
    tags_by_id = {tag.character_id: tag for tag in tags.tag_sets}
    referenced = set()
    for panel in page.panels:
        for name in visible_cast[str(panel.panel_id)]:
            profile = names.get(name) or next(
                (item for item in characters.characters if name in item.aliases), None
            )
            if profile is not None and profile.character_id not in referenced:
                narrative.append(
                    f"Character reference for {profile.name}, not an extra panel: "
                    f"age {profile.age_range}; {profile.face_shape}; {profile.hair}; "
                    f"{profile.body_type}. Outfit: {', '.join(profile.outfit)}. "
                    f"Distinctive features: {', '.join(profile.signature_features)}. "
                    "Only draw the body parts visible in each shot."
                )
                referenced.add(profile.character_id)
    text_strings: list[str] = []
    lettering_map: list[str] = []
    for index, frame in enumerate(leaves, start=1):
        panel = by_id[str(frame.panel_id)]
        character_notes = []
        for name in visible_cast[str(panel.panel_id)]:
            profile = names.get(name) or next(
                (profile for profile in characters.characters if name in profile.aliases), None
            )
            if profile is None or profile.character_id not in tags_by_id:
                raise ApplicationError(
                    "FULL_PAGE_CHARACTER_MISSING", "分镜角色缺少已批准固定 tags。", 409
                )
            character_tags = tags_by_id[profile.character_id]
            position = next(
                (p for p in frame.character_positions if p.character_id == profile.character_id),
                None,
            )
            note = f"{profile.name} ({', '.join(character_tags.fixed_tags)})"
            if position is not None and (
                len(visible_cast[str(panel.panel_id)]) > 1
                or position.center.x != 0.5
                or position.center.y != 0.5
                or position.prominence != "primary"
            ):
                note += (
                    f" at {percent(position.center.x)} from the panel's left "
                    f"and {percent(position.center.y)} from its top, {position.prominence}"
                )
            character_notes.append(note)
        # Strip only the old top-level output bans, not ordinary punctuation or natural sentences.
        description = options.panel_descriptions.get(str(panel.panel_id), panel.visual_prompt)
        description = (
            re.sub(
                r"(?:,\s*)?\b(?:no text|no letters|no speech bubbles?|"
                r"single full-page comic illustration)\b",
                "",
                description,
                flags=re.IGNORECASE,
            )
            .strip(" ,")
            .rstrip(".")
        )
        visible = "; ".join(character_notes) or "none; show only the setting or object"
        location = locations[index - 1]
        heading = location if location.startswith("across") else f"at the {location}"
        part = (
            f"Panel {heading}: "
            f"{SHOT_TAGS[frame.shot_scale]}. {description}. "
            f"Character identity: {visible}."
        )
        if frame.focal_point.x != 0.5 or frame.focal_point.y != 0.5:
            part += (
                f" Within this panel, focus at {percent(frame.focal_point.x)} from the left "
                f"and {percent(frame.focal_point.y)} from the top."
            )
        if (
            frame.crop_safe_rect.x,
            frame.crop_safe_rect.y,
            frame.crop_safe_rect.width,
            frame.crop_safe_rect.height,
        ) != (0, 0, 1, 1):
            part += f" Protect important details inside {region(frame.crop_safe_rect)}."
        for zone in frame.text_safe_zones:
            part += f" Keep {region(zone.rect)} of this panel light and clear for lettering."
        if options.text_policy == "model":
            strings = options.panel_texts.get(
                str(panel.panel_id),
                [
                    *panel.narration,
                    *(line.text for line in panel.dialogue),
                    *panel.sfx,
                ],
            )
            if strings:
                quoted = ", ".join(json.dumps(text, ensure_ascii=False) for text in strings)
                part += " Render " + quoted
                part += " only in this panel's lettering area."
                subject = (
                    f"panel {location}" if location.startswith("across") else f"{location} panel"
                )
                lettering_map.append(f"The {subject} says {quoted}")
                text_strings.extend(strings)
        narrative.append(part)
    if options.text_policy == "local":
        narrative.append("Keep lettering areas empty. Do not draw letters or speech balloons.")
    elif text_strings:
        narrative.insert(3, "Lettering map: " + ". ".join(lettering_map) + ". No additional text.")
        if len("\n\n".join(text_strings)) > 750:
            raise ApplicationError(
                "FULL_PAGE_TEXT_BUDGET", "整页文字超过 V5 Full 的 750 字符上限。", 422
            )
        narrative.append("Text: " + "\n\n".join(text_strings))
    base_narrative = "\n\n".join(narrative)
    prompt = ", ".join(positive) + "\n\n" + base_narrative
    if len(prompt) > 6000:
        raise ApplicationError(
            "FULL_PAGE_PROMPT_BUDGET", "整页提示超过本地 6000 字符预算，请缩短各格描述。", 422
        )
    aspect = layout.canvas.width / layout.canvas.height
    if not 0.5 <= aspect <= 2:
        raise ApplicationError("FULL_PAGE_ASPECT_UNSUPPORTED", "长条版式请使用逐格生成。", 422)
    width, height = (832, 1216) if aspect < 0.85 else (1216, 832) if aspect > 1.18 else (1024, 1024)
    negative_prompt = ", ".join(negative)
    payload = NovelAIPayload.model_validate(
        {
            "action": "generate",
            "input": prompt,
            "model": "nai-diffusion-5-full",
            "parameters": {
                "width": width,
                "height": height,
                "steps": 23,
                "scale": 7,
                "sampler": "k_euler_ancestral",
                "noise_schedule": "karras",
                "seed": options.seed,
                "n_samples": 1,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "qualityToggle": False,
                "prefer_brownian": True,
                "straight_alpha": True,
                "tag_hint_qt": 0,
                "tag_hint_uc_preset": 0,
                "v4_prompt": {
                    "caption": {"base_caption": prompt, "char_captions": []},
                    "use_coords": False,
                },
                "v4_negative_prompt": {
                    "caption": {"base_caption": negative_prompt, "char_captions": []},
                    "use_coords": False,
                    "legacy_uc": False,
                },
            },
        }
    )
    execution = ProviderExecutionSpec(
        provider_execution_spec_id=uuid5(UUID(generation_id), "provider-execution"),
        version=1,
        generation_spec_id=UUID(generation_id),
        generation_scope="full_page",
        mapping_version=FULL_PAGE_MAPPING_VERSION,
        contract_sha256=CONTRACT_SHA256,
        capability_snapshot_sha256=canonical_sha256({"model": payload.model, "scope": "full_page"}),
        model_id=payload.model,
        prompt_plan_id=uuid5(UUID(generation_id), "prompt-plan"),
        prompt_plan_version=1,
        prompt_plan_sha256=canonical_sha256({"prompt": prompt, "negative": negative_prompt}),
        page_layout_draft_id=layout.page_layout_draft_id,
        page_layout_draft_version=layout.version,
        page_layout_draft_sha256=layout.content_sha256,
        width=width,
        height=height,
        seed=options.seed,
        base_positive_tags=positive,
        base_negative_tags=negative,
        base_narrative=base_narrative,
        character_captions=[],
        payload_sha256=canonical_sha256(payload.model_dump(mode="json", exclude_none=True)),
    )
    require_frozen_novelai_payload(execution, payload)
    document = compose_layout_document(
        page,
        str(inputs["storyboard_version_id"]),
        layout,
        PageLayoutSource(
            version_id=str(approved.version.page_layout_draft_version_id),
            content_sha256=layout.content_sha256,
        ),
        {panel_id: {"asset_version_id": generation_id} for panel_id in page_ids},
        include_text=options.text_policy == "local",
    )
    document = document.model_copy(
        update={
            "page_image": PageImageSource(
                generation_id=generation_id, text_policy=options.text_policy
            ),
            "show_page_number": False,
        }
    )
    return FullPagePlan(
        generation_id=generation_id,
        project_id=project_id,
        inputs_sha256=input_fingerprint(inputs),
        configuration_revision=configuration_revision,
        options=options,
        provider_execution_spec=execution,
        provider_payload=payload,
        page_document=document,
        removed_conflicting_tags=removed,
    )


def layout_language(rects: list[PixelRect]) -> tuple[str, list[str]]:
    """Describe actual page geometry without pretending percentages are model controls."""
    left, top = min(r.x for r in rects), min(r.y for r in rects)
    width = max(r.x + r.width for r in rects) - left
    height = max(r.y + r.height for r in rects) - top
    locations = []
    for rect in rects:
        x, y = (rect.x + rect.width / 2 - left) / width, (rect.y + rect.height / 2 - top) / height
        vertical = "upper" if y < 0.4 else "lower" if y > 0.6 else "middle"
        horizontal = "left" if x < 0.42 else "right" if x > 0.58 else "center"
        if rect.width >= width * 0.85:
            label = "top" if vertical == "upper" else "bottom" if vertical == "lower" else "middle"
            locations.append(f"across the {label}")
        else:
            locations.append(f"{vertical} {horizontal}")

    rows: list[list[int]] = []
    for index in sorted(range(len(rects)), key=lambda i: (rects[i].y, rects[i].x)):
        rect = rects[index]
        if rows and abs(rect.y - rects[rows[-1][0]].y) <= height * 0.02:
            rows[-1].append(index)
        else:
            rows.append([index])
    # A panel spanning multiple rows cannot be described as a regular row grid.
    regular = all(
        max(rects[i].y + rects[i].height for i in current)
        <= min(rects[i].y for i in following) + height * 0.02
        for current, following in pairwise(rows)
    )
    if not regular:
        return "Layout: " + "; ".join(
            f"a panel {location}" for location in locations
        ) + ".", locations
    sentences = [f"Layout: exactly {len(rows)} rows."]
    for index, row in enumerate(rows):
        name = (
            "Top row"
            if index == 0
            else "Bottom row"
            if index == len(rows) - 1
            else f"Row {index + 1}"
        )
        ordered = sorted(row, key=lambda i: rects[i].x)
        if len(row) == 1 and rects[row[0]].width >= width * 0.85:
            content = "one panel spanning the full width"
        else:
            content = f"{len(row)} panels side by side"
            if len(row) == 2:
                ratio = rects[ordered[0]].width / rects[ordered[1]].width
                if ratio > 1.1:
                    content += ", the left panel wider than the right"
                elif ratio < 1 / 1.1:
                    content += ", the right panel wider than the left"
        sentences.append(f"{name}: {content}.")
    return " ".join(sentences), locations


def split_tags(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]


def fold(value: str) -> str:
    return value.strip("{}[] ").casefold()


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
