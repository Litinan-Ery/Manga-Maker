from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from ..modules.adaptation.public import AdaptationFacade
from ..modules.composition.public import CompositionFacade
from ..modules.layout.public import LayoutFacade
from ..modules.lineage.public import LineageFacade
from ..platform.durable_work.outbox import SQLiteOutboxStore
from ..platform.recovery.coordinator import RecoveryCoordinator
from ..security import session_headers
from .container import AppContainer

SessionHeaders = Annotated[tuple[str | None, str | None], Depends(session_headers)]


def get_app_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


def require_local_session(
    headers: SessionHeaders,
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> None:
    container.local_session.verify(*headers)


def get_composition_facade(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> CompositionFacade:
    return container.composition


def get_adaptation_facade(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> AdaptationFacade:
    return container.adaptation_facade


def get_layout_facade(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> LayoutFacade:
    return container.layout


def get_lineage_facade(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> LineageFacade:
    return container.lineage


def get_outbox_store(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> SQLiteOutboxStore:
    return container.outbox


def get_recovery_coordinator(
    container: Annotated[AppContainer, Depends(get_app_container)],
) -> RecoveryCoordinator:
    return container.recovery_coordinator
