CREATE TABLE provider_execution_specs (
    provider_execution_spec_id TEXT PRIMARY KEY,
    generation_spec_id TEXT NOT NULL UNIQUE REFERENCES generation_specs(spec_id),
    version INTEGER NOT NULL CHECK(version >= 1),
    schema_version TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(provider = 'novelai'),
    mapping_version TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    capability_snapshot_sha256 TEXT NOT NULL,
    prompt_plan_id TEXT NOT NULL,
    prompt_plan_version INTEGER NOT NULL CHECK(prompt_plan_version >= 1),
    prompt_plan_sha256 TEXT NOT NULL,
    execution_spec_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX provider_execution_specs_by_prompt_plan
ON provider_execution_specs(prompt_plan_id, prompt_plan_version, prompt_plan_sha256);
