from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    schema_version: int
    vault_configured: bool
    vault_unlocked: bool


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    database = request.app.state.database
    vault = request.app.state.vault
    settings = request.app.state.settings
    database_ok = database.check()
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database="ok" if database_ok else "error",
        schema_version=database.schema_version(),
        vault_configured=vault.is_configured,
        vault_unlocked=vault.is_unlocked,
    )
