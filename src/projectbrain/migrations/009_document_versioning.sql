CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    version TEXT NOT NULL,
    parent_version TEXT,
    base_branch TEXT,
    filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    total_units INTEGER DEFAULT 0,
    reused_units INTEGER DEFAULT 0,
    new_units INTEGER DEFAULT 0,
    deleted_units INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at BIGINT NOT NULL,
    UNIQUE(project_id, branch, doc_key, version)
);

CREATE TABLE IF NOT EXISTS document_units (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    version TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    unit_type TEXT NOT NULL,
    unit_title TEXT,
    unit_sha256 TEXT NOT NULL,
    markdown_text TEXT NOT NULL,
    memory_ids_json TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_doc_units_lookup ON document_units(project_id, doc_key, unit_sha256);
CREATE INDEX IF NOT EXISTS idx_doc_versions_lookup ON document_versions(project_id, branch, doc_key, status);
