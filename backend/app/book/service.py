from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, cast

from ..database import Database
from ..errors import ApplicationError
from ..generation.assets import canonical_json
from ..generation.queue import GenerationQueueService
from ..ids import uuid7

TERMINAL_PLAN_STATUSES = {"completed", "canceled"}
OPEN_JOB_STATUSES = {"queued", "running", "paused", "needs_review"}


class BookProductionService:
    """A bounded plan that delegates one chapter at a time to existing safe jobs."""

    def __init__(self, database: Database, queue: GenerationQueueService) -> None:
        self.database = database
        self.queue = queue

    def estimate(
        self, project_id: str, *, per_panel_cost_ceiling_anlas: int
    ) -> dict[str, Any]:
        chapter_set_id, chapters = self._current_chapters(project_id)
        continuity = self._approved_continuity(project_id, int(chapters[-1]["ordinal"]))
        estimates: list[dict[str, Any]] = []
        for chapter in chapters:
            estimate = self.queue.estimate(
                project_id,
                str(chapter["chapter_id"]),
                per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
            )
            estimates.append(
                {
                    "chapter_id": str(chapter["chapter_id"]),
                    "ordinal": int(chapter["ordinal"]),
                    "title": str(chapter["title"]),
                    "storyboard_version_id": estimate["storyboard_version_id"],
                    "character_bible_version_id": estimate["character_bible_version_id"],
                    "style_bible_version_id": estimate["style_bible_version_id"],
                    "generation_plan_fingerprint": estimate["plan_fingerprint"],
                    "page_count": estimate["page_count"],
                    "panel_count": estimate["panel_count"],
                    "estimated_calls": estimate["estimated_calls"],
                    "estimated_cost_upper_anlas": estimate[
                        "estimated_cost_upper_anlas"
                    ],
                }
            )
        snapshot = {
            "schema_version": "1.0",
            "project_id": project_id,
            "source_chapter_set_id": chapter_set_id,
            "continuity_version_id": continuity["continuity_version_id"],
            "per_panel_cost_ceiling_anlas": per_panel_cost_ceiling_anlas,
            "chapters": estimates,
        }
        fingerprint = hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()
        page_count = sum(int(item["page_count"]) for item in estimates)
        panel_count = sum(int(item["panel_count"]) for item in estimates)
        cost = sum(int(item["estimated_cost_upper_anlas"]) for item in estimates)
        return {
            **snapshot,
            "chapter_count": len(estimates),
            "estimated_page_count": page_count,
            "estimated_panel_count": panel_count,
            "estimated_calls": panel_count,
            "estimated_cost_upper_anlas": cost,
            "cost_basis": "user_confirmed_per_panel_ceiling",
            "cost_notice": "整本预算是每格保守预留之和，不是账户实际扣费预测。",
            "plan_fingerprint": fingerprint,
            "external_request_created": False,
        }

    def create_plan(
        self,
        project_id: str,
        *,
        per_panel_cost_ceiling_anlas: int,
        plan_fingerprint: str,
        max_calls: int,
        max_cost_anlas: int,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ApplicationError(
                "BOOK_PLAN_CONFIRMATION_REQUIRED",
                "请先确认整本章节范围、调用上限和成本预留。",
                422,
            )
        estimate = self.estimate(
            project_id,
            per_panel_cost_ceiling_anlas=per_panel_cost_ceiling_anlas,
        )
        if estimate["plan_fingerprint"] != plan_fingerprint:
            raise ApplicationError(
                "BOOK_PLAN_STALE", "章节、设定、连续性账本或模型配置已变化。", 409
            )
        minimum_calls = int(estimate["estimated_calls"])
        minimum_cost = int(estimate["estimated_cost_upper_anlas"])
        if max_calls < minimum_calls or max_calls > minimum_calls * 3:
            raise ApplicationError(
                "BOOK_CALL_LIMIT_INVALID",
                "整本调用上限必须覆盖全部分格，且不得超过预计调用数的三倍。",
                422,
            )
        if max_cost_anlas < minimum_cost or max_cost_anlas > 100_000_000:
            raise ApplicationError(
                "BOOK_COST_LIMIT_INVALID", "整本成本上限无效。", 422
            )
        allocations = self._allocate_limits(
            estimate["chapters"],
            per_panel_cost_ceiling_anlas,
            max_calls,
            max_cost_anlas,
        )
        book_plan_id = str(uuid7())
        snapshot = {
            key: value
            for key, value in estimate.items()
            if key not in {"cost_notice", "cost_basis", "external_request_created"}
        }
        with self.database.writer() as connection:
            current = connection.execute(
                """SELECT book_plan_id, status FROM book_production_plans
                   WHERE project_id = ? AND is_current = 1""",
                (project_id,),
            ).fetchone()
            if current is not None and str(current["status"]) not in TERMINAL_PLAN_STATUSES:
                raise ApplicationError(
                    "BOOK_PLAN_ALREADY_ACTIVE", "当前项目已有未结束的整本计划。", 409
                )
            if current is not None:
                connection.execute(
                    "UPDATE book_production_plans SET is_current = 0 WHERE book_plan_id = ?",
                    (str(current["book_plan_id"]),),
                )
            version = int(
                connection.execute(
                    """SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                       FROM book_production_plans WHERE project_id = ?""",
                    (project_id,),
                ).fetchone()["next_version"]
            )
            connection.execute(
                """INSERT INTO book_production_plans(
                       book_plan_id, project_id, version, source_chapter_set_id,
                       continuity_version_id, status, per_panel_cost_ceiling_anlas,
                       estimated_page_count, estimated_panel_count, estimated_calls,
                       estimated_cost_upper_anlas, max_calls, max_cost_anlas,
                       plan_fingerprint, snapshot_json, is_current
                   ) VALUES (?, ?, ?, ?, ?, 'awaiting_approval', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    book_plan_id,
                    project_id,
                    version,
                    estimate["source_chapter_set_id"],
                    estimate["continuity_version_id"],
                    per_panel_cost_ceiling_anlas,
                    estimate["estimated_page_count"],
                    estimate["estimated_panel_count"],
                    estimate["estimated_calls"],
                    estimate["estimated_cost_upper_anlas"],
                    max_calls,
                    max_cost_anlas,
                    plan_fingerprint,
                    canonical_json(snapshot),
                ),
            )
            for chapter, limits in zip(estimate["chapters"], allocations, strict=True):
                connection.execute(
                    """INSERT INTO book_production_chapters(
                           book_chapter_plan_id, book_plan_id, chapter_id, ordinal, title,
                           storyboard_version_id, character_bible_version_id,
                           style_bible_version_id, generation_plan_fingerprint,
                           page_count, panel_count, estimated_cost_upper_anlas,
                           max_calls, max_cost_anlas, status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_approval')""",
                    (
                        str(uuid7()),
                        book_plan_id,
                        chapter["chapter_id"],
                        chapter["ordinal"],
                        chapter["title"],
                        chapter["storyboard_version_id"],
                        chapter["character_bible_version_id"],
                        chapter["style_bible_version_id"],
                        chapter["generation_plan_fingerprint"],
                        chapter["page_count"],
                        chapter["panel_count"],
                        chapter["estimated_cost_upper_anlas"],
                        limits["max_calls"],
                        limits["max_cost_anlas"],
                    ),
                )
            self._audit(
                connection,
                project_id,
                "book.plan_created",
                {
                    "book_plan_id": book_plan_id,
                    "chapter_count": estimate["chapter_count"],
                    "max_calls": max_calls,
                    "max_cost_anlas": max_cost_anlas,
                    "external_request_created": False,
                },
            )
        return self.get_plan(project_id, book_plan_id)

    def current(self, project_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT book_plan_id FROM book_production_plans
                   WHERE project_id = ? AND is_current = 1""",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError("BOOK_PLAN_NOT_FOUND", "尚未创建整本生产计划。", 404)
        return self.get_plan(project_id, str(row["book_plan_id"]))

    def get_plan(self, project_id: str, book_plan_id: str) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT * FROM book_production_plans
                   WHERE project_id = ? AND book_plan_id = ?""",
                (project_id, book_plan_id),
            ).fetchone()
            chapters = connection.execute(
                """SELECT bc.*, gj.status AS generation_job_status,
                          gj.calls_started, gj.calls_completed,
                          gj.recorded_cost_anlas, gj.allocated_cost_anlas
                   FROM book_production_chapters bc
                   LEFT JOIN generation_jobs gj ON gj.job_id = bc.generation_job_id
                   WHERE bc.book_plan_id = ? ORDER BY bc.ordinal""",
                (book_plan_id,),
            ).fetchall()
        if row is None:
            raise ApplicationError("BOOK_PLAN_NOT_FOUND", "没有找到该整本计划。", 404)
        chapter_payloads = [self._chapter_payload(item) for item in chapters]
        calls_started = sum(item["calls_started"] for item in chapter_payloads)
        calls_completed = sum(item["calls_completed"] for item in chapter_payloads)
        recorded_cost = sum(item["recorded_cost_anlas"] for item in chapter_payloads)
        allocated_cost = sum(item["allocated_cost_anlas"] for item in chapter_payloads)
        return {
            "book_plan_id": str(row["book_plan_id"]),
            "project_id": project_id,
            "version": int(row["version"]),
            "source_chapter_set_id": str(row["source_chapter_set_id"]),
            "continuity_version_id": str(row["continuity_version_id"]),
            "status": str(row["status"]),
            "per_panel_cost_ceiling_anlas": int(row["per_panel_cost_ceiling_anlas"]),
            "estimated_page_count": int(row["estimated_page_count"]),
            "estimated_panel_count": int(row["estimated_panel_count"]),
            "estimated_calls": int(row["estimated_calls"]),
            "estimated_cost_upper_anlas": int(row["estimated_cost_upper_anlas"]),
            "max_calls": int(row["max_calls"]),
            "max_cost_anlas": int(row["max_cost_anlas"]),
            "plan_fingerprint": str(row["plan_fingerprint"]),
            "revision": int(row["revision"]),
            "is_current": bool(row["is_current"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": row["completed_at"],
            "chapters": chapter_payloads,
            "calls_started": calls_started,
            "calls_completed": calls_completed,
            "allocated_cost_anlas": allocated_cost,
            "recorded_cost_anlas": recorded_cost,
            "external_requests_started": calls_started,
        }

    def approve_chapter(
        self,
        project_id: str,
        book_plan_id: str,
        book_chapter_plan_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        plan = self._plan_row(project_id, book_plan_id)
        self._check_revision(plan, expected_revision, {"awaiting_approval", "ready"})
        with self.database.writer() as connection:
            chapter = connection.execute(
                """SELECT * FROM book_production_chapters
                   WHERE book_plan_id = ? AND book_chapter_plan_id = ?""",
                (book_plan_id, book_chapter_plan_id),
            ).fetchone()
            if chapter is None:
                raise ApplicationError(
                    "BOOK_CHAPTER_NOT_FOUND", "没有找到该章节计划。", 404
                )
            if str(chapter["status"]) != "awaiting_approval":
                raise ApplicationError(
                    "BOOK_CHAPTER_ALREADY_APPROVED", "该章节已经完成审批。", 409
                )
            approval_hash = hashlib.sha256(
                (
                    str(chapter["generation_plan_fingerprint"])
                    + str(chapter["max_calls"])
                    + str(chapter["max_cost_anlas"])
                ).encode()
            ).hexdigest()
            connection.execute(
                """UPDATE book_production_chapters
                   SET status = 'approved', approval_hash = ?,
                       approved_at = CURRENT_TIMESTAMP, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE book_chapter_plan_id = ?""",
                (approval_hash, book_chapter_plan_id),
            )
            remaining = int(
                connection.execute(
                    """SELECT COUNT(*) AS count FROM book_production_chapters
                       WHERE book_plan_id = ? AND status = 'awaiting_approval'""",
                    (book_plan_id,),
                ).fetchone()["count"]
            )
            next_status = "ready" if remaining == 0 else "awaiting_approval"
            self._update_plan(connection, book_plan_id, expected_revision, next_status)
            self._audit(
                connection,
                project_id,
                "book.chapter_approved",
                {
                    "book_plan_id": book_plan_id,
                    "book_chapter_plan_id": book_chapter_plan_id,
                    "approval_hash": approval_hash,
                    "external_requests_started": 0,
                },
            )
        return self.get_plan(project_id, book_plan_id)

    def start(
        self, project_id: str, book_plan_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        plan = self._plan_row(project_id, book_plan_id)
        self._check_revision(plan, expected_revision, {"ready"})
        self._assert_snapshot_fresh(plan)
        with self.database.writer() as connection:
            self._update_plan(connection, book_plan_id, expected_revision, "active")
            self._audit(
                connection,
                project_id,
                "book.plan_started",
                {"book_plan_id": book_plan_id, "external_requests_started": 0},
            )
        return self.get_plan(project_id, book_plan_id)

    def advance(
        self, project_id: str, book_plan_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        initial = self._plan_row(project_id, book_plan_id)
        self._check_revision(initial, expected_revision, {"active"})
        self._sync_linked_jobs(project_id, book_plan_id)
        plan = self._plan_row(project_id, book_plan_id)
        if str(plan["status"]) != "active":
            return self.get_plan(project_id, book_plan_id)
        self._assert_snapshot_fresh(plan)
        with self.database.reader() as connection:
            next_chapter = connection.execute(
                """SELECT * FROM book_production_chapters
                   WHERE book_plan_id = ? AND status != 'completed'
                   ORDER BY ordinal LIMIT 1""",
                (book_plan_id,),
            ).fetchone()
        if next_chapter is None:
            self._complete_plan(book_plan_id)
            return self.get_plan(project_id, book_plan_id)
        if str(next_chapter["status"]) != "approved":
            return self.get_plan(project_id, book_plan_id)
        try:
            job = self.queue.create_job(
                project_id,
                str(next_chapter["chapter_id"]),
                plan_fingerprint=str(next_chapter["generation_plan_fingerprint"]),
                per_panel_cost_ceiling_anlas=int(plan["per_panel_cost_ceiling_anlas"]),
                max_calls=int(next_chapter["max_calls"]),
                max_cost_anlas=int(next_chapter["max_cost_anlas"]),
                confirmed=True,
            )
        except ApplicationError as exc:
            self._mark_plan_needs_review(
                project_id,
                book_plan_id,
                str(next_chapter["book_chapter_plan_id"]),
                exc.code,
            )
            raise
        with self.database.writer() as connection:
            connection.execute(
                """UPDATE book_production_chapters
                   SET status = 'job_created', generation_job_id = ?,
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE book_chapter_plan_id = ? AND status = 'approved'""",
                (job["job_id"], str(next_chapter["book_chapter_plan_id"])),
            )
            connection.execute(
                """UPDATE book_production_plans SET revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP WHERE book_plan_id = ?""",
                (book_plan_id,),
            )
            self._audit(
                connection,
                project_id,
                "book.chapter_job_created",
                {
                    "book_plan_id": book_plan_id,
                    "book_chapter_plan_id": str(next_chapter["book_chapter_plan_id"]),
                    "generation_job_id": job["job_id"],
                    "external_request_created": False,
                },
            )
        return self.get_plan(project_id, book_plan_id)

    def pause(
        self, project_id: str, book_plan_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        plan = self._plan_row(project_id, book_plan_id)
        self._check_revision(plan, expected_revision, {"active"})
        self._transition_linked_job(project_id, book_plan_id, "pause")
        with self.database.writer() as connection:
            self._set_plan_status(connection, book_plan_id, "paused")
            self._audit(
                connection,
                project_id,
                "book.plan_paused",
                {"book_plan_id": book_plan_id, "external_requests_started": 0},
            )
        self._sync_linked_jobs(project_id, book_plan_id, preserve_plan_status=True)
        return self.get_plan(project_id, book_plan_id)

    def resume(
        self, project_id: str, book_plan_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        plan = self._plan_row(project_id, book_plan_id)
        self._check_revision(plan, expected_revision, {"paused"})
        self._assert_snapshot_fresh(plan)
        self._transition_linked_job(project_id, book_plan_id, "resume")
        with self.database.writer() as connection:
            self._set_plan_status(connection, book_plan_id, "active")
            self._audit(
                connection,
                project_id,
                "book.plan_resumed",
                {"book_plan_id": book_plan_id, "external_requests_started": 0},
            )
        self._sync_linked_jobs(project_id, book_plan_id, preserve_plan_status=True)
        return self.get_plan(project_id, book_plan_id)

    def cancel(
        self, project_id: str, book_plan_id: str, *, expected_revision: int
    ) -> dict[str, Any]:
        plan = self._plan_row(project_id, book_plan_id)
        self._check_revision(
            plan,
            expected_revision,
            {"awaiting_approval", "ready", "active", "paused", "needs_review"},
        )
        self._transition_linked_job(project_id, book_plan_id, "cancel")
        with self.database.writer() as connection:
            connection.execute(
                """UPDATE book_production_chapters SET status = 'canceled',
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE book_plan_id = ? AND status != 'completed'""",
                (book_plan_id,),
            )
            self._set_plan_status(connection, book_plan_id, "canceled", completed=True)
            self._audit(
                connection,
                project_id,
                "book.plan_canceled",
                {"book_plan_id": book_plan_id, "external_requests_started": 0},
            )
        return self.get_plan(project_id, book_plan_id)

    def retry_chapter(
        self,
        project_id: str,
        book_plan_id: str,
        book_chapter_plan_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        plan = self._plan_row(project_id, book_plan_id)
        self._check_revision(plan, expected_revision, {"needs_review", "paused"})
        with self.database.reader() as connection:
            chapter = connection.execute(
                """SELECT * FROM book_production_chapters
                   WHERE book_plan_id = ? AND book_chapter_plan_id = ?""",
                (book_plan_id, book_chapter_plan_id),
            ).fetchone()
        if chapter is None or str(chapter["status"]) not in {
            "needs_review",
            "failed",
            "canceled",
        }:
            raise ApplicationError(
                "BOOK_CHAPTER_RETRY_INVALID", "该章节当前不需要恢复。", 409
            )
        if chapter["generation_job_id"] is not None:
            job = self.queue.get_job(project_id, str(chapter["generation_job_id"]))
            if job["status"] in OPEN_JOB_STATUSES or job["status"] == "failed":
                self.queue.cancel_job(
                    project_id,
                    job["job_id"],
                    expected_revision=job["revision"],
                )
        with self.database.writer() as connection:
            connection.execute(
                """UPDATE book_production_chapters
                   SET status = 'approved', generation_job_id = NULL,
                       retry_count = retry_count + 1, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE book_chapter_plan_id = ?""",
                (book_chapter_plan_id,),
            )
            self._set_plan_status(connection, book_plan_id, "paused")
            self._audit(
                connection,
                project_id,
                "book.chapter_retry_approved",
                {
                    "book_plan_id": book_plan_id,
                    "book_chapter_plan_id": book_chapter_plan_id,
                    "external_requests_started": 0,
                },
            )
        return self.get_plan(project_id, book_plan_id)

    def reconcile_startup(self) -> dict[str, int]:
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT book_plan_id, project_id FROM book_production_plans
                   WHERE status = 'active'"""
            ).fetchall()
        paused = 0
        needs_review = 0
        for row in rows:
            plan_id = str(row["book_plan_id"])
            project_id = str(row["project_id"])
            self._sync_linked_jobs(project_id, plan_id)
            plan = self._plan_row(project_id, plan_id)
            if str(plan["status"]) == "needs_review":
                needs_review += 1
                continue
            with self.database.writer() as connection:
                self._set_plan_status(connection, plan_id, "paused")
            paused += 1
        return {"book_plans_paused": paused, "book_plans_needs_review": needs_review}

    def _sync_linked_jobs(
        self,
        project_id: str,
        book_plan_id: str,
        *,
        preserve_plan_status: bool = False,
    ) -> None:
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT book_chapter_plan_id, generation_job_id, status
                   FROM book_production_chapters
                   WHERE book_plan_id = ? AND generation_job_id IS NOT NULL""",
                (book_plan_id,),
            ).fetchall()
        needs_review = False
        changed = False
        mapping = {
            "queued": "job_created",
            "running": "running",
            "paused": "paused",
            "needs_review": "needs_review",
            "failed": "failed",
            "completed": "completed",
            "canceled": "canceled",
        }
        with self.database.writer() as connection:
            for row in rows:
                job = self.queue.get_job(project_id, str(row["generation_job_id"]))
                target = mapping[str(job["status"])]
                if target in {"needs_review", "failed", "canceled"}:
                    needs_review = True
                if target != str(row["status"]):
                    connection.execute(
                        """UPDATE book_production_chapters SET status = ?,
                               revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                           WHERE book_chapter_plan_id = ?""",
                        (target, str(row["book_chapter_plan_id"])),
                    )
                    changed = True
            remaining = int(
                connection.execute(
                    """SELECT COUNT(*) AS count FROM book_production_chapters
                       WHERE book_plan_id = ? AND status != 'completed'""",
                    (book_plan_id,),
                ).fetchone()["count"]
            )
            plan = connection.execute(
                "SELECT status FROM book_production_plans WHERE book_plan_id = ?",
                (book_plan_id,),
            ).fetchone()
            if plan is None or str(plan["status"]) in TERMINAL_PLAN_STATUSES:
                return
            target_plan: str | None = None
            if remaining == 0:
                target_plan = "completed"
            elif needs_review and not preserve_plan_status:
                target_plan = "needs_review"
            if target_plan is not None and target_plan != str(plan["status"]):
                self._set_plan_status(
                    connection,
                    book_plan_id,
                    target_plan,
                    completed=target_plan == "completed",
                )
            elif changed:
                connection.execute(
                    """UPDATE book_production_plans SET revision = revision + 1,
                           updated_at = CURRENT_TIMESTAMP WHERE book_plan_id = ?""",
                    (book_plan_id,),
                )

    def _transition_linked_job(
        self, project_id: str, book_plan_id: str, action: str
    ) -> None:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT generation_job_id FROM book_production_chapters
                   WHERE book_plan_id = ? AND generation_job_id IS NOT NULL
                     AND status IN ('job_created', 'running', 'paused', 'needs_review', 'failed')
                   ORDER BY ordinal LIMIT 1""",
                (book_plan_id,),
            ).fetchone()
        if row is None:
            return
        job = self.queue.get_job(project_id, str(row["generation_job_id"]))
        if action == "pause" and job["status"] in {"queued", "running"}:
            self.queue.pause_job(project_id, job["job_id"], expected_revision=job["revision"])
        elif action == "resume" and job["status"] == "paused":
            self.queue.resume_job(project_id, job["job_id"], expected_revision=job["revision"])
        elif action == "cancel" and job["status"] in OPEN_JOB_STATUSES | {"failed"}:
            self.queue.cancel_job(project_id, job["job_id"], expected_revision=job["revision"])

    def _assert_snapshot_fresh(self, plan: sqlite3.Row) -> None:
        chapter_set_id, chapters = self._current_chapters(str(plan["project_id"]))
        if chapter_set_id != str(plan["source_chapter_set_id"]):
            raise ApplicationError(
                "BOOK_PLAN_SOURCE_STALE", "章节边界已变化，请创建新的整本计划。", 409
            )
        continuity = self._approved_continuity(
            str(plan["project_id"]), int(chapters[-1]["ordinal"])
        )
        if continuity["continuity_version_id"] != str(plan["continuity_version_id"]):
            raise ApplicationError(
                "BOOK_PLAN_CONTINUITY_STALE", "连续性账本已变化，请创建新的整本计划。", 409
            )

    def _current_chapters(self, project_id: str) -> tuple[str, list[sqlite3.Row]]:
        with self.database.reader() as connection:
            rows = connection.execute(
                """SELECT cs.chapter_set_id, sc.chapter_id, sc.ordinal, sc.title
                   FROM source_chapter_sets cs
                   JOIN source_files sf ON sf.source_file_id = cs.source_file_id
                   JOIN source_chapters sc ON sc.chapter_set_id = cs.chapter_set_id
                   WHERE sf.project_id = ? AND cs.is_current = 1 ORDER BY sc.ordinal""",
                (project_id,),
            ).fetchall()
        if not rows:
            raise ApplicationError("BOOK_SOURCE_EMPTY", "当前项目没有可规划章节。", 409)
        return str(rows[0]["chapter_set_id"]), list(rows)

    def _approved_continuity(
        self, project_id: str, final_ordinal: int
    ) -> dict[str, Any]:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT v.continuity_version_id, sc.ordinal
                   FROM continuity_ledgers l
                   JOIN continuity_ledger_versions v
                     ON v.continuity_ledger_id = l.continuity_ledger_id
                   JOIN continuity_approvals a
                     ON a.continuity_version_id = v.continuity_version_id
                   JOIN source_chapters sc ON sc.chapter_id = v.through_chapter_id
                   WHERE l.project_id = ? AND v.is_current = 1""",
                (project_id,),
            ).fetchone()
        if row is None or int(row["ordinal"]) != final_ordinal:
            raise ApplicationError(
                "BOOK_CONTINUITY_APPROVAL_REQUIRED",
                "请先把连续性账本逐章推进并批准到最后一章。",
                409,
            )
        return {
            "continuity_version_id": str(row["continuity_version_id"]),
            "through_chapter_ordinal": int(row["ordinal"]),
        }

    @staticmethod
    def _allocate_limits(
        chapters: list[dict[str, Any]],
        per_panel_cost: int,
        max_calls: int,
        max_cost: int,
    ) -> list[dict[str, int]]:
        minimum_calls = sum(int(item["panel_count"]) for item in chapters)
        minimum_cost = sum(int(item["estimated_cost_upper_anlas"]) for item in chapters)
        extra_calls = max_calls - minimum_calls
        extra_cost = max_cost - minimum_cost
        allocations: list[dict[str, int]] = []
        for item in chapters:
            panel_count = int(item["panel_count"])
            affordable_extra = extra_calls if per_panel_cost == 0 else extra_cost // per_panel_cost
            allocated_extra = min(panel_count * 2, extra_calls, affordable_extra)
            chapter_calls = panel_count + allocated_extra
            chapter_cost = int(item["estimated_cost_upper_anlas"]) + (
                allocated_extra * per_panel_cost
            )
            allocations.append({"max_calls": chapter_calls, "max_cost_anlas": chapter_cost})
            extra_calls -= allocated_extra
            extra_cost -= allocated_extra * per_panel_cost
        return allocations

    @staticmethod
    def _chapter_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "book_chapter_plan_id": str(row["book_chapter_plan_id"]),
            "chapter_id": str(row["chapter_id"]),
            "ordinal": int(row["ordinal"]),
            "title": str(row["title"]),
            "storyboard_version_id": str(row["storyboard_version_id"]),
            "character_bible_version_id": str(row["character_bible_version_id"]),
            "style_bible_version_id": str(row["style_bible_version_id"]),
            "generation_plan_fingerprint": str(row["generation_plan_fingerprint"]),
            "page_count": int(row["page_count"]),
            "panel_count": int(row["panel_count"]),
            "estimated_cost_upper_anlas": int(row["estimated_cost_upper_anlas"]),
            "max_calls": int(row["max_calls"]),
            "max_cost_anlas": int(row["max_cost_anlas"]),
            "status": str(row["status"]),
            "approval_hash": row["approval_hash"],
            "approved_at": row["approved_at"],
            "generation_job_id": row["generation_job_id"],
            "generation_job_status": row["generation_job_status"],
            "retry_count": int(row["retry_count"]),
            "revision": int(row["revision"]),
            "calls_started": int(row["calls_started"] or 0),
            "calls_completed": int(row["calls_completed"] or 0),
            "recorded_cost_anlas": int(row["recorded_cost_anlas"] or 0),
            "allocated_cost_anlas": int(row["allocated_cost_anlas"] or 0),
        }

    def _plan_row(self, project_id: str, book_plan_id: str) -> sqlite3.Row:
        with self.database.reader() as connection:
            row = connection.execute(
                """SELECT * FROM book_production_plans
                   WHERE project_id = ? AND book_plan_id = ?""",
                (project_id, book_plan_id),
            ).fetchone()
        if row is None:
            raise ApplicationError("BOOK_PLAN_NOT_FOUND", "没有找到该整本计划。", 404)
        return cast(sqlite3.Row, row)

    @staticmethod
    def _check_revision(
        plan: sqlite3.Row, expected_revision: int, allowed: set[str]
    ) -> None:
        if int(plan["revision"]) != expected_revision:
            raise ApplicationError(
                "BOOK_PLAN_REVISION_CONFLICT", "整本计划已变化，请刷新后重试。", 409
            )
        if str(plan["status"]) not in allowed:
            raise ApplicationError(
                "BOOK_PLAN_TRANSITION_INVALID", "当前整本计划状态不允许此操作。", 409
            )

    @staticmethod
    def _update_plan(
        connection: sqlite3.Connection,
        book_plan_id: str,
        expected_revision: int,
        status: str,
    ) -> None:
        connection.execute(
            """UPDATE book_production_plans SET status = ?, revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
               WHERE book_plan_id = ? AND revision = ?""",
            (status, book_plan_id, expected_revision),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise ApplicationError(
                "BOOK_PLAN_REVISION_CONFLICT", "整本计划已变化，请刷新后重试。", 409
            )

    @staticmethod
    def _set_plan_status(
        connection: sqlite3.Connection,
        book_plan_id: str,
        status: str,
        *,
        completed: bool = False,
    ) -> None:
        completed_sql = ", completed_at = CURRENT_TIMESTAMP" if completed else ""
        connection.execute(
            f"""UPDATE book_production_plans SET status = ?, revision = revision + 1,
                    updated_at = CURRENT_TIMESTAMP {completed_sql}
                WHERE book_plan_id = ?""",
            (status, book_plan_id),
        )

    def _complete_plan(self, book_plan_id: str) -> None:
        with self.database.writer() as connection:
            self._set_plan_status(connection, book_plan_id, "completed", completed=True)

    def _mark_plan_needs_review(
        self,
        project_id: str,
        book_plan_id: str,
        book_chapter_plan_id: str,
        error_code: str,
    ) -> None:
        with self.database.writer() as connection:
            connection.execute(
                """UPDATE book_production_chapters SET status = 'needs_review',
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                   WHERE book_chapter_plan_id = ?""",
                (book_chapter_plan_id,),
            )
            self._set_plan_status(connection, book_plan_id, "needs_review")
            self._audit(
                connection,
                project_id,
                "book.plan_needs_review",
                {"book_plan_id": book_plan_id, "error_code": error_code},
            )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(event_id, project_id, event_type, payload_json)
               VALUES (?, ?, ?, ?)""",
            (str(uuid7()), project_id, event_type, canonical_json(payload)),
        )
