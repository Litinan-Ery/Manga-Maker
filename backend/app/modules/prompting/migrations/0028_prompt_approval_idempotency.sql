ALTER TABLE prompt_bundle_approvals
ADD COLUMN snapshot_sha256 TEXT NOT NULL DEFAULT '';

ALTER TABLE prompt_bundle_approvals
ADD COLUMN idempotency_key TEXT;

ALTER TABLE prompt_bundle_approvals
ADD COLUMN request_sha256 TEXT;

CREATE UNIQUE INDEX prompt_approval_idempotency
ON prompt_bundle_approvals(idempotency_key)
WHERE idempotency_key IS NOT NULL;
