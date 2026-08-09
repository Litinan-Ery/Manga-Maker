from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request

from ..recovery import RecoveryService
from ..security import session_headers

router = APIRouter(prefix="/api/v1/system/recovery", tags=["system"])
Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


def service(request: Request) -> RecoveryService:
    return cast(RecoveryService, request.app.state.recovery)


@router.get("")
def latest_recovery(request: Request) -> dict[str, Any]:
    return service(request).latest()


@router.post("")
def run_recovery_check(request: Request, headers: Headers) -> dict[str, Any]:
    request.app.state.local_session.verify(*headers)
    return service(request).run_manual_check()
