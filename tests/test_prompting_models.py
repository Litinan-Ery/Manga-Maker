from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.prompting.models import PanelPromptDraft, PromptCharacterBlockDraft


def test_character_action_rejects_flat_prompt_delimiters_before_provider_mapping() -> None:
    with pytest.raises(ValidationError, match="action entries must be individual tags"):
        PromptCharacterBlockDraft(
            character_id=uuid4(),
            tag_set_id=uuid4(),
            action="steps forward, then stops",
            order=0,
            center={"x": 0.5, "y": 0.5},
        )


def test_relationship_action_rejects_flat_prompt_delimiters_before_provider_mapping() -> None:
    with pytest.raises(
        ValidationError,
        match="relationship_action entries must be individual tags",
    ):
        PanelPromptDraft(
            prompt_package_id=uuid4(),
            panel_id=uuid4(),
            base_visual_tags=["cinematic wide shot"],
            relationship_action="one person points, the other follows",
        )
