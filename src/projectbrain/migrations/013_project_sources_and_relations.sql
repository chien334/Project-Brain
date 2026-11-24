-- 013_project_sources_and_relations.sql
-- Multi-Source Project Hub, Shared Docs Bindings, Source Upgrade Ledger & Cross-Source Relations.
-- Cross-dialect ANSI DDL (SQLite & PostgreSQL compatible).

CREATE TABLE IF NOT EXISTS master_project_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    display_name TEXT,
    source_type TEXT DEFAULT 'service',
    tech_stack_json TEXT DEFAULT '[]',
    repo_url TEXT,
    workspace_subpath TEXT,
    root_branch TEXT DEFAULT 'main',
    root_schema TEXT NOT NULL,
    current_version TEXT DEFAULT '1.0.0',
    sync_generation INTEGER DEFAULT 0,
    last_commit_sha TEXT,
    last_sync_status TEXT DEFAULT 'pending',
    inherit_shared_docs INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    metadata_json TEXT DEFAULT '{}',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(project_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_master_project_sources_proj
    ON master_project_sources(project_id);

CREATE INDEX IF NOT EXISTS idx_master_project_sources_proj_src
    ON master_project_sources(project_id, source_id);

CREATE TABLE IF NOT EXISTS master_source_branches (
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    db_name TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    base_branch TEXT DEFAULT 'main',
    is_root INTEGER DEFAULT 0,
    has_graph_schema INTEGER DEFAULT 1,
    has_doc_schema INTEGER DEFAULT 1,
    source_version TEXT,
    sync_generation INTEGER DEFAULT 0,
    commit_sha TEXT,
    author TEXT DEFAULT 'system',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (project_id, source_id, branch_name)
);

CREATE INDEX IF NOT EXISTS idx_master_source_branches_proj_src
    ON master_source_branches(project_id, source_id);

CREATE INDEX IF NOT EXISTS idx_master_source_branches_schema
    ON master_source_branches(project_id, schema_name);

CREATE TABLE IF NOT EXISTS master_source_upgrades (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    from_version TEXT,
    to_version TEXT NOT NULL,
    sync_generation INTEGER DEFAULT 1,
    upgrade_mode TEXT DEFAULT 'delta',
    nodes_delta INTEGER DEFAULT 0,
    edges_delta INTEGER DEFAULT 0,
    docs_updated INTEGER DEFAULT 0,
    affected_relations INTEGER DEFAULT 0,
    drifted_relations INTEGER DEFAULT 0,
    commit_sha TEXT,
    actor TEXT DEFAULT 'system',
    created_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_master_source_upgrades_proj_src
    ON master_source_upgrades(project_id, source_id, created_at);

CREATE TABLE IF NOT EXISTS master_shared_doc_bindings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    target_source_id TEXT NOT NULL DEFAULT '*',
    pinned_version TEXT,
    binding_role TEXT DEFAULT 'reference',
    priority DOUBLE PRECISION DEFAULT 1.0,
    status TEXT DEFAULT 'active',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(project_id, doc_key, target_source_id)
);

CREATE INDEX IF NOT EXISTS idx_master_shared_doc_bindings_proj
    ON master_shared_doc_bindings(project_id, target_source_id);

CREATE TABLE IF NOT EXISTS cross_source_relations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    src_source_id TEXT NOT NULL,
    src_branch TEXT DEFAULT 'main',
    src_version TEXT,
    src_node_id TEXT DEFAULT '',
    src_file_path TEXT,
    dst_source_id TEXT NOT NULL,
    dst_branch TEXT DEFAULT 'main',
    dst_version TEXT,
    dst_node_id TEXT DEFAULT '',
    dst_doc_key TEXT,
    relation_type TEXT NOT NULL,
    origin TEXT DEFAULT 'manual',
    confidence TEXT DEFAULT 'high',
    confidence_score DOUBLE PRECISION DEFAULT 1.0,
    contract_key TEXT DEFAULT '',
    contract_metadata_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'active',
    is_bidirectional INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'system',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(project_id, src_source_id, src_branch, dst_source_id, dst_branch, relation_type, contract_key, src_node_id, dst_node_id)
);

CREATE INDEX IF NOT EXISTS idx_cross_src_rel_proj_src
    ON cross_source_relations(project_id, src_source_id, status);

CREATE INDEX IF NOT EXISTS idx_cross_src_rel_proj_dst
    ON cross_source_relations(project_id, dst_source_id, status);

CREATE INDEX IF NOT EXISTS idx_cross_src_rel_contract
    ON cross_source_relations(project_id, contract_key);

CREATE TABLE IF NOT EXISTS cross_source_contract_artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    branch TEXT DEFAULT 'main',
    contract_kind TEXT NOT NULL,
    role TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    node_id TEXT DEFAULT '',
    file_path TEXT,
    symbol_name TEXT,
    signature_hash TEXT,
    metadata_json TEXT DEFAULT '{}',
    sync_generation INTEGER DEFAULT 0,
    updated_at BIGINT NOT NULL,
    UNIQUE(project_id, source_id, branch, contract_kind, role, normalized_key, node_id)
);

CREATE INDEX IF NOT EXISTS idx_cross_src_artifacts_lookup
    ON cross_source_contract_artifacts(project_id, contract_kind, normalized_key);

CREATE INDEX IF NOT EXISTS idx_cross_src_artifacts_src
    ON cross_source_contract_artifacts(project_id, source_id, branch);
