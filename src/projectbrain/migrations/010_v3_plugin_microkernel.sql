-- Project-Brain v3.0 Plugin Microkernel & Layer 1/Layer 2 Schema Migration
-- Cross-dialect SQLite & PostgreSQL compatible DDL (no PL/pgSQL $$ blocks).

CREATE TABLE IF NOT EXISTS plugin_project_configs (
    project_id TEXT NOT NULL,
    plugin_id TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    priority INTEGER,
    config_json TEXT DEFAULT '{}',
    updated_at BIGINT,
    PRIMARY KEY (project_id, plugin_id)
);

CREATE INDEX IF NOT EXISTS idx_plugin_project_configs_proj
    ON plugin_project_configs(project_id);

CREATE TABLE IF NOT EXISTS udcp_script_cache (
    cache_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    build_version TEXT NOT NULL,
    screen_hash TEXT DEFAULT '',
    ui_fingerprint_json TEXT DEFAULT '{}',
    udcp_commands_json TEXT NOT NULL,
    hit_count INTEGER DEFAULT 0,
    fallback_count INTEGER DEFAULT 0,
    fallback_rate DOUBLE PRECISION DEFAULT 0.0,
    invalidated INTEGER DEFAULT 0,
    invalidation_reason TEXT,
    self_heal_history_json TEXT DEFAULT '[]',
    ttl_seconds INTEGER DEFAULT 2592000,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_udcp_cache_lookup
    ON udcp_script_cache(project_id, intent_id, platform, build_version);

CREATE INDEX IF NOT EXISTS idx_udcp_cache_screen
    ON udcp_script_cache(project_id, intent_id, screen_hash);

CREATE TABLE IF NOT EXISTS telemetry_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    device_id TEXT DEFAULT '',
    build_version TEXT DEFAULT '',
    artifact_type TEXT NOT NULL,
    ntp_timestamp_ms BIGINT NOT NULL,
    duration_ms BIGINT DEFAULT 0,
    uri TEXT NOT NULL,
    sha256 TEXT DEFAULT '',
    size_bytes BIGINT DEFAULT 0,
    summary_metrics_json TEXT DEFAULT '{}',
    retention_days INTEGER DEFAULT 30,
    expires_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telemetry_run_step_ts
    ON telemetry_artifacts(run_id, step_id, ntp_timestamp_ms);

CREATE INDEX IF NOT EXISTS idx_telemetry_run_ts
    ON telemetry_artifacts(run_id, ntp_timestamp_ms);

CREATE INDEX IF NOT EXISTS idx_telemetry_expires
    ON telemetry_artifacts(expires_at);

CREATE TABLE IF NOT EXISTS v3_rca_incidents (
    incident_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    signature_hash TEXT NOT NULL,
    fault_domain TEXT NOT NULL,
    normalized_signature TEXT NOT NULL,
    ticket_refs_json TEXT DEFAULT '[]',
    occurrence_count INTEGER DEFAULT 1,
    last_run_id TEXT,
    self_heal_hint TEXT,
    metadata_json TEXT DEFAULT '{}',
    first_seen_at BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL,
    UNIQUE(project_id, signature_hash)
);

CREATE INDEX IF NOT EXISTS idx_v3_rca_project_hash
    ON v3_rca_incidents(project_id, signature_hash);
