"""Wave 2A Verification Suite: Cross-Source Contract Extractor, Linker, Drift Detector, Plugin, and Auto-Link API."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import tenant_manager
from projectbrain.graphify_engine.cross_source_linker import CrossSourceLinker
from projectbrain.graphify_engine.kb_linker import KBCoverageReport, UnifiedKBBuilder
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.plugins.builtins.etl_and_flow_plugins import CrossSourceLinkerPlugin
from projectbrain.plugins.registry import PluginRegistry
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes


@pytest.fixture
def isolated_cross_source_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    )
    orig_os_env = {k: os.environ.get(k) for k in env_keys}

    monkeypatch.chdir(tmp_path)
    master_db_path = tmp_path / "master_cross_source.db"

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


@pytest.fixture
def multi_source_workspace(isolated_cross_source_env: Dict[str, Any]) -> Dict[str, Any]:
    """Create a multi-source workspace with backend-api, web-frontend, billing-worker, and __shared__ docs."""
    root = isolated_cross_source_env["tmp_path"] / "erp_ws"
    backend_dir = root / "backend-api"
    frontend_dir = root / "web-frontend"
    worker_dir = root / "billing-worker"
    shared_dir = root / "__shared__"

    for d in (backend_dir, frontend_dir, worker_dir, shared_dir):
        d.mkdir(parents=True, exist_ok=True)

    (backend_dir / "orders.py").write_text(
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        "app = FastAPI()\n\n"
        "class OrderDTO(BaseModel):\n"
        "    id: int\n\n"
        '@app.post("/api/v1/orders")\n'
        "async def create_order(order: OrderDTO):\n"
        '    event_bus.publish("order.created", {"id": order.id})\n'
        "    return {'id': order.id}\n",
        encoding="utf-8",
    )

    (frontend_dir / "orders.ts").write_text(
        "export interface OrderDTO {\n"
        "  id: number;\n"
        "}\n\n"
        "export async function submitOrder(payload: OrderDTO) {\n"
        '  return fetch("/api/v1/orders");\n'
        "}\n",
        encoding="utf-8",
    )

    (worker_dir / "consumer.py").write_text(
        '@subscribe("order.created")\n'
        "def handle_order_created(event):\n"
        "    return event\n",
        encoding="utf-8",
    )

    (shared_dir / "ERP_Spec.md").write_text(
        "# ERP Orders Specification\n\n"
        "## OrderDTO\n"
        "All order mutations go through `/api/v1/orders` using `OrderDTO`.\n"
        "Downstream workers listen for `order.created`.\n",
        encoding="utf-8",
    )

    project_id = "acme/erp"
    tenant_manager.ensure_project_database(project_id, name="Acme ERP")
    tenant_manager.register_project_source(
        project_id, "backend-api", source_type="service", workspace_subpath=str(backend_dir)
    )
    tenant_manager.register_project_source(
        project_id, "web-frontend", source_type="web", workspace_subpath=str(frontend_dir)
    )
    tenant_manager.register_project_source(
        project_id, "billing-worker", source_type="worker", workspace_subpath=str(worker_dir)
    )

    return {
        "project_id": project_id,
        "root": root,
        "backend_dir": backend_dir,
        "frontend_dir": frontend_dir,
        "worker_dir": worker_dir,
        "shared_dir": shared_dir,
        "workspace_map": {
            "backend-api": str(backend_dir),
            "web-frontend": str(frontend_dir),
            "billing-worker": str(worker_dir),
            "__shared__": str(shared_dir),
        },
    }


def test_auto_link_detects_cross_source_relations_and_preserves_manual(
    multi_source_workspace: Dict[str, Any],
) -> None:
    project_id = multi_source_workspace["project_id"]
    ws_map = multi_source_workspace["workspace_map"]
    linker = CrossSourceLinker()

    # 1. Seed manual pinned and manual rejected relations BEFORE auto_link_project
    pinned_manual = tenant_manager.upsert_cross_source_relation(
        project_id=project_id,
        src_source_id="web-frontend",
        dst_source_id="backend-api",
        relation_type="calls_api",
        contract_key="POST:/api/v1/orders",
        origin="manual",
        confidence="MANUAL",
        status="pinned",
        created_by="architect",
    )
    rejected_manual = tenant_manager.upsert_cross_source_relation(
        project_id=project_id,
        src_source_id="billing-worker",
        dst_source_id="backend-api",
        relation_type="consumes_events",
        contract_key="event:order.created",
        origin="manual",
        confidence="MANUAL",
        status="rejected",
        created_by="architect",
    )

    res = linker.auto_link_project(project_id=project_id, branch="main", workspace_map=ws_map)
    assert res["status"] == "ok"
    assert res["artifacts_extracted"] >= 6

    rels = tenant_manager.list_cross_source_relations(project_id)
    rel_types = {r["relation_type"] for r in rels}
    assert {"calls_api", "shares_schema", "documented_by", "consumes_events"} <= rel_types

    # Verify calls_api stayed manual + pinned (never overwritten or duplicated)
    calls_rels = [r for r in rels if r["relation_type"] == "calls_api"]
    assert len(calls_rels) == 1
    assert calls_rels[0]["id"] == pinned_manual["id"]
    assert calls_rels[0]["origin"] == "manual"
    assert calls_rels[0]["status"] == "pinned"

    # Verify rejected relation stayed rejected (never reactivated or duplicated)
    ev_rels = [r for r in rels if r["relation_type"] == "consumes_events"]
    assert len(ev_rels) == 1
    assert ev_rels[0]["id"] == rejected_manual["id"]
    assert ev_rels[0]["origin"] == "manual"
    assert ev_rels[0]["status"] == "rejected"

    # Verify shares_schema and documented_by auto_detected relations exist and are active
    schema_rels = [r for r in rels if r["relation_type"] == "shares_schema"]
    assert len(schema_rels) == 1
    assert schema_rels[0]["src_source_id"] == "web-frontend"
    assert schema_rels[0]["dst_source_id"] == "backend-api"
    assert schema_rels[0]["origin"] == "auto_detected"
    assert schema_rels[0]["status"] == "active"

    doc_rels = [r for r in rels if r["relation_type"] == "documented_by"]
    assert len(doc_rels) >= 2
    assert all(r["dst_source_id"] == "__shared__" for r in doc_rels)
    assert any(r["src_source_id"] == "backend-api" for r in doc_rels)
    assert any(r["src_source_id"] == "web-frontend" for r in doc_rels)

    # Verify an auto-detected relation patched to pinned/rejected also survives re-running auto_link_project
    doc_id = doc_rels[0]["id"]
    tenant_manager.update_cross_source_relation(doc_id, project_id=project_id, status="rejected")
    linker.auto_link_project(project_id=project_id, branch="main", workspace_map=ws_map)
    reloaded = {r["id"]: r for r in tenant_manager.list_cross_source_relations(project_id)}
    assert reloaded[doc_id]["status"] == "rejected"
    assert reloaded[pinned_manual["id"]]["status"] == "pinned"
    assert reloaded[rejected_manual["id"]]["status"] == "rejected"


def test_schema_mutation_triggers_drifted_and_missing_provider_triggers_stale(
    multi_source_workspace: Dict[str, Any],
) -> None:
    project_id = multi_source_workspace["project_id"]
    backend_dir: Path = multi_source_workspace["backend_dir"]
    ws_map = multi_source_workspace["workspace_map"]

    linker = CrossSourceLinker()
    initial = linker.auto_link_project(project_id=project_id, branch="main", workspace_map=ws_map)
    assert initial["drifted_count"] == 0
    assert initial["stale_count"] == 0

    # Mutate OrderDTO in backend-api by adding `total: float`
    (backend_dir / "orders.py").write_text(
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        "app = FastAPI()\n\n"
        "class OrderDTO(BaseModel):\n"
        "    id: int\n"
        "    total: float\n\n"
        '@app.post("/api/v1/orders")\n'
        "async def create_order(order: OrderDTO):\n"
        '    event_bus.publish("order.created", {"id": order.id})\n'
        "    return {'id': order.id}\n",
        encoding="utf-8",
    )

    linker.extract_contract_artifacts(
        project_id=project_id,
        source_id="backend-api",
        branch="main",
        workspace_path=backend_dir,
    )
    reconciled = linker.reconcile_relations(project_id=project_id, branch="main")
    assert reconciled["drifted_count"] >= 1

    rels = tenant_manager.list_cross_source_relations(project_id)
    schema_rel = next(r for r in rels if r["relation_type"] == "shares_schema")
    assert schema_rel["status"] == "drifted"
    assert "signature changed" in str(schema_rel["contract_metadata"].get("drift_reason", "")).lower()

    # Re-running reconcile_relations keeps status == "drifted"
    reconciled_again = linker.reconcile_relations(project_id=project_id, branch="main")
    assert reconciled_again["drifted_count"] >= 1
    schema_rel_again = next(
        r for r in tenant_manager.list_cross_source_relations(project_id) if r["relation_type"] == "shares_schema"
    )
    assert schema_rel_again["status"] == "drifted"

    # Remove POST /api/v1/orders endpoint completely -> calls_api becomes stale
    (backend_dir / "orders.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class OrderDTO(BaseModel):\n"
        "    id: int\n"
        "    total: float\n",
        encoding="utf-8",
    )
    linker.extract_contract_artifacts(
        project_id=project_id,
        source_id="backend-api",
        branch="main",
        workspace_path=backend_dir,
    )
    stale_res = linker.reconcile_relations(project_id=project_id, branch="main")
    assert stale_res["stale_count"] >= 1

    calls_rel = next(
        r for r in tenant_manager.list_cross_source_relations(project_id) if r["relation_type"] == "calls_api"
    )
    assert calls_rel["status"] == "stale"


def test_rest_auto_link_kb_coverage_and_security(
    multi_source_workspace: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = multi_source_workspace["project_id"]
    backend_dir: Path = multi_source_workspace["backend_dir"]
    ws_map = multi_source_workspace["workspace_map"]

    app = create_app()
    client = TestClient(app)

    # 1. Path traversal rejection on workspace_map
    bad_abs = client.post(
        "/master/relations/auto-link",
        json={
            "project_id": project_id,
            "workspace_map": {"backend-api": "/etc"},
        },
    )
    assert bad_abs.status_code == 400
    assert "traversal" in bad_abs.json()["detail"].lower()

    bad_rel = client.post(
        "/master/relations/auto-link",
        json={
            "project_id": project_id,
            "workspace_map": {"backend-api": "../../secret"},
        },
    )
    assert bad_rel.status_code == 400

    # 2. Read-Only enforcement
    monkeypatch.setenv("PB_READ_ONLY", "true")
    ro_res = client.post(
        "/master/relations/auto-link",
        json={"project_id": project_id, "workspace_map": ws_map},
    )
    assert ro_res.status_code == 403
    monkeypatch.setenv("PB_READ_ONLY", "false")

    # 3. POST /master/relations/auto-link links all sources cleanly
    link_res = client.post(
        "/master/relations/auto-link",
        json={
            "project_id": project_id,
            "branch": "main",
            "workspace_map": ws_map,
        },
    )
    assert link_res.status_code == 200, link_res.text
    body = link_res.json()
    assert body["status"] == "ok"
    assert body["relations_upserted"] >= 4
    assert body["drifted_count"] == 0
    assert {r["relation_type"] for r in body["relations"]} >= {
        "calls_api",
        "shares_schema",
        "consumes_events",
        "documented_by",
    }

    # 4. Mutate OrderDTO and re-trigger auto-link via API
    (backend_dir / "orders.py").write_text(
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        "app = FastAPI()\n\n"
        "class OrderDTO(BaseModel):\n"
        "    id: int\n"
        "    total: float\n\n"
        '@app.post("/api/v1/orders")\n'
        "async def create_order(order: OrderDTO):\n"
        '    event_bus.publish("order.created", {"id": order.id})\n'
        "    return {'id': order.id}\n",
        encoding="utf-8",
    )
    drift_link_res = client.post(
        "/master/relations/auto-link",
        json={
            "project_id": project_id,
            "branch": "main",
            "workspace_map": ws_map,
        },
    )
    assert drift_link_res.status_code == 200, drift_link_res.text
    assert drift_link_res.json()["drifted_count"] >= 1

    # 5. GET /kb/coverage returns cross_source_edges, cross_source_drifted_count, and cross_source_drift_items
    cov_res = client.get(
        "/kb/coverage",
        params={"project_id": project_id, "branch": "main", "workspace_path": "."},
    )
    assert cov_res.status_code == 200, cov_res.text
    cov = cov_res.json()
    assert cov["cross_source_edges"] >= 4
    assert cov["cross_source_drifted_count"] >= 1
    assert len(cov["cross_source_drift_items"]) >= 1
    assert any(
        item["relation_type"] == "shares_schema" and item["status"] == "drifted"
        for item in cov["cross_source_drift_items"]
    )

    # Also verify UnifiedKBBuilder.get_kb_coverage_report directly
    builder = UnifiedKBBuilder(workspace_path=Path("."))
    report = builder.get_kb_coverage_report(project_id=project_id, branch="main")
    assert isinstance(report, KBCoverageReport)
    assert report.cross_source_edges == cov["cross_source_edges"]
    assert report.cross_source_drifted_count == cov["cross_source_drifted_count"]
    assert len(report.cross_source_drift_items) == len(cov["cross_source_drift_items"])


def test_multi_format_infra_sql_openapi_and_plugin(
    isolated_cross_source_env: Dict[str, Any],
) -> None:
    root = isolated_cross_source_env["tmp_path"] / "polyglot_ws"
    infra_dir = root / "deploy-infra"
    api_dir = root / "orders-go"
    db_dir = root / "orders-db"
    for d in (infra_dir, api_dir, db_dir):
        d.mkdir(parents=True, exist_ok=True)

    (infra_dir / "docker-compose.yml").write_text(
        "services:\n"
        "  gateway:\n"
        "    image: nginx:latest\n"
        "    depends_on:\n"
        "      - orders-go\n"
        "    environment:\n"
        "      ORDERS_API_URL: http://orders-go:8080\n"
        "  orders-go:\n"
        "    image: orders:1.0\n",
        encoding="utf-8",
    )
    (infra_dir / ".env.prod").write_text("ORDERS_API_URL=http://orders-go:8080/api\n", encoding="utf-8")
    (infra_dir / "main.tf").write_text('resource "aws_ecs_service" "orders-go" {}\n', encoding="utf-8")

    (api_dir / "routes.go").write_text(
        'package main\n'
        'type OrderRecord struct {\n'
        '  ID int64\n'
        '}\n'
        'func setup(r Router) {\n'
        '  r.GET("/api/v1/orders/:id", getOrder)\n'
        '}\n',
        encoding="utf-8",
    )
    (api_dir / "openapi.yaml").write_text(
        "openapi: '3.0.3'\n"
        "paths:\n"
        "  /api/v1/orders/{order_id}:\n"
        "    get:\n"
        "      operationId: getOrder\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n",
        encoding="utf-8",
    )
    (db_dir / "schema.sql").write_text(
        "CREATE TABLE OrderRecord (\n"
        "  id BIGINT PRIMARY KEY,\n"
        "  amount DECIMAL(10, 2)\n"
        ");\n",
        encoding="utf-8",
    )

    project_id = "acme/polyglot"
    tenant_manager.ensure_project_database(project_id)
    tenant_manager.register_project_source(project_id, "deploy-infra")
    tenant_manager.register_project_source(project_id, "orders-go")
    tenant_manager.register_project_source(project_id, "orders-db")

    # Verify CrossSourceLinkerPlugin directly & inside PluginRegistry
    plugin = CrossSourceLinkerPlugin()
    assert plugin.metadata.plugin_id == "linker.cross_source"
    assert plugin.metadata.priority == 90

    reg = PluginRegistry()
    reg.discover_builtins()
    # Built-in discovery remains 26 so existing plugin count contracts stay unchanged
    assert len(reg.list_plugins()) == 26
    reg.register(plugin)
    resolved = reg.resolve_all("knowledge_linker")
    assert any(p.metadata.plugin_id == "linker.cross_source" for p in resolved)

    link_out = plugin.link(
        project_id=project_id,
        branch="main",
        context={
            "workspace_map": {
                "deploy-infra": str(infra_dir),
                "orders-go": str(api_dir),
                "orders-db": str(db_dir),
            }
        },
    )
    assert link_out["plugin_id"] == "linker.cross_source"
    assert link_out["relations_upserted"] >= 2
    rel_types = {r["relation_type"] for r in link_out["relations"]}
    assert "shares_schema" in rel_types
    assert "depends_on" in rel_types or "deploys" in rel_types
