from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, SecretStr

from ..errors import ApplicationError
from ..security import session_headers
from ..vault import (
    CredentialVault,
    VaultAlreadyExistsError,
    VaultAuthenticationError,
    VaultLockedError,
    VaultNotConfiguredError,
)

router = APIRouter(prefix="/api/v1/vault", tags=["credentials"])


class VaultStatus(BaseModel):
    configured: bool
    unlocked: bool
    profiles: list[dict[str, str]] = Field(default_factory=list)


class PasswordRequest(BaseModel):
    master_password: SecretStr = Field(min_length=10, max_length=1024)


class CredentialRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    secret: SecretStr = Field(min_length=1, max_length=8192)


Headers = Annotated[tuple[str | None, str | None], Depends(session_headers)]


def verify_session(request: Request, headers: Headers) -> None:
    request.app.state.local_session.verify(*headers)


def vault_status(request: Request) -> VaultStatus:
    vault = cast(CredentialVault, request.app.state.vault)
    profiles = vault.list_profiles() if vault.is_unlocked else []
    return VaultStatus(
        configured=vault.is_configured,
        unlocked=vault.is_unlocked,
        profiles=profiles,
    )


@router.get("", response_model=VaultStatus)
def get_status(request: Request) -> VaultStatus:
    return vault_status(request)


@router.post("", response_model=VaultStatus, status_code=status.HTTP_201_CREATED)
def create_vault(request: Request, body: PasswordRequest, headers: Headers) -> VaultStatus:
    verify_session(request, headers)
    try:
        request.app.state.vault.create(body.master_password.get_secret_value())
    except VaultAlreadyExistsError as exc:
        raise ApplicationError(
            code="VAULT_ALREADY_EXISTS",
            message="本地凭证库已经存在。",
            status_code=409,
        ) from exc
    return vault_status(request)


@router.post("/unlock", response_model=VaultStatus)
def unlock_vault(request: Request, body: PasswordRequest, headers: Headers) -> VaultStatus:
    verify_session(request, headers)
    try:
        request.app.state.vault.unlock(body.master_password.get_secret_value())
    except VaultNotConfiguredError as exc:
        raise ApplicationError(
            code="VAULT_NOT_CONFIGURED",
            message="尚未创建本地凭证库。",
            status_code=404,
        ) from exc
    except VaultAuthenticationError as exc:
        raise ApplicationError(
            code="VAULT_AUTHENTICATION_FAILED",
            message="主密码错误或凭证库已损坏。",
            status_code=401,
        ) from exc
    return vault_status(request)


@router.post("/lock", response_model=VaultStatus)
def lock_vault(request: Request, headers: Headers) -> VaultStatus:
    verify_session(request, headers)
    request.app.state.vault.lock()
    return vault_status(request)


@router.put("/profiles/{profile_id}", response_model=dict[str, str])
def upsert_profile(
    profile_id: str,
    request: Request,
    body: CredentialRequest,
    headers: Headers,
) -> dict[str, str]:
    verify_session(request, headers)
    try:
        vault = cast(CredentialVault, request.app.state.vault)
        secret = body.secret.get_secret_value()
        with vault.credential_transaction():
            vault.validate_secret_update(profile_id, secret)
            existing = next(
                (
                    profile
                    for profile in vault.list_profiles()
                    if profile["profile_id"] == profile_id
                ),
                None,
            )
            if existing is not None:
                request.app.state.novelai.invalidate_credential_profile(profile_id)
            return vault.upsert_secret(
                profile_id,
                provider=body.provider,
                label=body.label,
                secret=secret,
            )
    except VaultLockedError as exc:
        raise ApplicationError(
            code="VAULT_LOCKED",
            message="请先解锁本地凭证库。",
            status_code=423,
        ) from exc
    except ValueError as exc:
        raise ApplicationError(
            code="INVALID_CREDENTIAL_PROFILE",
            message="凭证配置无效。",
            status_code=422,
        ) from exc


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: str, request: Request, headers: Headers) -> None:
    verify_session(request, headers)
    try:
        vault = cast(CredentialVault, request.app.state.vault)
        with vault.credential_transaction():
            existing = next(
                (
                    profile
                    for profile in vault.list_profiles()
                    if profile["profile_id"] == profile_id
                ),
                None,
            )
            if existing is not None:
                request.app.state.novelai.invalidate_credential_profile(profile_id)
            removed = vault.delete_secret(profile_id)
    except VaultLockedError as exc:
        raise ApplicationError(
            code="VAULT_LOCKED",
            message="请先解锁本地凭证库。",
            status_code=423,
        ) from exc
    if not removed:
        raise ApplicationError(
            code="CREDENTIAL_PROFILE_NOT_FOUND",
            message="没有找到该凭证配置。",
            status_code=404,
        )
