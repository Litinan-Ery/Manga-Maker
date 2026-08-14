CREATE TABLE work_items (
    work_item_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 1),
    command_contract TEXT NOT NULL,
    command_contract_version TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued'
        CHECK(state IN ('queued', 'running', 'completed', 'failed', 'canceled', 'needs_review')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    attempt_limit INTEGER NOT NULL CHECK(attempt_limit BETWEEN 1 AND 100),
    attempts_started INTEGER NOT NULL DEFAULT 0
        CHECK(attempts_started >= 0 AND attempts_started <= attempt_limit),
    not_before TEXT NOT NULL,
    requires_user_action INTEGER NOT NULL DEFAULT 0
        CHECK(requires_user_action IN (0, 1)),
    last_safe_error_code TEXT,
    last_safe_error_message TEXT,
    last_safe_error_retryable INTEGER
        CHECK(last_safe_error_retryable IS NULL OR last_safe_error_retryable IN (0, 1)),
    result_artifact_type TEXT,
    result_artifact_id TEXT,
    result_artifact_version INTEGER CHECK(
        result_artifact_version IS NULL OR result_artifact_version >= 1
    ),
    result_content_sha256 TEXT CHECK(
        result_content_sha256 IS NULL OR length(result_content_sha256) = 64
    ),
    result_schema_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, idempotency_key),
    CHECK(
        (last_safe_error_code IS NULL AND last_safe_error_message IS NULL
            AND last_safe_error_retryable IS NULL)
        OR
        (last_safe_error_code IS NOT NULL AND last_safe_error_message IS NOT NULL
            AND last_safe_error_retryable IS NOT NULL)
    ),
    CHECK(
        (result_artifact_type IS NULL AND result_artifact_id IS NULL
            AND result_artifact_version IS NULL AND result_content_sha256 IS NULL
            AND result_schema_version IS NULL)
        OR
        (result_artifact_type IS NOT NULL AND result_artifact_id IS NOT NULL
            AND result_artifact_version IS NOT NULL AND result_content_sha256 IS NOT NULL
            AND result_schema_version IS NOT NULL)
    )
);

CREATE INDEX work_items_claimable
ON work_items(state, requires_user_action, not_before, created_at)
WHERE state = 'queued';

CREATE INDEX work_items_by_project
ON work_items(project_id, created_at);

CREATE TABLE work_attempts (
    work_attempt_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    state TEXT NOT NULL
        CHECK(state IN ('running', 'completed', 'failed', 'canceled', 'needs_review')),
    external_request_started INTEGER NOT NULL DEFAULT 0
        CHECK(external_request_started IN (0, 1)),
    safe_error_code TEXT,
    safe_error_message TEXT,
    safe_error_retryable INTEGER
        CHECK(safe_error_retryable IS NULL OR safe_error_retryable IN (0, 1)),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(work_item_id, ordinal)
);

CREATE UNIQUE INDEX one_running_attempt_per_work_item
ON work_attempts(work_item_id)
WHERE state = 'running';

CREATE TABLE work_handler_receipts (
    receipt_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
    handler_version TEXT NOT NULL,
    completed_revision INTEGER NOT NULL CHECK(completed_revision >= 1),
    result_content_sha256 TEXT NOT NULL CHECK(length(result_content_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE(work_item_id, handler_version)
);
