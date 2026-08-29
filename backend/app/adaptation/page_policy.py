from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import PageType, StoryboardDocument

STORYBOARD_PAGE_POLICY_VERSION = "storyboard-page-count-v1"
SPECIAL_PAGE_TYPES: frozenset[PageType] = frozenset({"cover", "splash", "special"})


@dataclass(frozen=True, slots=True)
class StoryboardPagePolicyFinding:
    code: Literal[
        "STORYBOARD_PAGE_POLICY_INVALID",
        "STORYBOARD_UPGRADE_REQUIRED",
        "STORYBOARD_SEMANTICS_INVALID",
    ]
    path: str
    message: str
    page_id: str | None
    page_number: int | None
    page_type: str | None
    panel_count: int | None
    minimum_panels: int | None
    maximum_panels: int | None

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "page_id": self.page_id,
            "page_number": self.page_number,
            "page_type": self.page_type,
            "panel_count": self.panel_count,
            "allowed_range": (
                {
                    "minimum": self.minimum_panels,
                    "maximum": self.maximum_panels,
                }
                if self.minimum_panels is not None and self.maximum_panels is not None
                else None
            ),
        }


class StoryboardPagePolicyError(ValueError):
    def __init__(self, findings: tuple[StoryboardPagePolicyFinding, ...]) -> None:
        self.findings = findings
        summary = "; ".join(
            f"{finding.path}: {finding.message}" for finding in findings[:5]
        )
        super().__init__(summary or "storyboard page policy is invalid")


def storyboard_page_policy_findings(
    document: StoryboardDocument,
    *,
    require_current_schema: bool = True,
) -> tuple[StoryboardPagePolicyFinding, ...]:
    if require_current_schema and document.schema_version != "1.1":
        return (
            StoryboardPagePolicyFinding(
                code="STORYBOARD_UPGRADE_REQUIRED",
                path="$.schema_version",
                message="Storyboard 1.0 仅供历史只读, 请重新规划为 1.1。",
                page_id=None,
                page_number=None,
                page_type=None,
                panel_count=None,
                minimum_panels=None,
                maximum_panels=None,
            ),
        )

    findings: list[StoryboardPagePolicyFinding] = []
    for index, page in enumerate(document.pages):
        panel_count = len(page.panels)
        path = f"$.pages[{index}]"
        if page.page_type is None:
            findings.append(
                StoryboardPagePolicyFinding(
                    code="STORYBOARD_PAGE_POLICY_INVALID",
                    path=f"{path}.page_type",
                    message="页面缺少模型生成的 page_type。",
                    page_id=str(page.page_id),
                    page_number=page.page_number,
                    page_type=None,
                    panel_count=panel_count,
                    minimum_panels=None,
                    maximum_panels=None,
                )
            )
            continue

        minimum = 3 if page.page_type == "standard" else 1
        maximum = 6
        if panel_count < minimum or panel_count > maximum:
            findings.append(
                StoryboardPagePolicyFinding(
                    code="STORYBOARD_PAGE_POLICY_INVALID",
                    path=f"{path}.panels",
                    message=(
                        f"{page.page_type} 页面需要 {minimum}-{maximum} 格，"
                        f"当前为 {panel_count} 格。"
                    ),
                    page_id=str(page.page_id),
                    page_number=page.page_number,
                    page_type=page.page_type,
                    panel_count=panel_count,
                    minimum_panels=minimum,
                    maximum_panels=maximum,
                )
            )
    return tuple(findings)


def validate_storyboard_page_policy(
    document: StoryboardDocument,
    *,
    require_current_schema: bool = True,
) -> None:
    findings = storyboard_page_policy_findings(
        document,
        require_current_schema=require_current_schema,
    )
    if findings:
        raise StoryboardPagePolicyError(findings)
