from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from backend.app.generation.references import (
    ReferencePreparationError,
    prepare_precise_reference,
)


@pytest.mark.parametrize(
    ("source_size", "expected_size"),
    [
        ((300, 500), (1024, 1536)),
        ((500, 300), (1536, 1024)),
        ((400, 400), (1472, 1472)),
    ],
)
def test_precise_reference_is_safely_padded_to_official_canvas(
    source_size: tuple[int, int], expected_size: tuple[int, int]
) -> None:
    raw = image_bytes(*source_size)
    prepared = prepare_precise_reference(raw)

    assert (prepared.width, prepared.height) == expected_size
    assert prepared.original_sha256 == hashlib.sha256(raw).hexdigest()
    assert prepared.prepared_sha256 == hashlib.sha256(prepared.png_bytes).hexdigest()
    with Image.open(BytesIO(prepared.png_bytes)) as image:
        assert image.format == "PNG"
        assert image.size == expected_size
        assert image.getpixel((0, 0)) == (0, 0, 0)


def test_reference_preparation_rejects_non_image_and_oversized_bytes() -> None:
    with pytest.raises(ReferencePreparationError):
        prepare_precise_reference(b"not-an-image")
    with pytest.raises(ReferencePreparationError):
        prepare_precise_reference(b"x" * (10 * 1024 * 1024 + 1))


def image_bytes(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(220, 210, 200)).save(output, format="PNG")
    return output.getvalue()
