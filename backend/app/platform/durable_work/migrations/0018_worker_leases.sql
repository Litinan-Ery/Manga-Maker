ALTER TABLE work_items
ADD COLUMN execution_safety TEXT NOT NULL DEFAULT 'local_idempotent'
    CHECK(execution_safety IN ('local_idempotent', 'external_side_effect'));

CREATE TABLE worker_leases (
    lease_id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL UNIQUE REFERENCES work_items(work_item_id),
    work_attempt_id TEXT NOT NULL UNIQUE REFERENCES work_attempts(work_attempt_id),
    lease_owner TEXT NOT NULL,
    lease_token TEXT NOT NULL UNIQUE,
    lease_revision INTEGER NOT NULL DEFAULT 1 CHECK(lease_revision >= 1),
    acquired_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX worker_leases_by_expiry
ON worker_leases(expires_at, lease_owner);
