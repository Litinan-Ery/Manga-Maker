from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ContinuityKind = Literal["character", "outfit", "prop", "location", "plot"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContinuityEntry(ContractModel):
    entry_id: UUID
    kind: ContinuityKind
    stable_key: str = Field(min_length=3, max_length=120, pattern=r"^[a-z]+:[a-f0-9:]+$")
    name: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=100)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=50)
    notes: str = Field(default="", max_length=2000)
    source_chapter_ids: list[UUID] = Field(min_length=1, max_length=500)
    source_panel_ids: list[UUID] = Field(default_factory=list, max_length=5000)

    @field_validator("attributes")
    @classmethod
    def bounded_attributes(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or len(key) > 80 or len(item) > 1000 for key, item in value.items()):
            raise ValueError("continuity attributes exceed local bounds")
        return value

    @model_validator(mode="after")
    def unique_sources(self) -> ContinuityEntry:
        if len(self.source_chapter_ids) != len(set(self.source_chapter_ids)):
            raise ValueError("source chapter ids must be unique")
        if len(self.source_panel_ids) != len(set(self.source_panel_ids)):
            raise ValueError("source panel ids must be unique")
        return self


class ContinuityLedgerDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    continuity_ledger_id: UUID
    project_id: UUID
    through_chapter_id: UUID
    through_chapter_ordinal: int = Field(ge=1, le=100_000)
    entries: list[ContinuityEntry] = Field(default_factory=list, max_length=20_000)
    notes: str = Field(default="", max_length=10_000)

    @model_validator(mode="after")
    def unique_entries(self) -> ContinuityLedgerDocument:
        keys = [entry.stable_key for entry in self.entries]
        ids = [entry.entry_id for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("continuity stable keys must be unique")
        if len(ids) != len(set(ids)):
            raise ValueError("continuity entry ids must be unique")
        return self
