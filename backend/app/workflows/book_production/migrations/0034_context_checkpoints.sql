CREATE TABLE workflow_context_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    revision INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'collecting',
    context_epoch INTEGER NOT NULL DEFAULT 0,
    compaction_attempts INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    observation_json TEXT,
    latest_checkpoint_id TEXT,
    writer_id TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    lease_expiry REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX workflow_context_by_project ON workflow_context_runs(project_id, created_at);
CREATE TABLE workflow_context_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_context_runs(run_id),
    revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, revision)
);
CREATE TABLE workflow_context_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_context_runs(run_id),
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE workflow_context_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES workflow_context_runs(run_id),
    kind TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
