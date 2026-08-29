from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlparse
from uuid import UUID

from pydantic import ValidationError

from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..ingestion.txt import TxtIngestionService
from ..vault import CredentialVault, VaultLockedError
from .models import (
    SourceBeatInput,
    StoryboardDocument,
    StoryboardRequest,
    validate_storyboard_semantics,
)
from .page_policy import (
    STORYBOARD_PAGE_POLICY_VERSION,
    StoryboardPagePolicyError,
    StoryboardPagePolicyFinding,
    storyboard_page_policy_findings,
    validate_storyboard_page_policy,
)
from .text_model import (
    OpenAICompatibleTextModel,
    SecretReader,
    TextModelAuthenticationError,
    TextModelConfiguration,
    TextModelConfigurationError,
    TextModelProvider,
    TextModelRateLimitError,
    TextModelStructuredOutputError,
    TextModelTemporaryError,
)


class ProviderFactory(Protocol):
    def __call__(
        self, configuration: TextModelConfiguration, secret_reader: SecretReader
    ) -> TextModelProvider: ...


def default_provider_factory(
    configuration: TextModelConfiguration, secret_reader: SecretReader
) -> TextModelProvider:
    return OpenAICompatibleTextModel(configuration, secret_reader)


@dataclass(frozen=True, slots=True)
class SourceContext:
    request: StoryboardRequest
    beat_set_id: str
    beat_set_version: int
    fingerprint: str


class AdaptationService:
    def __init__(
        self,
        database: Database,
        ingestion: TxtIngestionService,
        vault: CredentialVault,
        *,
        provider_factory: ProviderFactory = default_provider_factory,
    ) -> None:
        self.database = database
        self.ingestion = ingestion
        self.vault = vault
        self.provider_factory = provider_factory

    def save_configuration(
        self,
        project_id: str,
        *,
        remark_name: str | None = None,
        base_url: str,
        model: str,
        api_key: str | None = None,
        credential_profile_id: str | None = None,
        timeout_seconds: float = 60,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        self._require_project(project_id)
        normalized_remark_name = self._normalize_remark_name(remark_name)
        with self.database.reader() as connection:
            existing = connection.execute(
                "SELECT credential_profile_id FROM text_model_configs WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        existing_profile_id = (
            str(existing["credential_profile_id"]) if existing is not None else None
        )
        profile_id: str
        if api_key is not None:
            profile_id = f"text-model-{project_id}"
        else:
            selected_profile_id = credential_profile_id or existing_profile_id
            if selected_profile_id is None:
                raise ApplicationError(
                    code="TEXT_MODEL_CREDENTIAL_REQUIRED",
                    message="首次保存文本模型配置时必须填写 Key/Password。",
                    status_code=422,
                )
            profile_id = selected_profile_id
        configuration = self._validated_configuration(
            base_url=base_url,
            model=model,
            credential_profile_id=profile_id,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
        )
        previous_secret: str | None = None
        created_profile = False
        if api_key is not None:
            try:
                try:
                    previous_secret = self.vault.get_secret(profile_id)
                except KeyError:
                    created_profile = True
                profile = self.vault.upsert_secret(
                    profile_id,
                    provider="openai-compatible",
                    label="文本模型",
                    secret=api_key,
                )
            except VaultLockedError as exc:
                raise ApplicationError(
                    code="VAULT_LOCKED",
                    message="请先解锁本地凭证库，再保存文本模型密钥。",
                    status_code=423,
                ) from exc
            except ValueError as exc:
                raise ApplicationError(
                    code="INVALID_TEXT_MODEL_CREDENTIAL",
                    message="文本模型密钥不能为空。",
                    status_code=422,
                ) from exc
        else:
            profile = self._require_credential_profile(profile_id)

        try:
            with self.database.writer() as connection:
                existing = connection.execute(
                    "SELECT revision FROM text_model_configs WHERE project_id = ?", (project_id,)
                ).fetchone()
                revision = int(existing["revision"]) + 1 if existing is not None else 1
                connection.execute(
                    """
                    INSERT INTO text_model_configs(
                        project_id, provider, remark_name, base_url, model, credential_profile_id,
                        timeout_seconds, temperature, revision
                    ) VALUES (?, 'openai-compatible', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        provider = excluded.provider,
                        remark_name = excluded.remark_name,
                        base_url = excluded.base_url,
                        model = excluded.model,
                        credential_profile_id = excluded.credential_profile_id,
                        timeout_seconds = excluded.timeout_seconds,
                        temperature = excluded.temperature,
                        revision = excluded.revision,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_id,
                        normalized_remark_name,
                        configuration.base_url,
                        configuration.model,
                        configuration.credential_profile_id,
                        configuration.timeout_seconds,
                        configuration.temperature,
                        revision,
                    ),
                )
                self._audit(
                    connection,
                    project_id,
                    "text_model.configuration_saved",
                    {
                        "provider": "openai-compatible",
                        "remark_name": normalized_remark_name,
                        "endpoint_host": urlparse(configuration.base_url).hostname,
                        "model": configuration.model,
                        "credential_profile_id": configuration.credential_profile_id,
                        "revision": revision,
                        "secret_persisted_locally": True,
                        "secret_updated": api_key is not None,
                    },
                )
        except Exception:
            if api_key is not None:
                if previous_secret is not None:
                    self.vault.upsert_secret(
                        profile_id,
                        provider="openai-compatible",
                        label="文本模型",
                        secret=previous_secret,
                    )
                elif created_profile:
                    self.vault.delete_secret(profile_id)
            raise
        return self._configuration_payload(
            project_id,
            configuration,
            remark_name=normalized_remark_name,
            revision=revision,
            credential_fingerprint=profile["fingerprint"],
        )

    def get_configuration(self, project_id: str) -> dict[str, Any]:
        configuration, revision, remark_name = self._load_configuration_details(project_id)
        fingerprint: str | None = None
        credential_status = "locked"
        if self.vault.is_unlocked:
            profiles = self.vault.list_profiles()
            matching = next(
                (
                    profile
                    for profile in profiles
                    if profile["profile_id"] == configuration.credential_profile_id
                ),
                None,
            )
            if matching is not None:
                fingerprint = matching["fingerprint"]
                credential_status = "available"
            else:
                credential_status = "missing"
        return self._configuration_payload(
            project_id,
            configuration,
            remark_name=remark_name,
            revision=revision,
            credential_fingerprint=fingerprint,
            credential_status=credential_status,
        )

    async def test_configuration(self, project_id: str) -> dict[str, Any]:
        configuration, revision = self._load_configuration(project_id)
        provider = self.provider_factory(configuration, self.vault.get_secret)
        try:
            valid = await provider.validate_configuration()
        except Exception as exc:
            raise self._provider_error(exc) from exc
        if not valid:
            raise ApplicationError(
                code="TEXT_MODEL_CONNECTION_FAILED",
                message="文本模型连接测试未通过。",
                status_code=424,
            )
        with self.database.writer() as connection:
            self._audit(
                connection,
                project_id,
                "text_model.connection_tested",
                {
                    "endpoint_host": urlparse(configuration.base_url).hostname,
                    "model": configuration.model,
                    "config_revision": revision,
                    "result": "ok",
                },
            )
        return {
            "status": "ok",
            "provider": "openai-compatible",
            "endpoint_host": urlparse(configuration.base_url).hostname,
            "model": configuration.model,
            "config_revision": revision,
        }

    def configured_provider(
        self, project_id: str
    ) -> tuple[TextModelProvider, TextModelConfiguration, int]:
        configuration, revision = self._load_configuration(project_id)
        return (
            self.provider_factory(configuration, self.vault.get_secret),
            configuration,
            revision,
        )

    def require_configuration_revision(self, project_id: str, expected_revision: int) -> None:
        self._require_configuration_revision(project_id, expected_revision)

    def provider_error(self, error: Exception) -> ApplicationError:
        return self._provider_error(error)

    async def generate_storyboard(
        self,
        project_id: str,
        chapter_id: str,
        *,
        page_budget: int,
        adaptation_preferences: list[str],
    ) -> dict[str, Any]:
        configuration, config_revision = self._load_configuration(project_id)
        source = self._source_context(
            project_id,
            chapter_id,
            page_budget=page_budget,
            adaptation_preferences=adaptation_preferences,
        )
        provider = self.provider_factory(configuration, self.vault.get_secret)
        try:
            candidate = await provider.generate_storyboard(source.request)
        except Exception as exc:
            raise self._provider_error(exc) from exc

        self._require_configuration_revision(project_id, config_revision)

        refreshed_source = self._source_context(
            project_id,
            chapter_id,
            page_budget=page_budget,
            adaptation_preferences=adaptation_preferences,
        )
        if refreshed_source.fingerprint != source.fingerprint:
            raise ApplicationError(
                code="ADAPTATION_SOURCE_CHANGED",
                message="模型处理期间章节或剧情节拍发生变化，结果未写入。请重新生成。",
                status_code=409,
            )

        provenance = {
            "change_type": "model_generation",
            "provider": candidate.provider,
            "model": candidate.model,
            "endpoint_host": candidate.endpoint_host,
            "prompt_template_version": candidate.prompt_template_version,
            "response_sha256": candidate.response_sha256,
            "input_tokens": candidate.input_tokens,
            "output_tokens": candidate.output_tokens,
            "duration_ms": candidate.duration_ms,
            "repair_attempts": candidate.repair_attempts,
            "config_revision": config_revision,
        }
        version_id = self._persist_document(
            project_id,
            source,
            candidate.document,
            page_budget=page_budget,
            provenance=provenance,
            enforce_page_policy=True,
        )
        return self.get_storyboard_version(project_id, version_id)

    def get_current_storyboard(self, project_id: str, chapter_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sv.storyboard_version_id
                FROM storyboards s
                JOIN storyboard_versions sv ON sv.storyboard_id = s.storyboard_id
                WHERE s.project_id = ? AND s.chapter_id = ? AND sv.is_current = 1
                """,
                (project_id, chapter_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="STORYBOARD_NOT_FOUND",
                message="该章节尚未生成结构化分镜。",
                status_code=404,
            )
        return self.get_storyboard_version(project_id, str(row["storyboard_version_id"]))

    def get_storyboard_version(self, project_id: str, storyboard_version_id: str) -> dict[str, Any]:
        row = self._version_row(project_id, storyboard_version_id)
        document = StoryboardDocument.model_validate_json(str(row["document_json"]))
        stale = self._is_stale(row)
        approved_at = str(row["approved_at"]) if row["approved_at"] is not None else None
        unresolved_count = sum(
            resolution.status == "unresolved" for resolution in document.beat_resolutions
        )
        approval_status = "stale" if stale else "approved" if approved_at else "draft"
        page_policy_findings = storyboard_page_policy_findings(document)
        document_payload = document.model_dump(mode="json")
        if document.schema_version == "1.0":
            for page in cast(list[dict[str, Any]], document_payload["pages"]):
                if page.get("page_type") is None:
                    page.pop("page_type", None)
        return {
            "storyboard_id": str(row["storyboard_id"]),
            "storyboard_version_id": str(row["storyboard_version_id"]),
            "version": int(row["version"]),
            "chapter_id": str(row["chapter_id"]),
            "chapter_version": int(row["chapter_version"]),
            "beat_set_id": str(row["beat_set_id"]),
            "page_budget": int(row["page_budget"]),
            "source_fingerprint": str(row["source_fingerprint"]),
            "document": document_payload,
            "provenance": json.loads(str(row["provenance_json"])),
            "approval_status": approval_status,
            "approval_hash": str(row["approval_hash"]) if row["approval_hash"] else None,
            "approved_at": approved_at,
            "unresolved_count": unresolved_count,
            "page_policy_version": STORYBOARD_PAGE_POLICY_VERSION,
            "page_policy_valid": not page_policy_findings,
            "page_policy_findings": [
                finding.payload() for finding in page_policy_findings
            ],
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
        }

    def revise_storyboard(
        self,
        project_id: str,
        storyboard_version_id: str,
        document: StoryboardDocument,
    ) -> dict[str, Any]:
        parent = self._version_row(project_id, storyboard_version_id)
        if not bool(parent["is_current"]):
            raise ApplicationError(
                code="STORYBOARD_VERSION_NOT_CURRENT",
                message="只能从当前分镜版本创建修改。",
                status_code=409,
            )
        if self._is_stale(parent):
            raise ApplicationError(
                code="STORYBOARD_SOURCE_STALE",
                message="章节或剧情节拍已经变化，请重新生成分镜。",
                status_code=409,
            )
        parent_document = StoryboardDocument.model_validate_json(str(parent["document_json"]))
        if parent_document.schema_version != "1.1" or document.schema_version != "1.1":
            legacy_document = (
                parent_document
                if parent_document.schema_version != "1.1"
                else document
            )
            raise self._page_policy_application_error(
                StoryboardPagePolicyError(
                    storyboard_page_policy_findings(legacy_document)
                )
            )
        source = self._source_context(
            project_id,
            str(parent["chapter_id"]),
            page_budget=int(parent["page_budget"]),
            adaptation_preferences=[],
        )
        normalized_document = document.model_copy(
            update={
                "storyboard_id": UUID(str(parent["storyboard_id"])),
                "chapter_version": int(parent["chapter_version"]),
            }
        )
        try:
            validate_storyboard_semantics(normalized_document, source.request)
            validate_storyboard_page_policy(normalized_document)
        except StoryboardPagePolicyError as exc:
            raise self._page_policy_application_error(exc) from exc
        except ValueError as exc:
            raise ApplicationError(
                code="INVALID_STORYBOARD_REVISION",
                message="分镜修改未通过来源和页数校验。",
                status_code=422,
                details={"problem": str(exc)},
            ) from exc
        version_id = self._persist_document(
            project_id,
            source,
            normalized_document,
            page_budget=int(parent["page_budget"]),
            provenance={
                "change_type": "manual_edit",
                "parent_storyboard_version_id": storyboard_version_id,
            },
            enforce_page_policy=True,
        )
        return self.get_storyboard_version(project_id, version_id)

    def approve_storyboard(self, project_id: str, storyboard_version_id: str) -> dict[str, Any]:
        row = self._version_row(project_id, storyboard_version_id)
        if not bool(row["is_current"]):
            raise ApplicationError(
                code="STORYBOARD_VERSION_NOT_CURRENT",
                message="只能审批当前分镜版本。",
                status_code=409,
            )
        if self._is_stale(row):
            raise ApplicationError(
                code="STORYBOARD_SOURCE_STALE",
                message="章节或剧情节拍已经变化，当前分镜不能审批。",
                status_code=409,
            )
        document = StoryboardDocument.model_validate_json(str(row["document_json"]))
        source = self._source_context(
            project_id,
            str(row["chapter_id"]),
            page_budget=int(row["page_budget"]),
            adaptation_preferences=[],
        )
        try:
            validate_storyboard_semantics(document, source.request)
            validate_storyboard_page_policy(document)
            validate_approval_readiness(document, source.request)
        except StoryboardPagePolicyError as exc:
            raise self._page_policy_application_error(exc) from exc
        except ValueError as exc:
            raise ApplicationError(
                code="STORYBOARD_NOT_APPROVABLE",
                message="分镜尚未满足审批条件。",
                status_code=422,
                details={"problem": str(exc)},
            ) from exc

        approval_hash = hashlib.sha256(
            (
                canonical_json(document.model_dump(mode="json"))
                + str(row["source_fingerprint"])
                + str(row["page_budget"])
            ).encode("utf-8")
        ).hexdigest()
        with self.database.writer() as connection:
            existing = connection.execute(
                """
                SELECT approval_id FROM storyboard_approvals
                WHERE storyboard_version_id = ?
                """,
                (storyboard_version_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO storyboard_approvals(
                        approval_id, storyboard_version_id, approval_hash
                    ) VALUES (?, ?, ?)
                    """,
                    (str(uuid7()), storyboard_version_id, approval_hash),
                )
                self._audit(
                    connection,
                    project_id,
                    "storyboard.approved",
                    {
                        "storyboard_version_id": storyboard_version_id,
                        "approval_hash": approval_hash,
                        "page_policy_version": STORYBOARD_PAGE_POLICY_VERSION,
                    },
                )
        return self.get_storyboard_version(project_id, storyboard_version_id)

    def validate_storyboard(
        self,
        project_id: str,
        storyboard_version_id: str,
    ) -> dict[str, Any]:
        row = self._version_row(project_id, storyboard_version_id)
        document = StoryboardDocument.model_validate_json(str(row["document_json"]))
        findings = storyboard_page_policy_findings(document)
        try:
            source = self._source_context(
                project_id,
                str(row["chapter_id"]),
                page_budget=int(row["page_budget"]),
                adaptation_preferences=[],
            )
            validate_storyboard_semantics(document, source.request)
            validate_approval_readiness(document, source.request)
        except ValueError as exc:
            findings = (
                *findings,
                self._semantic_finding(str(exc)),
            )
        return {
            "contract_version": "1.0",
            "storyboard_version_id": storyboard_version_id,
            "storyboard_schema_version": document.schema_version,
            "page_policy_version": STORYBOARD_PAGE_POLICY_VERSION,
            "valid": not findings,
            "findings": [finding.payload() for finding in findings],
            "external_requests_started": 0,
        }

    def _persist_document(
        self,
        project_id: str,
        source: SourceContext,
        document: StoryboardDocument,
        *,
        page_budget: int,
        provenance: dict[str, Any],
        enforce_page_policy: bool,
    ) -> str:
        with self.database.writer() as connection:
            current_source = connection.execute(
                """
                SELECT c.chapter_id
                FROM source_chapters c
                JOIN source_chapter_sets cs ON cs.chapter_set_id = c.chapter_set_id
                JOIN story_beat_sets sbs ON sbs.chapter_id = c.chapter_id
                WHERE c.chapter_id = ? AND cs.is_current = 1
                  AND sbs.beat_set_id = ? AND sbs.is_current = 1
                """,
                (source.request.chapter_id, source.beat_set_id),
            ).fetchone()
            if current_source is None:
                raise ApplicationError(
                    code="ADAPTATION_SOURCE_CHANGED",
                    message="章节或剧情节拍已变化，分镜结果未写入。",
                    status_code=409,
                )
            storyboard = connection.execute(
                """
                SELECT storyboard_id FROM storyboards
                WHERE project_id = ? AND chapter_id = ?
                """,
                (project_id, source.request.chapter_id),
            ).fetchone()
            if storyboard is None:
                storyboard_id = str(uuid7())
                connection.execute(
                    """
                    INSERT INTO storyboards(storyboard_id, project_id, chapter_id)
                    VALUES (?, ?, ?)
                    """,
                    (storyboard_id, project_id, source.request.chapter_id),
                )
                next_version = 1
            else:
                storyboard_id = str(storyboard["storyboard_id"])
                latest = connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) AS version
                    FROM storyboard_versions WHERE storyboard_id = ?
                    """,
                    (storyboard_id,),
                ).fetchone()
                next_version = int(latest["version"]) + 1
                connection.execute(
                    """
                    UPDATE storyboard_versions SET is_current = 0
                    WHERE storyboard_id = ? AND is_current = 1
                    """,
                    (storyboard_id,),
                )

            normalized_document = document.model_copy(
                update={
                    "storyboard_id": UUID(storyboard_id),
                    "chapter_version": source.request.chapter_version,
                }
            )
            try:
                validate_storyboard_semantics(normalized_document, source.request)
                if enforce_page_policy:
                    validate_storyboard_page_policy(normalized_document)
            except StoryboardPagePolicyError as exc:
                raise ApplicationError(
                    code="INVALID_STORYBOARD_DOCUMENT",
                    message="模型分镜未通过逐页页型与格数校验。",
                    status_code=422,
                    details={
                        "page_policy_version": STORYBOARD_PAGE_POLICY_VERSION,
                        "findings": [finding.payload() for finding in exc.findings],
                    },
                ) from exc
            except ValueError as exc:
                raise ApplicationError(
                    code="INVALID_STORYBOARD_DOCUMENT",
                    message="分镜未通过来源和页数校验。",
                    status_code=422,
                    details={"problem": str(exc)},
                ) from exc
            storyboard_version_id = str(uuid7())
            connection.execute(
                """
                INSERT INTO storyboard_versions(
                    storyboard_version_id, storyboard_id, version, beat_set_id,
                    chapter_version, page_budget, source_fingerprint,
                    document_json, provenance_json, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    storyboard_version_id,
                    storyboard_id,
                    next_version,
                    source.beat_set_id,
                    source.request.chapter_version,
                    page_budget,
                    source.fingerprint,
                    canonical_json(normalized_document.model_dump(mode="json")),
                    canonical_json(provenance),
                ),
            )
            self._audit(
                connection,
                project_id,
                "storyboard.version_created",
                {
                    "storyboard_id": storyboard_id,
                    "storyboard_version_id": storyboard_version_id,
                    "version": next_version,
                    "change_type": provenance.get("change_type"),
                },
            )
        return storyboard_version_id

    @staticmethod
    def _page_policy_application_error(
        error: StoryboardPagePolicyError,
    ) -> ApplicationError:
        upgrade_required = any(
            finding.code == "STORYBOARD_UPGRADE_REQUIRED" for finding in error.findings
        )
        return ApplicationError(
            code=(
                "STORYBOARD_UPGRADE_REQUIRED"
                if upgrade_required
                else "STORYBOARD_PAGE_POLICY_INVALID"
            ),
            message=(
                "Storyboard 1.0 仅供历史只读，请重新生成 1.1 分镜。"
                if upgrade_required
                else "分镜页型或逐页格数不符合审批规则。"
            ),
            status_code=409 if upgrade_required else 422,
            details={
                "page_policy_version": STORYBOARD_PAGE_POLICY_VERSION,
                "findings": [finding.payload() for finding in error.findings],
            },
        )

    @staticmethod
    def _semantic_finding(problem: str) -> StoryboardPagePolicyFinding:
        return StoryboardPagePolicyFinding(
            code="STORYBOARD_PAGE_POLICY_INVALID",
            path="$",
            message=problem,
            page_id=None,
            page_number=None,
            page_type=None,
            panel_count=None,
            minimum_panels=None,
            maximum_panels=None,
        )

    def _source_context(
        self,
        project_id: str,
        chapter_id: str,
        *,
        page_budget: int,
        adaptation_preferences: list[str],
    ) -> SourceContext:
        chapter = self.ingestion.chapter_text(project_id, chapter_id)
        beat_set = self.ingestion.current_story_beats(project_id, chapter_id)
        try:
            request = StoryboardRequest(
                chapter_id=chapter_id,
                chapter_version=int(chapter["chapter_version"]),
                chapter_text=str(chapter["text"]),
                story_beats=[
                    SourceBeatInput(
                        beat_id=str(beat["beat_id"]),
                        anchor_id=str(beat["anchor_id"]),
                        excerpt=str(beat["source_excerpt"]),
                    )
                    for beat in beat_set["beats"]
                ],
                page_budget=page_budget,
                adaptation_preferences=adaptation_preferences,
            )
        except ValidationError as exc:
            raise ApplicationError(
                code="ADAPTATION_INPUT_INVALID",
                message="章节或剧情节拍超出结构化改编限制。",
                status_code=422,
                details={"problems": exc.errors(include_url=False)},
            ) from exc
        fingerprint_payload = {
            "chapter_id": chapter_id,
            "chapter_version": request.chapter_version,
            "chapter_text_sha256": hashlib.sha256(request.chapter_text.encode("utf-8")).hexdigest(),
            "beat_set_id": beat_set["beat_set_id"],
            "beat_set_version": beat_set["beat_set_version"],
            "beats": [
                {
                    "beat_id": beat["beat_id"],
                    "anchor_id": beat["anchor_id"],
                    "excerpt_sha256": beat["excerpt_sha256"],
                }
                for beat in beat_set["beats"]
            ],
        }
        return SourceContext(
            request=request,
            beat_set_id=str(beat_set["beat_set_id"]),
            beat_set_version=int(beat_set["beat_set_version"]),
            fingerprint=hashlib.sha256(
                canonical_json(fingerprint_payload).encode("utf-8")
            ).hexdigest(),
        )

    def _load_configuration(self, project_id: str) -> tuple[TextModelConfiguration, int]:
        configuration, revision, _remark_name = self._load_configuration_details(project_id)
        return configuration, revision

    def _load_configuration_details(
        self, project_id: str
    ) -> tuple[TextModelConfiguration, int, str | None]:
        self._require_project(project_id)
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM text_model_configs WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="TEXT_MODEL_NOT_CONFIGURED",
                message="该项目尚未配置文本模型。",
                status_code=404,
            )
        configuration = self._validated_configuration(
            base_url=str(row["base_url"]),
            model=str(row["model"]),
            credential_profile_id=str(row["credential_profile_id"]),
            timeout_seconds=float(row["timeout_seconds"]),
            temperature=float(row["temperature"]),
        )
        remark_name = str(row["remark_name"]) if row["remark_name"] is not None else None
        return configuration, int(row["revision"]), remark_name

    @staticmethod
    def _normalize_remark_name(remark_name: str | None) -> str | None:
        if remark_name is None:
            return None
        normalized = remark_name.strip()
        if not normalized:
            return None
        if len(normalized) > 200:
            raise ApplicationError(
                code="INVALID_TEXT_MODEL_CONFIGURATION",
                message="备注名称不能超过 200 个字符。",
                status_code=422,
            )
        return normalized

    @staticmethod
    def _validated_configuration(
        *,
        base_url: str,
        model: str,
        credential_profile_id: str,
        timeout_seconds: float,
        temperature: float,
    ) -> TextModelConfiguration:
        try:
            return TextModelConfiguration(
                base_url=base_url.strip(),
                model=model.strip(),
                credential_profile_id=credential_profile_id.strip(),
                timeout_seconds=timeout_seconds,
                temperature=temperature,
            )
        except TextModelConfigurationError as exc:
            raise ApplicationError(
                code="INVALID_TEXT_MODEL_CONFIGURATION",
                message="文本模型配置无效。",
                status_code=422,
                details={"problem": str(exc)},
            ) from exc

    def _require_project(self, project_id: str) -> None:
        with self.database.reader() as connection:
            exists = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if exists is None:
            raise ApplicationError(
                code="PROJECT_NOT_FOUND",
                message="没有找到该项目。",
                status_code=404,
            )

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
                message="没有找到所选文本模型凭证。",
                status_code=422,
            )
        if matching["provider"] != "openai-compatible":
            raise ApplicationError(
                code="CREDENTIAL_PROVIDER_MISMATCH",
                message="所选密钥不是文本模型凭证。",
                status_code=422,
            )
        return matching

    def _require_configuration_revision(self, project_id: str, expected_revision: int) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT revision FROM text_model_configs WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None or int(row["revision"]) != expected_revision:
            raise ApplicationError(
                code="TEXT_MODEL_CONFIGURATION_CHANGED",
                message="文本模型配置在任务执行期间发生变化，结果未写入。请重新生成。",
                status_code=409,
            )

    @staticmethod
    def _configuration_payload(
        project_id: str,
        configuration: TextModelConfiguration,
        *,
        remark_name: str | None,
        revision: int,
        credential_fingerprint: str | None,
        credential_status: str = "available",
    ) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "text_model_profile_id": project_id,
            "provider": "openai-compatible",
            "remark_name": remark_name,
            "url": configuration.base_url,
            "provider_api_url": configuration.base_url,
            "base_url": configuration.base_url,
            "endpoint_host": urlparse(configuration.base_url).hostname,
            "request_model": configuration.model,
            "model_name": configuration.model,
            "model": configuration.model,
            "credential_profile_id": configuration.credential_profile_id,
            "credential_fingerprint": credential_fingerprint,
            "credential_status": credential_status,
            "timeout_seconds": configuration.timeout_seconds,
            "temperature": configuration.temperature,
            "revision": revision,
        }

    def _version_row(self, project_id: str, storyboard_version_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sv.*, s.project_id, s.chapter_id,
                       sa.approval_hash, sa.created_at AS approved_at
                FROM storyboard_versions sv
                JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
                LEFT JOIN storyboard_approvals sa
                  ON sa.storyboard_version_id = sv.storyboard_version_id
                WHERE sv.storyboard_version_id = ? AND s.project_id = ?
                """,
                (storyboard_version_id, project_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                code="STORYBOARD_VERSION_NOT_FOUND",
                message="没有找到该分镜版本。",
                status_code=404,
            )
        return cast(sqlite3.Row, row)

    def _is_stale(self, row: sqlite3.Row) -> bool:
        with self.database.reader() as connection:
            current = connection.execute(
                """
                SELECT 1
                FROM source_chapters c
                JOIN source_chapter_sets cs ON cs.chapter_set_id = c.chapter_set_id
                JOIN story_beat_sets sbs ON sbs.chapter_id = c.chapter_id
                WHERE c.chapter_id = ? AND cs.is_current = 1
                  AND sbs.beat_set_id = ? AND sbs.is_current = 1
                """,
                (row["chapter_id"], row["beat_set_id"]),
            ).fetchone()
        return current is None

    @staticmethod
    def _provider_error(error: Exception) -> ApplicationError:
        if isinstance(error, VaultLockedError):
            return ApplicationError(
                code="VAULT_LOCKED",
                message="请先解锁本地凭证库。",
                status_code=423,
            )
        if isinstance(error, KeyError):
            return ApplicationError(
                code="CREDENTIAL_PROFILE_NOT_FOUND",
                message="文本模型凭证已经不存在。",
                status_code=422,
            )
        if isinstance(error, TextModelAuthenticationError):
            return ApplicationError(
                code="TEXT_MODEL_AUTHENTICATION_FAILED",
                message="文本模型拒绝了当前凭证。",
                status_code=424,
            )
        if isinstance(error, TextModelRateLimitError):
            return ApplicationError(
                code="TEXT_MODEL_RATE_LIMITED",
                message="文本模型当前请求过多，请稍后由你重新触发。",
                status_code=429,
            )
        if isinstance(error, TextModelTemporaryError):
            return ApplicationError(
                code="TEXT_MODEL_TEMPORARY_FAILURE",
                message="文本模型暂时不可用，本次不会自动重试。",
                status_code=503,
            )
        if isinstance(error, TextModelStructuredOutputError):
            return ApplicationError(
                code="TEXT_MODEL_OUTPUT_INVALID",
                message="文本模型输出经过两次修复后仍不符合分镜结构。",
                status_code=422,
            )
        if isinstance(error, TextModelConfigurationError):
            return ApplicationError(
                code="TEXT_MODEL_CONFIGURATION_FAILED",
                message="文本模型配置或响应格式无效。",
                status_code=422,
            )
        return ApplicationError(
            code="TEXT_MODEL_FAILURE",
            message="文本模型调用失败。",
            status_code=502,
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
            (str(uuid7()), project_id, event_type, canonical_json(payload)),
        )


def validate_approval_readiness(document: StoryboardDocument, request: StoryboardRequest) -> None:
    beats = {beat.beat_id: beat for beat in request.story_beats}
    anchors_by_page = {
        page.page_number: {
            anchor_id for panel in page.panels for anchor_id in panel.source_anchor_ids
        }
        for page in document.pages
    }
    for resolution in document.beat_resolutions:
        if resolution.status == "unresolved":
            raise ValueError("unresolved story beats block approval")
        if resolution.status in {"represented", "condensed"}:
            if not resolution.page_numbers:
                raise ValueError("represented or condensed beats must reference a page")
            anchor_id = beats[resolution.beat_id].anchor_id
            if not any(
                anchor_id in anchors_by_page[page_number] for page_number in resolution.page_numbers
            ):
                raise ValueError("resolved story beat is not anchored on its referenced pages")
        if resolution.status == "omitted" and resolution.page_numbers:
            raise ValueError("omitted story beats must not reference a page")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
