from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Header

from .errors import ApplicationError


@dataclass(frozen=True, slots=True)
class LocalSession:
    token: str
    csrf_token: str

    @classmethod
    def create(cls) -> LocalSession:
        return cls(token=secrets.token_urlsafe(32), csrf_token=secrets.token_urlsafe(32))

    def verify(self, session_token: str | None, csrf_token: str | None) -> None:
        if session_token is None or not secrets.compare_digest(session_token, self.token):
            raise ApplicationError(
                code="LOCAL_SESSION_REQUIRED",
                message="本地会话无效，请从 Manga Maker 启动页重新打开。",
                status_code=401,
            )
        if csrf_token is None or not secrets.compare_digest(csrf_token, self.csrf_token):
            raise ApplicationError(
                code="CSRF_TOKEN_REQUIRED",
                message="请求校验失败，请刷新页面后重试。",
                status_code=403,
            )


def session_headers(
    x_manga_maker_session: str | None = Header(default=None),
    x_csrf_token: str | None = Header(default=None),
) -> tuple[str | None, str | None]:
    return x_manga_maker_session, x_csrf_token
