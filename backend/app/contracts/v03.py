from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..modules.layout.contracts import PageLayoutDraft
from ..modules.production.contracts import ProviderExecutionSpec
from ..modules.prompting.contracts import PromptPackage, PromptPlan
from ..modules.review.contracts import (
    PageApproval,
    PanelCandidate,
    PanelCandidateSet,
    QualityFinding,
    ReviewDecision,
    ReviewSnapshot,
)
from ..modules.text_execution.contracts import TextStageRun
from ..shared_kernel.canonical_json import canonical_json_bytes

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "page-layout-draft": PageLayoutDraft,
    "prompt-plan": PromptPlan,
    "prompt-package": PromptPackage,
    "provider-execution-spec": ProviderExecutionSpec,
    "panel-candidate": PanelCandidate,
    "panel-candidate-set": PanelCandidateSet,
    "quality-finding": QualityFinding,
    "review-decision": ReviewDecision,
    "page-approval": PageApproval,
    "review-snapshot": ReviewSnapshot,
    "text-stage-run": TextStageRun,
}


def schema_directory(root: Path) -> Path:
    return root / "contracts" / "schemas" / "v0.3"


def rendered_schemas() -> dict[str, bytes]:
    rendered: dict[str, bytes] = {}
    for name, model in SCHEMA_MODELS.items():
        schema: dict[str, Any] = model.model_json_schema(
            mode="validation",
            ref_template="#/$defs/{model}",
        )
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://manga-maker.local/contracts/v0.3/{name}.schema.json"
        rendered[f"{name}.schema.json"] = canonical_json_bytes(schema) + b"\n"
    return rendered
