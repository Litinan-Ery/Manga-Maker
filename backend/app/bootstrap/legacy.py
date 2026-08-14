from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from fastapi import FastAPI

from .container import AppContainer

LEGACY_BINDING_OWNER: Final = "MM-026"
LEGACY_BINDING_DELETE_WHEN: Final = (
    "all v0.2 routes use typed Depends providers, MM-024 fixtures still pass, "
    "and the compatibility allowlist is empty"
)
LEGACY_APP_STATE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "adaptation",
        "asset_library",
        "asset_store",
        "bibles",
        "book_production",
        "continuity",
        "database",
        "exports",
        "generation_executor",
        "generation_queue",
        "ingestion",
        "local_session",
        "novelai",
        "pages",
        "projects",
        "prompting",
        "recovery",
        "revisions",
        "secret_scanner",
        "settings",
        "vault",
    }
)

# This is deliberately file- and attribute-specific. Architecture tests compare it with
# the AST so a legacy route cannot acquire another service-locator dependency unnoticed.
LEGACY_API_APP_STATE_LOOKUPS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "adaptation.py": frozenset({"adaptation", "local_session"}),
        "bibles.py": frozenset({"bibles", "local_session"}),
        "book.py": frozenset({"book_production", "local_session"}),
        "continuity.py": frozenset({"continuity", "local_session"}),
        "exports.py": frozenset({"exports", "local_session"}),
        "generation.py": frozenset(
            {
                "asset_store",
                "generation_executor",
                "generation_queue",
                "local_session",
                "revisions",
            }
        ),
        "health.py": frozenset({"database", "settings", "vault"}),
        "library.py": frozenset({"asset_library", "local_session"}),
        "novelai.py": frozenset({"local_session", "novelai"}),
        "pages.py": frozenset({"local_session", "pages"}),
        "projects.py": frozenset({"ingestion", "local_session", "projects"}),
        "prompting.py": frozenset({"local_session", "prompting"}),
        "recovery.py": frozenset({"local_session", "recovery"}),
        "vault.py": frozenset({"local_session", "vault"}),
    }
)


def install_legacy_compatibility_bindings(app: FastAPI, container: AppContainer) -> None:
    """Expose the exact v0.2 app.state seam until MM-026 migrates route dependencies."""

    app.state.container = container
    app.state.settings = container.settings
    app.state.database = container.database
    app.state.vault = container.vault
    app.state.local_session = container.local_session
    app.state.secret_scanner = container.secret_scanner
    app.state.projects = container.legacy.projects
    app.state.ingestion = container.legacy.ingestion
    app.state.adaptation = container.legacy.adaptation
    app.state.bibles = container.legacy.bibles
    app.state.prompting = container.legacy.prompting
    app.state.continuity = container.legacy.continuity
    app.state.novelai = container.legacy.novelai
    app.state.generation_queue = container.legacy.generation_queue
    app.state.book_production = container.legacy.book_production
    app.state.asset_store = container.legacy.asset_store
    app.state.generation_executor = container.legacy.generation_executor
    app.state.pages = container.legacy.pages
    app.state.asset_library = container.legacy.asset_library
    app.state.revisions = container.legacy.revisions
    app.state.exports = container.legacy.exports
    app.state.recovery = container.legacy.recovery
