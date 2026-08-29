from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..adaptation.models import StoryboardDocument
from ..bibles.models import CharacterBibleDocument, StyleBibleDocument
from ..modules.layout.contracts import NormalizedPoint
from ..modules.prompting.contracts import PromptPackage


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _validate_tag_list(values: list[str], field_name: str) -> None:
    folded = [value.casefold() for value in values]
    if len(folded) != len(set(folded)):
        raise ValueError(f"{field_name} must not contain duplicates")
    if any("," in value or "\n" in value for value in values):
        raise ValueError(f"{field_name} entries must be individual tags")


class CharacterTagSetDraft(ContractModel):
    tag_set_id: UUID
    character_id: UUID
    character_name: str = Field(min_length=1, max_length=100)
    appearance_version: str = Field(default="default", min_length=1, max_length=100)
    fixed_tags: list[str] = Field(min_length=1, max_length=60)
    negative_tags: list[str] = Field(default_factory=list, max_length=60)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_tags(self) -> CharacterTagSetDraft:
        _validate_tag_list(self.fixed_tags, "fixed_tags")
        _validate_tag_list(self.negative_tags, "negative_tags")
        return self


class CharacterTagDraftBundle(ContractModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    storyboard_version_id: UUID
    character_bible_version_id: UUID
    style_bible_version_id: UUID
    tag_sets: list[CharacterTagSetDraft] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_sets(self) -> CharacterTagDraftBundle:
        tag_ids = [item.tag_set_id for item in self.tag_sets]
        character_ids = [item.character_id for item in self.tag_sets]
        if len(tag_ids) != len(set(tag_ids)):
            raise ValueError("tag set ids must be unique")
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("each character must have one default tag set")
        return self


class CharacterTagSetDocument(CharacterTagSetDraft):
    fixed_tags_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class CharacterTagBundleDocument(ContractModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    storyboard_version_id: UUID
    character_bible_version_id: UUID
    style_bible_version_id: UUID
    tag_sets: list[CharacterTagSetDocument] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_sets(self) -> CharacterTagBundleDocument:
        tag_ids = [item.tag_set_id for item in self.tag_sets]
        character_ids = [item.character_id for item in self.tag_sets]
        if len(tag_ids) != len(set(tag_ids)):
            raise ValueError("tag set ids must be unique")
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("each character must have one default tag set")
        return self


class CharacterTagGenerationRequest(ContractModel):
    project_id: str = Field(min_length=1, max_length=64)
    chapter_id: str = Field(min_length=1, max_length=64)
    storyboard_version_id: UUID
    character_bible_version_id: UUID
    style_bible_version_id: UUID
    character_bible: CharacterBibleDocument
    style_bible: StyleBibleDocument
    target_tag_set_ids: dict[str, UUID]
    provider_model_id: str = Field(min_length=1, max_length=100)


class PromptCharacterBlockDraft(ContractModel):
    character_id: UUID
    tag_set_id: UUID
    variable_tags: list[str] = Field(default_factory=list, max_length=60)
    negative_tags: list[str] = Field(default_factory=list, max_length=60)
    action: str = Field(min_length=1, max_length=500)
    order: int = Field(ge=0, le=19)
    center: NormalizedPoint

    @model_validator(mode="after")
    def unique_tags(self) -> PromptCharacterBlockDraft:
        _validate_tag_list(self.variable_tags, "variable_tags")
        _validate_tag_list(self.negative_tags, "negative_tags")
        _validate_tag_list([self.action], "action")
        return self


class PanelPromptDraft(ContractModel):
    prompt_package_id: UUID
    panel_id: UUID
    base_visual_tags: list[str] = Field(min_length=1, max_length=80)
    character_blocks: list[PromptCharacterBlockDraft] = Field(default_factory=list, max_length=20)
    style_tags: list[str] = Field(default_factory=list, max_length=80)
    negative_tags: list[str] = Field(default_factory=list, max_length=100)
    relationship_action: str | None = Field(default=None, min_length=1, max_length=500)
    continuity_tags: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_values(self) -> PanelPromptDraft:
        for field_name in (
            "base_visual_tags",
            "style_tags",
            "negative_tags",
            "continuity_tags",
        ):
            _validate_tag_list(getattr(self, field_name), field_name)
        character_ids = [block.character_id for block in self.character_blocks]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("prompt character blocks must be unique")
        orders = sorted(block.order for block in self.character_blocks)
        if orders != list(range(len(self.character_blocks))):
            raise ValueError("prompt character block order must be contiguous and start at zero")
        if len(self.character_blocks) > 1 and self.relationship_action is None:
            raise ValueError("multi-character prompts require a relationship action")
        if self.relationship_action is not None:
            _validate_tag_list([self.relationship_action], "relationship_action")
        return self


class PromptDraftBundleDocument(ContractModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    storyboard_version_id: UUID
    character_tag_bundle_version_id: UUID
    packages: list[PanelPromptDraft] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_packages(self) -> PromptDraftBundleDocument:
        package_ids = [item.prompt_package_id for item in self.packages]
        panel_ids = [item.panel_id for item in self.packages]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("prompt package ids must be unique")
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("each panel must have one prompt package")
        return self


class PromptGenerationRequest(ContractModel):
    project_id: str = Field(min_length=1, max_length=64)
    chapter_id: str = Field(min_length=1, max_length=64)
    storyboard: StoryboardDocument
    storyboard_version_id: UUID
    character_bible_version_id: UUID
    style_bible_version_id: UUID
    character_bible: CharacterBibleDocument
    style_bible: StyleBibleDocument
    character_tag_bundle_version_id: UUID
    character_tags: CharacterTagBundleDocument
    target_prompt_package_ids: dict[str, UUID]
    provider_model_id: str = Field(min_length=1, max_length=100)
    layout_snapshot: dict[str, object]


class PromptCharacterBlock(ContractModel):
    character_id: UUID
    tag_set_id: UUID
    fixed_tags: list[str] = Field(min_length=1, max_length=60)
    fixed_tags_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    variable_tags: list[str] = Field(default_factory=list, max_length=60)

    @model_validator(mode="after")
    def unique_tags(self) -> PromptCharacterBlock:
        _validate_tag_list(self.fixed_tags, "fixed_tags")
        _validate_tag_list(self.variable_tags, "variable_tags")
        return self


class PromptLayoutBinding(ContractModel):
    page_layout_draft_id: UUID
    page_layout_draft_version_id: UUID
    layout_version: int = Field(ge=1)
    layout_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layout_approval_id: UUID
    layout_approval_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_id: UUID
    frame_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimension_selection_id: UUID
    dimension_selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_width: int = Field(ge=64, le=16_384)
    selected_height: int = Field(ge=64, le=16_384)
    expected_crop_ratio: float = Field(ge=0, le=1)
    dimension_rule_version: str = Field(min_length=1, max_length=64)
    capability_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromptPackageDocument(ContractModel):
    prompt_package_id: UUID
    panel_id: UUID
    base_visual_tags: list[str] = Field(min_length=1, max_length=80)
    character_blocks: list[PromptCharacterBlock] = Field(default_factory=list, max_length=20)
    style_tags: list[str] = Field(default_factory=list, max_length=80)
    negative_tags: list[str] = Field(default_factory=list, max_length=100)
    compiled_prompt: str = Field(min_length=1, max_length=12_000)
    compiled_negative_prompt: str = Field(min_length=1, max_length=12_000)
    compiled_prompt_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    compiled_negative_prompt_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    layout_binding: PromptLayoutBinding | None = None
    structured_package: PromptPackage | None = None


class PromptBundleDocument(ContractModel):
    schema_version: str = Field(pattern=r"^1\.[012]$")
    storyboard_version_id: UUID
    character_bible_version_id: UUID
    style_bible_version_id: UUID
    character_tag_bundle_version_id: UUID
    text_model_profile_id: str = Field(min_length=1, max_length=64)
    text_model_config_revision: int = Field(ge=1)
    text_model_name: str = Field(min_length=1, max_length=200)
    prompt_template_version: str = Field(min_length=1, max_length=100)
    provider_model_id: str = Field(min_length=1, max_length=100)
    layout_snapshot_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    packages: list[PromptPackageDocument] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_packages(self) -> PromptBundleDocument:
        package_ids = [item.prompt_package_id for item in self.packages]
        panel_ids = [item.panel_id for item in self.packages]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("prompt package ids must be unique")
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("each panel must have one prompt package")
        if self.schema_version in {"1.1", "1.2"} and (
            self.layout_snapshot_sha256 is None
            or any(package.layout_binding is None for package in self.packages)
        ):
            raise ValueError("schema 1.1+ requires an approved layout binding per package")
        if self.schema_version == "1.2" and any(
            package.structured_package is None for package in self.packages
        ):
            raise ValueError("schema 1.2 requires a structured PromptPackage v2 per panel")
        if self.schema_version in {"1.0", "1.1"} and any(
            package.structured_package is not None for package in self.packages
        ):
            raise ValueError("legacy flat prompt bundles cannot contain PromptPackage v2")
        if self.schema_version == "1.0" and (
            self.layout_snapshot_sha256 is not None
            or any(package.layout_binding is not None for package in self.packages)
        ):
            raise ValueError("schema 1.0 cannot contain v0.3 layout bindings")
        return self
