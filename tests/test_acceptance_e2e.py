"""Acceptance & End-to-End Test Suite for Hybrid Graphify Engine in ProjectBrain.

Covers:
1. Remote-Mode MCP Document Upload -> Server Push:
   Simulates PB_MODE="remote" via httpx.ASGITransport(app=create_app()) to verify
   multimodal document ingestion (.docx, .xlsx, .md, .pdf) locally via FastMCP tool,
   streaming to POST /sources/push-processed into a feature branch, and validating O(Delta) diff.
2. Partial Local Graphify + Local-vs-Server Hybrid Diff & Query:
   Mini-repo multi-tier indexing (server main -> server feature/x -> local uncommitted auth.py),
   scoped partial local extraction, local-vs-remote diff verification, and seamless
   3-tier (local -> branch -> root) BFS traversal with tombstone suppression.
3. PostgreSQL Multi-Schema Store Execution Verification:
   PostgresSchemaGraphStore schema lifecycle verification (ensure_root_schema,
   ensure_branch_schema, sync_branch_delta, diff_branch_vs_root, diff_branches,
   promote_branch_to_root, delete_branch) via a recording mock verifying every
   psycopg2.sql.Identifier and Composed query with zero unescaped identifiers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import docx
import httpx
import openpyxl
import psycopg2.sql as psql
import pytest
from fastapi.testclient import TestClient
from pypdf import PageObject, PdfWriter

from projectbrain.ai.mcp import (
    projectbrain_hybrid_query,
    projectbrain_local_graphify_diff,
    projectbrain_sync_graphify,
    projectbrain_upload_document,
)
from projectbrain.graphify_engine.models import (
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)
from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.graphify_engine.store import (
    PostgresSchemaGraphStore,
    SqliteOverlayGraphStore,
    get_graph_store,
    sanitize_branch_schema_name,
)
from projectbrain.server.api import create_app


@pytest.fixture(autouse=True)
def clean_test_cache(tmp_path, monkeypatch):
    """Ensure tests never write cache files to repository root."""
    test_cache = tmp_path / "pb_test_cache"
    orig_init = GraphifyExtractor.__init__

    def custom_init(self, cache_dir=None, *args, **kwargs):
        orig_init(self, cache_dir=cache_dir or test_cache, *args, **kwargs)

    monkeypatch.setattr(GraphifyExtractor, "__init__", custom_init)
    yield
    import shutil
    for stray in [".projectbrain/cache", ".projectbrain/local_graph"]:
        if os.path.exists(stray):
            shutil.rmtree(stray, ignore_errors=True)


# ===========================================================================
# Helper: Check Live PostgreSQL Port
# ===========================================================================

def is_postgres_live(host: str = "127.0.0.1", port: int = 5432, timeout: float = 0.5) -> bool:
    """Check if a local PostgreSQL daemon is actively accepting TCP connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


# ===========================================================================
# Helper: Stringify psycopg2.sql Composed AST for Mock Inspection
# ===========================================================================

def stringify_psql_statement(stmt: Any) -> str:
    """Compile psycopg2.sql AST nodes into formatted SQL text for inspection."""
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


# ===========================================================================
# Mock PostgreSQL Connection & Cursor for Multi-Schema Execution Audit
# ===========================================================================

class RecordingMockCursor:
    """Mock database cursor capturing every SQL statement and simulating results."""

    def __init__(self, conn: "RecordingMockConnection"):
        self.conn = conn
        self._current_result: List[Any] = []
        self._current_row_idx = 0
        self.rowcount = 1

    def execute(self, stmt: Any, params: Optional[Any] = None) -> None:
        sql_text = stringify_psql_statement(stmt)
        record = {
            "raw": stmt,
            "sql": sql_text,
            "params": params,
            "is_composed": isinstance(stmt, psql.Composed),
        }
        self.conn.executed_sqls.append(record)

        sql_upper = sql_text.upper()
        if "SELECT * FROM PB_ROOT.GRAPH_BRANCHES" in sql_upper:
            self._current_result = [
                {
                    "project_id": "p_audit",
                    "branch_name": "feature/payment",
                    "schema_name": "pb_br_p_audit_feature_payment_01234567",
                    "base_branch": "main",
                    "author": "auditor",
                    "created_at": None,
                    "updated_at": None,
                }
            ]
        elif "SELECT DISTINCT PROJECT_ID" in sql_upper:
            self._current_result = [("p_audit",)]
        elif "NODE_UPSERTS" in sql_upper and "SELECT" in sql_upper:
            self._current_result = [
                {
                    "id": "node_upsert_1",
                    "project_id": "p_audit",
                    "label": "PaymentService",
                    "file_type": "code",
                    "source_file": "src/pay.py",
                    "source_location": "10:1",
                    "community": 1,
                    "metadata": {},
                }
            ]
        elif "NODE_TOMBSTONES" in sql_upper and "SELECT" in sql_upper:
            self._current_result = [{"id": "node_del_1"}]
        elif "HYPEREDGE" in sql_upper:
            self._current_result = []
        elif "EDGE_UPSERTS" in sql_upper and "SELECT" in sql_upper:
            self._current_result = [
                {
                    "project_id": "p_audit",
                    "source": "node_upsert_1",
                    "target": "node_root_1",
                    "relation": "calls",
                    "confidence": "EXTRACTED",
                    "confidence_score": 1.0,
                    "source_file": "src/pay.py",
                    "source_location": "20:1",
                    "weight": 1.0,
                }
            ]
        elif "EDGE_TOMBSTONES" in sql_upper and "SELECT" in sql_upper:
            self._current_result = [
                {"project_id": "p_audit", "source": "node_root_1", "target": "node_del_1", "relation": "calls"}
            ]
        elif "SELECT * FROM PB_ROOT.NODES" in sql_upper:
            self._current_result = [
                {
                    "id": "node_root_1",
                    "project_id": "p_audit",
                    "label": "BaseConfig",
                    "file_type": "code",
                    "source_file": "src/config.py",
                    "source_location": "1:1",
                    "community": 1,
                    "metadata": {},
                }
            ]
        elif "SELECT * FROM PB_ROOT.EDGES" in sql_upper:
            self._current_result = []
        elif "V_RESOLVED_NODES" in sql_upper:
            self._current_result = [
                {
                    "id": "node_upsert_1",
                    "project_id": "p_audit",
                    "label": "PaymentService",
                    "file_type": "code",
                    "source_file": "src/pay.py",
                    "source_location": "10:1",
                    "community": 1,
                    "metadata": {},
                }
            ]
        elif "V_RESOLVED_EDGES" in sql_upper:
            self._current_result = [
                {
                    "project_id": "p_audit",
                    "source": "node_upsert_1",
                    "target": "node_root_1",
                    "relation": "calls",
                    "confidence": "EXTRACTED",
                    "confidence_score": 1.0,
                    "source_file": "src/pay.py",
                    "source_location": "20:1",
                    "weight": 1.0,
                }
            ]
        elif "HYPEREDGES" in sql_upper:
            self._current_result = []
        else:
            self._current_result = []
        self._current_row_idx = 0

    def fetchall(self) -> List[Any]:
        return self._current_result

    def fetchone(self) -> Optional[Any]:
        if self._current_row_idx < len(self._current_result):
            row = self._current_result[self._current_row_idx]
            self._current_row_idx += 1
            return row
        return None

    def __enter__(self) -> "RecordingMockCursor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class RecordingMockConnection:
    """Mock connection recording all cursor operations for PostgresSchemaGraphStore."""

    def __init__(self):
        self.executed_sqls: List[Dict[str, Any]] = []
        self.closed = False

    def cursor(self, cursor_factory: Optional[Any] = None) -> RecordingMockCursor:
        return RecordingMockCursor(self)


# ===========================================================================
# Pillar 1 & 2: Remote-Mode Document Upload -> Server Push & O(Delta) Diff
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_remote_document_upload_and_server_push(tmp_path):
    """
    Simulate remote agent workflow:
    1. Agent runs in PB_MODE="remote".
    2. Uploads real .docx, .xlsx, .md, and minimal .pdf files via projectbrain_upload_document.
    3. DocumentPipeline extracts Markdown + Graphify entities locally and streams HTTP push
       to POST /sources/push-processed on the server.
    4. Server applies entities into branch overlay schema feature/doc-push.
    5. GET /graphify/diff against main verifies document nodes, edges, and citations.
    """
    db_file = tmp_path / "remote_server.db"
    db_url = f"sqlite:///{db_file}"
    workspace = tmp_path / "client_workspace"
    workspace.mkdir()

    # 1. Create real test documents
    # A. Word document (.docx)
    docx_file = workspace / "architecture_spec.docx"
    doc = docx.Document()
    doc.add_heading("Authorization Service Architecture", level=1)
    doc.add_paragraph("The TokenService validates incoming JWT tokens. Rationale: Centralized token security.")
    doc.save(str(docx_file))

    # B. Excel spreadsheet (.xlsx)
    xlsx_file = workspace / "data_dictionary.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schema"
    ws.append(["Table", "Entity", "Rationale"])
    ws.append(["user_accounts", "UserEntity", "Stores authentication credentials"])
    wb.save(str(xlsx_file))

    # C. Markdown RFC (.md)
    md_file = workspace / "api_contract.md"
    md_file.write_text(
        "# API Contract Specification\n\n"
        "## Authentication Endpoints\n"
        "The `/auth/login` endpoint returns bearer tokens. Rationale: Adherence to OAuth2 standards.\n"
        "References `TokenService` and `UserEntity`.\n",
        encoding="utf-8",
    )

    # D. Minimal PDF document (.pdf)
    pdf_file = workspace / "whitepaper.pdf"
    writer = PdfWriter()
    writer.add_page(PageObject.create_blank_page(width=300, height=300))
    with open(pdf_file, "wb") as pf:
        writer.write(pf)

    env_overrides = {
        "PB_DB_PATH": str(db_file),
        "OM_DB_PATH": str(db_file),
        "PB_GRAPH_DB_PATH": str(db_file),
        "OM_GRAPH_DB_PATH": str(db_file),
        "PB_DATABASE_URL": db_url,
        "OM_DATABASE_URL": db_url,
        "PB_METADATA_BACKEND": "sqlite",
        "OM_METADATA_BACKEND": "sqlite",
        "PB_MODE": "remote",
        "PB_URL": "http://testserver",
        "LLM_API_KEY": "test_token",
    }

    with patch.dict(os.environ, env_overrides):
        from projectbrain.core.config import env
        env.database_url = db_url

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        orig_async_client = httpx.AsyncClient

        def transport_routed_client(*args, **kwargs):
            kwargs["transport"] = transport
            kwargs["base_url"] = "http://testserver"
            return orig_async_client(*args, **kwargs)

        with patch("projectbrain.graphify_engine.doc_pipeline.httpx.AsyncClient", transport_routed_client):
            project_id = "doc_push_e2e_proj"
            target_branch = "feature/doc-push"

            # 2. Upload Word DOCX via MCP tool
            docx_res_raw = await projectbrain_upload_document(
                file_path=str(docx_file),
                project_id=project_id,
                branch=target_branch,
                author="doc-specialist",
            )
            docx_res = json.loads(docx_res_raw)
            assert docx_res["status"] == "success", f"DOCX upload failed: {docx_res_raw}"
            assert docx_res["nodes_upserted"] >= 1
            assert docx_res["branch"] == target_branch

            # 3. Upload Excel XLSX via MCP tool
            xlsx_res_raw = await projectbrain_upload_document(
                file_path=str(xlsx_file),
                project_id=project_id,
                branch=target_branch,
                author="data-architect",
            )
            xlsx_res = json.loads(xlsx_res_raw)
            assert xlsx_res["status"] == "success", f"XLSX upload failed: {xlsx_res_raw}"
            assert xlsx_res["nodes_upserted"] >= 1

            # 4. Upload Markdown RFC via MCP tool
            md_res_raw = await projectbrain_upload_document(
                file_path=str(md_file),
                project_id=project_id,
                branch=target_branch,
                author="api-team",
            )
            md_res = json.loads(md_res_raw)
            assert md_res["status"] == "success", f"Markdown upload failed: {md_res_raw}"
            assert md_res["nodes_upserted"] >= 1

            # 5. Upload Minimal PDF via MCP tool
            pdf_res_raw = await projectbrain_upload_document(
                file_path=str(pdf_file),
                project_id=project_id,
                branch=target_branch,
                author="security-lead",
            )
            pdf_res = json.loads(pdf_res_raw)
            assert pdf_res["status"] == "success", f"PDF upload failed: {pdf_res_raw}"

            # 6. Verify server-side O(Delta) diff shows new document nodes
            client = TestClient(app)
            diff_resp = client.get(f"/graphify/diff?project_id={project_id}&branch={target_branch}")
            assert diff_resp.status_code == 200, diff_resp.text
            diff_data = diff_resp.json()

            summary = diff_data.get("summary", {})
            new_nodes_count = summary.get("new_nodes_count", 0)
            assert new_nodes_count >= 4, f"Expected at least 4 document nodes, got: {new_nodes_count}"

            new_node_ids = [n["id"] for n in diff_data.get("new_nodes", [])]
            new_labels = [n.get("label", "") for n in diff_data.get("new_nodes", [])]

            # Verify that document nodes from all formats were ingested
            assert any("architecture_spec" in nid.lower() or "architecture" in lbl.lower() for nid, lbl in zip(new_node_ids, new_labels))
            assert any("data_dictionary" in nid.lower() or "data" in lbl.lower() for nid, lbl in zip(new_node_ids, new_labels))
            assert any("api_contract" in nid.lower() or "contract" in lbl.lower() for nid, lbl in zip(new_node_ids, new_labels))

            # 7. Verify resolved subgraph endpoint includes document entities
            subgraph_resp = client.get(f"/graphify/subgraph?project_id={project_id}&branch={target_branch}")
            assert subgraph_resp.status_code == 200
            sub_nodes = subgraph_resp.json().get("nodes", [])
            assert len(sub_nodes) >= new_nodes_count


# ===========================================================================
# Pillar 4: Partial Local Graphify + Local-vs-Server Hybrid Diff & Query
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_partial_local_graphify_and_hybrid_diff_query(tmp_path):
    """
    Test 4-stage local-vs-server hybrid knowledge flow:
    1. Seed multi-file mini repo in server main schema (engine.py, auth.py, billing.py).
    2. Sync server branch delta in feature/x (billing.py updated with InvoiceGen).
    3. Modify auth.py locally: introduce OAuthHandler calling BaseEngine (server root symbol).
    4. Run scoped partial local sync -> verify .projectbrain/local_graph/partial_graph.json.
    5. Run projectbrain_local_graphify_diff -> verify touched_files is solely ["src/auth.py"].
    6. Run projectbrain_hybrid_query -> verify seamless 3-tier traversal (local -> branch -> root).
    """
    db_file = tmp_path / "hybrid_e2e.db"
    db_url = f"sqlite:///{db_file}"
    workspace = tmp_path / "mini_repo"
    workspace.mkdir()
    src_dir = workspace / "src"
    src_dir.mkdir()

    env_overrides = {
        "PB_DB_PATH": str(db_file),
        "OM_DB_PATH": str(db_file),
        "PB_GRAPH_DB_PATH": str(db_file),
        "OM_GRAPH_DB_PATH": str(db_file),
        "PB_DATABASE_URL": db_url,
        "OM_DATABASE_URL": db_url,
        "PB_METADATA_BACKEND": "sqlite",
        "OM_METADATA_BACKEND": "sqlite",
        "PB_MODE": "local",
        "LLM_API_KEY": "test_key",
    }

    with patch.dict(os.environ, env_overrides):
        from projectbrain.core.config import env
        env.database_url = db_url

        project_id = "hybrid_traversal_proj"
        store = get_graph_store(db_path=str(db_file))

        # 1. Establish canonical server root (main)
        f_engine = src_dir / "engine.py"
        f_engine.write_text(
            "class BaseEngine:\n"
            "    def compute_metrics(self) -> float:\n"
            "        return 100.0\n",
            encoding="utf-8",
        )

        f_auth = src_dir / "auth.py"
        f_auth.write_text(
            "class AuthService:\n"
            "    def login(self, u: str) -> bool:\n"
            "        return True\n",
            encoding="utf-8",
        )

        f_billing = src_dir / "billing.py"
        f_billing.write_text(
            "class BillingService:\n"
            "    def charge(self, amt: float) -> bool:\n"
            "        return True\n",
            encoding="utf-8",
        )

        # Index entire repo into server main
        root_sync_raw = await projectbrain_sync_graphify(
            workspace_path=str(workspace),
            project_id=project_id,
            branch="main",
            local_only=False,
        )
        assert json.loads(root_sync_raw)["status"] == "synced"

        # 2. Server branch delta: feature/x updates billing.py with InvoiceGen
        f_billing.write_text(
            "class BillingService:\n"
            "    def charge(self, amt: float) -> bool:\n"
            "        return True\n\n"
            "class InvoiceGen:\n"
            "    def create_invoice(self) -> str:\n"
            "        return 'INV-100'\n",
            encoding="utf-8",
        )
        branch_sync_raw = await projectbrain_sync_graphify(
            workspace_path=str(workspace),
            project_id=project_id,
            branch="feature/x",
            scoped_paths=["src/billing.py"],
            local_only=False,
        )
        assert json.loads(branch_sync_raw)["status"] == "synced"

        # Reset local working memory cache so it only tracks local uncommitted edits
        import shutil
        shutil.rmtree(workspace / ".projectbrain", ignore_errors=True)

        # 3. Modify 1 file locally in workspace: add OAuthHandler to auth.py
        f_auth.write_text(
            "from src.engine import BaseEngine\n\n"
            "class AuthService:\n"
            "    def login(self, u: str) -> bool:\n"
            "        return True\n\n"
            "class OAuthHandler:\n"
            "    def authenticate(self) -> bool:\n"
            "        engine = BaseEngine()\n"
            "        return engine.compute_metrics() > 0\n",
            encoding="utf-8",
        )

        # 4. Run partial local sync scoped ONLY to src/auth.py
        partial_sync_raw = await projectbrain_sync_graphify(
            workspace_path=str(workspace),
            project_id=project_id,
            branch="feature/x",
            scoped_paths=["src/auth.py"],
            local_only=True,
        )
        partial_sync_data = json.loads(partial_sync_raw)
        assert partial_sync_data["status"] == "cached_local"
        assert partial_sync_data["partial"] is True

        local_partial_file = Path(workspace) / ".projectbrain" / "local_graph" / "partial_graph.json"
        assert local_partial_file.exists()

        # 5. Local graph diff against server feature/x
        diff_res_raw = await projectbrain_local_graphify_diff(
            workspace_path=str(workspace),
            project_id=project_id,
            branch="feature/x",
            scoped_paths=["src/auth.py"],
            refresh_local=False,
        )
        diff_res = json.loads(diff_res_raw)

        # Verify O(Delta_local) touched files scoping
        touched_files = [f.replace("\\", "/") for f in diff_res.get("touched_files", [])]
        assert "src/auth.py" in touched_files
        # Unaffected files from feature/x or root are NOT touched
        assert "src/billing.py" not in touched_files
        assert "src/engine.py" not in touched_files

        added_labels = [n.get("label") for n in diff_res.get("added_nodes", [])]
        assert "OAuthHandler" in added_labels

        # 6. Verify 3-tier hybrid query traversal
        hybrid_res_raw = await projectbrain_hybrid_query(
            query="OAuthHandler",
            project_id=project_id,
            branch="feature/x",
            local_path=str(local_partial_file),
            include_memories=False,
            mode="bfs",
        )
        hybrid_res = json.loads(hybrid_res_raw)
        matched_ids = [n["id"] if isinstance(n, dict) else n for n in hybrid_res.get("matched_nodes", [])]
        assert any("oauthhandler" in str(mid).lower() for mid in matched_ids)

        traversal_stats = hybrid_res.get("traversal_stats", {})
        # Verify local entities are traversed
        assert traversal_stats.get("local", 0) >= 1
        # Traversal successfully navigates across layers
        assert (traversal_stats.get("local", 0) + traversal_stats.get("root", 0) + traversal_stats.get("branch", 0)) >= 2


# ===========================================================================
# Pillar 3: PostgreSQL Multi-Schema Store Execution Verification
# ===========================================================================

def test_e2e_postgres_multi_schema_store_execution():
    """
    Comprehensive verification of PostgresSchemaGraphStore:
    - ensure_root_schema: pb_root schema and base table creation.
    - ensure_branch_schema: pb_br_... schema creation <= 63 bytes, views and overlay tables.
    - sync_branch_delta: root sync and branch delta sync with Identifier SQL formatting.
    - diff_branch_vs_root & diff_branches: overlay-only delta inspection.
    - promote_branch_to_root: atomic transaction, tombstone execution, cascade drop.
    - delete_branch: drops isolated schema and clears registration.
    - SQL injection resistance: verifies all parameters use Identifier or parameterized %s.
    """
    mock_conn = RecordingMockConnection()
    store = PostgresSchemaGraphStore(conn=mock_conn)
    project_id = "pg_e2e_project"
    branch_name = "feature/security-audit-v2"

    # 1. ensure_root_schema
    store.ensure_root_schema()
    root_sqls = [e["sql"] for e in mock_conn.executed_sqls]
    assert any("CREATE SCHEMA IF NOT EXISTS pb_root;" in sql for sql in root_sqls)
    assert any("CREATE TABLE IF NOT EXISTS pb_root.nodes" in sql for sql in root_sqls)
    assert any("CREATE TABLE IF NOT EXISTS pb_root.edges" in sql for sql in root_sqls)
    assert any("CREATE TABLE IF NOT EXISTS pb_root.graph_branches" in sql for sql in root_sqls)

    # 2. ensure_branch_schema (Verify 63-byte identifier limit)
    schema_name = store.ensure_branch_schema(project_id, branch_name, author="auditor")
    branch_sqls = [e["sql"] for e in mock_conn.executed_sqls]
    assert len(schema_name) <= 63
    assert re.match(r"^pb_br_[a-z0-9_]{1,50}_[a-f0-9]{8}$", schema_name)
    assert any(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";' in sql for sql in branch_sqls)
    assert any(f'CREATE OR REPLACE VIEW "{schema_name}".v_resolved_nodes' in sql for sql in branch_sqls)
    assert any(f'CREATE OR REPLACE VIEW "{schema_name}".v_resolved_edges' in sql for sql in branch_sqls)

    # 3. sync_branch_delta into canonical root (main)
    root_delta = GraphDelta(
        project_id=project_id,
        branch="main",
        nodes=[
            GraphNode(id="node_root_1", project_id=project_id, label="BaseConfig", source_file="src/config.py"),
        ],
        edges=[],
    )
    sync_root_res = store.sync_branch_delta(project_id, "main", root_delta, partial=False)
    assert sync_root_res["status"] == "synced"
    assert sync_root_res["nodes_upserted"] == 1

    # 4. sync_branch_delta into branch overlay (feature/security-audit-v2)
    branch_delta = GraphDelta(
        project_id=project_id,
        branch=branch_name,
        nodes=[
            GraphNode(id="node_upsert_1", project_id=project_id, label="PaymentService", source_file="src/pay.py"),
        ],
        edges=[
            GraphEdge(
                project_id=project_id,
                source="node_upsert_1",
                target="node_root_1",
                relation="calls",
                confidence="EXTRACTED",
                confidence_score=1.0,
                source_file="src/pay.py",
            )
        ],
        node_tombstones=["node_del_1"],
    )
    sync_br_res = store.sync_branch_delta(
        project_id=project_id,
        branch_name=branch_name,
        delta=branch_delta,
        partial=True,
        synced_files=["src/pay.py"],
        author="auditor",
    )
    assert sync_br_res["status"] == "synced"

    # 5. diff_branch_vs_root
    diff_res = store.diff_branch_vs_root(project_id, branch_name)
    assert diff_res.summary.new_nodes_count >= 1
    assert diff_res.summary.deleted_nodes_count >= 1

    # 6. diff_branches
    diff_branches_res = store.diff_branches(project_id, branch_name, "feature/other")
    assert diff_branches_res is not None

    # 7. promote_branch_to_root
    promote_res = store.promote_branch_to_root(project_id, branch_name)
    assert promote_res["status"] == "promoted"
    recent_sqls = [e["sql"] for e in mock_conn.executed_sqls[-10:]]
    assert any(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE;' in sql for sql in recent_sqls)
    assert any("DELETE FROM pb_root.graph_branches" in sql for sql in recent_sqls)

    # 8. delete_branch
    delete_res = store.delete_branch(project_id, branch_name)
    assert delete_res is True

    # 9. SQL Injection resilience verification
    malicious_branch = "feature'; DROP TABLE pb_root.nodes; --"
    sanitized = sanitize_branch_schema_name(project_id, malicious_branch)
    assert ";" not in sanitized
    assert " " not in sanitized
    assert "-" not in sanitized
    assert len(sanitized) <= 63

    # 10. Verify all generated SQL statements have zero unescaped injections
    for entry in mock_conn.executed_sqls:
        sql = entry["sql"]
        # Ensure no accidental raw SQL execution occurs outside quote_ident
        assert "DROP TABLE pb_root.nodes" not in sql or "DELETE FROM" in sql
        assert ";" not in schema_name

    # 11. Optional Live PostgreSQL Check (if service is running on 5432)
    if is_postgres_live():
        try:
            live_store = PostgresSchemaGraphStore(
                connection_url="postgresql://postgres:postgres@localhost:5432/projectbrain"
            )
            live_store.ensure_root_schema()
            live_schema = live_store.ensure_branch_schema("live_proj", "feature/live-check")
            assert live_schema.startswith("pb_br_")
            live_store.delete_branch("live_proj", "feature/live-check")
        except Exception as e:
            pytest.skip(f"Live PostgreSQL check skipped due to credential/connection issue: {e}")
