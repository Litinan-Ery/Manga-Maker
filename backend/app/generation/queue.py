from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal, cast

from ..adaptation.models import StoryboardDocument
from ..bibles.service import BibleService
from ..database import Database
from ..errors import ApplicationError
from ..ids import uuid7
from ..novelai.contracts import CONTRACT_SHA256, MAPPING_VERSION
from ..prompting.service import PromptingService

JobStatus = Literal[
    "draft",
    "awaiting_approval",
    "queued",
    "running",
    "paused",
    "needs_review",
    "failed",
    "completed",
    "canceled",
]


@dataclass(frozen=True, slots=True)
class PlannedPanel:
    ordinal: int
    page_id: str
    page_number: int
    panel_id: str
    panel_order: int
    cost_ceiling_anlas: int
    prompt_package_id: str
    compiled_prompt: str
    compiled_negative_prompt: str
    compiled_prompt_sha256: str

    def payload(self) -> dict[str, int | str]:
        return {
            "ordinal": self.ordinal,
            "page_id": self.page_id,
            "page_number": self.page_number,
            "panel_id": self.panel_id,
            "panel_order": self.panel_order,
            "cost_ceiling_anlas": self.cost_ceiling_anlas,
            "prompt_package_id": self.prompt_package_id,
            "compiled_prompt": self.compiled_prompt,
            "compiled_negative_prompt": self.compiled_negative_prompt,
            "compiled_prompt_sha256": self.compiled_prompt_sha256,
        }


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    project_id: str
    chapter_id: str
    storyboard_version_id: str
    character_bible_version_id: str
    style_bible_version_id: str
    character_tag_bundle_version_id: str
    prompt_bundle_version_id: str
    text_model_config_revision: int
    novelai_config_revision: int
    provider_model_id: str
    inpaint_model_id: str
    mapping_version: str
    contract_sha256: str
    credential_profile_id: str
    timeout_seconds: float
    panels: tuple[PlannedPanel, ...]
    page_count: int
    per_panel_cost_ceiling_anlas: int
    fingerprint: str

    @property
    def panel_count(self) -> int:
        return len(self.panels)

    @property
    def estimated_cost_upper_anlas(self) -> int:
        return sum(panel.cost_ceiling_anlas for panel in self.panels)

    def payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "chapter_id": self.chapter_id,
            "storyboard_version_id": self.storyboard_version_id,
            "character_bible_version_id": self.character_bible_version_id,
            "style_bible_version_id": self.style_bible_version_id,
            "character_tag_bundle_version_id": self.character_tag_bundle_version_id,
            "prompt_bundle_version_id": self.prompt_bundle_version_id,
            "text_model_config_revision": self.text_model_config_revision,
            "novelai_config_revision": self.novelai_config_revision,
            "provider_model_id": self.provider_model_id,
            "inpaint_model_id": self.inpaint_model_id,
            "mapping_version": self.mapping_version,
            "contract_sha256": self.contract_sha256,
            "credential_profile_id": self.credential_profile_id,
            "timeout_seconds": self.timeout_seconds,
            "page_count": self.page_count,
            "panel_count": self.panel_count,
            "estimated_calls": self.panel_count,
            "per_panel_cost_ceiling_anlas": self.per_panel_cost_ceiling_anlas,
            "estimated_cost_upper_anlas": self.estimated_cost_upper_anlas,
            "cost_basis": "user_confirmed_per_panel_ceiling",
            "cost_notice": (
                "这是用户确认的每格保守预留上限，不是 NovelAI 账户实际扣费预测，"
                "实际成本将在供应商可验证时单独记录。"
            ),
            "plan_fingerprint": self.fingerprint,
            "panels": [panel.payload() for panel in self.panels],
            "external_request_created": False,
        }


class GenerationQueueService:
    def __init__(
        self, database: Database, bibles: BibleService, prompting: PromptingService
    ) -> None:
        self.database = database
        self.bibles = bibles
        self.prompting = prompting

    def estimate(
        self,
        project_id: str,
        chapter_id: str,
        *,
        per_panel_cost_ceiling_anlas: int,
    ) -> dict[str, Any]:
        return self._build_plan(
            project_id,
            chapter_id,
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        ).payload()

    def build_plan(
        self,
        project_id: str,
        chapter_id: str,
        *,
        per_panel_cost_ceiling_anlas: int,
    ) -> GenerationPlan:
        """Build an approved snapshot for a scoped revision without creating a job."""
        return self._build_plan(
            project_id,
            chapter_id,
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        )

    def create_job(
        self,
        project_id: str,
        chapter_id: str,
        *,
        plan_fingerprint: str,
        per_panel_cost_ceiling_anlas: int,
        max_calls: int,
        max_cost_anlas: int,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ApplicationError(
                code="GENERATION_APPROVAL_REQUIRED",
                message="请确认固定面板范围、调用上限和成本预留后再创建队列。",
                status_code=422,
            )
        plan = self._build_plan(
            project_id,
            chapter_id,
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        )
        if plan.fingerprint != plan_fingerprint:
            raise ApplicationError(
                code="GENERATION_PLAN_STALE",
                message="分镜、设定、模型或成本预留已经变化，请重新估算并确认。",
                status_code=409,
            )
        if max_calls < plan.panel_count or max_calls > plan.panel_count * 3:
            raise ApplicationError(
                code="GENERATION_CALL_LIMIT_INVALID",
                message="调用上限必须覆盖全部面板，且不得超过面板数的三倍。",
                status_code=422,
            )
        if max_cost_anlas < plan.estimated_cost_upper_anlas:
            raise ApplicationError(
                code="GENERATION_COST_LIMIT_INVALID",
                message="成本上限低于已确认的全部面板预留，请重新确认。",
                status_code=422,
            )
        if max_cost_anlas > 10_000_000:
            raise ApplicationError(
                code="GENERATION_COST_LIMIT_INVALID",
                message="成本上限超出本地安全范围。",
                status_code=422,
            )

        job_id = str(uuid7())
        user_action_id = str(uuid7())
        try:
            with self.database.writer() as connection:
                connection.execute(
                    """
                    INSERT INTO generation_jobs(
                        job_id, project_id, chapter_id, storyboard_version_id,
                        character_bible_version_id, style_bible_version_id,
                        character_tag_bundle_version_id, prompt_bundle_version_id,
                        text_model_config_revision,
                        novelai_config_revision, provider_model_id, mapping_version,
                        contract_sha256, credential_profile_id, timeout_seconds,
                        plan_fingerprint, status, user_action_id, page_count,
                        panel_count, max_calls, max_cost_anlas,
                        estimated_cost_upper_anlas, cost_basis
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'queued', ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        job_id,
                        project_id,
                        chapter_id,
                        plan.storyboard_version_id,
                        plan.character_bible_version_id,
                        plan.style_bible_version_id,
                        plan.character_tag_bundle_version_id,
                        plan.prompt_bundle_version_id,
                        plan.text_model_config_revision,
                        plan.novelai_config_revision,
                        plan.provider_model_id,
                        plan.mapping_version,
                        plan.contract_sha256,
                        plan.credential_profile_id,
                        plan.timeout_seconds,
                        plan.fingerprint,
                        user_action_id,
                        plan.page_count,
                        plan.panel_count,
                        max_calls,
                        max_cost_anlas,
                        plan.estimated_cost_upper_anlas,
                        "user_confirmed_per_panel_ceiling",
                    ),
                )
                for panel in plan.panels:
                    connection.execute(
                        """
                        INSERT INTO generation_job_items(
                            item_id, job_id, ordinal, page_id, page_number, panel_id,
                            status, cost_ceiling_anlas
                        ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
                        """,
                        (
                            str(uuid7()),
                            job_id,
                            panel.ordinal,
                            panel.page_id,
                            panel.page_number,
                            panel.panel_id,
                            panel.cost_ceiling_anlas,
                        ),
                    )
                self._audit(
                    connection,
                    project_id,
                    "generation.job_created",
                    {
                        "job_id": job_id,
                        "user_action_id": user_action_id,
                        "plan_fingerprint": plan.fingerprint,
                        "panel_count": plan.panel_count,
                        "max_calls": max_calls,
                        "max_cost_anlas": max_cost_anlas,
                        "external_request_created": False,
                    },
                )
        except sqlite3.IntegrityError as exc:
            raise ApplicationError(
                code="GENERATION_JOB_ALREADY_ACTIVE",
                message="该项目已有未结束的生成队列，请先完成、取消或审阅。",
                status_code=409,
            ) from exc
        return self.get_job(project_id, job_id)

    def list_jobs(self, project_id: str) -> list[dict[str, Any]]:
        self._require_project(project_id)
        with self.database.reader() as connection:
            rows = connection.execute(
                """
                SELECT job_id FROM generation_jobs
                WHERE project_id = ? ORDER BY created_at DESC, job_id DESC
                LIMIT 100
                """,
                (project_id,),
            ).fetchall()
        return [self.get_job(project_id, str(row["job_id"])) for row in rows]

    def get_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        row = self._job_row(project_id, job_id)
        with self.database.reader() as connection:
            items = connection.execute(
                """
                SELECT gi.*,
                       (SELECT ga.error_code FROM generation_attempts ga
                        WHERE ga.item_id = gi.item_id
                        ORDER BY ga.attempt_number DESC LIMIT 1) AS last_error_code
                FROM generation_job_items gi
                WHERE gi.job_id = ? ORDER BY gi.ordinal
                """,
                (job_id,),
            ).fetchall()
        return {
            "job_id": job_id,
            "project_id": project_id,
            "chapter_id": str(row["chapter_id"]),
            "storyboard_version_id": str(row["storyboard_version_id"]),
            "character_bible_version_id": str(row["character_bible_version_id"]),
            "style_bible_version_id": str(row["style_bible_version_id"]),
            "character_tag_bundle_version_id": row["character_tag_bundle_version_id"],
            "prompt_bundle_version_id": row["prompt_bundle_version_id"],
            "text_model_config_revision": row["text_model_config_revision"],
            "novelai_config_revision": int(row["novelai_config_revision"]),
            "provider_model_id": str(row["provider_model_id"]),
            "operation_kind": str(row["operation_kind"]),
            "target_page_id": row["target_page_id"],
            "target_page_version_id": row["target_page_version_id"],
            "result_page_version_id": row["result_page_version_id"],
            "mapping_version": str(row["mapping_version"]),
            "contract_sha256": str(row["contract_sha256"]),
            "credential_profile_id": str(row["credential_profile_id"]),
            "timeout_seconds": float(row["timeout_seconds"]),
            "plan_fingerprint": str(row["plan_fingerprint"]),
            "status": str(row["status"]),
            "user_action_id": str(row["user_action_id"]),
            "page_count": int(row["page_count"]),
            "panel_count": int(row["panel_count"]),
            "max_calls": int(row["max_calls"]),
            "max_cost_anlas": int(row["max_cost_anlas"]),
            "estimated_cost_upper_anlas": int(row["estimated_cost_upper_anlas"]),
            "cost_basis": str(row["cost_basis"]),
            "calls_started": int(row["calls_started"]),
            "items_claimed": int(row["items_claimed"]),
            "calls_completed": int(row["calls_completed"]),
            "recorded_cost_anlas": int(row["recorded_cost_anlas"]),
            "allocated_cost_anlas": int(row["allocated_cost_anlas"]),
            "unverified_cost_calls": int(row["unverified_cost_calls"]),
            "revision": int(row["revision"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "started_at": row["started_at"],
            "paused_at": row["paused_at"],
            "completed_at": row["completed_at"],
            "items": [self._item_payload(item) for item in items],
            "external_requests_started": int(row["calls_started"]),
        }

    def start_job(
        self, project_id: str, job_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        return self._transition(
            project_id,
            job_id,
            expected_revision=expected_revision,
            allowed_from={"queued"},
            target="running",
            event_type="generation.job_started",
            started=True,
        )

    def pause_job(
        self, project_id: str, job_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        return self._transition(
            project_id,
            job_id,
            expected_revision=expected_revision,
            allowed_from={"queued", "running"},
            target="paused",
            event_type="generation.job_paused",
            paused=True,
        )

    def resume_job(
        self, project_id: str, job_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        return self._transition(
            project_id,
            job_id,
            expected_revision=expected_revision,
            allowed_from={"paused"},
            target="running",
            event_type="generation.job_resumed",
        )

    def cancel_job(
        self, project_id: str, job_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        row = self._job_row(project_id, job_id)
        self._check_revision_and_status(
            row,
            expected_revision,
            {"queued", "running", "paused", "needs_review", "failed"},
        )
        with self.database.writer() as connection:
            self._assert_revision(connection, job_id, expected_revision)
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = 'canceled', revision = revision + 1,
                    completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (job_id,),
            )
            connection.execute(
                """
                UPDATE generation_job_items
                SET status = 'canceled', updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'queued'
                """,
                (job_id,),
            )
            self._audit(
                connection,
                project_id,
                "generation.job_canceled",
                {"job_id": job_id, "expected_revision": expected_revision},
            )
        return self.get_job(project_id, job_id)

    def claim_next(self, job_id: str) -> dict[str, Any] | None:
        """Claim one item for a worker. Never called automatically at application startup."""
        with self.database.writer() as connection:
            job = connection.execute(
                "SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise ApplicationError("GENERATION_JOB_NOT_FOUND", "没有找到该生成队列。", 404)
            if str(job["status"]) != "running":
                return None
            active = connection.execute(
                "SELECT 1 FROM generation_attempts WHERE status = 'running'"
            ).fetchone()
            if active is not None:
                return None
            item = connection.execute(
                """
                SELECT * FROM generation_job_items
                WHERE job_id = ? AND status = 'queued'
                ORDER BY ordinal LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if item is None:
                self._finish_if_empty(connection, job_id)
                return None
            attempt_id = str(uuid7())
            attempt_number = int(item["attempt_count"]) + 1
            connection.execute(
                """
                INSERT INTO generation_attempts(
                    attempt_id, item_id, attempt_number, status, cost_ceiling_anlas
                ) VALUES (?, ?, ?, 'running', ?)
                """,
                (
                    attempt_id,
                    str(item["item_id"]),
                    attempt_number,
                    int(item["cost_ceiling_anlas"]),
                ),
            )
            connection.execute(
                """
                UPDATE generation_job_items
                SET status = 'running', attempt_count = ?, active_attempt_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ?
                """,
                (attempt_number, attempt_id, str(item["item_id"])),
            )
            connection.execute(
                """
                UPDATE generation_jobs
                SET items_claimed = items_claimed + 1, revision = revision + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (job_id,),
            )
            self._audit(
                connection,
                str(job["project_id"]),
                "generation.item_claimed",
                {
                    "job_id": job_id,
                    "item_id": str(item["item_id"]),
                    "attempt_id": attempt_id,
                    "ordinal": int(item["ordinal"]),
                },
            )
            return {
                "attempt_id": attempt_id,
                "item_id": str(item["item_id"]),
                "job_id": job_id,
                "panel_id": str(item["panel_id"]),
                "page_id": str(item["page_id"]),
                "page_number": int(item["page_number"]),
                "attempt_number": attempt_number,
                "cost_ceiling_anlas": int(item["cost_ceiling_anlas"]),
            }

    def mark_provider_request_started(self, attempt_id: str) -> bool:
        """Consume one call/cost allocation immediately before the HTTP request."""
        with self.database.writer() as connection:
            row = connection.execute(
                """
                SELECT ga.status AS attempt_status, ga.provider_request_started,
                       ga.item_id, gi.job_id, gi.cost_ceiling_anlas,
                       gj.project_id, gj.status AS job_status, gj.calls_started,
                       gj.max_calls, gj.allocated_cost_anlas, gj.max_cost_anlas
                FROM generation_attempts ga
                JOIN generation_job_items gi ON gi.item_id = ga.item_id
                JOIN generation_jobs gj ON gj.job_id = gi.job_id
                WHERE ga.attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ApplicationError(
                    "GENERATION_ATTEMPT_NOT_FOUND", "没有找到该生成尝试。", 404
                )
            if str(row["attempt_status"]) != "running":
                return False
            if bool(row["provider_request_started"]):
                return True
            if str(row["job_status"]) != "running":
                self._stop_prepared_attempt_for_job_state(connection, row, attempt_id)
                return False
            projected_cost = int(row["allocated_cost_anlas"]) + int(
                row["cost_ceiling_anlas"]
            )
            if int(row["calls_started"]) >= int(row["max_calls"]):
                self._stop_prepared_attempt_for_limit(
                    connection, row, attempt_id, "CALL_LIMIT_REACHED"
                )
                return False
            if projected_cost > int(row["max_cost_anlas"]):
                self._stop_prepared_attempt_for_limit(
                    connection, row, attempt_id, "COST_LIMIT_REACHED"
                )
                return False
            connection.execute(
                """
                UPDATE generation_attempts
                SET provider_request_started = 1, provider_started_at = CURRENT_TIMESTAMP
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            )
            connection.execute(
                """
                UPDATE generation_jobs
                SET calls_started = calls_started + 1,
                    allocated_cost_anlas = allocated_cost_anlas + ?,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (int(row["cost_ceiling_anlas"]), str(row["job_id"])),
            )
            self._audit(
                connection,
                str(row["project_id"]),
                "generation.provider_request_started",
                {"job_id": str(row["job_id"]), "attempt_id": attempt_id},
            )
            return True

    def complete_attempt(
        self, attempt_id: str, *, recorded_cost_anlas: int | None
    ) -> None:
        if recorded_cost_anlas is not None and recorded_cost_anlas < 0:
            raise ValueError("recorded cost must not be negative")
        with self.database.writer() as connection:
            self.complete_attempt_in_transaction(
                connection,
                attempt_id,
                recorded_cost_anlas=recorded_cost_anlas,
            )

    def complete_attempt_in_transaction(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        *,
        recorded_cost_anlas: int | None,
        asset_version_id: str | None = None,
    ) -> None:
        """Finalize an attempt inside an existing asset registration transaction."""
        if recorded_cost_anlas is not None and recorded_cost_anlas < 0:
            raise ValueError("recorded cost must not be negative")
        recorded_cost = recorded_cost_anlas or 0
        unverified = int(recorded_cost_anlas is None)
        row = self._attempt_row(connection, attempt_id)
        if str(row["attempt_status"]) != "running":
            raise ApplicationError(
                "GENERATION_ATTEMPT_NOT_RUNNING", "该生成尝试已经结束。", 409
            )
        if not bool(row["provider_request_started"]):
            raise ApplicationError(
                "GENERATION_PROVIDER_REQUEST_NOT_STARTED",
                "供应商请求尚未开始，不能登记完成。",
                409,
            )
        connection.execute(
            """
            UPDATE generation_attempts
            SET status = 'completed', recorded_cost_anlas = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE attempt_id = ?
            """,
            (recorded_cost_anlas, attempt_id),
        )
        connection.execute(
            """
            UPDATE generation_job_items
            SET status = 'completed', recorded_cost_anlas = recorded_cost_anlas + ?,
                active_attempt_id = NULL, asset_version_id = COALESCE(?, asset_version_id),
                updated_at = CURRENT_TIMESTAMP
            WHERE item_id = ?
            """,
            (recorded_cost, asset_version_id, str(row["item_id"])),
        )
        connection.execute(
            """
            UPDATE generation_jobs
            SET calls_completed = calls_completed + 1,
                recorded_cost_anlas = recorded_cost_anlas + ?,
                unverified_cost_calls = unverified_cost_calls + ?,
                revision = revision + 1, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (recorded_cost, unverified, str(row["job_id"])),
        )
        refreshed = connection.execute(
            """
            SELECT status, max_cost_anlas, recorded_cost_anlas
            FROM generation_jobs WHERE job_id = ?
            """,
            (str(row["job_id"]),),
        ).fetchone()
        if (
            refreshed is not None
            and str(refreshed["status"]) not in {"canceled", "paused"}
            and int(refreshed["recorded_cost_anlas"]) > int(refreshed["max_cost_anlas"])
        ):
            self._mark_job_needs_review(
                connection, str(row["job_id"]), "RECORDED_COST_EXCEEDED_LIMIT"
            )
        else:
            self._finish_if_empty(connection, str(row["job_id"]))

    def fail_attempt(
        self, attempt_id: str, *, error_code: str, outcome_unknown: bool
    ) -> None:
        with self.database.writer() as connection:
            row = self._attempt_row(connection, attempt_id)
            if str(row["attempt_status"]) != "running":
                raise ApplicationError(
                    "GENERATION_ATTEMPT_NOT_RUNNING", "该生成尝试已经结束。", 409
                )
            target = "needs_review" if outcome_unknown else "failed"
            connection.execute(
                """
                UPDATE generation_attempts
                SET status = ?, error_code = ?, completed_at = CURRENT_TIMESTAMP
                WHERE attempt_id = ?
                """,
                (target, error_code[:100], attempt_id),
            )
            connection.execute(
                """
                UPDATE generation_job_items
                SET status = ?, active_attempt_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ?
                """,
                (target, str(row["item_id"])),
            )
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status != 'canceled'
                """,
                (target, str(row["job_id"])),
            )

    def requeue_attempt(self, attempt_id: str, *, error_code: str) -> None:
        """Record a definite temporary failure and return its item to the same bounded job."""
        with self.database.writer() as connection:
            row = self._attempt_row(connection, attempt_id)
            if str(row["attempt_status"]) != "running":
                raise ApplicationError(
                    "GENERATION_ATTEMPT_NOT_RUNNING", "该生成尝试已经结束。", 409
                )
            connection.execute(
                """
                UPDATE generation_attempts
                SET status = 'failed', error_code = ?, completed_at = CURRENT_TIMESTAMP
                WHERE attempt_id = ?
                """,
                (error_code[:100], attempt_id),
            )
            item_status = (
                "canceled" if str(row["job_status"]) == "canceled" else "queued"
            )
            connection.execute(
                """
                UPDATE generation_job_items
                SET status = ?, active_attempt_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ?
                """,
                (item_status, str(row["item_id"])),
            )
            connection.execute(
                """
                UPDATE generation_jobs
                SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (str(row["job_id"]),),
            )

    def reconcile_startup(self) -> dict[str, int]:
        """Fail closed after restart; never claims queued work or resumes paid calls."""
        with self.database.writer() as connection:
            in_flight = connection.execute(
                """
                SELECT ga.attempt_id, gi.item_id, gi.job_id
                FROM generation_attempts ga
                JOIN generation_job_items gi ON gi.item_id = ga.item_id
                WHERE ga.status = 'running'
                """
            ).fetchall()
            affected_jobs: set[str] = set()
            for row in in_flight:
                affected_jobs.add(str(row["job_id"]))
                connection.execute(
                    """
                    UPDATE generation_attempts
                    SET status = 'needs_review', error_code = 'PROCESS_RESTARTED',
                        completed_at = CURRENT_TIMESTAMP
                    WHERE attempt_id = ?
                    """,
                    (str(row["attempt_id"]),),
                )
                connection.execute(
                    """
                    UPDATE generation_job_items
                    SET status = 'needs_review', active_attempt_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE item_id = ?
                    """,
                    (str(row["item_id"]),),
                )
            for job_id in affected_jobs:
                connection.execute(
                    """
                    UPDATE generation_jobs
                    SET status = 'needs_review', revision = revision + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = ? AND status != 'canceled'
                    """,
                    (job_id,),
                )
            paused = connection.execute(
                """
                UPDATE generation_jobs
                SET status = 'paused', paused_at = CURRENT_TIMESTAMP,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            ).rowcount
        return {"needs_review": len(affected_jobs), "paused": max(paused, 0)}

    def mark_job_needs_review(self, job_id: str, reason: str) -> None:
        with self.database.writer() as connection:
            row = connection.execute(
                "SELECT status FROM generation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise ApplicationError(
                    "GENERATION_JOB_NOT_FOUND", "没有找到该生成队列。", 404
                )
            if str(row["status"]) == "canceled":
                return
            self._mark_job_needs_review(connection, job_id, reason)

    def _build_plan(
        self,
        project_id: str,
        chapter_id: str,
        *,
        per_panel_cost_ceiling_anlas: int,
    ) -> GenerationPlan:
        if not 0 <= per_panel_cost_ceiling_anlas <= 100_000:
            raise ApplicationError(
                "GENERATION_COST_ESTIMATE_INVALID",
                "每格成本预留必须在 0 到 100000 Anlas 之间。",
                422,
            )
        bundle = self.bibles.get_bundle(project_id, chapter_id)
        readiness = cast(dict[str, Any], bundle["generation_readiness"])
        if not bool(readiness["ready"]):
            raise ApplicationError(
                "GENERATION_INPUTS_NOT_APPROVED",
                "角色设定和风格板尚未全部批准，不能建立生成计划。",
                409,
                {"blockers": readiness["blockers"]},
            )
        character_version_id = str(readiness["character_bible_version_id"])
        style_version_id = str(readiness["style_bible_version_id"])
        character_document = cast(dict[str, Any], bundle["character_bible"])["document"]
        storyboard_version_id = str(character_document["storyboard_version_id"])
        style_storyboard_version_id = str(
            cast(dict[str, Any], bundle["style_bible"])["document"]["storyboard_version_id"]
        )
        if style_storyboard_version_id != storyboard_version_id:
            raise ApplicationError(
                "GENERATION_INPUT_VERSION_MISMATCH",
                "角色设定与风格板没有绑定同一个分镜版本。",
                409,
            )
        prompt_workflow = self.prompting.get_workflow(project_id, chapter_id)
        prompt_readiness = cast(dict[str, Any], prompt_workflow["generation_readiness"])
        if not bool(prompt_readiness["ready"]):
            raise ApplicationError(
                "GENERATION_PROMPTS_NOT_APPROVED",
                "角色固定 tags 和逐格 PromptPackage 尚未全部批准。",
                409,
                {"blockers": prompt_readiness["blockers"]},
            )
        character_tag_version_id = str(
            prompt_readiness["character_tag_bundle_version_id"]
        )
        prompt_bundle_version_id = str(prompt_readiness["prompt_bundle_version_id"])
        text_model_config_revision = int(prompt_readiness["text_model_config_revision"])
        prompt_document = cast(dict[str, Any], prompt_workflow["prompt_bundle"])["document"]
        prompt_packages = {
            str(item["panel_id"]): item
            for item in cast(list[dict[str, Any]], prompt_document["packages"])
        }
        with self.database.reader() as connection:
            storyboard_row = connection.execute(
                """
                SELECT sv.document_json
                FROM storyboard_versions sv
                JOIN storyboards s ON s.storyboard_id = sv.storyboard_id
                JOIN storyboard_approvals sa
                  ON sa.storyboard_version_id = sv.storyboard_version_id
                WHERE s.project_id = ? AND s.chapter_id = ?
                  AND sv.storyboard_version_id = ? AND sv.is_current = 1
                """,
                (project_id, chapter_id, storyboard_version_id),
            ).fetchone()
            novelai_row = connection.execute(
                """
                SELECT provider_model_id, inpaint_model_id, mapping_version,
                       contract_sha256, revision,
                       credential_profile_id, timeout_seconds, last_connection_status
                FROM novelai_configs WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if storyboard_row is None:
            raise ApplicationError(
                "GENERATION_STORYBOARD_NOT_READY",
                "当前已审批分镜与设定版本不一致，请重新确认。",
                409,
            )
        if novelai_row is None:
            raise ApplicationError(
                "NOVELAI_CONFIGURATION_NOT_FOUND",
                "请先保存并验证 NovelAI 项目配置。",
                409,
            )
        if str(novelai_row["last_connection_status"]) != "ok":
            raise ApplicationError(
                "NOVELAI_CONNECTION_NOT_VERIFIED",
                "请先由用户点击完成一次无出图 NovelAI 连接测试。",
                409,
            )
        if (
            str(novelai_row["mapping_version"]) != MAPPING_VERSION
            or str(novelai_row["contract_sha256"]) != CONTRACT_SHA256
        ):
            raise ApplicationError(
                "NOVELAI_CONTRACT_STALE",
                "NovelAI 契约映射已经升级，请重新保存配置并执行连接测试。",
                409,
            )
        document = StoryboardDocument.model_validate_json(str(storyboard_row["document_json"]))
        panels: list[PlannedPanel] = []
        ordinal = 0
        for page in document.pages:
            for panel in page.panels:
                ordinal += 1
                package = prompt_packages.get(str(panel.panel_id))
                if package is None:
                    raise ApplicationError(
                        "GENERATION_PROMPT_PACKAGE_NOT_FOUND",
                        "已审批 PromptPackage 未覆盖全部分镜格。",
                        409,
                    )
                panels.append(
                    PlannedPanel(
                        ordinal=ordinal,
                        page_id=str(page.page_id),
                        page_number=page.page_number,
                        panel_id=str(panel.panel_id),
                        panel_order=panel.order,
                        cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
                        prompt_package_id=str(package["prompt_package_id"]),
                        compiled_prompt=str(package["compiled_prompt"]),
                        compiled_negative_prompt=str(package["compiled_negative_prompt"]),
                        compiled_prompt_sha256=str(package["compiled_prompt_sha256"]),
                    )
                )
        if not panels:
            raise ApplicationError(
                "GENERATION_PLAN_EMPTY", "分镜没有可生成的面板。", 422
            )
        stable = {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "storyboard_version_id": storyboard_version_id,
            "character_bible_version_id": character_version_id,
            "style_bible_version_id": style_version_id,
            "character_tag_bundle_version_id": character_tag_version_id,
            "prompt_bundle_version_id": prompt_bundle_version_id,
            "text_model_config_revision": text_model_config_revision,
            "novelai_config_revision": int(novelai_row["revision"]),
            "provider_model_id": str(novelai_row["provider_model_id"]),
            "inpaint_model_id": str(novelai_row["inpaint_model_id"]),
            "mapping_version": str(novelai_row["mapping_version"]),
            "contract_sha256": str(novelai_row["contract_sha256"]),
            "credential_profile_id": str(novelai_row["credential_profile_id"]),
            "timeout_seconds": float(novelai_row["timeout_seconds"]),
            "per_panel_cost_ceiling_anlas": per_panel_cost_ceiling_anlas,
            "panels": [panel.payload() for panel in panels],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                stable,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return GenerationPlan(
            project_id=project_id,
            chapter_id=chapter_id,
            storyboard_version_id=storyboard_version_id,
            character_bible_version_id=character_version_id,
            style_bible_version_id=style_version_id,
            character_tag_bundle_version_id=character_tag_version_id,
            prompt_bundle_version_id=prompt_bundle_version_id,
            text_model_config_revision=text_model_config_revision,
            novelai_config_revision=int(novelai_row["revision"]),
            provider_model_id=str(novelai_row["provider_model_id"]),
            inpaint_model_id=str(novelai_row["inpaint_model_id"]),
            mapping_version=str(novelai_row["mapping_version"]),
            contract_sha256=str(novelai_row["contract_sha256"]),
            credential_profile_id=str(novelai_row["credential_profile_id"]),
            timeout_seconds=float(novelai_row["timeout_seconds"]),
            panels=tuple(panels),
            page_count=len(document.pages),
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
            fingerprint=fingerprint,
        )

    def _transition(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        allowed_from: set[str],
        target: JobStatus,
        event_type: str,
        started: bool = False,
        paused: bool = False,
    ) -> dict[str, Any]:
        row = self._job_row(project_id, job_id)
        self._check_revision_and_status(row, expected_revision, allowed_from)
        started_sql = ", started_at = COALESCE(started_at, CURRENT_TIMESTAMP)" if started else ""
        paused_sql = ", paused_at = CURRENT_TIMESTAMP" if paused else ""
        with self.database.writer() as connection:
            self._assert_revision(connection, job_id, expected_revision)
            connection.execute(
                f"""
                UPDATE generation_jobs
                SET status = ?, revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                    {started_sql} {paused_sql}
                WHERE job_id = ?
                """,
                (target, job_id),
            )
            self._audit(
                connection,
                project_id,
                event_type,
                {
                    "job_id": job_id,
                    "from": str(row["status"]),
                    "to": target,
                    "user_action_id": str(uuid7()),
                },
            )
        return self.get_job(project_id, job_id)

    def _job_row(self, project_id: str, job_id: str) -> sqlite3.Row:
        self._require_project(project_id)
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT * FROM generation_jobs WHERE project_id = ? AND job_id = ?",
                (project_id, job_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("GENERATION_JOB_NOT_FOUND", "没有找到该生成队列。", 404)
        return cast(sqlite3.Row, row)

    def _require_project(self, project_id: str) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ApplicationError("PROJECT_NOT_FOUND", "没有找到该项目。", 404)

    @staticmethod
    def _check_revision_and_status(
        row: sqlite3.Row, expected_revision: int, allowed_from: set[str]
    ) -> None:
        if int(row["revision"]) != expected_revision:
            raise ApplicationError(
                "GENERATION_JOB_REVISION_CONFLICT",
                "队列状态已变化，请刷新后重试。",
                409,
            )
        if str(row["status"]) not in allowed_from:
            raise ApplicationError(
                "GENERATION_JOB_TRANSITION_INVALID",
                "当前队列状态不允许这个操作。",
                409,
            )

    @staticmethod
    def _assert_revision(
        connection: sqlite3.Connection, job_id: str, expected_revision: int
    ) -> None:
        row = connection.execute(
            "SELECT revision FROM generation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None or int(row["revision"]) != expected_revision:
            raise ApplicationError(
                "GENERATION_JOB_REVISION_CONFLICT",
                "队列状态已变化，请刷新后重试。",
                409,
            )

    @staticmethod
    def _attempt_row(connection: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT ga.status AS attempt_status, ga.provider_request_started,
                   ga.item_id, gi.job_id, gj.status AS job_status
            FROM generation_attempts ga
            JOIN generation_job_items gi ON gi.item_id = ga.item_id
            JOIN generation_jobs gj ON gj.job_id = gi.job_id
            WHERE ga.attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise ApplicationError(
                "GENERATION_ATTEMPT_NOT_FOUND", "没有找到该生成尝试。", 404
            )
        return cast(sqlite3.Row, row)

    @staticmethod
    def _item_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "item_id": str(row["item_id"]),
            "ordinal": int(row["ordinal"]),
            "page_id": str(row["page_id"]),
            "page_number": int(row["page_number"]),
            "panel_id": str(row["panel_id"]),
            "status": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "cost_ceiling_anlas": int(row["cost_ceiling_anlas"]),
            "recorded_cost_anlas": int(row["recorded_cost_anlas"]),
            "active_attempt_id": row["active_attempt_id"],
            "asset_version_id": row["asset_version_id"],
            "operation_kind": str(row["operation_kind"]),
            "parent_asset_version_id": row["parent_asset_version_id"],
            "mask_asset_id": row["mask_asset_id"],
            "edit_prompt": row["edit_prompt"],
            "inpaint_strength": row["inpaint_strength"],
            "last_error_code": row["last_error_code"],
        }

    @staticmethod
    def _finish_if_empty(connection: sqlite3.Connection, job_id: str) -> None:
        job = connection.execute(
            "SELECT status FROM generation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if job is None or str(job["status"]) in {"canceled", "paused", "needs_review", "failed"}:
            return
        remaining = connection.execute(
            """
            SELECT 1 FROM generation_job_items
            WHERE job_id = ? AND status IN ('queued', 'running', 'needs_review') LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if remaining is None:
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                    revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (job_id,),
            )

    @staticmethod
    def _mark_job_needs_review(
        connection: sqlite3.Connection, job_id: str, reason: str
    ) -> None:
        connection.execute(
            """
            UPDATE generation_jobs
            SET status = 'needs_review', revision = revision + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ? AND status != 'canceled'
            """,
            (job_id,),
        )
        connection.execute(
            """
            INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
            SELECT ?, project_id, 'generation.job_needs_review', ?
            FROM generation_jobs WHERE job_id = ?
            """,
            (
                str(uuid7()),
                json.dumps({"job_id": job_id, "reason": reason}, sort_keys=True),
                job_id,
            ),
        )

    @classmethod
    def _stop_prepared_attempt_for_limit(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        attempt_id: str,
        reason: str,
    ) -> None:
        connection.execute(
            """
            UPDATE generation_attempts
            SET status = 'canceled', error_code = ?, completed_at = CURRENT_TIMESTAMP
            WHERE attempt_id = ?
            """,
            (reason, attempt_id),
        )
        connection.execute(
            """
            UPDATE generation_job_items
            SET status = 'queued', active_attempt_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE item_id = ?
            """,
            (str(row["item_id"]),),
        )
        cls._mark_job_needs_review(connection, str(row["job_id"]), reason)

    @classmethod
    def _stop_prepared_attempt_for_job_state(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        attempt_id: str,
    ) -> None:
        job_status = str(row["job_status"])
        item_status = "canceled" if job_status == "canceled" else "queued"
        reason = f"JOB_{job_status.upper()}_BEFORE_REQUEST"
        connection.execute(
            """
            UPDATE generation_attempts
            SET status = 'canceled', error_code = ?, completed_at = CURRENT_TIMESTAMP
            WHERE attempt_id = ? AND provider_request_started = 0
            """,
            (reason[:100], attempt_id),
        )
        connection.execute(
            """
            UPDATE generation_job_items
            SET status = ?, active_attempt_id = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE item_id = ?
            """,
            (item_status, str(row["item_id"])),
        )
        connection.execute(
            """
            UPDATE generation_jobs
            SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (str(row["job_id"]),),
        )
        cls._audit(
            connection,
            str(row["project_id"]),
            "generation.prepared_attempt_stopped",
            {
                "job_id": str(row["job_id"]),
                "attempt_id": attempt_id,
                "job_status": job_status,
                "external_request_created": False,
            },
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
