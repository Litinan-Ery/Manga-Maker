CREATE TABLE generation_approvals (
    generation_approval_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
    plan_fingerprint TEXT NOT NULL,
    approval_sha256 TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    candidate_count_per_panel INTEGER NOT NULL CHECK(candidate_count_per_panel >= 1),
    quality_rule_version TEXT NOT NULL,
    user_action_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active', 'stale', 'revoked')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, idempotency_key)
);

ALTER TABLE generation_jobs
ADD COLUMN generation_approval_id TEXT REFERENCES generation_approvals(generation_approval_id);

ALTER TABLE generation_jobs
ADD COLUMN generation_approval_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_jobs
ADD COLUMN prompt_approval_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_jobs
ADD COLUMN prompt_snapshot_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_jobs
ADD COLUMN candidate_count_per_panel INTEGER NOT NULL DEFAULT 1
    CHECK(candidate_count_per_panel >= 1);

ALTER TABLE generation_jobs
ADD COLUMN quality_rule_version TEXT NOT NULL DEFAULT 'quality-rules-v1';

ALTER TABLE generation_job_items
ADD COLUMN prompt_plan_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN prompt_plan_version INTEGER NOT NULL DEFAULT 0;

ALTER TABLE generation_job_items
ADD COLUMN prompt_plan_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN prompt_plan_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE generation_job_items
ADD COLUMN prompt_package_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN character_tag_set_refs_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE generation_job_items
ADD COLUMN provider_execution_spec_id TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN provider_execution_spec_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE generation_job_items
ADD COLUMN provider_execution_spec_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN provider_payload_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE generation_job_items
ADD COLUMN provider_payload_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE generation_job_items
ADD COLUMN provider_seed INTEGER NOT NULL DEFAULT 0;

ALTER TABLE generation_job_items
ADD COLUMN candidate_count INTEGER NOT NULL DEFAULT 1 CHECK(candidate_count >= 1);

ALTER TABLE generation_job_items
ADD COLUMN reference_use_json TEXT;

CREATE INDEX generation_approvals_by_project
ON generation_approvals(project_id, chapter_id, created_at);
