ALTER TABLE projects
ADD COLUMN workflow_version TEXT NOT NULL DEFAULT 'legacy_v02'
    CHECK(workflow_version IN ('legacy_v02', 'v03'));
