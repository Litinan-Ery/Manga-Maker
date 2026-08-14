from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
APP = SRC / "app"
FEATURES = SRC / "features"
SHARED = SRC / "shared"
GENERATED = SRC / "generated"

FROM_IMPORT = re.compile(r"\bfrom\s+['\"]([^'\"]+)['\"]")
SIDE_EFFECT_IMPORT = re.compile(r"\bimport\s+['\"]([^'\"]+)['\"]")
DYNAMIC_IMPORT = re.compile(r"\bimport\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _source_files(root: Path) -> Iterable[Path]:
    return sorted((*root.rglob("*.ts"), *root.rglob("*.tsx")))


def _imports(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    return {
        match.group(1)
        for pattern in (FROM_IMPORT, SIDE_EFFECT_IMPORT, DYNAMIC_IMPORT)
        for match in pattern.finditer(source)
    }


def _target(path: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    return (path.parent / specifier).resolve()


def test_features_only_import_their_own_code_shared_ui_and_generated_api() -> None:
    violations: list[str] = []
    for path in _source_files(FEATURES):
        own_feature = path.relative_to(FEATURES).parts[0]
        for specifier in sorted(_imports(path)):
            target = _target(path, specifier)
            if target is None:
                continue
            if target.is_relative_to(FEATURES):
                target_feature = target.relative_to(FEATURES).parts[0]
                if target_feature != own_feature:
                    violations.append(
                        f"{path.relative_to(ROOT)} imports feature {target_feature} via {specifier}"
                    )
                continue
            if target.is_relative_to(SHARED / "ui") or target.is_relative_to(GENERATED / "api"):
                continue
            if path.name.endswith((".test.ts", ".test.tsx")) and target.is_relative_to(
                ROOT / "contracts" / "fixtures"
            ):
                continue
            violations.append(
                f"{path.relative_to(ROOT)} crosses the feature boundary via {specifier}"
            )
    assert not violations, "\n".join(violations)


def test_app_composes_feature_public_entries_only() -> None:
    violations: list[str] = []
    for path in _source_files(APP):
        for specifier in sorted(_imports(path)):
            target = _target(path, specifier)
            if target is None or not target.is_relative_to(FEATURES):
                continue
            feature_parts = target.relative_to(FEATURES).parts
            if len(feature_parts) != 1 and feature_parts[1:] != ("index",):
                violations.append(
                    f"{path.relative_to(ROOT)} imports feature internals via {specifier}"
                )
    assert not violations, "\n".join(violations)


def test_generated_and_shared_code_do_not_depend_on_features() -> None:
    violations: list[str] = []
    for root in (GENERATED, SHARED):
        for path in _source_files(root):
            for specifier in sorted(_imports(path)):
                target = _target(path, specifier)
                if target is not None and target.is_relative_to(FEATURES):
                    violations.append(f"{path.relative_to(ROOT)} -> {specifier}")
    assert not violations, "reverse feature dependencies:\n" + "\n".join(violations)


def test_legacy_shell_exemption_and_v03_api_seam_are_exact() -> None:
    app_index = (APP / "index.ts").read_text(encoding="utf-8")
    assert set(FROM_IMPORT.findall(app_index)) == {"../App", "./workflow"}

    main = (SRC / "main.tsx").read_text(encoding="utf-8")
    assert 'from "./app/index"' in main
    assert 'from "./App"' not in main

    legacy_api = (SRC / "api.ts").read_text(encoding="utf-8")
    assert "@deprecated v0.2 compatibility seam" in legacy_api
    forbidden = (
        "PageLayoutDraft",
        "PromptPlan",
        "ProviderExecutionSpec",
        "PanelCandidateSet",
        "QualityRun",
        "ReviewDecision",
        "PageApproval",
        "TextStageRun",
    )
    leaked = [name for name in forbidden if name in legacy_api]
    assert not leaked, f"v0.3 contracts leaked into legacy api.ts: {', '.join(leaked)}"
