CREATE TABLE IF NOT EXISTS memory_branch_links (
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    updated_at BIGINT,
    PRIMARY KEY (project_id, branch)
);

CREATE TABLE IF NOT EXISTS memory_overlay_tombstones (
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    created_at BIGINT,
    PRIMARY KEY (project_id, branch, target_type, target_key)
);

CREATE INDEX IF NOT EXISTS idx_mem_tomb_proj_branch ON memory_overlay_tombstones(project_id, branch);
