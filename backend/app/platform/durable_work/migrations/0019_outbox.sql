CREATE TABLE outbox_project_sequences (
    project_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_sequence >= 0)
);

CREATE TABLE outbox_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    project_sequence INTEGER NOT NULL CHECK(project_sequence >= 1),
    event_type TEXT NOT NULL,
    event_version TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 1),
    aggregate_sha256 TEXT NOT NULL CHECK(length(aggregate_sha256) = 64),
    event_json TEXT NOT NULL,
    event_sha256 TEXT NOT NULL CHECK(length(event_sha256) = 64),
    deduplication_key TEXT NOT NULL,
    publish_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(publish_state IN ('pending', 'published')),
    publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK(publish_attempts >= 0),
    last_safe_error_code TEXT,
    last_safe_error_message TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(project_id, project_sequence),
    UNIQUE(project_id, deduplication_key)
);

CREATE INDEX outbox_events_pending
ON outbox_events(publish_state, created_at, event_id)
WHERE publish_state = 'pending';

CREATE INDEX outbox_events_replay
ON outbox_events(project_id, project_sequence);

CREATE TABLE handled_events (
    event_id TEXT NOT NULL REFERENCES outbox_events(event_id),
    handler_version TEXT NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256) = 64),
    handled_at TEXT NOT NULL,
    PRIMARY KEY(event_id, handler_version)
);
