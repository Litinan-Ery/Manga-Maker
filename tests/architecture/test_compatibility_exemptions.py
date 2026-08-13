from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.app.bootstrap.architecture_exemptions import ARCHITECTURE_EXEMPTIONS
from backend.app.bootstrap.legacy import (
    LEGACY_API_APP_STATE_LOOKUPS,
    LEGACY_APP_STATE_ALLOWLIST,
)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "app"
ADR_DOCUMENT = ROOT / "docs" / "adr" / "ADR-010-018.md"


def _attribute_path(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _state_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parent = _attribute_path(node.value)
        if parent is not None and (parent == "app.state" or parent.endswith(".app.state")):
            result.add(node.attr)
    return result


def test_every_architecture_exemption_is_owned_specific_and_deletable() -> None:
    assert ARCHITECTURE_EXEMPTIONS
    ids = [exemption.exemption_id for exemption in ARCHITECTURE_EXEMPTIONS]
    assert len(ids) == len(set(ids)), "architecture exemption ids must be unique"
    adr_source = ADR_DOCUMENT.read_text(encoding="utf-8")
    forbidden_lifetime_terms = ("permanent", "forever", "never remove", "永久", "永不删除")

    for exemption in ARCHITECTURE_EXEMPTIONS:
        assert re.fullmatch(r"ARCH-EX-\d{3}", exemption.exemption_id)
        assert re.fullmatch(r"MM-\d{3}", exemption.owner)
        assert len(exemption.reason) >= 20
        assert len(exemption.impact) >= 20
        assert len(exemption.delete_condition) >= 30
        assert f"## {exemption.adr}{chr(0xFF1A)}" in adr_source
        metadata = " ".join(
            (exemption.reason, exemption.impact, exemption.delete_condition)
        ).lower()
        assert not any(term in metadata for term in forbidden_lifetime_terms)
        for pattern in exemption.paths:
            matches = tuple(ROOT.glob(pattern))
            assert matches, f"{exemption.exemption_id} path does not exist: {pattern}"


def test_legacy_api_state_lookup_allowlist_matches_the_ast_exactly() -> None:
    observed: dict[str, frozenset[str]] = {}
    for path in sorted((BACKEND / "api").glob("*.py")):
        attributes = _state_attributes(path)
        if attributes:
            observed[path.name] = frozenset(attributes)
    assert observed == dict(LEGACY_API_APP_STATE_LOOKUPS)


def test_all_remaining_backend_state_access_is_an_exact_compatibility_seam() -> None:
    expected = {
        "backend/app/bootstrap/dependencies.py": frozenset({"container"}),
        "backend/app/launcher.py": frozenset({"local_session"}),
    }
    observed: dict[str, frozenset[str]] = {}
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts or path.parent == BACKEND / "api":
            continue
        relative = path.relative_to(ROOT).as_posix()
        attributes = _state_attributes(path)
        if not attributes:
            continue
        if relative == "backend/app/bootstrap/legacy.py":
            assert attributes == LEGACY_APP_STATE_ALLOWLIST | {"container"}
            continue
        observed[relative] = frozenset(attributes)
    assert observed == expected
