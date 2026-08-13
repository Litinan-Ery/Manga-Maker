from __future__ import annotations


class LineageError(RuntimeError):
    code = "LINEAGE_ERROR"


class ArtifactNotFoundError(LineageError):
    code = "LINEAGE_ARTIFACT_NOT_FOUND"


class ArtifactConflictError(LineageError):
    code = "LINEAGE_ARTIFACT_CONFLICT"


class DependencyRuleError(LineageError):
    code = "LINEAGE_EDGE_NOT_ALLOWED"


class DependencyConflictError(LineageError):
    code = "LINEAGE_DEPENDENCY_CONFLICT"


class DependencyCycleError(LineageError):
    code = "LINEAGE_CYCLE"

    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        super().__init__(f"artifact dependency cycle: {' -> '.join(path)}")


class InvalidationConflictError(LineageError):
    code = "LINEAGE_INVALIDATION_CONFLICT"
