from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from .client import (
    NovelAIConnectionResult,
    NovelAIError,
    NovelAIGeneratedImage,
    NovelAIImageRequest,
)


@dataclass(slots=True)
class MockNovelAIClient:
    """Deterministic offline provider used by tests and later queue simulations."""

    provider_model_id: str = "nai-diffusion-4-5-full"
    suggestion_count: int = 1
    failure: NovelAIError | None = None
    generation_failure: NovelAIError | None = None
    connection_calls: int = 0
    generation_calls: int = 0

    async def validate_connection(self) -> NovelAIConnectionResult:
        self.connection_calls += 1
        if self.failure is not None:
            raise self.failure
        return NovelAIConnectionResult(
            provider_model_id=self.provider_model_id,
            suggestion_count=self.suggestion_count,
        )

    async def generate_image(self, request: NovelAIImageRequest) -> NovelAIGeneratedImage:
        self.generation_calls += 1
        if self.generation_failure is not None:
            raise self.generation_failure
        output = BytesIO()
        color = (
            (request.seed >> 16) & 0x7F,
            (request.seed >> 8) & 0x7F,
            request.seed & 0x7F,
        )
        generated = Image.new("RGB", (request.width, request.height), color=color)
        if request.action == "infill":
            if request.source_image_base64 is None or request.mask_base64 is None:
                raise ValueError("mock inpaint requires source and mask")
            with Image.open(BytesIO(base64.b64decode(request.source_image_base64))) as source:
                base = source.convert("RGB")
            with Image.open(BytesIO(base64.b64decode(request.mask_base64))) as mask_source:
                mask = mask_source.convert("L")
            if base.size != generated.size or mask.size != generated.size:
                raise ValueError("mock inpaint dimensions do not match")
            generated = Image.composite(generated, base, mask)
        generated.save(output, format="PNG")
        return NovelAIGeneratedImage(
            png_bytes=output.getvalue(),
            seed=request.seed,
            index=0,
            width=request.width,
            height=request.height,
        )
