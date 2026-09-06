from __future__ import annotations

import hashlib
from typing import Any

from ..errors import ApplicationError
from ..fullpages.service import FullPageService
from ..ingestion.txt import TxtIngestionService
from ..pages.service import PageService
from ..workflows.book_production.contracts import (
    PAGE_REVIEW_RULE_VERSION,
    ArtifactPointer,
    TaskSnapshot,
    UnitReference,
)


class LegacyContextDomainReader:
    """Read-only compatibility seam; orchestration never queries other owners' tables."""

    def __init__(
        self, ingestion: TxtIngestionService, full_pages: FullPageService, pages: PageService
    ) -> None:
        self.ingestion, self.full_pages, self.pages = ingestion, full_pages, pages

    def source_matches(self, project_id: str, source: ArtifactPointer) -> bool:
        try:
            if source.kind == "source":
                chapters = self.ingestion.current_chapters(project_id)
                current = any(
                    str(chapter["chapter_id"]) == str(source.artifact_id)
                    and chapter["text_sha256"] == source.sha256
                    for chapter in chapters["chapters"]
                )
                text = self.ingestion.chapter_text(project_id, str(source.artifact_id))["text"]
                return current and hashlib.sha256(text.encode("utf-8")).hexdigest() == source.sha256
            if source.kind == "generation":
                return bool(
                    self.full_pages.get(project_id, str(source.artifact_id))["plan_sha256"]
                    == source.sha256
                )
            if source.kind == "page":
                page = self.pages.get_current(project_id, str(source.artifact_id))
                path = self.pages.content_path(
                    project_id, str(source.artifact_id), page["page_version_id"]
                )
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                return digest == str(page["render_sha256"]) == source.sha256
        except (ApplicationError, OSError, UnicodeError):
            return False
        return False

    def capture(self, project_id: str, objective: str) -> TaskSnapshot:
        chapters = self.ingestion.current_chapters(project_id)
        units: list[UnitReference] = []
        sources: list[ArtifactPointer] = []
        for chapter in chapters["chapters"]:
            sources.append(
                ArtifactPointer(
                    kind="source", artifact_id=chapter["chapter_id"], sha256=chapter["text_sha256"]
                )
            )
            generations = self.full_pages.list_current_generations(
                project_id, chapter["chapter_id"]
            )
            selected: dict[str, dict[str, Any]] = {}
            for generation in generations:
                selected.setdefault(generation["page_id"], generation)
            for generation in sorted(selected.values(), key=lambda row: row["page_number"]):
                page_id = None
                try:
                    page_id = self.pages.get_current(project_id, generation["page_id"])["page_id"]
                except ApplicationError as exc:
                    if exc.status_code != 404:
                        raise
                units.append(
                    UnitReference(
                        unit_id=f"page-{len(units) + 1}",
                        page_number=len(units) + 1,
                        generation_id=generation["generation_id"],
                        plan_sha256=generation["plan_sha256"],
                        page_id=page_id,
                    )
                )
        if not units:
            raise ApplicationError("NO_FROZEN_PAGE_PLANS", "请先准备并冻结页面生产计划。", 409)
        return TaskSnapshot(
            objective=objective,
            constraints=("沿用已有生成审批、计划和成本上限",),
            acceptance_criteria=("所有计划页面逐页审查", "全书连续性、导出与重开验证"),
            stage="production",
            next_action="核对下一页生成与审查状态",
            source_refs=tuple(sources),
            units=tuple(units),
        )

    def inspect_unit(self, project_id: str, unit: UnitReference) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "draft",
            "generated": False,
            "lettered": False,
            "reviewed": False,
            "blocked": False,
            "image_sha256": None,
            "render_sha256": None,
            "review_record": "pending",
        }
        try:
            if unit.generation_id is None:
                return result
            generation = self.full_pages.get(project_id, str(unit.generation_id))
            result.update(status=generation["status"], image_sha256=generation["image_sha256"])
            if generation["plan_sha256"] != unit.plan_sha256:
                result.update(blocked=True, code="GENERATION_PLAN_CHANGED")
                return result
            self.full_pages.verify_current_plan(project_id, str(unit.generation_id))
            if generation["status"] in {"running", "needs_review", "failed"}:
                result.update(blocked=True, code="GENERATION_OUTCOME_REQUIRES_REVIEW")
                return result
            if generation["status"] != "ready":
                return result
            path = self.full_pages.content_path(project_id, str(unit.generation_id))
            with path.open("rb") as stream:
                image_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if image_hash != generation["image_sha256"]:
                result.update(blocked=True, code="GENERATION_IMAGE_HASH_MISMATCH")
                return result
            result["generated"] = True
            if unit.page_id is not None and str(unit.page_id) != generation["page_id"]:
                result.update(blocked=True, code="GENERATION_PAGE_MISMATCH")
                return result
            page = None
            try:
                page = self.pages.get_current(project_id, generation["page_id"])
            except ApplicationError as exc:
                if exc.status_code != 404 or unit.page_id is not None:
                    raise
            if page is not None:
                page_image = page["document"].get("page_image")
                if not page_image or page_image["generation_id"] != str(unit.generation_id):
                    result.update(blocked=True, code="PAGE_GENERATION_MISMATCH")
                    return result
                content = self.pages.content_path(
                    project_id, generation["page_id"], page["page_version_id"]
                )
                with content.open("rb") as stream:
                    rendered_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                if rendered_hash != page["render_sha256"]:
                    result.update(blocked=True, code="PAGE_RENDER_HASH_MISMATCH")
                    return result
                result.update(
                    lettered=True,
                    render_sha256=rendered_hash,
                    renderer_version=page["renderer_version"],
                    font_sha256=page["font_sha256"],
                )
            review = unit.review
            if review is not None and review.result == "accepted":
                valid = (
                    review.image_sha256 == result["image_sha256"]
                    and review.render_sha256 is not None
                    and review.render_sha256 == result["render_sha256"]
                    and review.plan_sha256 == unit.plan_sha256
                    and review.rule_version == PAGE_REVIEW_RULE_VERSION
                    and review.renderer_version == result.get("renderer_version")
                    and review.font_sha256 == result.get("font_sha256")
                )
                result.update(reviewed=valid, review_record="valid" if valid else "stale")
            return result
        except (ApplicationError, OSError) as exc:
            code = exc.code if isinstance(exc, ApplicationError) else "CONTEXT_ARTIFACT_MISSING"
            result.update(blocked=True, code=code)
            return result
