from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol, cast

from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..vault import CredentialVault, VaultLockedError
from .client import (
    NovelAIAuthenticationError,
    NovelAIClient,
    NovelAIConfiguration,
    NovelAIConfigurationError,
    NovelAIInsufficientBalanceError,
    NovelAIInvalidRequestError,
    NovelAIOpusRequiredError,
    NovelAIPermissionError,
    NovelAIProvider,
    NovelAIRateLimitError,
    NovelAIResponseFormatError,
    NovelAITemporaryError,
    NovelAIUsageLimitUnavailableError,
    SecretReader,
)
from .contracts import (
    CONTRACT_SHA256,
    MAPPING_VERSION,
    contract_payload,
    require_model_profile,
)


class ProviderFactory(Protocol):
    def __call__(
        self, configuration: NovelAIConfiguration, secret_reader: SecretReader
    ) -> NovelAIProvider: ...


def default_provider_factory(
    configuration: NovelAIConfiguration, secret_reader: SecretReader
) -> NovelAIProvider:
    return NovelAIClient(configuration, secret_reader)


class NovelAIService:
    def __init__(
        self,
        database: Database,
        vault: CredentialVault,
        *,
        provider_factory: ProviderFactory = default_provider_factory,
    ) -> None:
        self.database = database
        self.vault = vault
        self.provider_factory = provider_factory

    def capabilities(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        return contract_payload()

    def save_configuration(
        self,
        project_id: str,
        *,
        provider_model_id: str,
        credential_profile_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self._require_project(project_id)
        try:
            configuration = NovelAIConfiguration(
                provider_model_id=provider_model_id,
                credential_profile_id=credential_profile_id,
                timeout_seconds=timeout_seconds,
            )
            model_profile = require_model_profile(configuration.provider_model_id)
        except (ValueError, NovelAIConfigurationError) as exc:
            raise ApplicationError(
                code="NOVELAI_CONFIGURATION_INVALID",
                message="NovelAI 配置无效或模型不在已审计的能力清单中。",
                status_code=422,
            ) from exc
        credential = self._require_credential_profile(credential_profile_id)
        if credential["provider"] != "novelai":
            raise ApplicationError(
                code="NOVELAI_CREDENTIAL_PROVIDER_MISMATCH",
                message="请选择类型为 NovelAI 的本地凭证。",
                status_code=422,
            )

        with self.database.writer() as connection:
            existing = connection.execute(
                "SELECT revision FROM novelai_configs WHERE project_id = ?", (project_id,)
            ).fetchone()
            revision = int(existing["revision"]) + 1 if existing is not None else 1
            connection.execute(
                """
                INSERT INTO novelai_configs(
                    project_id, model_label, provider_model_id, inpaint_model_id,
                    credential_profile_id, timeout_seconds, contract_sha256,
                    mapping_version, revision, last_connection_status,
                    last_connection_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(project_id) DO UPDATE SET
                    model_label = excluded.model_label,
                    provider_model_id = excluded.provider_model_id,
                    inpaint_model_id = excluded.inpaint_model_id,
                    credential_profile_id = excluded.credential_profile_id,
                    timeout_seconds = excluded.timeout_seconds,
                    contract_sha256 = excluded.contract_sha256,
                    mapping_version = excluded.mapping_version,
                    revision = excluded.revision,
                    last_connection_status = NULL,
                    last_connection_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    model_profile.label,
                    str(model_profile.model),
                    model_profile.inpaint_model_id,
                    credential_profile_id,
                    timeout_seconds,
                    CONTRACT_SHA256,
                    MAPPING_VERSION,
                    revision,
                ),
            )
            self._audit(
                connection,
                project_id,
                "novelai.configuration_saved",
                {
                    "provider_model_id": str(model_profile.model),
                    "credential_profile_id": credential_profile_id,
                    "contract_sha256": CONTRACT_SHA256,
                    "mapping_version": MAPPING_VERSION,
                    "revision": revision,
                },
            )
        return self.get_configuration(project_id)

    def get_configuration(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        row = self._configuration_row(project_id)
        fingerprint: str | None = None
        credential_status = "locked"
        if self.vault.is_unlocked:
            matching = next(
                (
                    profile
                    for profile in self.vault.list_profiles()
                    if profile["profile_id"] == str(row["credential_profile_id"])
                ),
                None,
            )
            if matching is None:
                credential_status = "missing"
            elif matching["provider"] != "novelai":
                credential_status = "provider_mismatch"
                fingerprint = matching["fingerprint"]
            else:
                credential_status = "available"
                fingerprint = matching["fingerprint"]
        return {
            "project_id": project_id,
            "provider": "novelai",
            "model_label": str(row["model_label"]),
            "provider_model_id": str(row["provider_model_id"]),
            "inpaint_model_id": str(row["inpaint_model_id"]),
            "credential_profile_id": str(row["credential_profile_id"]),
            "credential_fingerprint": fingerprint,
            "credential_status": credential_status,
            "timeout_seconds": float(row["timeout_seconds"]),
            "contract_sha256": str(row["contract_sha256"]),
            "mapping_version": str(row["mapping_version"]),
            "revision": int(row["revision"]),
            "last_connection_status": row["last_connection_status"],
            "last_connection_at": row["last_connection_at"],
        }

    async def test_connection(self, project_id: str) -> dict[str, Any]:
        row = self._configuration_row(project_id)
        if (
            str(row["mapping_version"]) != MAPPING_VERSION
            or str(row["contract_sha256"]) != CONTRACT_SHA256
        ):
            self._record_connection_result(
                project_id,
                config_revision=int(row["revision"]),
                result="failed",
                error_code="NOVELAI_CONFIGURATION_STALE",
            )
            raise ApplicationError(
                code="NOVELAI_CONFIGURATION_STALE",
                message="NovelAI 契约已更新，请先重新保存当前模型配置。",
                status_code=409,
            )
        configuration = NovelAIConfiguration(
            provider_model_id=str(row["provider_model_id"]),
            credential_profile_id=str(row["credential_profile_id"]),
            timeout_seconds=float(row["timeout_seconds"]),
        )
        provider = self.provider_factory(configuration, self.vault.get_secret)
        try:
            result = await provider.validate_connection()
            subscription = await provider.get_subscription()
        except Exception as exc:
            mapped = self._provider_error(exc)
            self._record_connection_result(
                project_id,
                config_revision=int(row["revision"]),
                result="failed",
                error_code=mapped.code,
            )
            raise mapped from exc

        model_supports_zero_anlas = require_model_profile(
            str(row["provider_model_id"])
        ).supports_opus_zero_anlas
        model_is_v5 = str(row["provider_model_id"]).startswith("nai-diffusion-5-")
        usage_ready = (
            subscription.v5_allowance_available is True if model_is_v5 else True
        )
        zero_anlas_ready = (
            subscription.opus_active and model_supports_zero_anlas and usage_ready
        )
        self._record_connection_result(
            project_id,
            config_revision=int(row["revision"]),
            result="ok",
            suggestion_count=result.suggestion_count,
            subscription_active=subscription.active,
            subscription_tier=subscription.tier,
            zero_anlas_ready=zero_anlas_ready,
        )
        refreshed = self.get_configuration(project_id)
        return {
            "status": "ok",
            "provider": "novelai",
            "provider_model_id": result.provider_model_id,
            "config_revision": int(row["revision"]),
            "suggestion_count": result.suggestion_count,
            "subscription": subscription.zero_anlas_verification(),
            "model_supports_zero_anlas": model_supports_zero_anlas,
            "zero_anlas_ready": zero_anlas_ready,
            "generated_images": 0,
            "last_connection_at": refreshed["last_connection_at"],
        }

    def invalidate_credential_profile(self, profile_id: str) -> int:
        """Fail closed when a credential value changes behind a frozen job."""

        with self.database.writer() as connection:
            rows = connection.execute(
                """
                SELECT project_id, revision
                FROM novelai_configs
                WHERE credential_profile_id = ?
                """,
                (profile_id,),
            ).fetchall()
            for row in rows:
                project_id = str(row["project_id"])
                previous_revision = int(row["revision"])
                next_revision = previous_revision + 1
                connection.execute(
                    """
                    UPDATE novelai_configs
                    SET revision = ?, last_connection_status = NULL,
                        last_connection_at = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = ? AND revision = ?
                    """,
                    (next_revision, project_id, previous_revision),
                )
                self._audit(
                    connection,
                    project_id,
                    "novelai.credential_binding_invalidated",
                    {
                        "credential_profile_id": profile_id,
                        "previous_revision": previous_revision,
                        "revision": next_revision,
                    },
                )
        return len(rows)

    def _record_connection_result(
        self,
        project_id: str,
        *,
        config_revision: int,
        result: str,
        suggestion_count: int | None = None,
        subscription_active: bool | None = None,
        subscription_tier: int | None = None,
        zero_anlas_ready: bool | None = None,
        error_code: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "config_revision": config_revision,
            "result": result,
            "generated_images": 0,
        }
        if suggestion_count is not None:
            payload["suggestion_count"] = suggestion_count
        if subscription_active is not None:
            payload["subscription_active"] = subscription_active
        if subscription_tier is not None:
            payload["subscription_tier"] = subscription_tier
        if zero_anlas_ready is not None:
            payload["zero_anlas_ready"] = zero_anlas_ready
        if error_code is not None:
            payload["error_code"] = error_code
        with self.database.writer() as connection:
            connection.execute(
                """
                UPDATE novelai_configs
                SET last_connection_status = ?, last_connection_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ? AND revision = ?
                """,
                (result, project_id, config_revision),
            )
            self._audit(connection, project_id, "novelai.connection_tested", payload)

    def _require_credential_profile(self, profile_id: str) -> dict[str, str]:
        try:
            profiles = self.vault.list_profiles()
        except VaultLockedError as exc:
            raise ApplicationError(
                code="VAULT_LOCKED",
                message="请先解锁本地凭证库。",
                status_code=423,
            ) from exc
        matching = next(
            (profile for profile in profiles if profile["profile_id"] == profile_id), None
        )
        if matching is None:
            raise ApplicationError(
                code="CREDENTIAL_PROFILE_NOT_FOUND",
                message="没有找到所选 NovelAI 凭证。",
                status_code=404,
            )
        return matching

    def _require_project(self, project_id: str) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="PROJECT_NOT_FOUND",
                message="没有找到该项目。",
                status_code=404,
            )

    def _configuration_row(self, project_id: str) -> sqlite3.Row:
        self._require_project(project_id)
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM novelai_configs WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="NOVELAI_CONFIGURATION_NOT_FOUND",
                message="该项目尚未配置 NovelAI。",
                status_code=404,
            )
        return cast(sqlite3.Row, row)

    @staticmethod
    def _provider_error(exc: Exception) -> ApplicationError:
        if isinstance(exc, VaultLockedError):
            return ApplicationError("VAULT_LOCKED", "请先解锁本地凭证库。", 423)
        if isinstance(exc, KeyError):
            return ApplicationError(
                "CREDENTIAL_PROFILE_NOT_FOUND", "没有找到所选 NovelAI 凭证。", 404
            )
        if isinstance(exc, NovelAIAuthenticationError):
            return ApplicationError("NOVELAI_AUTHENTICATION_FAILED", "NovelAI Token 无效。", 401)
        if isinstance(exc, NovelAIPermissionError):
            return ApplicationError("NOVELAI_PERMISSION_DENIED", "NovelAI 拒绝了当前凭证。", 403)
        if isinstance(exc, NovelAIInsufficientBalanceError):
            return ApplicationError(
                "NOVELAI_INSUFFICIENT_BALANCE", "NovelAI 余额或订阅权限不足。", 402
            )
        if isinstance(exc, NovelAIOpusRequiredError):
            return ApplicationError(
                "NOVELAI_OPUS_REQUIRED", "零 Anlas 生成需要有效的 NovelAI Opus 订阅。", 409
            )
        if isinstance(exc, NovelAIUsageLimitUnavailableError):
            return ApplicationError(
                "NOVELAI_V5_USAGE_LIMIT_UNAVAILABLE",
                "NovelAI V5 的 Opus 免费使用额度当前不可用。",
                409,
            )
        if isinstance(exc, NovelAIRateLimitError):
            return ApplicationError("NOVELAI_RATE_LIMITED", "NovelAI 当前请求过多。", 429)
        if isinstance(exc, NovelAIInvalidRequestError):
            return ApplicationError(
                "NOVELAI_REQUEST_REJECTED", "NovelAI 拒绝了连接测试参数。", 422
            )
        if isinstance(exc, NovelAITemporaryError):
            return ApplicationError(
                "NOVELAI_TEMPORARILY_UNAVAILABLE", "NovelAI 暂时无法连接，请稍后手动重试。", 503
            )
        if isinstance(exc, NovelAIResponseFormatError):
            return ApplicationError(
                "NOVELAI_RESPONSE_INVALID", "NovelAI 返回了无法识别的连接响应。", 502
            )
        if isinstance(exc, (NovelAIConfigurationError, ValueError)):
            return ApplicationError(
                "NOVELAI_CONFIGURATION_INVALID", "NovelAI 配置无效。", 422
            )
        return ApplicationError(
            "NOVELAI_CONNECTION_FAILED", "NovelAI 连接测试失败，未生成图片。", 502
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(uuid7()),
                project_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
