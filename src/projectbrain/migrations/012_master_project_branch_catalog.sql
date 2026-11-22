-- 012_master_project_branch_catalog.sql
-- Master Control-Plane catalog for 3-Tier Multi-Database & Multi-Schema isolation.

CREATE TABLE IF NOT EXISTS master_projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    db_name TEXT UNIQUE NOT NULL,
    root_branch TEXT DEFAULT 'main',
    root_schema TEXT DEFAULT 'pb_root',
    backend_type TEXT NOT NULL,
    db_descriptor TEXT,
    status TEXT DEFAULT 'active',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_master_projects_db_name
    ON master_projects(db_name);

CREATE TABLE IF NOT EXISTS master_branches (
    project_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    db_name TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    base_branch TEXT DEFAULT 'main',
    is_root INTEGER DEFAULT 0,
    has_graph_schema INTEGER DEFAULT 1,
    has_doc_schema INTEGER DEFAULT 1,
    author TEXT DEFAULT 'system',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (project_id, branch_name)
);

CREATE INDEX IF NOT EXISTS idx_master_branches_proj_schema
    ON master_branches(project_id, schema_name);

CREATE INDEX IF NOT EXISTS idx_master_branches_db_name
    ON master_branches(db_name);
