CREATE TABLE IF NOT EXISTS finding_index (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    snapshot_artifact_id INTEGER NULL REFERENCES snapshot_artifacts(id) ON DELETE SET NULL,
    scan_summary_soc_id INTEGER NULL REFERENCES scan_summaries_soc(id) ON DELETE SET NULL,
    scan_summary_noc_id INTEGER NULL REFERENCES scan_summaries_noc(id) ON DELETE SET NULL,
    scan_id VARCHAR(255) NOT NULL,
    scanner_type VARCHAR(50) NOT NULL,
    domain VARCHAR(10) NOT NULL DEFAULT 'soc',
    finding_idx INTEGER NOT NULL,
    severity VARCHAR(50) NOT NULL,
    name TEXT NULL,
    cve VARCHAR(255) NULL,
    host TEXT NULL,
    port VARCHAR(50) NULL,
    protocol VARCHAR(50) NULL,
    status VARCHAR(50) NULL,
    event_type VARCHAR(100) NULL,
    service VARCHAR(100) NULL,
    cvss DOUBLE PRECISION NULL,
    description TEXT NULL,
    solution TEXT NULL,
    impact TEXT NULL,
    s3_bucket VARCHAR(255) NOT NULL,
    s3_key VARCHAR(1024) NOT NULL,
    detected_at TIMESTAMP WITHOUT TIME ZONE NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_finding_index_tenant_id
    ON finding_index (tenant_id);

CREATE INDEX IF NOT EXISTS ix_finding_index_snapshot_artifact_id
    ON finding_index (snapshot_artifact_id);

CREATE INDEX IF NOT EXISTS ix_finding_index_severity
    ON finding_index (severity);

CREATE INDEX IF NOT EXISTS ix_finding_index_cve
    ON finding_index (cve);

CREATE INDEX IF NOT EXISTS ix_finding_index_scan_summary_soc_id
    ON finding_index (scan_summary_soc_id);

CREATE INDEX IF NOT EXISTS ix_finding_index_scan_summary_noc_id
    ON finding_index (scan_summary_noc_id);
