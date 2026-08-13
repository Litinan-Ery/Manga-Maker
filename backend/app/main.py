from __future__ import annotations

from fastapi import FastAPI

from .bootstrap.application import create_application
from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the local app through the typed composition root."""

    return create_application(settings)


app = create_app()
