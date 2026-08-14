CREATE TABLE artifact_versions (
    artifact_row_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    schema_version TEXT NOT NULL,
    is_stale INTEGER NOT NULL DEFAULT 0 CHECK(is_stale IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, artifact_type, artifact_id, version)
);

CREATE INDEX artifact_versions_lookup
ON artifact_versions(project_id, artifact_type, artifact_id, version);

CREATE TABLE artifact_dependencies (
    dependency_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    upstream_artifact_row_id TEXT NOT NULL
        REFERENCES artifact_versions(artifact_row_id),
    downstream_artifact_row_id TEXT NOT NULL
        REFERENCES artifact_versions(artifact_row_id),
    edge_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(upstream_artifact_row_id, downstream_artifact_row_id),
    CHECK(upstream_artifact_row_id <> downstream_artifact_row_id)
);

CREATE INDEX artifact_dependencies_forward
ON artifact_dependencies(project_id, upstream_artifact_row_id, downstream_artifact_row_id);

CREATE INDEX artifact_dependencies_reverse
ON artifact_dependencies(project_id, downstream_artifact_row_id, upstream_artifact_row_id);

CREATE TABLE invalidation_events (
    invalidation_event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    origin_artifact_row_id TEXT NOT NULL REFERENCES artifact_versions(artifact_row_id),
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, source_event_id)
);

CREATE TABLE invalidation_impacts (
    invalidation_impact_id TEXT PRIMARY KEY,
    invalidation_event_id TEXT NOT NULL
        REFERENCES invalidation_events(invalidation_event_id),
    artifact_row_id TEXT NOT NULL REFERENCES artifact_versions(artifact_row_id),
    path_json TEXT NOT NULL,
    path_sha256 TEXT NOT NULL CHECK(length(path_sha256) = 64),
    marked_stale INTEGER NOT NULL CHECK(marked_stale IN (0, 1)),
    UNIQUE(invalidation_event_id, artifact_row_id)
);

CREATE INDEX invalidation_impacts_by_artifact
ON invalidation_impacts(artifact_row_id, invalidation_event_id);
