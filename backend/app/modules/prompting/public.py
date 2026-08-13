"""Public import surface for prompting contracts."""

from __future__ import annotations

from typing import Protocol

from .compiler import (
    ApprovedCharacterTagSet,
    CharacterPromptDraft,
    LegacyFlatPromptSnapshot,
    PanelPromptDraft,
    PromptCompilationInput,
    compile_prompt_package,
    prompt_plan_sha256,
    read_legacy_flat_prompt,
    require_prompt_package_integrity,
)
from .contracts import PromptPackage, PromptPlan, TextModelSource
from .errors import PromptCompilationError


class PromptingFacade(Protocol):
    def get_prompt_package(self, package_id: str, version: int) -> PromptPackage: ...


__all__ = [
    "ApprovedCharacterTagSet",
    "CharacterPromptDraft",
    "LegacyFlatPromptSnapshot",
    "PanelPromptDraft",
    "PromptCompilationError",
    "PromptCompilationInput",
    "PromptPackage",
    "PromptPlan",
    "PromptingFacade",
    "TextModelSource",
    "compile_prompt_package",
    "prompt_plan_sha256",
    "read_legacy_flat_prompt",
    "require_prompt_package_integrity",
]
