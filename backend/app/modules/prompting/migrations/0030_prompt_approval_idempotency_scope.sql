DROP INDEX prompt_approval_idempotency;

CREATE UNIQUE INDEX prompt_approval_idempotency
ON prompt_bundle_approvals(prompt_bundle_version_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;
