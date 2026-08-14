CREATE TABLE outbox_publish_attempts (
    publish_attempt_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES outbox_events(event_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    state TEXT NOT NULL CHECK(state IN ('sending', 'confirmed', 'failed')),
    safe_error_code TEXT,
    safe_error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(event_id, ordinal)
);

CREATE UNIQUE INDEX one_sending_publish_attempt_per_event
ON outbox_publish_attempts(event_id)
WHERE state = 'sending';
