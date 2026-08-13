from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchitectureExemption:
    exemption_id: str
    owner: str
    reason: str
    impact: str
    delete_condition: str
    adr: str
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        values = (
            self.exemption_id,
            self.owner,
            self.reason,
            self.impact,
            self.delete_condition,
            self.adr,
        )
        if any(not value.strip() for value in values) or not self.paths:
            raise ValueError("architecture exemptions require complete metadata and exact paths")


ARCHITECTURE_EXEMPTIONS: tuple[ArchitectureExemption, ...] = (
    ArchitectureExemption(
        exemption_id="ARCH-EX-001",
        owner="MM-026",
        reason="v0.2 API routes still consume compatibility services while use cases migrate",
        impact="only the attributes recorded in LEGACY_API_APP_STATE_LOOKUPS remain visible",
        delete_condition=(
            "remove each path when its route uses a typed Depends provider and the v0.2 "
            "characterization fixture still passes"
        ),
        adr="ADR-018",
        paths=("backend/app/api/*.py",),
    ),
    ArchitectureExemption(
        exemption_id="ARCH-EX-002",
        owner="MM-026",
        reason="composition delegates page revision behavior to the characterized v0.2 service",
        impact="composition/adapters/legacy.py may import only pages.models and pages.service",
        delete_condition=(
            "remove after composition owns page persistence and the shared Port contract suite "
            "passes against the replacement adapter"
        ),
        adr="ADR-016",
        paths=("backend/app/modules/composition/adapters/legacy.py",),
    ),
    ArchitectureExemption(
        exemption_id="ARCH-EX-003",
        owner="MM-027",
        reason="the immutable v0.2 migrations remain inline for fixture-compatible replay",
        impact="database.py may register versions 1 through 16 only as legacy_v02 compatibility",
        delete_condition=(
            "remove the inline statements after v0.2 upgrade and rollback fixtures use the "
            "module migration registry without changing their schema hashes"
        ),
        adr="ADR-017",
        paths=("backend/app/database.py",),
    ),
    ArchitectureExemption(
        exemption_id="ARCH-EX-004",
        owner="MM-053",
        reason="the v0.2 App remains the rendered shell while feature screens migrate",
        impact="app/index.ts may re-export App.tsx and no other v0.3 file may import legacy UI",
        delete_condition=(
            "remove when all P0 screens are composed from feature public entries and the v0.2 "
            "frontend regression suite remains green"
        ),
        adr="ADR-018",
        paths=("frontend/src/app/index.ts",),
    ),
)


__all__ = ["ARCHITECTURE_EXEMPTIONS", "ArchitectureExemption"]
