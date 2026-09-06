CREATE TABLE full_page_generations (
    generation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    chapter_id TEXT NOT NULL REFERENCES source_chapters(chapter_id),
    page_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft', 'running', 'ready', 'failed', 'needs_review')),
    plan_json TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    approval_sha256 TEXT,
    external_requests_started INTEGER NOT NULL DEFAULT 0,
    image_relative_path TEXT,
    image_sha256 TEXT,
    result_json TEXT,
    error_code TEXT,
    started_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX full_page_generations_by_project
ON full_page_generations(project_id, chapter_id, created_at);
CREATE UNIQUE INDEX one_running_full_page_generation
ON full_page_generations((1)) WHERE status = 'running';
