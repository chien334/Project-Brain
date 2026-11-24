"""Verification Suite for Zero-Touch External Agent Hub Bootstrap & Discovery."""

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
    projectbrain_bootstrap_project_hub,
    projectbrain_discover_workspace_sources,
    projectbrain_get_agent_setup_playbook,
    projectbrain_hybrid_query,
    projectbrain_query,
)
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import tenant_manager
from projectbrain.graphify_engine.hub_bootstrap import (
    build_agent_setup_playbook,
    discover_workspace_sources,
)
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.server.api import SSEFilter, _is_write_tool, create_app
from projectbrain.server.routes import auth as auth_routes


@pytest.fixture
def isolated_hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate workspace, Master SQLite DB, and per-project DBs in tmp_path."""
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
        "PB_WORKSPACE_ROOT",
    )
    orig_os_env = {k: os.environ.get(k) for k in env_keys}

    monkeypatch.chdir(tmp_path)
    master_db_path = tmp_path / "master_hub_bootstrap.db"

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
    monkeypatch.delenv("PB_WORKSPACE_ROOT", raising=False)

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


def _create_multi_service_fixture(base_dir: Path) -> Path:
    """Create a realistic multi-service repository fixture for zero-touch discovery."""
    ws = base_dir / "erp-monorepo"
    be_dir = ws / "backend-api"
    fe_dir = ws / "web-frontend"
    docs_dir = ws / "docs"
    reports_dir = docs_dir / "reports"
    ignored_nm = ws / "node_modules" / "fake-pkg"

    for d in (be_dir, fe_dir, docs_dir, reports_dir, ignored_nm):
        d.mkdir(parents=True, exist_ok=True)

    (be_dir / "pyproject.toml").write_text(
        '[project]\nname = "backend-api"\nversion = "1.0.0"\ndependencies = ["fastapi", "pydantic"]\n',
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

    (fe_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "web-frontend",
                "version": "1.0.0",
                "dependencies": {"react": "^18.2.0"},
                "devDependencies": {"typescript": "^5.4.0", "vite": "^5.0.0"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
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

    (docs_dir / "ERP_Spec.md").write_text(
        "# ERP Orders Architecture Specification\n\n"
        "## OrderDTO\n"
        "The `web-frontend` submits `OrderDTO` payloads to `POST /api/v1/orders` on `backend-api`.\n",
        encoding="utf-8",
    )
    (reports_dir / "ignored_report.md").write_text(
        "# Weekly Report\nShould be excluded from shared docs.\n",
        encoding="utf-8",
    )

    (ws / "openapi.yaml").write_text(
        "openapi: '3.0.3'\n"
        "info:\n"
        "  title: ERP Orders API\n"
        "  version: '1.0.0'\n"
        "paths:\n"
        "  /api/v1/orders:\n"
        "    post:\n"
        "      operationId: createOrder\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Created\n"
        "components:\n"
        "  schemas:\n"
        "    OrderDTO:\n"
        "      type: object\n"
        "      properties:\n"
        "        id:\n"
        "          type: integer\n"
        "        total:\n"
        "          type: number\n",
        encoding="utf-8",
    )

    # Should be skipped by discovery
    (ignored_nm / "package.json").write_text('{"name": "fake-pkg", "dependencies": {"react": "1.0.0"}}', encoding="utf-8")
    return ws


# ===========================================================================
# 1. Workspace Auto-Discovery (Multi-Service, Nested Monorepo, Single-Module)
# ===========================================================================


def test_discover_workspace_sources_multi_service_and_edge_cases(
    isolated_hub_env: Dict[str, Any],
):
    tmp_path = isolated_hub_env["tmp_path"]
    ws = _create_multi_service_fixture(tmp_path)

    res = discover_workspace_sources(str(ws))
    assert res["status"] == "ok"
    assert res["workspace_root"] == str(ws.resolve())
    assert res["suggested_project_id"] == "erp-monorepo"

    sources_by_id = {s["source_id"]: s for s in res["sources"]}
    assert set(sources_by_id.keys()) == {"backend-api", "web-frontend"}
    assert "fake-pkg" not in sources_by_id

    be = sources_by_id["backend-api"]
    assert be["source_type"] == "backend"
    assert "python" in be["tech_stack"]
    assert "fastapi" in be["tech_stack"]
    assert be["workspace_subpath"] == "backend-api"

    fe = sources_by_id["web-frontend"]
    assert fe["source_type"] == "frontend"
    assert "typescript" in fe["tech_stack"]
    assert "react" in fe["tech_stack"]
    assert fe["workspace_subpath"] == "web-frontend"

    docs_by_key = {d["doc_key"]: d for d in res["shared_docs"]}
    assert "openapi" in docs_by_key
    assert docs_by_key["openapi"]["doc_role"] == "api_contract"
    assert "erp_spec" in docs_by_key
    assert docs_by_key["erp_spec"]["doc_role"] == "prd"
    assert "ignored_report" not in docs_by_key

    # Nested container + infra + worker + polyglot detection
    mono = tmp_path / "polyglot-mono"
    (mono / "services" / "billing-worker").mkdir(parents=True)
    (mono / "services" / "billing-worker" / "requirements.txt").write_text("celery==5.4.0\n", encoding="utf-8")
    (mono / "apps" / "gateway-go").mkdir(parents=True)
    (mono / "apps" / "gateway-go" / "go.mod").write_text("module example.com/gateway\n", encoding="utf-8")
    (mono / "infra").mkdir(parents=True)
    (mono / "infra" / "docker-compose.yml").write_text("services:\n  api:\n    image: app:latest\n", encoding="utf-8")
    (mono / "node-express").mkdir(parents=True)
    (mono / "node-express" / "package.json").write_text(
        json.dumps(
            {
                "name": "node-express",
                "description": "Next-generation express order service",
                "dependencies": {"express": "^4.19.0"},
            }
        ),
        encoding="utf-8",
    )
    (mono / "docs").mkdir(parents=True)
    (mono / "docs" / "ADR-001.md").write_text("# ADR 001\nUse Postgres.\n", encoding="utf-8")
    (mono / "docs" / "address_guide.md").write_text("# Address Guide\nPostal rules.\n", encoding="utf-8")

    mono_res = discover_workspace_sources(str(mono))
    mono_map = {s["source_id"]: s for s in mono_res["sources"]}
    assert mono_map["billing-worker"]["source_type"] == "worker"
    assert mono_map["billing-worker"]["tech_stack"] == ["python"]
    assert mono_map["gateway-go"]["source_type"] == "backend"
    assert mono_map["gateway-go"]["tech_stack"] == ["go"]
    assert mono_map["infra"]["source_type"] == "infra"
    assert mono_map["node-express"]["source_type"] == "backend"
    assert "express" in mono_map["node-express"]["tech_stack"]
    mono_docs = {d["doc_key"]: d for d in mono_res["shared_docs"]}
    assert mono_docs["adr-001"]["doc_role"] == "adr"
    assert mono_docs["address_guide"]["doc_role"] == "reference"

    # Single-module fallback when root itself has markers and no child sub-sources exist
    single = tmp_path / "single-rust-lib"
    (single / "src").mkdir(parents=True)
    (single / "Cargo.toml").write_text('[package]\nname = "single-rust-lib"\n', encoding="utf-8")
    (single / "src" / "lib.rs").write_text("pub fn ping() -> bool { true }\n", encoding="utf-8")

    single_res = discover_workspace_sources(str(single))
    assert len(single_res["sources"]) == 1
    assert single_res["sources"][0]["workspace_subpath"] == "."
    assert single_res["sources"][0]["source_type"] == "backend"
    assert single_res["sources"][0]["tech_stack"] == ["rust"]


# ===========================================================================
# 2. One-Shot Zero-Touch MCP Bootstrap + Immediate Cross-Source Queries
# ===========================================================================


@pytest.mark.asyncio
async def test_mcp_zero_touch_bootstrap_and_immediate_cross_source_retrieval(
    isolated_hub_env: Dict[str, Any],
):
    tmp_path = isolated_hub_env["tmp_path"]
    ws = _create_multi_service_fixture(tmp_path)

    # Check ToolAnnotations on new MCP tools
    tools_by_name = {t.name: t for t in mcp_server._tool_manager.list_tools()}
    expected_tools = {
        "projectbrain_discover_workspace_sources": True,
        "projectbrain_bootstrap_project_hub": False,
        "projectbrain_get_agent_setup_playbook": True,
    }
    for name, expected_ro in expected_tools.items():
        assert name in tools_by_name, f"Missing FastMCP tool: {name}"
        ann = getattr(tools_by_name[name], "annotations", None)
        assert ann is not None, f"Missing ToolAnnotations on {name}"
        assert getattr(ann, "readOnlyHint", None) is expected_ro

    # 1. Discover via MCP tool
    disc_raw = await projectbrain_discover_workspace_sources(workspace_root=str(ws))
    disc_data = json.loads(disc_raw)
    assert disc_data["status"] == "ok"
    assert {s["source_id"] for s in disc_data["sources"]} == {"backend-api", "web-frontend"}

    # 2. One-shot zero-touch bootstrap via MCP tool
    project_id = "acme/erp-zero-touch"
    boot_raw = await projectbrain_bootstrap_project_hub(
        project_id=project_id,
        workspace_root=str(ws),
        auto_discover=True,
        sync_graphs=True,
        auto_link=True,
    )
    boot_data = json.loads(boot_raw)
    assert boot_data["status"] == "ok"
    assert boot_data["project_id"] == project_id
    assert {s["source_id"] for s in boot_data["sources_registered"]} == {"backend-api", "web-frontend"}
    assert {d["doc_key"] for d in boot_data["shared_docs_ingested"]} >= {"erp_spec", "openapi"}
    assert len(boot_data["sync_results"]) == 2
    assert boot_data["auto_link"]["status"] == "ok"
    assert boot_data["auto_link"]["relations_upserted"] >= 2
    assert "mcp_query_example" in boot_data["ready_queries"]
    assert "mcp_hybrid_example" in boot_data["ready_queries"]

    # 3. Verify projectbrain_query IMMEDIATELY pulls web-frontend + __shared__ + backend-api
    q_raw = await projectbrain_query(
        query="OrderDTO create_order /api/v1/orders",
        user_id=f"{project_id}@web-frontend:main",
        source_id="web-frontend",
        include_shared_docs=True,
        relation_depth=1,
        k=15,
    )
    q_json = json.loads(q_raw.split("\n\n", 1)[1])
    ctx_hits = q_json["contextual"]
    hit_sources = {h.get("source_id") for h in ctx_hits}
    assert {"web-frontend", "__shared__", "backend-api"} <= hit_sources, (
        f"Expected all 3 sources in projectbrain_query hits, got {hit_sources}"
    )
    assert any(
        h.get("via_relation")
        for h in ctx_hits
        if h.get("source_id") == "backend-api"
    )

    # 4. Verify projectbrain_hybrid_query IMMEDIATELY pulls web-frontend + __shared__ + backend-api
    hyb_raw = await projectbrain_hybrid_query(
        query="OrderDTO",
        project_id=project_id,
        branch="main",
        source_id="web-frontend",
        include_shared_docs=True,
        relation_depth=1,
    )
    hyb_json = json.loads(hyb_raw)
    node_sources = {
        n.get("source_id") or (n.get("metadata") or {}).get("source_id")
        for n in hyb_json["matched_nodes"]
    }
    assert {"web-frontend", "__shared__", "backend-api"} <= node_sources, (
        f"Expected all 3 sources in hybrid_query matched_nodes, got {node_sources}"
    )
    assert any(
        n.get("via_relation")
        for n in hyb_json["matched_nodes"]
        if (n.get("source_id") or (n.get("metadata") or {}).get("source_id")) == "backend-api"
    )

    # 5. Playbook MCP tool & MCP resource
    pb_raw = await projectbrain_get_agent_setup_playbook(
        project_id=project_id,
        workspace_root=str(ws),
    )
    pb_data = json.loads(pb_raw)
    assert pb_data["status"] == "ok"
    assert pb_data["project_id"] == project_id
    assert len(pb_data["steps"]) >= 3
    assert len(pb_data["mcp_sequence"]) >= 3
    assert "projectbrain_bootstrap_project_hub" in pb_data["rules_snippet"]
    for agent_name in ("Claude Code", "Cursor", "Antigravity", "Codex"):
        assert agent_name in pb_data["supported_agents"]
        assert agent_name in pb_data["agent_configs"]

    resources = await mcp_server.list_resources()
    assert any(str(r.uri) == "projectbrain://guides/agent-multi-source-setup" for r in resources)
    res_contents = await mcp_server.read_resource("projectbrain://guides/agent-multi-source-setup")
    res_payload = json.loads(next(iter(res_contents)).content)
    assert res_payload["status"] == "ok"
    assert "rules_snippet" in res_payload


# ===========================================================================
# 3. REST Endpoints (/master/hub/discover & /master/hub/bootstrap) + Security + RBAC
# ===========================================================================


@pytest.mark.asyncio
async def test_rest_hub_discover_bootstrap_and_reader_rbac(
    isolated_hub_env: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    tmp_path = isolated_hub_env["tmp_path"]
    ws = _create_multi_service_fixture(tmp_path)

    app = create_app()
    client = TestClient(app)

    # 1. Path traversal rejection on REST endpoints
    for bad_path in ("/etc", "../../etc/passwd", str(Path.home() / ".ssh")):
        r_bad_disc = client.post("/master/hub/discover", json={"workspace_root": bad_path})
        assert r_bad_disc.status_code == 400
        r_bad_boot = client.post(
            "/master/hub/bootstrap",
            json={"project_id": "acme/bad", "workspace_root": bad_path},
        )
        assert r_bad_boot.status_code == 400

    # 2. PB_READ_ONLY mode blocks bootstrap (403) but allows discover (200)
    monkeypatch.setenv("PB_READ_ONLY", "true")
    r_ro_disc = client.post("/master/hub/discover", json={"workspace_root": str(ws)})
    assert r_ro_disc.status_code == 200
    r_ro_boot = client.post(
        "/master/hub/bootstrap",
        json={"project_id": "acme/ro-test", "workspace_root": str(ws)},
    )
    assert r_ro_boot.status_code == 403
    monkeypatch.setenv("PB_READ_ONLY", "false")

    # 3. Reader RBAC enforcement (MCP + REST) under PB_REQUIRE_REST_AUTH=true
    assert _is_write_tool("projectbrain_bootstrap_project_hub") is True
    assert _is_write_tool("projectbrain_discover_workspace_sources") is False
    assert _is_write_tool("projectbrain_get_agent_setup_playbook") is False

    sse_filter_reader = SSEFilter("reader", "*")
    sample_tools_chunk = (
        "event: message\ndata: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {"name": "projectbrain_discover_workspace_sources", "description": "Discover"},
                        {"name": "projectbrain_get_agent_setup_playbook", "description": "Playbook"},
                        {"name": "projectbrain_bootstrap_project_hub", "description": "Bootstrap"},
                    ]
                },
                "id": 77,
            }
        )
        + "\n\n"
    ).encode("utf-8")
    filtered = sse_filter_reader.process_chunk(sample_tools_chunk).decode("utf-8")
    assert "projectbrain_discover_workspace_sources" in filtered
    assert "projectbrain_get_agent_setup_playbook" in filtered
    assert "projectbrain_bootstrap_project_hub" not in filtered

    auth_routes.init_auth_system()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res_reader_acc = await ac.post(
            "/auth/accounts",
            json={
                "username": "hub_reader",
                "token": "pb_tok_hub_reader",
                "role": "reader",
                "allowed_tools": "*",
            },
        )
        assert res_reader_acc.status_code == 200, res_reader_acc.text
        res_collab_acc = await ac.post(
            "/auth/accounts",
            json={
                "username": "hub_collab",
                "token": "pb_tok_hub_collab",
                "role": "collaborator",
                "allowed_tools": "*",
            },
        )
        assert res_collab_acc.status_code == 200, res_collab_acc.text

        monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
        monkeypatch.setattr(env, "require_rest_auth", True)

        reader_headers = {"Authorization": "Bearer pb_tok_hub_reader"}
        collab_headers = {"Authorization": "Bearer pb_tok_hub_collab"}

        # Unauthenticated REST -> 401
        assert (await ac.post("/master/hub/discover", json={"workspace_root": str(ws)})).status_code == 401
        assert (
            await ac.post("/master/hub/bootstrap", json={"project_id": "acme/rest-hub", "workspace_root": str(ws)})
        ).status_code == 401

        # Reader REST: /master/hub/discover -> 200, /master/hub/bootstrap -> 403
        r_read_disc = await ac.post(
            "/master/hub/discover",
            json={"workspace_root": str(ws)},
            headers=reader_headers,
        )
        assert r_read_disc.status_code == 200, r_read_disc.text
        assert {s["source_id"] for s in r_read_disc.json()["sources"]} == {"backend-api", "web-frontend"}

        r_read_boot = await ac.post(
            "/master/hub/bootstrap",
            json={"project_id": "acme/rest-hub", "workspace_root": str(ws)},
            headers=reader_headers,
        )
        assert r_read_boot.status_code == 403

        # Reader MCP: projectbrain_bootstrap_project_hub -> 403, discover/playbook -> allowed
        r_mcp_block = await ac.post(
            "/mcp/messages/?session_id=hub_sess",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "projectbrain_bootstrap_project_hub",
                    "arguments": {"project_id": "acme/rest-hub", "workspace_root": str(ws)},
                },
                "id": 1,
            },
            headers=reader_headers,
        )
        assert r_mcp_block.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in r_mcp_block.json()["error"]["message"]

        for idx, ro_tool in enumerate(
            ("projectbrain_discover_workspace_sources", "projectbrain_get_agent_setup_playbook"),
            start=2,
        ):
            r_mcp_allow = await ac.post(
                "/mcp/messages/?session_id=hub_sess",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": ro_tool, "arguments": {"workspace_root": str(ws)}},
                    "id": idx,
                },
                headers=reader_headers,
            )
            assert r_mcp_allow.status_code != 403

        # Collaborator REST: POST /master/hub/bootstrap -> 200
        r_collab_boot = await ac.post(
            "/master/hub/bootstrap",
            json={"project_id": "acme/rest-hub", "workspace_root": str(ws)},
            headers=collab_headers,
        )
        assert r_collab_boot.status_code == 200, r_collab_boot.text
        boot_json = r_collab_boot.json()
        assert boot_json["status"] == "ok"
        assert boot_json["project_id"] == "acme/rest-hub"
        assert {s["source_id"] for s in boot_json["sources_registered"]} == {"backend-api", "web-frontend"}
        assert boot_json["auto_link"]["relations_upserted"] >= 2


@pytest.mark.asyncio
async def test_mcp_remote_mode_forwarding_and_explicit_overrides(
    isolated_hub_env: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    from unittest.mock import AsyncMock, patch

    tmp_path = isolated_hub_env["tmp_path"]
    ws = _create_multi_service_fixture(tmp_path)

    # 1. Explicit inline shared_docs & explicit pinned relations via local MCP call
    raw_explicit = await projectbrain_bootstrap_project_hub(
        project_id="acme/explicit-hub",
        workspace_root=str(ws),
        sources=[
            {
                "source_id": "web-frontend",
                "workspace_subpath": "web-frontend",
                "source_type": "frontend",
                "tech_stack": ["typescript", "react"],
            }
        ],
        shared_docs=[
            {
                "doc_key": "inline_arch",
                "doc_role": "architecture",
                "content": "# Inline Architecture\n`web-frontend` talks to `/api/v1/orders`.\n",
            }
        ],
        relations=[
            {
                "src_source_id": "web-frontend",
                "dst_source_id": "__shared__",
                "relation_type": "documented_by",
                "dst_doc_key": "inline_arch",
                "contract_key": "spec:inline_arch",
                "pin": True,
            }
        ],
        auto_discover=False,
        sync_graphs=False,
        auto_link=False,
    )
    explicit_res = json.loads(raw_explicit)
    assert explicit_res["status"] == "ok"
    assert [s["source_id"] for s in explicit_res["sources_registered"]] == ["web-frontend"]
    assert [d["doc_key"] for d in explicit_res["shared_docs_ingested"]] == ["inline_arch"]
    assert len(explicit_res["relations_applied"]) == 1
    assert explicit_res["relations_applied"][0]["status"] == "pinned"

    # 2. Remote mode forwarding to /master/hub/discover and /master/hub/bootstrap
    monkeypatch.setenv("PB_MODE", "remote")
    monkeypatch.setenv("PB_URL", "http://remote-pb.test:8080")
    calls: list = []

    async def _fake_req(method: str, path: str, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        if path == "/master/hub/discover":
            return {"status": "ok", "sources": [], "shared_docs": []}
        if path == "/master/hub/bootstrap":
            return {"status": "ok", "project_id": kwargs["json_body"]["project_id"]}
        raise AssertionError(f"Unexpected path: {path}")

    with patch("projectbrain.ai.mcp._pb_remote_v3_request", new=AsyncMock(side_effect=_fake_req)):
        d_out = json.loads(await projectbrain_discover_workspace_sources(workspace_root=str(ws)))
        b_out = json.loads(
            await projectbrain_bootstrap_project_hub(project_id="acme/remote", workspace_root=str(ws))
        )

    assert d_out["status"] == "ok"
    assert b_out["project_id"] == "acme/remote"
    assert [c["path"] for c in calls] == ["/master/hub/discover", "/master/hub/bootstrap"]
