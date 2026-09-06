from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..modules.production.adapters.novelai import NovelAIPayload
from ..modules.production.contracts import ProviderExecutionSpec
from ..pages.models import PageDocument

ShortText = Annotated[str, Field(min_length=1, max_length=800)]
VisibleCast = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=120)]], Field(max_length=12)
]


class FullPageOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    page_number: int = Field(ge=1, le=64)
    text_policy: Literal["local", "model"] = "local"
    seed: int = Field(ge=0, le=4_294_967_287)
    cost_ceiling_anlas: int = Field(default=0, ge=0, le=100)
    panel_descriptions: dict[str, ShortText] = Field(default_factory=dict, max_length=6)
    panel_texts: dict[str, list[ShortText]] = Field(default_factory=dict, max_length=6)
    panel_characters: dict[str, VisibleCast] = Field(default_factory=dict, max_length=6)


class FullPagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    generation_id: str
    project_id: str
    inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_revision: int
    options: FullPageOptions
    provider_execution_spec: ProviderExecutionSpec
    provider_payload: NovelAIPayload
    page_document: PageDocument
    removed_conflicting_tags: list[str]
