from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = ROOT / "backend" / "app" / "modules"
WORKFLOWS_ROOT = ROOT / "backend" / "app" / "workflows"

BUSINESS_MODULES = frozenset(
    {
        "adaptation",
        "asset_catalog",
        "composition",
        "exporting",
        "layout",
        "lineage",
        "production",
        "project_source",
        "prompting",
        "review",
        "text_execution",
        "world_bible",
    }
)
ALLOWED_DEPENDENCIES: dict[str, frozenset[str]] = {
    "project_source": frozenset(),
    "text_execution": frozenset(),
    "lineage": frozenset(),
    "adaptation": frozenset({"project_source", "text_execution"}),
    "world_bible": frozenset({"project_source", "adaptation", "text_execution"}),
    "layout": frozenset({"adaptation"}),
    "prompting": frozenset({"adaptation", "world_bible", "layout", "text_execution"}),
    "production": frozenset({"prompting", "world_bible", "layout", "lineage"}),
    "review": frozenset({"production", "world_bible", "layout", "lineage"}),
    "composition": frozenset({"production", "review", "layout", "lineage"}),
    "asset_catalog": frozenset({"project_source", "production"}),
    "exporting": frozenset({"composition", "review", "lineage"}),
}
LEGACY_INTERNAL_IMPORTS = {
    (
        "backend/app/modules/composition/adapters/legacy.py",
        "backend.app.pages.models",
    ),
    (
        "backend/app/modules/composition/adapters/legacy.py",
        "backend.app.pages.service",
    ),
}


@dataclass(frozen=True, slots=True)
class ImportReference:
    module: str
    line: int


def _python_files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> list[ImportReference]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = ".".join(path.parent.relative_to(ROOT).parts)
    references: list[ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(ImportReference(alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = f"{'.' * node.level}{node.module or ''}"
            try:
                resolved = importlib.util.resolve_name(name, package) if node.level else name
            except ImportError as exc:  # pragma: no cover - reports malformed source clearly
                raise AssertionError(f"{path}:{node.lineno}: cannot resolve {name!r}") from exc
            references.append(ImportReference(resolved, node.lineno))
    return references


def _business_target(module: str) -> tuple[str, str | None] | None:
    parts = module.split(".")
    if parts[:3] != ["backend", "app", "modules"] or len(parts) < 4:
        return None
    target = parts[3]
    if target not in BUSINESS_MODULES:
        return None
    surface = parts[4] if len(parts) > 4 else None
    return target, surface


def _first_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(node: str) -> list[str] | None:
        visited.add(node)
        active.append(node)
        active_set.add(node)
        for dependency in sorted(graph[node]):
            if dependency in active_set:
                start = active.index(dependency)
                return [*active[start:], dependency]
            if dependency not in visited:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
        active.pop()
        active_set.remove(node)
        return None

    for module in sorted(graph):
        if module not in visited:
            cycle = visit(module)
            if cycle is not None:
                return cycle
    return None


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


def test_module_dependency_whitelist_public_surfaces_and_cycles() -> None:
    graph = {module: set() for module in BUSINESS_MODULES}
    violations: list[str] = []
    for source in sorted(BUSINESS_MODULES):
        for path in _python_files(MODULES_ROOT / source):
            for reference in _imports(path):
                target = _business_target(reference.module)
                if target is None or target[0] == source:
                    continue
                target_module, surface = target
                graph[source].add(target_module)
                location = f"{path.relative_to(ROOT)}:{reference.line}"
                if target_module not in ALLOWED_DEPENDENCIES[source]:
                    violations.append(
                        f"{location}: {source} -> {target_module} is outside the whitelist"
                    )
                if surface not in {"public", "contracts"}:
                    violations.append(
                        f"{location}: {source} -> {reference.module} bypasses public/contracts"
                    )

    assert not violations, "\n".join(violations)
    cycle = _first_cycle(graph)
    assert cycle is None, f"business module cycle: {' -> '.join(cycle or [])}"


def test_every_module_has_public_contract_and_migration_entries() -> None:
    missing: list[str] = []
    for module in sorted(BUSINESS_MODULES):
        root = MODULES_ROOT / module
        for relative in ("contracts.py", "public.py", "migrations/__init__.py"):
            if not (root / relative).is_file():
                missing.append(f"{module}/{relative}")
    assert not missing, f"missing module entry points: {', '.join(missing)}"


def test_internal_backend_imports_and_workflows_use_supported_surfaces() -> None:
    violations: list[str] = []
    for source in sorted(BUSINESS_MODULES):
        for path in _python_files(MODULES_ROOT / source):
            relative_path = path.relative_to(ROOT).as_posix()
            for reference in _imports(path):
                if not reference.module.startswith("backend.app."):
                    continue
                target = _business_target(reference.module)
                supported = (
                    reference.module.startswith(f"backend.app.modules.{source}.")
                    or reference.module in {f"backend.app.modules.{source}"}
                    or reference.module.startswith("backend.app.shared_kernel")
                    or reference.module.startswith("backend.app.platform")
                    or (
                        target is not None
                        and target[0] != source
                        and target[1] in {"public", "contracts"}
                    )
                    or (relative_path, reference.module) in LEGACY_INTERNAL_IMPORTS
                )
                if not supported:
                    violations.append(
                        f"{relative_path}:{reference.line}: unsupported internal import "
                        f"{reference.module}"
                    )

    for path in _python_files(WORKFLOWS_ROOT):
        for reference in _imports(path):
            target = _business_target(reference.module)
            if target is not None and target[1] != "public":
                violations.append(
                    f"{path.relative_to(ROOT)}:{reference.line}: workflow import "
                    f"must target public, got {reference.module}"
                )
    assert not violations, "\n".join(violations)


def test_domain_is_pure_and_modules_do_not_locate_services_or_construct_http_clients() -> None:
    violations: list[str] = []
    forbidden_domain_roots = {
        "PIL",
        "aiohttp",
        "fastapi",
        "httpx",
        "os",
        "pathlib",
        "requests",
        "sqlite3",
        "urllib.request",
    }
    http_roots = {"aiohttp", "httpx", "requests", "urllib.request"}
    for root in (MODULES_ROOT, WORKFLOWS_ROOT):
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(ROOT)
            is_domain = "domain" in relative.parts
            for reference in _imports(path):
                imported_root = reference.module.split(".")[0]
                if is_domain and (
                    reference.module in forbidden_domain_roots
                    or imported_root in forbidden_domain_roots
                ):
                    violations.append(
                        f"{relative}:{reference.line}: domain imports {reference.module}"
                    )
                if imported_root in http_roots and "adapters" not in relative.parts:
                    violations.append(
                        f"{relative}:{reference.line}: HTTP dependency must live under adapters"
                    )
            for node in ast.walk(tree):
                attribute = _attribute_path(node)
                if attribute and (
                    attribute.startswith("app.state.") or ".app.state." in attribute
                ):
                    violations.append(
                        f"{relative}:{getattr(node, 'lineno', '?')}: service locator {attribute}"
                    )
                if isinstance(node, ast.Call) and attribute in {
                    "httpx.AsyncClient",
                    "httpx.Client",
                    "requests.Session",
                }:
                    violations.append(
                        f"{relative}:{node.lineno}: concrete HTTP clients are "
                        "composition-root owned"
                    )
    assert not violations, "\n".join(violations)


def test_shared_kernel_and_platform_do_not_depend_on_business_modules() -> None:
    violations: list[str] = []
    for root in (ROOT / "backend/app/shared_kernel", ROOT / "backend/app/platform"):
        for path in _python_files(root):
            for reference in _imports(path):
                if _business_target(reference.module) is not None:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{reference.line}: reverse dependency "
                        f"{reference.module}"
                    )
    assert not violations, "\n".join(violations)
