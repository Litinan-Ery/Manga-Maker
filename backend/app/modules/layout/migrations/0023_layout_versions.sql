CREATE TABLE page_layout_drafts (
    page_layout_draft_version_id TEXT PRIMARY KEY,
    page_layout_draft_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    revision INTEGER NOT NULL CHECK(revision = version),
    origin TEXT NOT NULL CHECK(origin IN ('planned', 'imported_legacy')),
    storyboard_id TEXT,
    storyboard_version_id TEXT,
    storyboard_version INTEGER CHECK(storyboard_version IS NULL OR storyboard_version >= 1),
    storyboard_content_sha256 TEXT
        CHECK(storyboard_content_sha256 IS NULL OR length(storyboard_content_sha256) = 64),
    approved_panel_ids_json TEXT NOT NULL,
    legacy_page_version_id TEXT,
    document_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    snapshot_relative_path TEXT NOT NULL UNIQUE,
    snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(page_layout_draft_id, version),
    CHECK(
        (storyboard_id IS NULL AND storyboard_version_id IS NULL
            AND storyboard_version IS NULL AND storyboard_content_sha256 IS NULL)
        OR
        (storyboard_id IS NOT NULL AND storyboard_version_id IS NOT NULL
            AND storyboard_version IS NOT NULL AND storyboard_content_sha256 IS NOT NULL)
    ),
    CHECK(origin <> 'planned' OR storyboard_version_id IS NOT NULL),
    CHECK(origin <> 'planned' OR legacy_page_version_id IS NULL),
    CHECK(origin <> 'imported_legacy' OR legacy_page_version_id IS NOT NULL)
);

CREATE UNIQUE INDEX one_current_page_layout_draft
ON page_layout_drafts(page_layout_draft_id)
WHERE is_current = 1;

CREATE UNIQUE INDEX one_current_layout_per_project_page
ON page_layout_drafts(project_id, page_id)
WHERE is_current = 1;

CREATE INDEX page_layout_drafts_by_project
ON page_layout_drafts(project_id, chapter_id, page_id, version);

CREATE TABLE layout_approvals (
    approval_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    page_layout_draft_id TEXT NOT NULL,
    page_layout_draft_version_id TEXT NOT NULL UNIQUE
        REFERENCES page_layout_drafts(page_layout_draft_version_id),
    layout_version INTEGER NOT NULL CHECK(layout_version >= 1),
    layout_content_sha256 TEXT NOT NULL CHECK(length(layout_content_sha256) = 64),
    storyboard_id TEXT NOT NULL,
    storyboard_version_id TEXT NOT NULL,
    storyboard_version INTEGER NOT NULL CHECK(storyboard_version >= 1),
    storyboard_content_sha256 TEXT NOT NULL CHECK(length(storyboard_content_sha256) = 64),
    approval_sha256 TEXT NOT NULL CHECK(length(approval_sha256) = 64),
    snapshot_relative_path TEXT NOT NULL UNIQUE,
    snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
    created_at TEXT NOT NULL
);

CREATE INDEX layout_approvals_by_layout
ON layout_approvals(page_layout_draft_id, layout_version);

CREATE TABLE dimension_selections (
    dimension_selection_id TEXT PRIMARY KEY,
    page_layout_draft_version_id TEXT NOT NULL
        REFERENCES page_layout_drafts(page_layout_draft_version_id),
    frame_id TEXT NOT NULL,
    capability_snapshot_id TEXT NOT NULL,
    capability_snapshot_sha256 TEXT NOT NULL
        CHECK(length(capability_snapshot_sha256) = 64),
    rule_version TEXT NOT NULL,
    selected_width INTEGER NOT NULL CHECK(selected_width >= 64),
    selected_height INTEGER NOT NULL CHECK(selected_height >= 64),
    expected_crop_ratio REAL NOT NULL
        CHECK(expected_crop_ratio >= 0 AND expected_crop_ratio <= 1),
    document_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    created_at TEXT NOT NULL,
    UNIQUE(page_layout_draft_version_id, frame_id)
);

CREATE INDEX dimension_selections_by_layout
ON dimension_selections(page_layout_draft_version_id, frame_id);
