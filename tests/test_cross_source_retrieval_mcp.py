"""Wave 3B Verification Suite: Cross-Source FastMCP Tools, Upgrades, Retrieval, and RBAC."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for p in (str(REPO_ROOT), str(SRC_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from projectbrain.ai.mcp import (
    mcp_server,
    projectbrain_auto_link_sources,
    projectbrain_bind_shared_doc,
    projectbrain_cross_source_impact,
    projectbrain_database_topology,
    projectbrain_hybrid_query,
    projectbrain_link_sources,
    projectbrain_list_sources,
    projectbrain_push_processed_document,
    projectbrain_query,
    projectbrain_query_codegraph,
    projectbrain_query_graphify,
    projectbrain_register_source,
    projectbrain_sync_graphify,
    projectbrain_trace_code_and_docs,
    projectbrain_upload_document,
)
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import tenant_manager
from projectbrain.main import Memory
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.server.api import SSEFilter, _is_write_tool, create_app
from projectbrain.server.routes import auth as auth_routes


@pytest.fixture
def isolated_mcp_cross_source_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate workspace, Master SQLite DB, and per-project physical DBs in tmp_path."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_emb_kind = env.emb_kind
    orig_min_score = env.min_score
    orig_root_branch = env.root_branch
    orig_multi_db = getattr(env, "multi_db_enabled", True)
    orig_require_rest_auth = getattr(env, "require_rest_auth", False)
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    env_keys = (
        "PB_DB_PATH",
        "OM_DB_PATH",
        "PB_GRAPH_DB_PATH",
        "OM_GRAPH_DB_PATH",
        "OM_EMBED_PROVIDER",
        "PB_MULTI_DB_ENABLED",
        "OM_MULTI_DB_ENABLED",
        "PB_ROOT_BRANCH",
        "OM_ROOT_BRANCH",
        "PB_REQUIRE_REST_AUTH",
        "PB_READ_ONLY",
        "OM_READ_ONLY",
        "PB_MODE",
        "PB_DEFAULT_USER_ID",
        "OM_DEFAULT_USER_ID",
    )
    orig_os_env = {k: os.environ.get(k) for k in env_keys}

    monkeypatch.chdir(tmp_path)
    master_db_path = tmp_path / "master_wave3b.db"

    monkeypatch.setenv("PB_DB_PATH", str(master_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(master_db_path))
    monkeypatch.delenv("PB_GRAPH_DB_PATH", raising=False)
    monkeypatch.delenv("OM_GRAPH_DB_PATH", raising=False)
    monkeypatch.setenv("OM_EMBED_PROVIDER", "synthetic")
    monkeypatch.setenv("PB_MULTI_DB_ENABLED", "true")
    monkeypatch.setenv("OM_MULTI_DB_ENABLED", "true")
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.delenv("PB_ROOT_BRANCH", raising=False)
    monkeypatch.delenv("OM_ROOT_BRANCH", raising=False)
    monkeypatch.setenv("PB_READ_ONLY", "false")
    monkeypatch.setenv("OM_READ_ONLY", "false")
    monkeypatch.delenv("PB_MODE", raising=False)
    monkeypatch.delenv("PB_DEFAULT_USER_ID", raising=False)
    monkeypatch.delenv("OM_DEFAULT_USER_ID", raising=False)

    monkeypatch.setattr(env, "database_url", f"sqlite:///{master_db_path}")
    monkeypatch.setattr(env, "db_path", str(master_db_path))
    monkeypatch.setattr(env, "emb_kind", "synthetic")
    monkeypatch.setattr(env, "min_score", 0.0)
    monkeypatch.setattr(env, "root_branch", "main")
    monkeypatch.setattr(env, "multi_db_enabled", True)
    monkeypatch.setattr(env, "require_rest_auth", False)

    tenant_manager.reset()
    db.conn = None
    db.is_postgres = False
    auth_routes._ensured_conn = None
    auth_routes._ensured_db_url = None
    db.connect()
    tenant_manager.bind_master_db(db)
    tenant_manager.ensure_master_catalog()
    invalidate_query_cache()

    try:
        yield {
            "tmp_path": tmp_path,
            "master_db_path": master_db_path,
        }
    finally:
        tenant_manager.reset()
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        env.emb_kind = orig_emb_kind
        env.min_score = orig_min_score
        env.root_branch = orig_root_branch
        env.multi_db_enabled = orig_multi_db
        env.require_rest_auth = orig_require_rest_auth
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None

        for key, prev_val in orig_os_env.items():
            if prev_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev_val

        invalidate_query_cache()


async def _seed_multi_source_erp(tmp_path: Path) -> Dict[str, Any]:
    """Seed acme/erp with web-frontend, backend-api, billing-worker, and __shared__ doc."""
    ws_root = tmp_path / "erp_ws"
    fe_dir = ws_root / "web-frontend"
    be_dir = ws_root / "backend-api"
    shared_dir = ws_root / "__shared__"
    for d in (fe_dir, be_dir, shared_dir):
        d.mkdir(parents=True, exist_ok=True)

    (fe_dir / "orders.ts").write_text(
        "export interface OrderDTO {\n"
        "  id: number;\n"
        "  total: number;\n"
        "}\n\n"
        "export async function submitOrder(payload: OrderDTO) {\n"
        '  return fetch("/api/v1/orders");\n'
        "}\n",
        encoding="utf-8",
    )
    (be_dir / "orders.py").write_text(
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        "app = FastAPI()\n\n"
        "class OrderDTO(BaseModel):\n"
        "    id: int\n"
        "    total: float\n\n"
        '@app.post("/api/v1/orders")\n'
        "async def create_order(order: OrderDTO):\n"
        '    return {"id": order.id, "status": "created"}\n',
        encoding="utf-8",
    )
    shared_md = shared_dir / "ERP_Orders_Spec.md"
    shared_md.write_text(
        "# ERP Orders Architecture Specification\n\n"
        "## OrderDTO Contract\n"
        "The `web-frontend` submits `OrderDTO` payloads to `POST /api/v1/orders` on `backend-api`.\n",
        encoding="utf-8",
    )

    project_id = "acme/erp"
    await projectbrain_register_source(
        project_id=project_id,
        source_id="web-frontend",
        display_name="Web Frontend",
        source_type="web",
        tech_stack=["typescript", "react"],
        workspace_subpath=str(fe_dir),
        current_version="1.2.0",
    )
    await projectbrain_register_source(
        project_id=project_id,
        source_id="backend-api",
        display_name="Backend API",
        source_type="service",
        tech_stack=["python", "fastapi"],
        workspace_subpath=str(be_dir),
        current_version="2.0.0",
    )

    await projectbrain_link_sources(
        project_id=project_id,
        src_source_id="web-frontend",
        dst_source_id="backend-api",
        relation_type="calls_api",
        contract_key="POST:/api/v1/orders",
        contract_metadata={"endpoint": "/api/v1/orders", "dto": "OrderDTO"},
        pin=True,
    )

    await projectbrain_push_processed_document(
        filename="ERP_Orders_Spec.md",
        markdown_content=shared_md.read_text(encoding="utf-8"),
        project_id=project_id,
        branch="main",
        doc_key="erp_orders_spec",
        doc_version="1.0.0",
        scope="project_shared",
        doc_role="architecture",
    )
    await projectbrain_bind_shared_doc(
        project_id=project_id,
        doc_key="erp_orders_spec",
        target_source_id="web-frontend",
        pinned_version="1.0.0",
        binding_role="spec",
    )

    await projectbrain_sync_graphify(
        workspace_path=str(fe_dir),
        project_id=project_id,
        branch="main",
        source_id="web-frontend",
        source_version="1.2.0",
        upgrade_mode="delta",
        auto_link_sources=False,
    )
    await projectbrain_sync_graphify(
        workspace_path=str(be_dir),
        project_id=project_id,
        branch="main",
        source_id="backend-api",
        source_version="2.0.0",
        upgrade_mode="delta",
        auto_link_sources=False,
    )

    m = Memory()
    await m.add(
        "Frontend submitOrder function calls POST /api/v1/orders with OrderDTO from web-frontend",
        user_id="acme/erp@web-frontend:main",
        tags=["frontend", "orders"],
        meta={"source_id": "web-frontend", "scope": "source"},
    )
    await m.add(
        "Backend create_order handler implements POST /api/v1/orders and validates OrderDTO on backend-api",
        user_id="acme/erp@backend-api:main",
        tags=["backend", "orders"],
        meta={"source_id": "backend-api", "scope": "source"},
    )

    return {
        "tmp_path": tmp_path,
        "project_id": project_id,
        "fe_dir": fe_dir,
        "be_dir": be_dir,
        "shared_dir": shared_dir,
        "shared_md": shared_md,
    }


# ===========================================================================
# 1. Tool Registration, Annotations & Management Tools
# ===========================================================================


@pytest.mark.asyncio
async def test_cross_source_mcp_tools_lifecycle_and_annotations(
    isolated_mcp_cross_source_env: Dict[str, Any],
):
    tools_by_name = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    expected_new = {
        "projectbrain_register_source": False,
        "projectbrain_list_sources": True,
        "projectbrain_link_sources": False,
        "projectbrain_auto_link_sources": False,
        "projectbrain_cross_source_impact": True,
        "projectbrain_bind_shared_doc": False,
    }
    for name, expected_ro in expected_new.items():
        assert name in tools_by_name, f"Missing FastMCP tool: {name}"
        ann = getattr(tools_by_name[name], "annotations", None)
        assert ann is not None, f"Missing ToolAnnotations on {name}"
        assert getattr(ann, "readOnlyHint", None) is expected_ro

    seeded = await _seed_multi_source_erp(isolated_mcp_cross_source_env["tmp_path"])
    project_id = seeded["project_id"]

    # List sources
    list_raw = await projectbrain_list_sources(project_id=project_id)
    list_data = json.loads(list_raw)
    assert list_data["status"] == "ok"
    assert list_data["project_id"] == project_id
    source_ids = {s["source_id"] for s in list_data["sources"]}
    assert {"web-frontend", "backend-api"} <= source_ids

    # Auto-link sources
    auto_raw = await projectbrain_auto_link_sources(
        project_id=project_id,
        branch="main",
        workspace_map={
            "web-frontend": str(seeded["fe_dir"]),
            "backend-api": str(seeded["be_dir"]),
            "__shared__": str(seeded["shared_dir"]),
        },
    )
    auto_data = json.loads(auto_raw)
    assert auto_data["status"] == "ok"
    assert auto_data["relations_upserted"] >= 1

    # Cross-source impact
    impact_raw = await projectbrain_cross_source_impact(
        project_id=project_id,
        target="/api/v1/orders",
        source_id="web-frontend",
        branch="main",
        relation_depth=2,
    )
    impact_data = json.loads(impact_raw)
    assert impact_data["status"] == "ok"
    assert "backend-api" in impact_data["affected_sources"]
    assert len(impact_data["direct_relations"]) >= 1
    assert any(
        b.get("doc_key") == "erp_orders_spec" for b in impact_data["shared_doc_bindings"]
    )

    # Database topology preserves sources, shared_doc_count, relation_count
    topo_raw = await projectbrain_database_topology(project_id=project_id)
    topo_data = json.loads(topo_raw)
    proj_entry = next(p for p in topo_data["projects"] if p["project_id"] == project_id)
    assert len(proj_entry["sources"]) >= 2
    assert proj_entry["shared_doc_count"] >= 1
    assert proj_entry["relation_count"] >= 1

    # Upload document with source_id & scope="project_shared"
    upload_doc = seeded["tmp_path"] / "Shared_Runbook.md"
    upload_doc.write_text("# Shared Runbook\nAlways check /api/v1/orders.\n", encoding="utf-8")
    up_raw = await projectbrain_upload_document(
        file_path=str(upload_doc),
        project_id=project_id,
        branch="main",
        scope="project_shared",
        doc_role="runbook",
    )
    up_data = json.loads(up_raw)
    assert up_data["status"] == "success"
    assert up_data["scope"] == "project_shared"
    assert up_data["source_id"] == "__shared__"

    # Query codegraph & graphify & trace_code_and_docs with source_id
    cg_raw = await projectbrain_query_codegraph(
        project_id=project_id,
        query="submitOrder",
        source_id="web-frontend",
        follow_connections=True,
        include_shared_docs=True,
        relation_depth=1,
    )
    cg_data = json.loads(cg_raw)
    assert cg_data["source_id"] == "web-frontend"
    assert len(cg_data.get("cross_source_relations", [])) >= 1
    assert len(cg_data.get("shared_doc_bindings", [])) >= 1

    gq_raw = await projectbrain_query_graphify(
        query="submitOrder",
        project_id=project_id,
        branch="main",
        source_id="web-frontend",
        include_shared_docs=True,
        relation_depth=1,
    )
    gq_data = json.loads(gq_raw)
    assert len(gq_data.get("nodes", [])) >= 1
    assert len(gq_data.get("cross_source_relations", [])) >= 1

    tr_raw = await projectbrain_trace_code_and_docs(
        target="OrderDTO",
        project_id=project_id,
        branch="main",
        workspace_path=str(seeded["fe_dir"]),
        source_id="web-frontend",
        include_shared_docs=True,
        relation_depth=1,
    )
    tr_data = json.loads(tr_raw)
    assert tr_data["source_id"] == "web-frontend"
    assert len(tr_data.get("cross_source_contracts", [])) >= 1
    assert len(tr_data.get("shared_docs", [])) >= 1


# ===========================================================================
# 2. Cross-Source Retrieval & Isolation (MCP + REST)
# ===========================================================================


@pytest.mark.asyncio
async def test_cross_source_retrieval_mcp_and_rest(
    isolated_mcp_cross_source_env: Dict[str, Any],
):
    seeded = await _seed_multi_source_erp(isolated_mcp_cross_source_env["tmp_path"])
    project_id = seeded["project_id"]

    # --- A. projectbrain_query ---
    q_expanded_raw = await projectbrain_query(
        query="OrderDTO create_order /api/v1/orders",
        user_id=f"{project_id}@web-frontend:main",
        source_id="web-frontend",
        include_shared_docs=True,
        relation_depth=1,
        k=15,
    )
    q_expanded_json = json.loads(q_expanded_raw.split("\n\n", 1)[1])
    ctx_hits = q_expanded_json["contextual"]
    hit_sources = {h.get("source_id") for h in ctx_hits}
    assert "web-frontend" in hit_sources
    assert "__shared__" in hit_sources
    assert "backend-api" in hit_sources
    be_hits = [h for h in ctx_hits if h.get("source_id") == "backend-api"]
    assert any(h.get("via_relation") for h in be_hits)

    q_strict_raw = await projectbrain_query(
        query="OrderDTO create_order /api/v1/orders",
        user_id=f"{project_id}@web-frontend:main",
        source_id="web-frontend",
        include_shared_docs=False,
        relation_depth=0,
        k=15,
    )
    q_strict_json = json.loads(q_strict_raw.split("\n\n", 1)[1])
    strict_hits = q_strict_json["contextual"]
    assert len(strict_hits) >= 1
    assert all(h.get("source_id") == "web-frontend" for h in strict_hits)

    # --- B. projectbrain_hybrid_query ---
    hyb_expanded_raw = await projectbrain_hybrid_query(
        query="OrderDTO",
        project_id=project_id,
        branch="main",
        source_id="web-frontend",
        include_shared_docs=True,
        relation_depth=1,
    )
    hyb_expanded = json.loads(hyb_expanded_raw)
    node_sources = {
        n.get("source_id") or (n.get("metadata") or {}).get("source_id")
        for n in hyb_expanded["matched_nodes"]
    }
    assert "web-frontend" in node_sources
    assert "__shared__" in node_sources
    assert "backend-api" in node_sources
    assert any(
        n.get("via_relation")
        for n in hyb_expanded["matched_nodes"]
        if (n.get("source_id") or (n.get("metadata") or {}).get("source_id")) == "backend-api"
    )

    hyb_strict_raw = await projectbrain_hybrid_query(
        query="OrderDTO",
        project_id=project_id,
        branch="main",
        source_id="web-frontend",
        include_shared_docs=False,
        relation_depth=0,
    )
    hyb_strict = json.loads(hyb_strict_raw)
    strict_node_sources = {
        n.get("source_id") or (n.get("metadata") or {}).get("source_id")
        for n in hyb_strict["matched_nodes"]
    }
    assert strict_node_sources == {"web-frontend"}
    assert all(
        (m.get("source_id") or (m.get("metadata") or {}).get("source_id")) == "web-frontend"
        for m in hyb_strict["cognitive_memories"]
    )

    # --- C. REST endpoints: POST /memory/search, POST /pm/search, POST /graphify/hybrid-query ---
    app = create_app()
    client = TestClient(app)

    mem_exp = client.post(
        "/memory/search",
        json={
            "query": "OrderDTO create_order /api/v1/orders",
            "user_id": f"{project_id}@web-frontend:main",
            "source_id": "web-frontend",
            "include_shared_docs": True,
            "relation_depth": 1,
            "limit": 15,
        },
    )
    assert mem_exp.status_code == 200, mem_exp.text
    mem_exp_sources = {r.get("source_id") for r in mem_exp.json()["results"]}
    assert {"web-frontend", "__shared__", "backend-api"} <= mem_exp_sources

    mem_strict = client.post(
        "/memory/search",
        json={
            "query": "OrderDTO create_order /api/v1/orders",
            "user_id": f"{project_id}@web-frontend:main",
            "source_id": "web-frontend",
            "include_shared_docs": False,
            "relation_depth": 0,
            "limit": 15,
        },
    )
    assert mem_strict.status_code == 200, mem_strict.text
    assert all(r.get("source_id") == "web-frontend" for r in mem_strict.json()["results"])

    pm_exp = client.post(
        "/pm/search",
        json={
            "query": "OrderDTO",
            "user_id": f"{project_id}@web-frontend:main",
            "source_id": "web-frontend",
            "include_shared_docs": True,
            "relation_depth": 1,
        },
    )
    assert pm_exp.status_code == 200, pm_exp.text
    pm_exp_sources = {m.get("source_id") for m in pm_exp.json().get("source_memories", [])}
    assert {"web-frontend", "__shared__", "backend-api"} <= pm_exp_sources

    hyb_rest_exp = client.post(
        "/graphify/hybrid-query",
        json={
            "project_id": project_id,
            "query": "OrderDTO",
            "branch": "main",
            "source_id": "web-frontend",
            "include_shared_docs": True,
            "relation_depth": 1,
        },
    )
    assert hyb_rest_exp.status_code == 200, hyb_rest_exp.text
    hyb_rest_sources = {
        n.get("source_id") or (n.get("metadata") or {}).get("source_id")
        for n in hyb_rest_exp.json().get("matched_nodes", hyb_rest_exp.json().get("nodes", []))
    }
    assert {"web-frontend", "__shared__", "backend-api"} <= hyb_rest_sources

    hyb_rest_strict = client.post(
        "/graphify/hybrid-query",
        json={
            "project_id": project_id,
            "query": "OrderDTO",
            "branch": "main",
            "source_id": "web-frontend",
            "include_shared_docs": False,
            "relation_depth": 0,
        },
    )
    assert hyb_rest_strict.status_code == 200, hyb_rest_strict.text
    strict_rest_nodes = hyb_rest_strict.json().get(
        "matched_nodes", hyb_rest_strict.json().get("nodes", [])
    )
    assert {
        n.get("source_id") or (n.get("metadata") or {}).get("source_id")
        for n in strict_rest_nodes
    } == {"web-frontend"}


# ===========================================================================
# 3. Reader RBAC Guard for New Write vs Read-Only Cross-Source MCP Tools
# ===========================================================================


@pytest.mark.asyncio
async def test_reader_rbac_blocks_new_write_mcp_tools_allows_reads(
    isolated_mcp_cross_source_env: Dict[str, Any],
):
    # Unit-level _is_write_tool checks
    for write_tool in (
        "projectbrain_register_source",
        "projectbrain_link_sources",
        "projectbrain_auto_link_sources",
        "projectbrain_bind_shared_doc",
    ):
        assert _is_write_tool(write_tool) is True, f"{write_tool} must be flagged as write tool"

    for read_tool in (
        "projectbrain_list_sources",
        "projectbrain_cross_source_impact",
        "projectbrain_database_topology",
    ):
        assert _is_write_tool(read_tool) is False, f"{read_tool} must remain read-only"

    # SSEFilter checks for reader role
    sse_filter_reader = SSEFilter("reader", "*")
    sample_tools_chunk = (
        "event: message\ndata: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {"name": "projectbrain_list_sources", "description": "List sources"},
                        {"name": "projectbrain_cross_source_impact", "description": "Impact"},
                        {"name": "projectbrain_register_source", "description": "Register"},
                        {"name": "projectbrain_link_sources", "description": "Link"},
                        {"name": "projectbrain_auto_link_sources", "description": "Auto link"},
                        {"name": "projectbrain_bind_shared_doc", "description": "Bind shared"},
                    ]
                },
                "id": 99,
            }
        )
        + "\n\n"
    ).encode("utf-8")
    filtered = sse_filter_reader.process_chunk(sample_tools_chunk).decode("utf-8")
    assert "projectbrain_list_sources" in filtered
    assert "projectbrain_cross_source_impact" in filtered
    for blocked in (
        "projectbrain_register_source",
        "projectbrain_link_sources",
        "projectbrain_auto_link_sources",
        "projectbrain_bind_shared_doc",
    ):
        assert blocked not in filtered

    # HTTP MCP middleware enforcement
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res_acc = await ac.post(
            "/auth/accounts",
            json={
                "username": "wave3b_reader",
                "token": "pb_tok_wave3b_reader",
                "role": "reader",
                "allowed_tools": "*",
            },
        )
        assert res_acc.status_code == 200, res_acc.text
        headers = {"Authorization": "Bearer pb_tok_wave3b_reader"}

        for idx, blocked_tool in enumerate(
            (
                "projectbrain_register_source",
                "projectbrain_link_sources",
                "projectbrain_auto_link_sources",
                "projectbrain_bind_shared_doc",
            ),
            start=1,
        ):
            res_block = await ac.post(
                "/mcp/messages/?session_id=dummy",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": blocked_tool, "arguments": {"project_id": "acme/erp"}},
                    "id": idx,
                },
                headers=headers,
            )
            assert res_block.status_code == 403
            assert "Read-only accounts cannot execute modifying tool" in res_block.json()["error"]["message"]

        # Read-only tools must pass MCP auth middleware (not blocked with 403)
        for idx, allowed_tool in enumerate(
            ("projectbrain_list_sources", "projectbrain_cross_source_impact"), start=10
        ):
            res_allow = await ac.post(
                "/mcp/messages/?session_id=dummy",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": allowed_tool, "arguments": {"project_id": "acme/erp"}},
                    "id": idx,
                },
                headers=headers,
            )
            assert res_allow.status_code != 403


# ===========================================================================
# 4. Unit Checks: Upgraded MCP Tool Signatures, Remote Forwarding & Provenance
# ===========================================================================


@pytest.mark.asyncio
async def test_mcp_upgraded_signatures_forwarding_and_provenance(
    isolated_mcp_cross_source_env: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    import inspect
    from unittest.mock import AsyncMock, patch
    from projectbrain.ai.mcp import (
        projectbrain_build_complete_kb,
        projectbrain_sync_codegraph,
    )

    # Verify all upgraded tools expose the required backward-compatible parameters
    for fn, expected_params in (
        (
            projectbrain_query,
            ("source_id", "include_shared_docs", "relation_depth", "relation_types"),
        ),
        (
            projectbrain_hybrid_query,
            ("source_id", "include_shared_docs", "relation_depth", "relation_types"),
        ),
        (
            projectbrain_query_codegraph,
            ("source_id", "include_shared_docs", "relation_depth"),
        ),
        (
            projectbrain_trace_code_and_docs,
            ("source_id", "include_shared_docs", "relation_depth"),
        ),
        (
            projectbrain_query_graphify,
            ("source_id", "include_shared_docs", "relation_depth"),
        ),
        (
            projectbrain_sync_graphify,
            ("source_id", "source_version", "upgrade_mode", "auto_link_sources"),
        ),
        (
            projectbrain_sync_codegraph,
            ("source_id", "source_version", "upgrade_mode", "auto_link_sources"),
        ),
        (
            projectbrain_build_complete_kb,
            ("source_id", "source_version", "upgrade_mode", "auto_link_sources"),
        ),
        (
            projectbrain_upload_document,
            ("source_id", "scope", "doc_role"),
        ),
        (
            projectbrain_push_processed_document,
            ("source_id", "scope", "doc_role"),
        ),
    ):
        sig_params = inspect.signature(fn).parameters
        for param_name in expected_params:
            assert param_name in sig_params, f"{fn.__name__} missing parameter '{param_name}'"

    # Local mem.search forwarding + provenance formatting
    mock_results = [
        {
            "id": "m_fe",
            "user_id": "acme/erp@web-frontend:main",
            "score": 0.91,
            "primary_sector": "semantic",
            "salience": 0.8,
            "source_id": "web-frontend",
            "scope": "source",
            "via_relation": None,
            "content": "submitOrder calls /api/v1/orders",
        },
        {
            "id": "m_shared",
            "user_id": "acme/erp@__shared__:main",
            "score": 0.84,
            "primary_sector": "semantic",
            "salience": 0.7,
            "source_id": "__shared__",
            "scope": "project_shared",
            "via_relation": "shared_doc",
            "content": "ERP_Orders_Spec defines OrderDTO",
        },
        {
            "id": "m_be",
            "user_id": "acme/erp@backend-api:main",
            "score": 0.79,
            "primary_sector": "semantic",
            "salience": 0.75,
            "source_id": "backend-api",
            "scope": "source",
            "via_relation": "calls_api",
            "content": "create_order endpoint POST /api/v1/orders",
        },
    ]
    with patch("projectbrain.ai.mcp.mem.search", new=AsyncMock(return_value=mock_results)) as mock_search:
        raw_local = await projectbrain_query(
            query="OrderDTO",
            user_id="acme/erp@web-frontend:main",
            source_id="web-frontend",
            include_shared_docs=True,
            relation_depth=1,
            relation_types=["calls_api"],
        )
        assert mock_search.await_count == 1
        _, call_kwargs = mock_search.call_args
        assert call_kwargs["source_id"] == "web-frontend"
        assert call_kwargs["include_shared_docs"] is True
        assert call_kwargs["relation_depth"] == 1
        assert call_kwargs["relation_types"] == ["calls_api"]

    parsed_local = json.loads(raw_local.split("\n\n", 1)[1])
    by_id = {entry["id"]: entry for entry in parsed_local["contextual"]}
    assert by_id["m_fe"]["source_id"] == "web-frontend"
    assert by_id["m_fe"]["scope"] == "source"
    assert by_id["m_shared"]["source_id"] == "__shared__"
    assert by_id["m_shared"]["scope"] == "project_shared"
    assert by_id["m_be"]["source_id"] == "backend-api"
    assert by_id["m_be"]["via_relation"] == "calls_api"

    # Remote mode forwarding to POST /memory/search and POST /graphify/hybrid-query
    monkeypatch.setenv("PB_MODE", "remote")
    monkeypatch.setenv("PB_URL", "http://remote-pb.test:8080")
    recorded_posts: list = []

    class _FakeResp:
        def __init__(self, payload: Dict[str, Any]):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Dict[str, Any]:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: Dict[str, Any] = None, headers: Dict[str, Any] = None):
            recorded_posts.append({"url": url, "json": json, "headers": headers})
            if url.endswith("/memory/search"):
                return _FakeResp({"results": mock_results})
            if url.endswith("/graphify/hybrid-query"):
                return _FakeResp(
                    {
                        "query": (json or {}).get("query"),
                        "project_id": (json or {}).get("project_id"),
                        "source_id": (json or {}).get("source_id"),
                        "matched_nodes": [],
                        "cross_source_relations": [],
                    }
                )
            raise AssertionError(f"Unexpected remote URL: {url}")

    with patch("httpx.AsyncClient", _FakeAsyncClient):
        raw_remote_q = await projectbrain_query(
            query="OrderDTO",
            user_id="acme/erp@web-frontend:main",
            source_id="web-frontend",
            include_shared_docs=True,
            relation_depth=1,
            relation_types=["calls_api", "shares_schema"],
        )
        raw_remote_hyb = await projectbrain_hybrid_query(
            query="OrderDTO",
            project_id="acme/erp",
            branch="main",
            source_id="web-frontend",
            include_shared_docs=False,
            relation_depth=0,
            relation_types=["calls_api"],
        )

    assert len(recorded_posts) == 2
    mem_post, hyb_post = recorded_posts
    assert mem_post["url"] == "http://remote-pb.test:8080/memory/search"
    assert mem_post["json"]["source_id"] == "web-frontend"
    assert mem_post["json"]["include_shared_docs"] is True
    assert mem_post["json"]["relation_depth"] == 1
    assert mem_post["json"]["relation_types"] == ["calls_api", "shares_schema"]

    assert hyb_post["url"] == "http://remote-pb.test:8080/graphify/hybrid-query"
    assert hyb_post["json"]["project_id"] == "acme/erp"
    assert hyb_post["json"]["source_id"] == "web-frontend"
    assert hyb_post["json"]["include_shared_docs"] is False
    assert hyb_post["json"]["relation_depth"] == 0
    assert hyb_post["json"]["relation_types"] == ["calls_api"]

    parsed_remote_q = json.loads(raw_remote_q.split("\n\n", 1)[1])
    assert any(h.get("via_relation") == "calls_api" for h in parsed_remote_q["contextual"])
    parsed_remote_hyb = json.loads(raw_remote_hyb)
    assert parsed_remote_hyb["source_id"] == "web-frontend"

