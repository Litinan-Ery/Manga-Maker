from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CharacterProfile(ContractModel):
    character_id: UUID
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    narrative_role: str = Field(min_length=1, max_length=200)
    age_range: str = Field(min_length=1, max_length=100)
    face_shape: str = Field(min_length=1, max_length=200)
    hair: str = Field(min_length=1, max_length=300)
    body_type: str = Field(min_length=1, max_length=200)
    outfit: list[str] = Field(min_length=1, max_length=30)
    signature_features: list[str] = Field(min_length=1, max_length=30)
    variable_features: list[str] = Field(default_factory=list, max_length=30)
    forbidden_changes: list[str] = Field(min_length=1, max_length=30)
    props: list[str] = Field(default_factory=list, max_length=30)
    relationships: list[str] = Field(default_factory=list, max_length=50)
    expression_range: list[str] = Field(min_length=1, max_length=30)
    positive_prompt_fragment: str = Field(min_length=1, max_length=3000)
    negative_prompt_fragment: str = Field(min_length=1, max_length=2000)
    reference_asset_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_lists(self) -> CharacterProfile:
        for field_name in (
            "aliases",
            "outfit",
            "signature_features",
            "variable_features",
            "forbidden_changes",
            "props",
            "relationships",
            "expression_range",
            "reference_asset_ids",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        return self


class CharacterBibleDocument(ContractModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    character_bible_id: UUID
    storyboard_version_id: UUID
    characters: list[CharacterProfile] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique_characters(self) -> CharacterBibleDocument:
        character_ids = [character.character_id for character in self.characters]
        names = [character.name.casefold() for character in self.characters]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("character ids must be unique")
        if len(names) != len(set(names)):
            raise ValueError("character names must be unique")
        return self


class StyleBibleDocument(ContractModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    style_bible_id: UUID
    storyboard_version_id: UUID
    summary: str = Field(min_length=1, max_length=1000)
    line_art: str = Field(min_length=1, max_length=500)
    screentone: str = Field(min_length=1, max_length=500)
    lighting: str = Field(min_length=1, max_length=500)
    background_density: str = Field(min_length=1, max_length=500)
    whitespace: str = Field(min_length=1, max_length=500)
    camera_language: str = Field(min_length=1, max_length=1000)
    positive_prompt_fragment: str = Field(min_length=1, max_length=3000)
    negative_prompt_fragment: str = Field(min_length=1, max_length=2000)
    prohibited_elements: list[str] = Field(min_length=1, max_length=50)
    reference_asset_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_lists(self) -> StyleBibleDocument:
        if len(self.prohibited_elements) != len(set(self.prohibited_elements)):
            raise ValueError("prohibited elements must not contain duplicates")
        if len(self.reference_asset_ids) != len(set(self.reference_asset_ids)):
            raise ValueError("reference asset ids must not contain duplicates")
        return self


def character_approval_issues(document: CharacterBibleDocument) -> list[str]:
    if not document.characters:
        return ["角色设定表至少需要一个角色。"]
    issues: list[str] = []
    for character in document.characters:
        serialized = " ".join(
            [
                character.narrative_role,
                character.age_range,
                character.face_shape,
                character.hair,
                character.body_type,
                *character.outfit,
                *character.signature_features,
                *character.forbidden_changes,
                *character.expression_range,
                character.positive_prompt_fragment,
                character.negative_prompt_fragment,
            ]
        )
        if "待补充" in serialized:
            issues.append(f"角色“{character.name}”仍有待补充字段。")
    return issues


def style_approval_issues(document: StyleBibleDocument) -> list[str]:
    serialized = " ".join(
        [
            document.summary,
            document.line_art,
            document.screentone,
            document.lighting,
            document.background_density,
            document.whitespace,
            document.camera_language,
            document.positive_prompt_fragment,
            document.negative_prompt_fragment,
            *document.prohibited_elements,
        ]
    )
    return ["风格板仍有待补充字段。"] if "待补充" in serialized else []
