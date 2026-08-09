from __future__ import annotations

from dataclasses import dataclass

from .client import NovelAIConnectionResult, NovelAIError


@dataclass(slots=True)
class MockNovelAIClient:
    """Deterministic offline provider used by tests and later queue simulations."""

    provider_model_id: str = "nai-diffusion-4-5-full"
    suggestion_count: int = 1
    failure: NovelAIError | None = None
    connection_calls: int = 0

    async def validate_connection(self) -> NovelAIConnectionResult:
        self.connection_calls += 1
        if self.failure is not None:
            raise self.failure
        return NovelAIConnectionResult(
            provider_model_id=self.provider_model_id,
            suggestion_count=self.suggestion_count,
        )
