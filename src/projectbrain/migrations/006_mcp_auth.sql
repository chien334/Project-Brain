CREATE TABLE IF NOT EXISTS mcp_accounts (
    username TEXT PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,          -- 'admin', 'collaborator', 'reader'
    allowed_tools TEXT,          -- Comma-separated list of allowed tools or '*' for all
    created_at INTEGER NOT NULL
);
