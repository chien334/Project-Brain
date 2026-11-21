"""Security Hardening and Remediation Verification Suite.

Validates remediations for:
- SEC-01: Token masking and REST authentication requirement enforcement.
- SEC-02: Path traversal rejection on /graphify/local-diff and /graphify/query.
- SEC-03: FastMCP reader role blocking write tools (push, promote, upload).
- SEC-05 & SEC-07: Upload payload limit (PB_MAX_UPLOAD_MB -> 413) and filename sanitization.
- CR-MED-01 & CR-MED-02: GraphHyperedge upserts/tombstones promotion and autocommit BEGIN/COMMIT wrapping.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import pytest
from fastapi.testclient import TestClient

from projectbrain.server.api import create_app, SSEFilter
from projectbrain.core.db import db
from projectbrain.graphify_engine.models import GraphDelta, GraphHyperedge, GraphNode
from projectbrain.graphify_engine.store import SqliteOverlayGraphStore, PostgresSchemaGraphStore


app = create_app()


def setup_module(module):
    db.connect()
    cursor = db.conn.cursor()
    if db.is_postgres:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE %s", ("sec_%%",))
    else:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE ?", ("sec_%",))
    db.conn.commit()
    cursor.close()


def teardown_module(module):
    db.connect()
    cursor = db.conn.cursor()
    if db.is_postgres:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE %s", ("sec_%%",))
    else:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE ?", ("sec_%",))
    db.conn.commit()
    cursor.close()


# =====================================================================
# 1. SEC-02: Path Traversal Defenses (/graphify/local-diff & /graphify/query)
# =====================================================================

def test_graphify_local_diff_path_traversal_blocked():
    client = TestClient(app)
    # Absolute path outside workspace
    res_abs = client.post(
        "/graphify/local-diff",
        json={"project_id": "sec_test_proj", "branch": "main", "local_path": "/etc/passwd"},
    )
    assert res_abs.status_code == 400
    assert "Invalid or out-of-workspace local_path" in res_abs.json()["detail"]

    # Relative path escaping workspace
    res_rel = client.post(
        "/graphify/local-diff",
        json={"project_id": "sec_test_proj", "branch": "main", "local_path": "../../etc/passwd"},
    )
    assert res_rel.status_code == 400
    assert "Invalid or out-of-workspace local_path" in res_rel.json()["detail"]


def test_graphify_query_path_traversal_blocked():
    client = TestClient(app)
    # Absolute path outside workspace
    res_abs = client.post(
        "/graphify/query",
        json={"project_id": "sec_test_proj", "query": "auth", "local_path": "/etc/passwd"},
    )
    assert res_abs.status_code == 400
    assert "Invalid or out-of-workspace local_path" in res_abs.json()["detail"]

    # Relative path escaping workspace
    res_rel = client.post(
        "/graphify/query",
        json={"project_id": "sec_test_proj", "query": "auth", "local_path": "../../etc/shadow"},
    )
    assert res_rel.status_code == 400
    assert "Invalid or out-of-workspace local_path" in res_rel.json()["detail"]


def test_graphify_inline_local_delta_accepted_without_filesystem():
    """Verify that clients can send inline local_delta without touching server host filesystem."""
    client = TestClient(app)
    delta_payload = {
        "project_id": "sec_inline_proj",
        "branch": "main",
        "nodes": [
            {"id": "inline_n1", "project_id": "sec_inline_proj", "label": "InlineNode", "source_file": "src/inline.py"}
        ],
        "edges": [],
        "synced_files": ["src/inline.py"],
    }

    # Query with inline delta
    res_query = client.post(
        "/graphify/query",
        json={
            "project_id": "sec_inline_proj",
            "query": "InlineNode",
            "local_delta": delta_payload,
        },
    )
    assert res_query.status_code == 200

    # Local diff with inline delta
    res_diff = client.post(
        "/graphify/local-diff",
        json={
            "project_id": "sec_inline_proj",
            "branch": "main",
            "local_delta": delta_payload,
        },
    )
    assert res_diff.status_code == 200


# =====================================================================
# 2. SEC-03: FastMCP Reader Role Authorization Enforcement
# =====================================================================

@pytest.mark.asyncio
async def test_fastmcp_reader_role_write_tools_blocked():
    """Verify that reader role accounts cannot call upload, push, or promote tools."""
    client = TestClient(app)
    # Register test reader account
    res = client.post("/auth/accounts", json={
        "username": "sec_reader_user",
        "token": "pb_tok_sec_reader_val",
        "role": "reader",
        "allowed_tools": "*",
    })
    assert res.status_code == 200

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        auth_headers = {"Authorization": "Bearer pb_tok_sec_reader_val"}

        # 1. projectbrain_upload_document
        res_upload = await ac.post(
            "/mcp/messages/?session_id=sec_sess_1",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "projectbrain_upload_document", "arguments": {"file_path": "test.md"}},
                "id": 101,
            },
            headers=auth_headers,
        )
        assert res_upload.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in res_upload.json()["error"]["message"]

        # 2. projectbrain_push_processed_document
        res_push = await ac.post(
            "/mcp/messages/?session_id=sec_sess_1",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "projectbrain_push_processed_document", "arguments": {"project_id": "p"}},
                "id": 102,
            },
            headers=auth_headers,
        )
        assert res_push.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in res_push.json()["error"]["message"]

        # 3. projectbrain_promote_branch_graph
        res_promote = await ac.post(
            "/mcp/messages/?session_id=sec_sess_1",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "projectbrain_promote_branch_graph", "arguments": {"project_id": "p", "branch": "feat"}},
                "id": 103,
            },
            headers=auth_headers,
        )
        assert res_promote.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in res_promote.json()["error"]["message"]


def test_sse_filter_omits_new_write_keywords_for_reader():
    """Verify that SSEFilter strips push, promote, upload tools for reader role accounts."""
    reader_filter = SSEFilter("reader", "*")
    mock_tools_chunk = (
        b'event: message\ndata: {"jsonrpc":"2.0","result":{"tools":['
        b'{"name":"projectbrain_query","description":"Query"},'
        b'{"name":"projectbrain_upload_document","description":"Upload"},'
        b'{"name":"projectbrain_push_processed_document","description":"Push"},'
        b'{"name":"projectbrain_promote_branch_graph","description":"Promote"}'
        b']},"id":20}\n\n'
    )
    filtered = reader_filter.process_chunk(mock_tools_chunk).decode("utf-8")
    assert "projectbrain_query" in filtered
    assert "projectbrain_upload_document" not in filtered
    assert "projectbrain_push_processed_document" not in filtered
    assert "projectbrain_promote_branch_graph" not in filtered


# =====================================================================
# 3. SEC-05 & SEC-07: Upload Size Cap and Filename Sanitization
# =====================================================================

def test_sources_upload_filename_sanitization():
    """Verify that /sources/upload sanitizes ../../evil.md to evil.md in metadata."""
    client = TestClient(app)
    file_content = b"# Safe Document Content\nThis is a test of filename sanitization."
    files = {"file": ("../../evil.md", io.BytesIO(file_content), "text/markdown")}
    res = client.post("/sources/upload", data={"project_id": "sec_upload_proj", "branch": "main"}, files=files)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["filename"] == "evil.md"
    assert ".." not in data["filename"]


def test_sources_upload_size_limit_enforced(monkeypatch):
    """Verify that /sources/upload rejects payloads exceeding PB_MAX_UPLOAD_MB with HTTP 413."""
    # Set limit to 0.001 MB (1048 bytes)
    monkeypatch.setenv("PB_MAX_UPLOAD_MB", "0.001")
    client = TestClient(app)

    oversized_content = b"X" * 2048
    files = {"file": ("large.txt", io.BytesIO(oversized_content), "text/plain")}
    res = client.post(
        "/sources/upload",
        data={"project_id": "sec_upload_proj", "branch": "main"},
        files=files,
        headers={"Content-Length": str(len(oversized_content))},
    )
    assert res.status_code == 413
    assert "Payload Too Large" in res.json()["detail"]


# =====================================================================
# 4. SEC-01: Token Redaction and PB_REQUIRE_REST_AUTH
# =====================================================================

def test_auth_token_masking_in_list_accounts():
    client = TestClient(app)
    # Register an account
    client.post("/auth/accounts", json={
        "username": "sec_mask_user",
        "token": "pb_tok_secret_value_12345",
        "role": "collaborator",
    })

    # Unauthenticated GET /auth/accounts returns masked tokens
    res_anon = client.get("/auth/accounts")
    assert res_anon.status_code == 200
    accounts = res_anon.json()["accounts"]
    user_acc = next((a for a in accounts if a["username"] == "sec_mask_user"), None)
    assert user_acc is not None
    assert user_acc["token"].startswith("pb_t")
    assert user_acc["token"].endswith("2345")
    assert "*" in user_acc["token"]
    assert "secret_value" not in user_acc["token"]

    # Authenticated admin GET /auth/accounts returns full cleartext token
    res_admin = client.get("/auth/accounts", headers={"Authorization": "Bearer pb_tok_admin"})
    assert res_admin.status_code == 200
    admin_accounts = res_admin.json()["accounts"]
    admin_user_acc = next((a for a in admin_accounts if a["username"] == "sec_mask_user"), None)
    assert admin_user_acc is not None
    assert admin_user_acc["token"] == "pb_tok_secret_value_12345"


def test_auth_require_rest_auth_enforcement(monkeypatch):
    """When PB_REQUIRE_REST_AUTH=true, anonymous requests to /auth/accounts are rejected with 401."""
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    client = TestClient(app)

    # Anonymous list accounts
    res_anon = client.get("/auth/accounts")
    assert res_anon.status_code == 401
    assert "Authentication required" in res_anon.json()["detail"]

    # Anonymous create account
    res_create = client.post("/auth/accounts", json={"username": "sec_anon", "role": "reader"})
    assert res_create.status_code == 401

    # Valid admin token succeeds
    res_admin = client.get("/auth/accounts", headers={"Authorization": "Bearer pb_tok_admin"})
    assert res_admin.status_code == 200


# =====================================================================
# 5. CR-MED-01 & CR-MED-02: Hyperedge Promotion and Autocommit Transaction
# =====================================================================

def test_sqlite_hyperedge_promotion_lifecycle(tmp_path):
    """Verify that SqliteOverlayGraphStore promotes hyperedge upserts and tombstones."""
    db_file = tmp_path / "sqlite_hyper_test.db"
    store = SqliteOverlayGraphStore(db_path=str(db_file))
    project_id = "hyp_proj"

    # 1. Sync canonical root with initial hyperedges
    store.sync_branch_delta(
        project_id,
        "main",
        GraphDelta(
            project_id=project_id,
            nodes=[GraphNode(id="n1", project_id=project_id, label="Node1", source_file="src/a.py")],
            hyperedges=[
                GraphHyperedge(
                    id="h_base_1",
                    project_id=project_id,
                    label="Cluster1",
                    nodes=["n1"],
                    relation="clusters",
                    source_file="src/a.py",
                ),
                GraphHyperedge(
                    id="h_to_delete",
                    project_id=project_id,
                    label="ClusterDelete",
                    nodes=["n1"],
                    relation="clusters",
                    source_file="src/a.py",
                ),
            ],
        ),
    )

    # 2. Sync branch overlay with new hyperedge and tombstone
    store.sync_branch_delta(
        project_id,
        "feature/hyper",
        GraphDelta(
            project_id=project_id,
            hyperedges=[
                GraphHyperedge(
                    id="h_new_1",
                    project_id=project_id,
                    label="ClusterNew",
                    nodes=["n1"],
                    relation="clusters",
                    source_file="src/b.py",
                )
            ],
            hyperedge_tombstones=["h_to_delete"],
        ),
    )

    # 3. Promote branch to root
    promote_res = store.promote_branch_to_root(project_id, "feature/hyper")
    assert promote_res["status"] == "promoted"
    assert promote_res["upserted_hyperedges"] == 1
    assert promote_res["deleted_hyperedges"] == 1

    # 4. Verify canonical root reflects hyperedge changes
    root_subgraph = store.get_resolved_subgraph(project_id, "main")
    root_hyperedge_ids = {h["id"] for h in root_subgraph.get("hyperedges", [])}
    assert "h_new_1" in root_hyperedge_ids
    assert "h_base_1" in root_hyperedge_ids
    assert "h_to_delete" not in root_hyperedge_ids


class MockAutocommitPostgresConn:
    """Mock connection recording autocommit transactions for PostgresSchemaGraphStore."""

    def __init__(self):
        self.autocommit = True
        self.executed_sqls: List[str] = []
        self.closed = False

    def cursor(self, cursor_factory=None):
        return MockAutocommitCursor(self)


def stringify_psql_statement(stmt: Any) -> str:
    from psycopg2 import sql as psql
    if isinstance(stmt, str):
        return stmt
    if isinstance(stmt, psql.Composed):
        parts: List[str] = []
        for part in stmt._wrapped:
            if isinstance(part, psql.Identifier):
                parts.append('"' + '.'.join(str(s) for s in part._wrapped) + '"')
            elif isinstance(part, psql.Literal):
                parts.append("'" + str(part._wrapped).replace("'", "''") + "'")
            elif isinstance(part, psql.SQL):
                parts.append(part._wrapped)
            elif isinstance(part, psql.Composed):
                parts.append(stringify_psql_statement(part))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(stmt)


class MockAutocommitCursor:

    def __init__(self, conn: MockAutocommitPostgresConn):
        self.conn = conn

    def execute(self, stmt: Any, params=None):
        sql_text = stringify_psql_statement(stmt)
        self.conn.executed_sqls.append(sql_text)

    def fetchall(self):
        return []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_postgres_autocommit_transaction_wrapping():
    """Verify that PostgresSchemaGraphStore wraps promotion in explicit BEGIN and COMMIT when autocommit=True."""
    mock_conn = MockAutocommitPostgresConn()
    store = PostgresSchemaGraphStore(conn=mock_conn)
    project_id = "pg_tx_proj"
    branch_name = "feature/atomic-tx"

    # Execute branch promotion
    res = store.promote_branch_to_root(project_id, branch_name)
    assert res["status"] == "promoted"

    executed = mock_conn.executed_sqls
    # Check that BEGIN; was executed before DROP SCHEMA and COMMIT; was executed after
    assert "BEGIN;" in executed
    assert "COMMIT;" in executed

    begin_idx = executed.index("BEGIN;")
    commit_idx = executed.index("COMMIT;")
    drop_indices = [i for i, s in enumerate(executed) if "DROP SCHEMA" in s]
    assert drop_indices, "DROP SCHEMA statement was not recorded"
    drop_idx = drop_indices[0]

    assert begin_idx < drop_idx < commit_idx, f"Ordering violation: BEGIN={begin_idx}, DROP={drop_idx}, COMMIT={commit_idx}"
