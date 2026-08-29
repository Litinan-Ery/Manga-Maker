"""Public runtime boundary for pinned NovelAI model capabilities."""

from ..novelai.contracts import (
    MAPPING_VERSION,
    ModelCapability,
    require_inpaint_model_profile,
    require_model_profile,
)

__all__ = [
    "MAPPING_VERSION",
    "ModelCapability",
    "require_inpaint_model_profile",
    "require_model_profile",
]
