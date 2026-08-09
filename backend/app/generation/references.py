from __future__ import annotations

import base64
import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_REFERENCE_INPUT_BYTES = 10 * 1024 * 1024
MAX_REFERENCE_INPUT_PIXELS = 25_000_000


class ReferencePreparationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedReference:
    png_bytes: bytes
    png_base64: str
    original_sha256: str
    prepared_sha256: str
    width: int
    height: int


def prepare_precise_reference(raw: bytes) -> PreparedReference:
    if not raw or len(raw) > MAX_REFERENCE_INPUT_BYTES:
        raise ReferencePreparationError("reference image is empty or too large")
    Image.MAX_IMAGE_PIXELS = MAX_REFERENCE_INPUT_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                source.load()
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > MAX_REFERENCE_INPUT_PIXELS:
                    raise ReferencePreparationError("reference image dimensions are invalid")
                converted = ImageOps.exif_transpose(source).convert("RGB")
                width, height = converted.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as exc:
        raise ReferencePreparationError("reference image failed safe decoding") from exc

    ratio = width / height
    if ratio > 1.1:
        target = (1536, 1024)
    elif ratio < 0.9:
        target = (1024, 1536)
    else:
        target = (1472, 1472)
    converted.thumbnail(target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target, color=(0, 0, 0))
    offset = ((target[0] - converted.width) // 2, (target[1] - converted.height) // 2)
    canvas.paste(converted, offset)
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=False)
    prepared = output.getvalue()
    return PreparedReference(
        png_bytes=prepared,
        png_base64=base64.b64encode(prepared).decode("ascii"),
        original_sha256=hashlib.sha256(raw).hexdigest(),
        prepared_sha256=hashlib.sha256(prepared).hexdigest(),
        width=target[0],
        height=target[1],
    )
