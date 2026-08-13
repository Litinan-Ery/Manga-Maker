from __future__ import annotations

from uuid import UUID


class LayoutError(RuntimeError):
    """Base error for the provider-neutral layout module."""


class LayoutNotFoundError(LayoutError):
    pass


class LayoutIdentityConflictError(LayoutError):
    pass


class LayoutSnapshotIntegrityError(LayoutError):
    pass


class LayoutApprovalConflictError(LayoutError):
    pass


class LayoutIdempotencyConflictError(LayoutError):
    pass


class LayoutStoryboardBindingError(LayoutError):
    pass


class LayoutGenerationGateError(LayoutError):
    """The current chapter lacks a complete, active, valid layout snapshot."""

    pass


class DimensionCapabilityIntegrityError(LayoutError):
    pass


class LayoutRevisionConflictError(LayoutError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__(f"layout revision conflict; current revision is {current_revision}")


class LayoutPanelCoverageError(LayoutError):
    def __init__(self, *, missing: tuple[UUID, ...], unexpected: tuple[UUID, ...]) -> None:
        self.missing = missing
        self.unexpected = unexpected
        details = []
        if missing:
            details.append(f"missing={','.join(map(str, missing))}")
        if unexpected:
            details.append(f"unexpected={','.join(map(str, unexpected))}")
        super().__init__("layout leaf panels do not match the storyboard: " + "; ".join(details))
