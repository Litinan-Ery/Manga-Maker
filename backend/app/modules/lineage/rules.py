from __future__ import annotations

from .contracts import DependencyEdgeType

EDGE_RULES: dict[DependencyEdgeType, tuple[frozenset[str], frozenset[str]]] = {
    "source_chapter_to_storyboard": (
        frozenset({"source_chapter"}),
        frozenset({"storyboard"}),
    ),
    "story_beat_to_storyboard": (frozenset({"story_beat"}), frozenset({"storyboard"})),
    "storyboard_to_layout": (
        frozenset({"storyboard"}),
        frozenset({"page_layout_draft", "frame"}),
    ),
    "storyboard_to_bible": (
        frozenset({"storyboard"}),
        frozenset({"character_bible", "character_tag_set", "style_bible"}),
    ),
    "layout_to_bible": (
        frozenset({"page_layout_draft", "frame"}),
        frozenset({"character_bible", "character_tag_set", "style_bible"}),
    ),
    "layout_to_prompt": (
        frozenset({"page_layout_draft"}),
        frozenset({"prompt_plan", "prompt_package"}),
    ),
    "frame_to_prompt": (
        frozenset({"frame"}),
        frozenset({"prompt_plan", "prompt_package"}),
    ),
    "character_bible_to_prompt": (
        frozenset({"character_bible"}),
        frozenset({"prompt_plan", "prompt_package"}),
    ),
    "character_tags_to_prompt": (
        frozenset({"character_tag_set"}),
        frozenset({"prompt_plan", "prompt_package"}),
    ),
    "style_bible_to_prompt": (
        frozenset({"style_bible"}),
        frozenset({"prompt_plan", "prompt_package"}),
    ),
    "prompt_to_generation_spec": (
        frozenset({"prompt_plan", "prompt_package"}),
        frozenset({"generation_spec"}),
    ),
    "layout_to_generation_spec": (
        frozenset({"page_layout_draft", "frame"}),
        frozenset({"generation_spec"}),
    ),
    "generation_spec_to_provider_spec": (
        frozenset({"generation_spec"}),
        frozenset({"provider_execution_spec"}),
    ),
    "generation_spec_to_candidate_set": (
        frozenset({"generation_spec", "provider_execution_spec"}),
        frozenset({"panel_candidate_set"}),
    ),
    "asset_to_candidate_set": (
        frozenset({"asset_version"}),
        frozenset({"panel_candidate_set"}),
    ),
    "candidate_set_to_review": (
        frozenset({"panel_candidate_set"}),
        frozenset({"review_decision"}),
    ),
    "quality_finding_to_review": (
        frozenset({"quality_finding"}),
        frozenset({"review_decision"}),
    ),
    "review_to_page_version": (
        frozenset({"review_decision"}),
        frozenset({"page_version"}),
    ),
    "layout_to_page_version": (
        frozenset({"page_layout_draft"}),
        frozenset({"page_version"}),
    ),
    "review_to_page_approval": (
        frozenset({"review_decision"}),
        frozenset({"page_approval"}),
    ),
    "page_version_to_page_approval": (
        frozenset({"page_version"}),
        frozenset({"page_approval"}),
    ),
    "quality_finding_to_page_approval": (
        frozenset({"quality_finding"}),
        frozenset({"page_approval"}),
    ),
    "page_approval_to_export": (
        frozenset({"page_approval"}),
        frozenset({"export_revision"}),
    ),
}


def edge_is_allowed(edge_type: DependencyEdgeType, upstream: str, downstream: str) -> bool:
    allowed_upstream, allowed_downstream = EDGE_RULES[edge_type]
    return upstream in allowed_upstream and downstream in allowed_downstream
