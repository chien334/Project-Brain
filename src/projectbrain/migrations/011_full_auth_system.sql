-- Project-Brain Full Identity & Access Management (IAM) Schema Migration
-- Cross-dialect SQLite & PostgreSQL compatible ANSI DDL (no $$ blocks or ALTER TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL,
    revoked_at BIGINT
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_username
    ON auth_sessions(username);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
    ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS auth_api_keys (
    key_id TEXT PRIMARY KEY,
    key_prefix TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    role_scope TEXT NOT NULL,
    allowed_tools TEXT DEFAULT '*',
    allowed_projects TEXT DEFAULT '*',
    expires_at BIGINT,
    last_used_at BIGINT,
    created_at BIGINT NOT NULL,
    revoked_at BIGINT
);

CREATE INDEX IF NOT EXISTS idx_auth_api_keys_username
    ON auth_api_keys(username);

CREATE TABLE IF NOT EXISTS auth_audit_logs (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor_username TEXT,
    target_username TEXT,
    ip_address TEXT,
    user_agent TEXT,
    status TEXT NOT NULL,
    details TEXT,
    created_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_audit_logs_created
    ON auth_audit_logs(created_at);

CREATE INDEX IF NOT EXISTS idx_auth_audit_logs_event
    ON auth_audit_logs(event_type);

CREATE TABLE IF NOT EXISTS auth_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at BIGINT NOT NULL
);
