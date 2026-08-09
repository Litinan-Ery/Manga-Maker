from __future__ import annotations

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
        Image.new("RGB", (request.width, request.height), color=color).save(output, format="PNG")
        return NovelAIGeneratedImage(
            png_bytes=output.getvalue(),
            seed=request.seed,
            index=0,
            width=request.width,
            height=request.height,
        )
