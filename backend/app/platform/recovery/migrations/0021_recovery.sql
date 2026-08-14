CREATE TABLE recovery_reports (
    recovery_report_id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL CHECK(trigger IN ('startup', 'manual')),
    status TEXT NOT NULL CHECK(status IN ('healthy', 'needs_attention', 'repaired')),
    summary_json TEXT NOT NULL,
    external_requests_started INTEGER NOT NULL DEFAULT 0
        CHECK(external_requests_started = 0),
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE INDEX recovery_reports_latest
ON recovery_reports(created_at, recovery_report_id);

CREATE TABLE recovery_findings (
    recovery_finding_id TEXT PRIMARY KEY,
    recovery_report_id TEXT NOT NULL REFERENCES recovery_reports(recovery_report_id),
    owner TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'critical')),
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    repair_command TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'repaired')),
    UNIQUE(recovery_report_id, owner, code, subject_type, subject_id)
);

CREATE INDEX recovery_findings_by_report
ON recovery_findings(recovery_report_id, status, severity);

CREATE TABLE recovery_repair_receipts (
    recovery_finding_id TEXT PRIMARY KEY
        REFERENCES recovery_findings(recovery_finding_id),
    receipt_id TEXT NOT NULL UNIQUE,
    result_code TEXT NOT NULL,
    repaired_at TEXT NOT NULL
);
