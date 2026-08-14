from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..api.adaptation import router as adaptation_router
from ..api.bibles import router as bibles_router
from ..api.book import router as book_router
from ..api.continuity import router as continuity_router
from ..api.durable_recovery import router as durable_recovery_router
from ..api.events import router as events_router
from ..api.exports import router as exports_router
from ..api.generation import router as generation_router
from ..api.health import router as health_router
from ..api.layouts import router as layouts_router
from ..api.library import router as library_router
from ..api.novelai import router as novelai_router
from ..api.pages import router as pages_router
from ..api.projects import router as projects_router
from ..api.prompting import router as prompting_router
from ..api.recovery import router as recovery_router
from ..api.vault import router as vault_router
from ..config import Settings, get_settings
from ..errors import ApplicationError, install_error_handlers
from .composition_root import build_app_container
from .container import AppContainer
from .installers import ModuleInstaller, default_module_installers


def create_application(settings: Settings | None = None) -> FastAPI:
    container = build_app_container(settings or get_settings())
    installers = default_module_installers()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await start_application(container, installers)
        try:
            yield
        finally:
            await stop_application(container, installers)

    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.app_version,
        docs_url=(
            "/api/docs" if container.settings.environment == "development" else None
        ),
        redoc_url=None,
        lifespan=lifespan,
    )
    for installer in installers:
        installer.install(app, container)
    install_http_entrypoints(app, container)
    install_frontend(app, container.settings.frontend_dist_dir)
    return app


async def start_application(
    container: AppContainer,
    installers: tuple[ModuleInstaller, ...],
) -> None:
    for installer in installers:
        await installer.start(container)


async def stop_application(
    container: AppContainer,
    installers: tuple[ModuleInstaller, ...],
) -> None:
    for installer in reversed(installers):
        await installer.stop(container)


PROJECT_SCOPED_PATH = re.compile(r"^/api/v1/projects/([^/]+)(?:/|$)")
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def install_http_entrypoints(app: FastAPI, container: AppContainer) -> None:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def reject_legacy_project_writes(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        match = PROJECT_SCOPED_PATH.match(request.url.path)
        if match is not None and request.method not in SAFE_HTTP_METHODS:
            try:
                container.local_session.verify(
                    request.headers.get("X-Manga-Maker-Session"),
                    request.headers.get("X-CSRF-Token"),
                )
                container.legacy.projects.require_writable(match.group(1))
            except ApplicationError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"error": {"code": exc.code, "message": exc.message}},
                )
        return await call_next(request)

    for router in (
        health_router,
        events_router,
        durable_recovery_router,
        vault_router,
        projects_router,
        layouts_router,
        adaptation_router,
        bibles_router,
        prompting_router,
        book_router,
        continuity_router,
        novelai_router,
        generation_router,
        pages_router,
        library_router,
        exports_router,
        recovery_router,
    ):
        app.include_router(router)


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
