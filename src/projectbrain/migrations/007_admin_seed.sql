INSERT INTO mcp_accounts (username, token, role, allowed_tools, created_at)
VALUES ('admin', 'pb_tok_admin', 'admin', '*', 1718438400)
ON CONFLICT (username) DO NOTHING;
