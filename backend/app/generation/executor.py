from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import secrets
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, cast

from ..adaptation.models import StoryboardDocument
from ..bibles.models import CharacterBibleDocument, CharacterProfile, StyleBibleDocument
from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..novelai.client import (
    NovelAIAuthenticationError,
    NovelAIClient,
    NovelAIConfiguration,
    NovelAIConfigurationError,
    NovelAIImageRequest,
    NovelAIInsufficientBalanceError,
    NovelAIInvalidRequestError,
    NovelAIPermissionError,
    NovelAIProvider,
    NovelAIRateLimitError,
    NovelAIResponseFormatError,
    NovelAITemporaryError,
    NovelAIUnknownOutcomeError,
    PreciseReferenceInput,
    SecretReader,
)
from ..novelai.contracts import require_model_profile
from ..vault import CredentialVault, VaultLockedError
from .assets import AssetStore
from .models import CompiledGenerationSpec, GenerationSpecDocument, ReferenceUse
from .queue import GenerationQueueService
from .references import ReferencePreparationError, prepare_precise_reference

MAX_PROVIDER_ATTEMPTS_PER_ITEM = 3


class ImageProviderFactory(Protocol):
    def __call__(
        self, configuration: NovelAIConfiguration, secret_reader: SecretReader
    ) -> NovelAIProvider: ...


class RevisionFinalizer(Protocol):
    def finalize_if_completed(self, job_id: str) -> dict[str, Any] | None: ...


def default_image_provider_factory(
    configuration: NovelAIConfiguration, secret_reader: SecretReader
) -> NovelAIProvider:
    return NovelAIClient(configuration, secret_reader)


class GenerationSpecCompiler:
    def __init__(self, database: Database) -> None:
        self.database = database

    def compile(self, claim: dict[str, Any]) -> CompiledGenerationSpec:
        context = self._context(str(claim["attempt_id"]))
        operation = str(context["operation_kind"])
        storyboard = StoryboardDocument.model_validate_json(str(context["storyboard_json"]))
        characters = CharacterBibleDocument.model_validate_json(str(context["character_json"]))
        style = StyleBibleDocument.model_validate_json(str(context["style_json"]))
        panel = next(
            (
                panel
                for page in storyboard.pages
                for panel in page.panels
                if str(panel.panel_id) == str(context["panel_id"])
            ),
            None,
        )
        if panel is None:
            raise ApplicationError(
                "GENERATION_PANEL_NOT_FOUND",
                "固定分镜版本中没有找到目标面板。",
                409,
            )
        matched_characters = match_characters(panel.characters, characters.characters)
        prompt = joined_prompt(
            [
                "black and white manga",
                style.positive_prompt_fragment,
                *(character.positive_prompt_fragment for character in matched_characters),
                panel.visual_prompt,
                str(context["edit_prompt"] or "") if operation == "inpaint" else "",
                "no text, no letters, no speech bubbles, no watermark, no logo",
            ]
        )
        negative_prompt = joined_prompt(
            [
                style.negative_prompt_fragment,
                *(character.negative_prompt_fragment for character in matched_characters),
                panel.negative_prompt,
                "color, text, letters, speech bubble, caption, page number, watermark, logo",
            ]
        )
        if len(prompt) > 12_000 or len(negative_prompt) > 12_000:
            raise ApplicationError(
                "GENERATION_PROMPT_TOO_LONG",
                "合并后的面板提示词超过本地安全上限，请先精简设定。",
                422,
            )

        reference_use: ReferenceUse | None = None
        provider_reference: PreciseReferenceInput | None = None
        reference_candidate = (
            first_reference_candidate(matched_characters, style)
            if operation != "inpaint"
            else None
        )
        if reference_candidate is not None:
            reference_asset_id, description = reference_candidate
            profile = require_model_profile(str(context["provider_model_id"]))
            if not profile.supports_precise_reference:
                raise ApplicationError(
                    "GENERATION_REFERENCE_UNSUPPORTED",
                    "已审批设定包含参考图，但所选模型不支持 Precise Reference。",
                    409,
                )
            asset = self._reference_asset(
                str(context["project_id"]),
                str(context["workspace_path"]),
                reference_asset_id,
            )
            prepared = prepare_precise_reference(asset["raw"])
            if prepared.original_sha256 != asset["sha256"]:
                raise ApplicationError(
                    "REFERENCE_ASSET_HASH_MISMATCH",
                    "本地参考图哈希与审批记录不一致。",
                    409,
                )
            reference_use = ReferenceUse(
                reference_asset_id=reference_asset_id,
                original_sha256=prepared.original_sha256,
                prepared_sha256=prepared.prepared_sha256,
                description=description,
                strength=0.7,
                fidelity=0.8,
                prepared_width=prepared.width,
                prepared_height=prepared.height,
            )
            provider_reference = PreciseReferenceInput(
                png_base64=prepared.png_base64,
                description=description,
                strength=0.7,
                fidelity=0.8,
            )

        seed = secrets.randbelow(4_294_967_288)
        spec_id = str(uuid7())
        correlation_id = str(uuid7())
        revision_inputs = self._revision_inputs(context)
        spec_action = {
            "chapter_generate": "generate",
            "panel_reroll": "reroll",
            "page_reroll": "reroll",
            "inpaint": "inpaint",
        }.get(operation)
        if spec_action is None:
            raise ApplicationError(
                "GENERATION_OPERATION_INVALID", "生成任务操作类型无效。", 409
            )
        document = GenerationSpecDocument(
            schema_version="1.0" if operation == "chapter_generate" else "1.1",
            spec_id=spec_id,
            project_id=str(context["project_id"]),
            chapter_id=str(context["chapter_id"]),
            job_id=str(context["job_id"]),
            item_id=str(context["item_id"]),
            attempt_id=str(context["attempt_id"]),
            correlation_id=correlation_id,
            panel_id=str(context["panel_id"]),
            storyboard_version_id=str(context["storyboard_version_id"]),
            character_bible_version_id=str(context["character_bible_version_id"]),
            style_bible_version_id=str(context["style_bible_version_id"]),
            provider_model_id=str(context["provider_model_id"]),
            mapping_version=str(context["mapping_version"]),
            contract_sha256=str(context["contract_sha256"]),
            action=spec_action,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=832,
            height=1216,
            steps=28,
            scale=5.0,
            sampler="k_euler_ancestral",
            noise_schedule="karras",
            seed=seed,
            references=[reference_use] if reference_use is not None else [],
            parent_asset_version_id=revision_inputs["parent_asset_version_id"],
            parent_image_sha256=revision_inputs["parent_image_sha256"],
            mask_asset_id=revision_inputs["mask_asset_id"],
            mask_sha256=revision_inputs["mask_sha256"],
            edit_prompt=(
                str(context["edit_prompt"]) if context["edit_prompt"] is not None else None
            ),
            inpaint_strength=(
                float(context["inpaint_strength"])
                if context["inpaint_strength"] is not None
                else None
            ),
            prompt_source=(
                "approved_storyboard_and_bibles_plus_user_edit"
                if operation == "inpaint"
                else "approved_storyboard_and_bibles"
            ),
        )
        return CompiledGenerationSpec(
            document=document,
            provider_request=self._provider_request(
                document,
                provider_reference,
                source_image_base64=revision_inputs["source_image_base64"],
                mask_base64=revision_inputs["mask_base64"],
            ),
        )

    def configuration_for_attempt(self, attempt_id: str) -> NovelAIConfiguration:
        context = self._context(attempt_id)
        credential_profile_id = str(context["credential_profile_id"])
        if not credential_profile_id:
            raise ApplicationError(
                "GENERATION_CREDENTIAL_SNAPSHOT_MISSING",
                "该旧队列没有冻结 NovelAI 凭证引用，请取消并重新创建。",
                409,
            )
        return NovelAIConfiguration(
            provider_model_id=str(context["provider_model_id"]),
            credential_profile_id=credential_profile_id,
            timeout_seconds=float(context["timeout_seconds"]),
        )

    def _context(self, attempt_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT ga.attempt_id, gi.item_id, gi.panel_id,
                       gi.operation_kind, gi.parent_asset_version_id,
                       gi.mask_asset_id, gi.edit_prompt, gi.inpaint_strength,
                       gj.job_id,
                       gj.project_id, gj.chapter_id, gj.storyboard_version_id,
                       gj.character_bible_version_id, gj.style_bible_version_id,
                       gj.provider_model_id, gj.mapping_version, gj.contract_sha256,
                       gj.credential_profile_id, gj.timeout_seconds,
                       sv.document_json AS storyboard_json,
                       cbv.document_json AS character_json,
                       sbv.document_json AS style_json,
                       p.workspace_path
                FROM generation_attempts ga
                JOIN generation_job_items gi ON gi.item_id = ga.item_id
                JOIN generation_jobs gj ON gj.job_id = gi.job_id
                JOIN storyboard_versions sv
                  ON sv.storyboard_version_id = gj.storyboard_version_id
                JOIN character_bible_versions cbv
                  ON cbv.character_bible_version_id = gj.character_bible_version_id
                JOIN style_bible_versions sbv
                  ON sbv.style_bible_version_id = gj.style_bible_version_id
                JOIN projects p ON p.project_id = gj.project_id
                WHERE ga.attempt_id = ? AND ga.status = 'running'
                """,
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "GENERATION_ATTEMPT_NOT_RUNNING", "生成尝试不存在或已经结束。", 409
            )
        return cast(sqlite3.Row, row)

    def _revision_inputs(self, context: sqlite3.Row) -> dict[str, str | None]:
        parent_asset_version_id = context["parent_asset_version_id"]
        if parent_asset_version_id is None:
            return {
                "parent_asset_version_id": None,
                "parent_image_sha256": None,
                "mask_asset_id": None,
                "mask_sha256": None,
                "source_image_base64": None,
                "mask_base64": None,
            }
        with self.database.reader() as connection:
            parent = connection.execute(
                """
                SELECT av.image_sha256, av.original_relative_path, p.workspace_path
                FROM asset_versions av
                JOIN projects p ON p.project_id = av.project_id
                WHERE av.asset_version_id = ? AND av.project_id = ?
                  AND av.panel_id = ? AND av.status = 'ready'
                """,
                (
                    str(parent_asset_version_id),
                    str(context["project_id"]),
                    str(context["panel_id"]),
                ),
            ).fetchone()
            mask = None
            if context["mask_asset_id"] is not None:
                mask = connection.execute(
                    """
                    SELECT sha256, relative_path FROM mask_assets
                    WHERE mask_asset_id = ? AND project_id = ?
                      AND panel_id = ? AND parent_asset_version_id = ?
                    """,
                    (
                        str(context["mask_asset_id"]),
                        str(context["project_id"]),
                        str(context["panel_id"]),
                        str(parent_asset_version_id),
                    ),
                ).fetchone()
        if parent is None:
            raise ApplicationError(
                "REVISION_PARENT_ASSET_INVALID", "冻结的父素材不存在或已损坏。", 409
            )
        workspace = Path(str(parent["workspace_path"])).resolve()
        parent_path = (workspace / str(parent["original_relative_path"])).resolve()
        if not parent_path.is_relative_to(workspace) or not parent_path.is_file():
            raise ApplicationError(
                "REVISION_PARENT_ASSET_FILE_MISSING", "冻结的父素材文件缺失。", 409
            )
        parent_raw = parent_path.read_bytes()
        if hashlib.sha256(parent_raw).hexdigest() != str(parent["image_sha256"]):
            raise ApplicationError(
                "REVISION_PARENT_ASSET_HASH_MISMATCH", "冻结的父素材哈希不一致。", 409
            )
        result: dict[str, str | None] = {
            "parent_asset_version_id": str(parent_asset_version_id),
            "parent_image_sha256": str(parent["image_sha256"]),
            "mask_asset_id": None,
            "mask_sha256": None,
            "source_image_base64": None,
            "mask_base64": None,
        }
        if str(context["operation_kind"]) != "inpaint":
            return result
        if mask is None:
            raise ApplicationError("MASK_NOT_FOUND", "冻结的局部重绘蒙版不存在。", 409)
        mask_path = (workspace / str(mask["relative_path"])).resolve()
        if not mask_path.is_relative_to(workspace) or not mask_path.is_file():
            raise ApplicationError("MASK_FILE_MISSING", "冻结的蒙版文件缺失。", 409)
        mask_raw = mask_path.read_bytes()
        if hashlib.sha256(mask_raw).hexdigest() != str(mask["sha256"]):
            raise ApplicationError("MASK_HASH_MISMATCH", "冻结的蒙版哈希不一致。", 409)
        result.update(
            {
                "mask_asset_id": str(context["mask_asset_id"]),
                "mask_sha256": str(mask["sha256"]),
                "source_image_base64": base64.b64encode(parent_raw).decode("ascii"),
                "mask_base64": base64.b64encode(mask_raw).decode("ascii"),
            }
        )
        return result

    def _reference_asset(
        self, project_id: str, workspace_path: str, reference_asset_id: str
    ) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """
                SELECT sha256, relative_path FROM reference_assets
                WHERE project_id = ? AND reference_asset_id = ? AND rights_confirmed = 1
                """,
                (project_id, reference_asset_id),
            ).fetchone()
        if row is None:
            raise ApplicationError(
                "REFERENCE_ASSET_NOT_FOUND", "已审批参考图不存在或没有授权确认。", 409
            )
        workspace = Path(workspace_path).resolve()
        path = (workspace / str(row["relative_path"])).resolve()
        if not path.is_relative_to(workspace) or not path.is_file():
            raise ApplicationError(
                "REFERENCE_ASSET_FILE_MISSING", "已审批参考图文件缺失或路径无效。", 409
            )
        return {"raw": path.read_bytes(), "sha256": str(row["sha256"])}

    @staticmethod
    def _provider_request(
        document: GenerationSpecDocument,
        reference: PreciseReferenceInput | None,
        *,
        source_image_base64: str | None,
        mask_base64: str | None,
    ) -> NovelAIImageRequest:
        return NovelAIImageRequest(
            correlation_id=document.correlation_id,
            provider_model_id=document.provider_model_id,
            prompt=document.prompt,
            negative_prompt=document.negative_prompt,
            width=document.width,
            height=document.height,
            steps=document.steps,
            scale=document.scale,
            seed=document.seed,
            sampler=document.sampler,
            noise_schedule=document.noise_schedule,
            precise_reference=reference,
            action="infill" if document.action == "inpaint" else "generate",
            source_image_base64=source_image_base64,
            mask_base64=mask_base64,
            inpaint_strength=document.inpaint_strength,
        )


class GenerationExecutor:
    def __init__(
        self,
        database: Database,
        queue: GenerationQueueService,
        vault: CredentialVault,
        assets: AssetStore,
        *,
        provider_factory: ImageProviderFactory = default_image_provider_factory,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        revision_finalizer: RevisionFinalizer | None = None,
    ) -> None:
        self.database = database
        self.queue = queue
        self.vault = vault
        self.assets = assets
        self.compiler = GenerationSpecCompiler(database)
        self.provider_factory = provider_factory
        self.retry_sleep = retry_sleep
        self.revision_finalizer = revision_finalizer
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, job_id: str) -> bool:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return False
        task = asyncio.create_task(self.run_until_blocked(job_id))
        self._tasks[job_id] = task

        def on_done(completed: asyncio.Task[None]) -> None:
            self._task_done(job_id, completed)

        task.add_done_callback(on_done)
        return True

    async def run_until_blocked(self, job_id: str) -> None:
        job = self._job_by_id(job_id)
        maximum_iterations = int(job["max_calls"]) + int(job["panel_count"]) + 1
        for _ in range(maximum_iterations):
            outcome = await self.execute_next(job_id)
            if outcome in {"completed", "retry"}:
                continue
            return

    async def execute_next(self, job_id: str) -> str:
        claim = self.queue.claim_next(job_id)
        if claim is None:
            return "blocked"
        attempt_id = str(claim["attempt_id"])
        try:
            compiled = self.compiler.compile(claim)
            spec_sha256 = self.assets.persist_spec(compiled.document)
            configuration = self.compiler.configuration_for_attempt(attempt_id)
        except (ApplicationError, NovelAIConfigurationError, ReferencePreparationError):
            self.queue.fail_attempt(
                attempt_id, error_code="SPEC_COMPILATION_FAILED", outcome_unknown=False
            )
            return "failed"

        try:
            secret_value: str | None = self.vault.get_secret(
                configuration.credential_profile_id
            )

            def frozen_secret_reader(profile_id: str) -> str:
                if (
                    secret_value is None
                    or profile_id != configuration.credential_profile_id
                ):
                    raise KeyError(profile_id)
                return secret_value

            provider = self.provider_factory(configuration, frozen_secret_reader)
        except (NovelAIConfigurationError, VaultLockedError, KeyError) as exc:
            self.queue.fail_attempt(
                attempt_id,
                error_code=provider_error_code(exc),
                outcome_unknown=False,
            )
            return "failed"
        except Exception:
            self.queue.fail_attempt(
                attempt_id,
                error_code="PROVIDER_CONFIGURATION_INVALID",
                outcome_unknown=False,
            )
            return "failed"

        if not self.queue.mark_provider_request_started(attempt_id):
            secret_value = None
            return "blocked"
        try:
            generated = await provider.generate_image(compiled.provider_request)
        except NovelAITemporaryError:
            if int(claim["attempt_number"]) < MAX_PROVIDER_ATTEMPTS_PER_ITEM:
                self.queue.requeue_attempt(attempt_id, error_code="PROVIDER_TEMPORARY")
                await self.retry_sleep(0.5 * (2 ** (int(claim["attempt_number"]) - 1)))
                return "retry"
            self.queue.fail_attempt(
                attempt_id, error_code="PROVIDER_TEMPORARY_EXHAUSTED", outcome_unknown=False
            )
            return "failed"
        except (NovelAIUnknownOutcomeError, NovelAIResponseFormatError):
            self.queue.fail_attempt(
                attempt_id, error_code="UNKNOWN_PROVIDER_OUTCOME", outcome_unknown=True
            )
            return "needs_review"
        except (
            NovelAIAuthenticationError,
            NovelAIPermissionError,
            NovelAIInsufficientBalanceError,
            NovelAIRateLimitError,
            NovelAIInvalidRequestError,
            NovelAIConfigurationError,
            VaultLockedError,
            KeyError,
        ) as exc:
            self.queue.fail_attempt(
                attempt_id,
                error_code=provider_error_code(exc),
                outcome_unknown=False,
            )
            return "failed"
        except Exception:
            self.queue.fail_attempt(
                attempt_id, error_code="UNKNOWN_PROVIDER_OUTCOME", outcome_unknown=True
            )
            return "needs_review"
        finally:
            secret_value = None

        try:
            self.assets.register_generated_image(
                compiled.document,
                generated,
                spec_sha256=spec_sha256,
                recorded_cost_anlas=None,
            )
        except Exception as exc:
            self.queue.fail_attempt(
                attempt_id,
                error_code=local_asset_error_code(exc),
                outcome_unknown=True,
            )
            return "needs_review"
        if self.revision_finalizer is not None:
            try:
                self.revision_finalizer.finalize_if_completed(job_id)
            except Exception:
                self.queue.mark_job_needs_review(job_id, "PAGE_REVISION_FINALIZATION_FAILED")
                return "needs_review"
        return "completed"

    async def shutdown(self) -> None:
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    def _job_by_id(self, job_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError("GENERATION_JOB_NOT_FOUND", "没有找到该生成队列。", 404)
        return cast(sqlite3.Row, row)

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(job_id, None)
        if not task.cancelled():
            task.exception()


def match_characters(
    panel_names: list[str], profiles: list[CharacterProfile]
) -> list[CharacterProfile]:
    wanted = {name.strip().casefold() for name in panel_names if name.strip()}
    return [
        profile
        for profile in profiles
        if profile.name.strip().casefold() in wanted
        or any(alias.strip().casefold() in wanted for alias in profile.aliases)
    ]


def first_reference_candidate(
    characters: list[CharacterProfile], style: StyleBibleDocument
) -> tuple[str, str] | None:
    for character in characters:
        if character.reference_asset_ids:
            return str(character.reference_asset_ids[0]), "character"
    if style.reference_asset_ids:
        return str(style.reference_asset_ids[0]), "style"
    return None


def joined_prompt(parts: list[str]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = " ".join(part.split()).strip(" ,")
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return ", ".join(result)


def provider_error_code(exc: Exception) -> str:
    if isinstance(exc, NovelAIAuthenticationError):
        return "PROVIDER_UNAUTHORIZED"
    if isinstance(exc, NovelAIPermissionError):
        return "PROVIDER_FORBIDDEN"
    if isinstance(exc, NovelAIInsufficientBalanceError):
        return "PROVIDER_QUOTA"
    if isinstance(exc, NovelAIRateLimitError):
        return "PROVIDER_RATE_LIMITED"
    if isinstance(exc, NovelAIInvalidRequestError):
        return "PROVIDER_REJECTED"
    if isinstance(exc, VaultLockedError):
        return "VAULT_LOCKED"
    if isinstance(exc, KeyError):
        return "CREDENTIAL_PROFILE_NOT_FOUND"
    return "PROVIDER_CONFIGURATION_INVALID"


def local_asset_error_code(exc: Exception) -> str:
    if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
        return "LOCAL_STORAGE_FULL"
    return "LOCAL_ASSET_COMMIT_FAILED"
