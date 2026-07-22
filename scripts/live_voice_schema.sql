CREATE TABLE IF NOT EXISTS live_voice_sessions (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_type VARCHAR(50) NOT NULL DEFAULT 'SOPHIA',
    agent_instance_id VARCHAR(36) NULL REFERENCES agent_instances(id) ON DELETE CASCADE,
    session_name VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    ended_at TIMESTAMP WITHOUT TIME ZONE NULL,
    duration_seconds INTEGER NULL,
    tokens_consumed INTEGER NULL,
    metrics JSON NULL,
    metadata JSON NULL
);

CREATE INDEX IF NOT EXISTS ix_live_voice_sessions_tenant_id
    ON live_voice_sessions (tenant_id);

CREATE TABLE IF NOT EXISTS live_voice_messages (
    id VARCHAR(36) PRIMARY KEY,
    live_voice_session_id VARCHAR(36) NOT NULL REFERENCES live_voice_sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_live_voice_messages_live_voice_session_id
    ON live_voice_messages (live_voice_session_id);
