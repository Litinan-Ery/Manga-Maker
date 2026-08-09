from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.health import router as health_router
from .api.projects import router as projects_router
from .api.vault import router as vault_router
from .config import Settings, get_settings
from .database import Database
from .errors import install_error_handlers
from .ingestion.txt import TxtIngestionService
from .projects import ProjectService
from .security import LocalSession
from .vault import CredentialVault


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.ensure_directories()
    database = Database(resolved_settings.database_path)
    vault = CredentialVault(resolved_settings.vault_path)
    local_session = LocalSession.create()
    projects = ProjectService(database, resolved_settings.projects_dir)
    ingestion = TxtIngestionService(database, projects)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.migrate()
        yield
        vault.lock()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        docs_url="/api/docs" if resolved_settings.environment == "development" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.vault = vault
    app.state.local_session = local_session
    app.state.projects = projects
    app.state.ingestion = ingestion
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(vault_router)
    app.include_router(projects_router)
    install_frontend(app, resolved_settings.frontend_dist_dir)
    return app


def install_frontend(app: FastAPI, frontend_dist_dir: Path) -> None:
    """Serve the built local UI without changing API 404 behavior."""
    index_path = frontend_dist_dir / "index.html"
    assets_path = frontend_dist_dir / "assets"
    if not index_path.is_file() or not assets_path.is_dir():
        return

    app.mount("/assets", StaticFiles(directory=assets_path), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index_path)


app = create_app()
