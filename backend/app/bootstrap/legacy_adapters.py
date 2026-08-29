from __future__ import annotations

from ..adaptation.models import StoryboardDocument
from ..adaptation.page_policy import (
    STORYBOARD_PAGE_POLICY_VERSION,
    StoryboardPagePolicyError,
    storyboard_page_policy_findings,
)
from ..adaptation.service import AdaptationService
from ..errors import ApplicationError
from ..modules.adaptation.contracts import StoryboardPageSnapshotV1, StoryboardVersionRefV1
from ..shared_kernel import canonical_sha256


class LegacyAdaptationFacade:
    """Typed read adapter over the v0.2 storyboard service during migration."""

    def __init__(self, service: AdaptationService) -> None:
        self._service = service

    def current_storyboard_ref(
        self,
        project_id: str,
        chapter_id: str,
    ) -> StoryboardVersionRefV1:
        return self._ref(self._service.get_current_storyboard(project_id, chapter_id))

    def storyboard_page(
        self,
        project_id: str,
        storyboard_version_id: str,
        page_id: str,
    ) -> StoryboardPageSnapshotV1:
        payload = self._service.get_storyboard_version(project_id, storyboard_version_id)
        document = payload["document"]
        if not isinstance(document, dict):
            raise ValueError("storyboard document is invalid")
        parsed_document = StoryboardDocument.model_validate(document)
        findings = storyboard_page_policy_findings(parsed_document)
        if findings:
            raise page_policy_application_error(StoryboardPagePolicyError(findings))
        if payload["approval_status"] != "approved":
            raise ApplicationError(
                code="STORYBOARD_NOT_APPROVED",
                message="请先审批当前 Storyboard。",
                status_code=409,
            )
        pages = document.get("pages")
        if not isinstance(pages, list):
            raise ValueError("storyboard pages are invalid")
        page = next(
            (
                item
                for item in pages
                if isinstance(item, dict) and str(item.get("page_id")) == page_id
            ),
            None,
        )
        if page is None:
            raise ValueError("storyboard version does not contain the requested page")
        panels = page.get("panels")
        if not isinstance(panels, list):
            raise ValueError("storyboard page panels are invalid")
        page_type = page.get("page_type")
        if page_type not in {"standard", "cover", "splash", "special"}:
            raise ValueError("storyboard page type is invalid")
        return StoryboardPageSnapshotV1(
            project_id=project_id,
            chapter_id=str(payload["chapter_id"]),
            page_id=page_id,
            page_type=page_type,
            page_policy_version=STORYBOARD_PAGE_POLICY_VERSION,
            storyboard=self._ref(payload),
            panel_ids=tuple(str(panel["panel_id"]) for panel in panels),
        )
    @staticmethod
    def _ref(payload: dict[str, object]) -> StoryboardVersionRefV1:
        document = payload["document"]
        return StoryboardVersionRefV1(
            storyboard_id=str(payload["storyboard_id"]),
            storyboard_version_id=str(payload["storyboard_version_id"]),
            version=int(str(payload["version"])),
            content_sha256=canonical_sha256(document),
            approved=payload["approval_status"] == "approved",
        )


def page_policy_application_error(error: StoryboardPagePolicyError) -> ApplicationError:
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
            else "Storyboard 页面政策无效，不能创建或修改版式。"
        ),
        status_code=409 if upgrade_required else 422,
        details={
            "page_policy_version": STORYBOARD_PAGE_POLICY_VERSION,
            "findings": [finding.payload() for finding in error.findings],
        },
    )
