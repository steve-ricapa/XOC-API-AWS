ALTER TABLE pending_ingestions
    ADD COLUMN IF NOT EXISTS raw_s3_bucket VARCHAR(255),
    ADD COLUMN IF NOT EXISTS raw_s3_key VARCHAR(1024),
    ADD COLUMN IF NOT EXISTS processed_s3_key VARCHAR(1024),
    ADD COLUMN IF NOT EXISTS quarantine_s3_key VARCHAR(1024),
    ADD COLUMN IF NOT EXISTS content_type VARCHAR(100),
    ADD COLUMN IF NOT EXISTS size_bytes INTEGER,
    ADD COLUMN IF NOT EXISTS checksum VARCHAR(64),
    ADD COLUMN IF NOT EXISTS schema_version VARCHAR(30),
    ADD COLUMN IF NOT EXISTS scan_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error_code VARCHAR(100),
    ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS processing_completed_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS processor_request_id VARCHAR(100);

ALTER TABLE pending_ingestions
    ALTER COLUMN status TYPE VARCHAR(30);

UPDATE pending_ingestions
SET raw_s3_key = COALESCE(raw_s3_key, s3_key),
    raw_s3_bucket = COALESCE(raw_s3_bucket, 'xoc-prod-snapshots-811776156524')
WHERE raw_s3_key IS NULL OR raw_s3_bucket IS NULL;

CREATE INDEX IF NOT EXISTS idx_pending_ingestions_tenant_status_created
    ON pending_ingestions (tenant_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pending_ingestions_provider_status
    ON pending_ingestions (provider, status);

CREATE INDEX IF NOT EXISTS idx_pending_ingestions_scan_id
    ON pending_ingestions (tenant_id, scan_id);
