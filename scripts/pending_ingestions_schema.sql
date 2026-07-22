CREATE TABLE IF NOT EXISTS pending_ingestions (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    upload_id VARCHAR(36) NOT NULL UNIQUE,
    api_key_id INTEGER NULL REFERENCES agent_api_keys(id) ON DELETE SET NULL,
    provider VARCHAR(100) NOT NULL,
    scanner_type VARCHAR(50) NOT NULL,
    idempotency_key VARCHAR(71) NULL,
    s3_key VARCHAR(1024) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message TEXT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pending_ingestions_tenant_id
    ON pending_ingestions (tenant_id);

CREATE INDEX IF NOT EXISTS ix_pending_ingestions_api_key_id
    ON pending_ingestions (api_key_id);
