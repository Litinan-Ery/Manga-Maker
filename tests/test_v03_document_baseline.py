from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "architecture" / "V03_IMPLEMENTATION_BASELINE.md"
ADR_RECORD = ROOT / "docs" / "adr" / "ADR-010-018.md"
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "PRD.md",
    ROOT / "TECHNICAL_ARCHITECTURE.md",
    ROOT / "WORK_ITEMS.md",
    BASELINE,
    ADR_RECORD,
)


def test_v03_status_and_traceability_are_explicit() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    work_items = (ROOT / "WORK_ITEMS.md").read_text(encoding="utf-8")
    baseline = BASELINE.read_text(encoding="utf-8")

    assert "v0.3 已完成 Wave 3，整体尚未完成" in readme
    for ticket in (
        "MM-035 PromptPlan v2 与固定 Tags 结构化编译",
        "MM-036 ProviderExecutionSpec 与 NovelAI 多角色映射",
        "MM-037 Prompt 审批与 Job 冻结",
        "MM-057 Prompt Inspector 与脱敏载荷预览",
    ):
        assert f"{ticket} | P0 | Done" in work_items
    for requirement in ("FR-20", "FR-21", "FR-22", "FR-23"):
        assert requirement in work_items
        assert requirement in baseline
    for acceptance in ("AC-09", "AC-10", "AC-11", "AC-12"):
        assert acceptance in work_items
        assert acceptance in baseline
    for module in (
        "project_source",
        "text_execution",
        "adaptation",
        "world_bible",
        "layout",
        "prompting",
        "production",
        "review",
        "composition",
        "asset_catalog",
        "exporting",
        "lineage",
    ):
        assert f"`{module}`" in baseline


def test_adrs_define_compatibility_rollback_and_deletion() -> None:
    record = ADR_RECORD.read_text(encoding="utf-8")
    for number in range(10, 19):
        section_start = record.index(f"## ADR-{number:03d}")
        next_marker = f"## ADR-{number + 1:03d}"
        section_end = record.find(next_marker, section_start)
        section = record[section_start:] if section_end == -1 else record[section_start:section_end]
        for label in ("选择\uff1a", "拒绝\uff1a", "兼容期\uff1a", "回滚\uff1a", "删除条件\uff1a"):
            assert label in section


def test_novelai_contract_metadata_matches_documented_baseline() -> None:
    metadata = json.loads(
        (ROOT / "contracts" / "novelai" / "image-api.contract.json").read_text(
            encoding="utf-8"
        )
    )
    baseline = BASELINE.read_text(encoding="utf-8")
    for value in (
        metadata["source_url"],
        metadata["fetched_on"],
        metadata["sha256"],
        metadata["mapping_version"],
        str(metadata["contract_bytes"]),
    ):
        assert value in baseline.replace(",", "")


def test_governance_documents_do_not_contain_secret_literals() -> None:
    secret_patterns = (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
        re.compile(r"\b(?:api[_-]?key|token|password)\s*[:=]\s*[\"'][^\"']{8,}[\"']", re.I),
    )
    for path in DOC_PATHS:
        content = path.read_text(encoding="utf-8")
        for pattern in secret_patterns:
            assert pattern.search(content) is None, f"possible secret literal in {path.name}"
