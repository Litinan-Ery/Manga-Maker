CREATE TABLE layout_command_receipts (
    receipt_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    command_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
    resource_type TEXT NOT NULL
        CHECK(resource_type IN ('layout_version', 'layout_approval')),
    resource_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, idempotency_key)
);

CREATE INDEX layout_command_receipts_by_resource
ON layout_command_receipts(resource_type, resource_id);

CREATE TABLE layout_approval_dimension_selections (
    approval_id TEXT NOT NULL REFERENCES layout_approvals(approval_id),
    dimension_selection_id TEXT NOT NULL
        REFERENCES dimension_selections(dimension_selection_id),
    frame_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    PRIMARY KEY(approval_id, dimension_selection_id),
    UNIQUE(approval_id, frame_id)
);

CREATE INDEX layout_approval_dimensions_by_selection
ON layout_approval_dimension_selections(dimension_selection_id, approval_id);
