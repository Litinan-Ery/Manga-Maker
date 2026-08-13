from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads(
    (ROOT / "contracts" / "novelai" / "image-api.contract.json").read_text(encoding="utf-8")
)
CAPABILITY = json.loads(
    (ROOT / "contracts" / "novelai" / "v4-multi-character.capability.json").read_text(
        encoding="utf-8"
    )
)


def swagger_multi_character_shape(swagger: dict[str, Any]) -> dict[str, list[str]]:
    definitions = swagger["definitions"]
    return {
        "v4_condition_fields": sorted(definitions["image.V4ConditionInput"]["properties"]),
        "v4_external_caption_fields": sorted(definitions["image.V4ExternalCaption"]["properties"]),
        "v4_character_caption_fields": sorted(
            definitions["image.V4ExternalCharacterCaption"]["properties"]
        ),
        "coordinate_fields": sorted(definitions["image.Coordinates"]["properties"]),
    }


def test_checked_in_capability_is_bound_to_the_audited_contract() -> None:
    assert CAPABILITY["source_url"] == CONTRACT["source_url"]
    assert CAPABILITY["contract_sha256"] == CONTRACT["sha256"]
    assert CAPABILITY["mapping_version"] == CONTRACT["mapping_version"]
    assert (
        CAPABILITY["manga_maker_v03_character_limit"]
        <= CAPABILITY["official_documented_character_limit"]
    )
    assert CAPABILITY["runtime_refresh"] is False


def test_swagger_shape_diff_reports_a_removed_multi_character_field() -> None:
    swagger = {
        "definitions": {
            "image.V4ConditionInput": {
                "properties": {
                    "caption": {},
                    "legacy_uc": {},
                    "use_coords": {},
                    "use_order": {},
                }
            },
            "image.V4ExternalCaption": {"properties": {"base_caption": {}, "char_captions": {}}},
            "image.V4ExternalCharacterCaption": {"properties": {"centers": {}, "char_caption": {}}},
            "image.Coordinates": {"properties": {"x": {}, "y": {}}},
        }
    }
    expected = {
        key: sorted(CAPABILITY[key])
        for key in (
            "v4_condition_fields",
            "v4_external_caption_fields",
            "v4_character_caption_fields",
            "coordinate_fields",
        )
    }
    assert swagger_multi_character_shape(swagger) == expected

    del swagger["definitions"]["image.V4ExternalCharacterCaption"]["properties"]["centers"]
    assert swagger_multi_character_shape(swagger) != expected


def test_audited_contract_hash_is_a_sha256_not_a_runtime_token() -> None:
    digest = CAPABILITY["contract_sha256"]
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)
    serialized = json.dumps(CAPABILITY)
    assert "authorization" not in serialized.casefold()
    assert "token" not in serialized.casefold()
