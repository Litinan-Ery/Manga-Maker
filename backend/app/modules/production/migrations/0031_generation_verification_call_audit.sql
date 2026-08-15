ALTER TABLE generation_jobs
ADD COLUMN verification_calls_started INTEGER NOT NULL DEFAULT 0
    CHECK(verification_calls_started >= 0);

ALTER TABLE generation_jobs
ADD COLUMN verification_calls_completed INTEGER NOT NULL DEFAULT 0
    CHECK(verification_calls_completed >= 0);

ALTER TABLE generation_attempts
ADD COLUMN verification_request_started INTEGER NOT NULL DEFAULT 0
    CHECK(verification_request_started IN (0, 1));

ALTER TABLE generation_attempts
ADD COLUMN verification_request_completed INTEGER NOT NULL DEFAULT 0
    CHECK(verification_request_completed IN (0, 1));
