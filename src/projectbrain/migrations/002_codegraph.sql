-- 002_codegraph.sql
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS project_nodes (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    docstring TEXT,
    signature TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_edges (
    project_id TEXT NOT NULL,
    id INTEGER NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT, -- JSON
    line INTEGER,
    col INTEGER,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_project_nodes_search ON project_nodes(project_id, name);
CREATE INDEX IF NOT EXISTS idx_project_edges_source ON project_edges(project_id, source);
