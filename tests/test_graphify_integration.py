"""End-to-End Integration Tests for Hybrid Graphify Engine in ProjectBrain.

Covers:
1. /graphify REST API endpoints (sync, subgraph, diff, promote, query, local-diff, branches, stats).
2. /sources/push-processed and /sources/upload multi-branch document & graph ingestion.
3. Legacy /codegraph routes (/data, /status, /diff, /projects) backward-compatibility over Graphify data.
4. FastMCP tool registration and async invocation across all 7 new and upgraded tools.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from projectbrain.ai.mcp import (
    mcp_server,
    projectbrain_hybrid_query,
    projectbrain_local_graphify_diff,
    projectbrain_promote_branch_graph,
    projectbrain_push_processed_document,
    projectbrain_query_codegraph,
    projectbrain_query_graphify,
    projectbrain_sync_graphify,
    projectbrain_upload_document,
)
from projectbrain.graphify_engine.models import (
    GraphDelta,
    GraphEdge,
    GraphNode,
)
from projectbrain.graphify_engine.store import get_graph_store
from projectbrain.server.api import create_app


@pytest.fixture
def isolated_env(tmp_path):
    """Fixture providing isolated temporary database and workspace paths."""
    db_file = tmp_path / "test_graphify_integration.db"
    db_url = f"sqlite:///{db_file}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create dummy source files in workspace
    py_file = workspace / "sample.py"
    py_file.write_text(
        "class OrderService:\n"
        "    def checkout(self, user_id: str):\n"
        "        return True\n"
    )
    doc_file = workspace / "architecture.md"
    doc_file.write_text(
        "# Architecture Spec\n\n"
        "The OrderService handles checkout operations.\n"
    )

    env_overrides = {
        "PB_DB_PATH": str(db_file),
        "OM_DB_PATH": str(db_file),
        "PB_GRAPH_DB_PATH": str(db_file),
        "OM_GRAPH_DB_PATH": str(db_file),
        "PB_DATABASE_URL": db_url,
        "OM_DATABASE_URL": db_url,
        "PB_METADATA_BACKEND": "sqlite",
        "OM_METADATA_BACKEND": "sqlite",
        "LLM_API_KEY": "mock_key",
    }

    with patch.dict(os.environ, env_overrides):
        from projectbrain.core.config import env
        env.database_url = db_url

        app = create_app()
        client = TestClient(app)
        yield {
            "client": client,
            "db_path": str(db_file),
            "workspace": str(workspace),
            "py_file": str(py_file),
            "doc_file": str(doc_file),
        }


# ===========================================================================
# 1. FastMCP Tool Registration Verification
# ===========================================================================

def test_fastmcp_tools_registration():
    """Verify that all new Graphify FastMCP tools are registered on mcp_server."""
    tools = mcp_server._tool_manager.list_tools()
    tool_names = [tool.name for tool in tools]

    expected_tools = [
        "projectbrain_sync_graphify",
        "projectbrain_query_graphify",
        "projectbrain_local_graphify_diff",
        "projectbrain_hybrid_query",
        "projectbrain_upload_document",
        "projectbrain_push_processed_document",
        "projectbrain_promote_branch_graph",
        "projectbrain_sync_codegraph",
        "projectbrain_query_codegraph",
        "projectbrain_diff_project_versions",
    ]

    for tool_name in expected_tools:
        assert tool_name in tool_names, f"Expected MCP tool '{tool_name}' was not found in registered tools"


# ===========================================================================
# 2. REST API /graphify Endpoints E2E Flow
# ===========================================================================

def test_graphify_rest_endpoints_lifecycle(isolated_env):
    r"""
    Test full lifecycle via REST:
    - Sync root 'main'
    - Sync partial 'feature/auth'
    - Diff branch vs root ($O(\Delta)$)
    - Query subgraph
    - Hybrid query
    - Branch listing & stats
    - Promote branch to root
    - Delete branch
    """
    client = isolated_env["client"]
    project_id = "ecommerce_app"

    # 1. Sync root graph
    root_payload = {
        "project_id": project_id,
        "branch": "main",
        "nodes": [
            {
                "id": "node::main.py",
                "project_id": project_id,
                "label": "main.py",
                "file_type": "code",
                "source_file": "main.py",
                "metadata": {"kind": "file"},
            },
            {
                "id": "node::app",
                "project_id": project_id,
                "label": "create_app",
                "file_type": "code",
                "source_file": "main.py",
                "metadata": {"kind": "function"},
            },
        ],
        "edges": [
            {
                "project_id": project_id,
                "source": "node::main.py",
                "target": "node::app",
                "relation": "defines",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            }
        ],
        "partial": False,
        "author": "tester",
    }
    resp_sync_root = client.post("/graphify/sync", json=root_payload)
    assert resp_sync_root.status_code == 200, resp_sync_root.text
    sync_root_data = resp_sync_root.json()
    assert sync_root_data["status"] == "synced"
    assert sync_root_data["nodes_upserted"] == 2

    # 2. Sync branch overlay
    branch_payload = {
        "project_id": project_id,
        "branch": "feature/auth",
        "nodes": [
            {
                "id": "node::auth.py",
                "project_id": project_id,
                "label": "auth.py",
                "file_type": "code",
                "source_file": "auth.py",
                "metadata": {"kind": "file"},
            },
            {
                "id": "node::login",
                "project_id": project_id,
                "label": "login_user",
                "file_type": "code",
                "source_file": "auth.py",
                "metadata": {"kind": "function"},
            },
        ],
        "edges": [
            {
                "project_id": project_id,
                "source": "node::auth.py",
                "target": "node::login",
                "relation": "defines",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            }
        ],
        "partial": True,
        "synced_files": [{"file_path": "auth.py", "sha256": "dummy_sha"}],
        "author": "dev_alice",
    }
    resp_sync_branch = client.post("/graphify/sync", json=branch_payload)
    assert resp_sync_branch.status_code == 200, resp_sync_branch.text
    sync_branch_data = resp_sync_branch.json()
    assert sync_branch_data["status"] == "synced"
    assert sync_branch_data["branch"] == "feature/auth"

    # 3. Diff branch vs root
    resp_diff = client.get(f"/graphify/diff?project_id={project_id}&branch=feature/auth")
    assert resp_diff.status_code == 200, resp_diff.text
    diff_data = resp_diff.json()
    new_node_ids = [n["id"] for n in diff_data.get("new_nodes", [])]
    assert "node::auth.py" in new_node_ids
    assert "node::login" in new_node_ids

    # 4. Get resolved subgraph
    resp_subgraph = client.get(f"/graphify/subgraph?project_id={project_id}&branch=feature/auth")
    assert resp_subgraph.status_code == 200, resp_subgraph.text
    subgraph_data = resp_subgraph.json()
    subgraph_node_ids = [n["id"] for n in subgraph_data.get("nodes", [])]
    # Virtual graph resolves both root and overlay
    assert "node::main.py" in subgraph_node_ids
    assert "node::login" in subgraph_node_ids

    # 5. Hybrid Query endpoint
    query_payload = {
        "project_id": project_id,
        "query": "login",
        "branch": "feature/auth",
        "mode": "bfs",
        "budget": 1500,
    }
    resp_query = client.post("/graphify/query", json=query_payload)
    assert resp_query.status_code == 200, resp_query.text
    query_data = resp_query.json()
    assert "login" in query_data.get("context_markdown", "").lower() or len(query_data.get("nodes", [])) > 0

    # 6. List branches
    resp_branches = client.get(f"/graphify/branches?project_id={project_id}")
    assert resp_branches.status_code == 200, resp_branches.text
    branches_data = resp_branches.json()
    branch_names = [b["branch_name"] if isinstance(b, dict) else b for b in branches_data.get("branches", [])]
    assert "feature/auth" in branch_names

    # 7. Stats
    resp_stats = client.get(f"/graphify/stats?project_id={project_id}&branch=feature/auth")
    assert resp_stats.status_code == 200, resp_stats.text
    stats_data = resp_stats.json()
    assert (stats_data.get("node_count", 0) or stats_data.get("nodes_count", 0)) >= 4

    # 8. Promote branch to root
    resp_promote = client.post("/graphify/promote", json={"project_id": project_id, "branch": "feature/auth"})
    assert resp_promote.status_code == 200, resp_promote.text
    promote_data = resp_promote.json()
    assert promote_data.get("status") == "promoted"

    # Verify that canonical root now includes promoted nodes
    resp_root_subgraph = client.get(f"/graphify/subgraph?project_id={project_id}&branch=main")
    root_nodes_after = [n["id"] for n in resp_root_subgraph.json().get("nodes", [])]
    assert "node::login" in root_nodes_after

    # 9. Delete branch
    resp_delete_br = client.delete(f"/graphify/branches?project_id={project_id}&branch=feature/auth")
    assert resp_delete_br.status_code == 200, resp_delete_br.text


# ===========================================================================
# 3. Sources Document Ingestion Integration (/push-processed & /upload)
# ===========================================================================

def test_sources_push_processed_and_upload(isolated_env):
    """Verify document processing and branch delta syncing via /sources routes."""
    client = isolated_env["client"]
    project_id = "doc_proj"

    # 1. Test POST /sources/push-processed
    payload = {
        "project_id": project_id,
        "branch": "feature/docs",
        "document": {
            "filename": "specs/auth.md",
            "sha256": "abc123sha",
            "author": "alice",
            "file_type": "document",
            "markdown_content": "# Auth Specification\n\nUsers log in with credentials.\n",
            "chunks": [
                {
                    "chunk_id": "auth_c1",
                    "content": "Users log in with credentials.",
                    "tags": ["auth", "security"],
                }
            ],
        },
        "graph_delta": {
            "project_id": project_id,
            "branch": "feature/docs",
            "nodes": [
                {
                    "id": "doc::specs/auth.md",
                    "project_id": project_id,
                    "label": "specs/auth.md",
                    "file_type": "document",
                    "source_file": "specs/auth.md",
                    "metadata": {"type": "spec"},
                }
            ],
            "edges": [],
            "synced_files": [{"file_path": "specs/auth.md", "sha256": "abc123sha"}],
        },
    }

    resp = client.post("/sources/push-processed", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["project_id"] == project_id
    assert data["branch"] == "feature/docs"
    assert data["memories_created"] == 1
    assert data["nodes_upserted"] >= 1

    # 2. Test POST /sources/upload with multipart form-data
    md_content = b"# Payment Flow\n\nProcesses customer credit card transactions."
    files = {"file": ("payment.md", md_content, "text/markdown")}
    upload_data = {
        "project_id": project_id,
        "branch": "feature/payment",
        "tags": "payments,billing",
        "author": "bob",
    }
    resp_upload = client.post("/sources/upload", files=files, data=upload_data)
    assert resp_upload.status_code == 200, resp_upload.text
    upload_res = resp_upload.json()
    assert upload_res["status"] == "success"
    assert upload_res["graphify_synced"] is True
    assert upload_res["branch"] == "feature/payment"


# ===========================================================================
# 4. Backward Compatibility for Legacy /codegraph Routes over Graphify
# ===========================================================================

def test_legacy_codegraph_routes_over_graphify(isolated_env):
    """
    Verify that Vis.js UI and callers using /codegraph routes seamlessly
    read and interact with Graphify data.
    """
    client = isolated_env["client"]
    project_id = "visjs_project"

    # Pre-populate Graphify graph store
    store = get_graph_store(db_path=isolated_env["db_path"])
    delta = GraphDelta(
        project_id=project_id,
        branch="main",
        nodes=[
            GraphNode(
                id="OrderClass",
                project_id=project_id,
                label="Order",
                file_type="code",
                source_file="order.py",
                source_location="10:45",
                metadata={"kind": "class", "signature": "class Order"},
            ),
            GraphNode(
                id="ProcessFunc",
                project_id=project_id,
                label="process_order",
                file_type="code",
                source_file="order.py",
                source_location="50:80",
                metadata={"kind": "function", "signature": "def process_order()"},
            ),
        ],
        edges=[
            GraphEdge(
                project_id=project_id,
                source="ProcessFunc",
                target="OrderClass",
                relation="creates",
                confidence="EXTRACTED",
                confidence_score=1.0,
            )
        ],
    )
    store.sync_branch_delta(
        project_id=project_id,
        branch_name="main",
        delta=delta,
        partial=False,
    )

    # 1. /codegraph/projects lists the project
    resp_projects = client.get("/codegraph/projects")
    assert resp_projects.status_code == 200
    proj_ids = [p["id"] for p in resp_projects.json().get("projects", [])]
    assert project_id in proj_ids

    # 2. /codegraph/status reflects Graphify node count
    resp_status = client.get(f"/codegraph/status?project_id={project_id}")
    assert resp_status.status_code == 200
    status_data = resp_status.json()
    assert status_data["status"] == "ready"
    assert status_data["nodes_count"] >= 2
    assert status_data["edges_count"] >= 1

    # 3. /codegraph/data formats nodes and edges for Vis.js UI
    resp_data = client.get(f"/codegraph/data?project_id={project_id}")
    assert resp_data.status_code == 200
    vis_data = resp_data.json()
    assert "nodes" in vis_data and "edges" in vis_data
    node_labels = [n["label"] for n in vis_data["nodes"]]
    assert "Order" in node_labels
    assert "process_order" in node_labels

    # 4. /codegraph/diff works when branching
    branch_delta = GraphDelta(
        project_id=project_id,
        branch="feature/coupon",
        nodes=[
            GraphNode(
                id="CouponClass",
                project_id=project_id,
                label="Coupon",
                file_type="code",
                source_file="coupon.py",
                metadata={"kind": "class"},
            )
        ],
        edges=[],
    )
    store.sync_branch_delta(
        project_id=project_id,
        branch_name="feature/coupon",
        delta=branch_delta,
        partial=True,
        synced_files=["coupon.py"],
    )

    resp_diff = client.get(f"/codegraph/diff?base_project_id={project_id}:main&target_project_id={project_id}:feature/coupon")
    assert resp_diff.status_code == 200
    cg_diff = resp_diff.json()
    added_ids = [n["id"] for n in cg_diff.get("added", [])]
    assert "CouponClass" in added_ids


# ===========================================================================
# 5. FastMCP Tools Execution Verification
# ===========================================================================

@pytest.mark.asyncio
async def test_fastmcp_tools_execution(isolated_env):
    """Verify in-process execution of newly registered FastMCP tools."""
    workspace = isolated_env["workspace"]
    project_id = "mcp_tool_test"

    # 1. projectbrain_sync_graphify (local_only cache test)
    res_sync = await projectbrain_sync_graphify(
        workspace_path=workspace,
        project_id=project_id,
        branch="main",
        local_only=True,
    )
    data_sync = json.loads(res_sync)
    assert data_sync["status"] == "cached_local"
    assert data_sync["nodes_count"] > 0

    # 2. projectbrain_sync_graphify (store sync)
    res_sync_store = await projectbrain_sync_graphify(
        workspace_path=workspace,
        project_id=project_id,
        branch="main",
        local_only=False,
    )
    data_sync_store = json.loads(res_sync_store)
    assert data_sync_store["status"] == "synced"

    # 3. projectbrain_query_graphify (action="query")
    res_query = await projectbrain_query_graphify(
        query="OrderService",
        project_id=project_id,
        branch="main",
        action="query",
    )
    data_query = json.loads(res_query)
    assert "order" in data_query.get("context_markdown", "").lower() or len(data_query.get("nodes", [])) > 0

    # 4. projectbrain_local_graphify_diff
    res_diff = await projectbrain_local_graphify_diff(
        workspace_path=workspace,
        project_id=project_id,
        branch="main",
        refresh_local=True,
    )
    data_diff = json.loads(res_diff)
    assert "stats" in data_diff

    # 5. projectbrain_hybrid_query
    res_hybrid = await projectbrain_hybrid_query(
        query="OrderService checkout",
        project_id=project_id,
        branch="main",
        include_memories=False,
    )
    data_hybrid = json.loads(res_hybrid)
    assert data_hybrid["project_id"] == project_id
    assert "graph_summary" in data_hybrid

    # 6. projectbrain_push_processed_document
    res_push_doc = await projectbrain_push_processed_document(
        filename="notes.md",
        markdown_content="# Note on Checkout\nRequires valid session.\n",
        project_id=project_id,
        branch="main",
    )
    data_push = json.loads(res_push_doc)
    assert data_push["status"] == "success"

    # 7. projectbrain_promote_branch_graph
    res_promote = await projectbrain_promote_branch_graph(
        project_id=project_id,
        branch="main",
    )
    data_promote = json.loads(res_promote)
    assert "status" in data_promote


# ===========================================================================
# 6. CLI Commands Verification
# ===========================================================================

def test_cli_commands(isolated_env):
    """Verify execution of CLI entry functions for graphify-sync, graphify-diff, upload-doc, and codegraph-sync fallback."""
    from projectbrain.main import (
        run_graphify_sync,
        run_graphify_diff,
        run_upload_doc,
        run_codegraph_sync,
    )

    workspace = isolated_env["workspace"]
    project_id = "cli_integration_proj"
    doc_file = isolated_env["doc_file"]

    # 1. run_graphify_sync (local-only)
    run_graphify_sync(
        project_id=project_id,
        workspace_path=workspace,
        branch="main",
        local_only=True,
    )
    partial_json = Path(workspace) / ".projectbrain" / "local_graph" / "partial_graph.json"
    assert partial_json.exists()

    # 2. run_graphify_diff (local diff)
    run_graphify_diff(
        project_id=project_id,
        base_branch="main",
        local=True,
        workspace_path=workspace,
    )

    # 3. run_upload_doc (mock server push)
    with patch("projectbrain.graphify_engine.doc_pipeline.DocumentPipeline.push_processed_to_server") as mock_push:
        mock_push.return_value = {"status": "success", "nodes_upserted": 1}
        run_upload_doc(
            project_id=project_id,
            file_path=doc_file,
            branch="main",
            server_url="http://mock-server:8080",
        )
        mock_push.assert_called_once()

    # 4. run_codegraph_sync fallback when .codegraph/codegraph.db does not exist
    with patch("shutil.which", return_value=None), patch("projectbrain.main.run_graphify_sync") as mock_graphify_sync:
        run_codegraph_sync(
            project_id=project_id,
            project_path=workspace,
            branch="main",
        )
        mock_graphify_sync.assert_called_once()

