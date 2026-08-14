ALTER TABLE generation_jobs
ADD COLUMN layout_snapshot_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN page_layout_draft_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN page_layout_draft_version_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN layout_version INTEGER NOT NULL DEFAULT 0;

ALTER TABLE generation_job_items
ADD COLUMN layout_content_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN layout_approval_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN layout_approval_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN frame_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN frame_content_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN dimension_selection_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN dimension_selection_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN selected_width INTEGER NOT NULL DEFAULT 0;

ALTER TABLE generation_job_items
ADD COLUMN selected_height INTEGER NOT NULL DEFAULT 0;

ALTER TABLE generation_job_items
ADD COLUMN expected_crop_ratio REAL NOT NULL DEFAULT 0;

ALTER TABLE generation_job_items
ADD COLUMN dimension_rule_version TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN capability_snapshot_sha256 TEXT NOT NULL DEFAULT '';
