from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("canonical JSON does not allow lone UTF-16 surrogates")


def _utf16_sort_key(value: str) -> bytes:
    _reject_surrogates(value)
    return value.encode("utf-16-be")


def _serialize_number(value: int | float) -> str:
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            as_float = float(value)
            if not math.isfinite(as_float) or int(as_float) != value:
                raise ValueError("canonical JSON integer cannot be represented as IEEE-754")
            if abs(value) >= 1_000_000_000_000_000_000_000:
                return _serialize_number(as_float)
        return str(value)
    if not math.isfinite(value):
        raise ValueError("canonical JSON only supports finite numbers")
    if value == 0:
        return "0"

    rendered = repr(value).lower()
    sign = ""
    if rendered.startswith("-"):
        sign = "-"
        rendered = rendered[1:]

    mantissa, separator, exponent_text = rendered.partition("e")
    exponent = int(exponent_text) if separator else 0
    integer_part, dot, fractional_part = mantissa.partition(".")
    digits = integer_part + (fractional_part if dot else "")
    decimal_position = len(integer_part) + exponent

    if 0 < decimal_position <= 21:
        if len(digits) <= decimal_position:
            body = digits + ("0" * (decimal_position - len(digits)))
        else:
            body = f"{digits[:decimal_position]}.{digits[decimal_position:]}"
    elif -6 < decimal_position <= 0:
        body = f"0.{('0' * -decimal_position)}{digits}"
    else:
        significand = digits[0] if len(digits) == 1 else f"{digits[0]}.{digits[1:]}"
        scientific_exponent = decimal_position - 1
        exponent_sign = "+" if scientific_exponent >= 0 else ""
        body = f"{significand}e{exponent_sign}{scientific_exponent}"
    return sign + body


def _coerce_json(value: Any) -> JsonValue:
    if isinstance(value, BaseModel):
        return _coerce_json(value.model_dump(mode="json", by_alias=True, exclude_none=False))
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            result[key] = _coerce_json(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_coerce_json(item) for item in value]
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _serialize(value: JsonValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _serialize_number(value)
    if isinstance(value, str):
        _reject_surrogates(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return f"[{','.join(_serialize(item) for item in value)}]"
    ordered_keys = sorted(value, key=_utf16_sort_key)
    members = (
        f"{json.dumps(key, ensure_ascii=False)}:{_serialize(value[key])}" for key in ordered_keys
    )
    return f"{{{','.join(members)}}}"


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes shared with the TypeScript consumer.

    Object keys use UTF-16 code-unit order, arrays retain their order, strings are
    emitted as Unicode, and finite numbers use ECMAScript-compatible shortest form.
    """

    return _serialize(_coerce_json(value)).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
