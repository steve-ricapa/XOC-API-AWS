CREATE TABLE IF NOT EXISTS tenant_preferences (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    dashboard_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tenant_preferences_updated_by_user_id
    ON tenant_preferences (updated_by_user_id);
