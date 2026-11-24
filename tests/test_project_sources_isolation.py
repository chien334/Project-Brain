"""Wave 1D Verification Suite: Multi-Source Project Hub, Shared Docs (pb_root),
Independent Sub-Source Upgrades, and REST/RBAC APIs.

Covers:
1. 3-part tenant spec parser (`parse_tenant_spec`) & schema naming (`sanitize_source_schema_name`)
2. Physical DB & sub-source isolation (`backend-api` vs `web-frontend` vs `__shared__` / `pb_root`),
   shared doc visibility via `list_documents`, independent `full_replace` upgrade +
   `record_source_upgrade`, and `drop_project_source` isolation
3. Multi-source REST APIs (`/master/sources`, `/master/sources/{id}/upgrade(s)`,
   `/sources/upload` with `scope="project_shared"`, `/sources/shared-bindings`,
   `/master/relations`, `/master/topology`) and `PB_REQUIRE_REST_AUTH=true` RBAC
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for p in (str(REPO_ROOT), str(SRC_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import (
    _source_dir_slug,
    parse_tenant_spec,
    sanitize_project_db_name,
    sanitize_source_schema_name,
    tenant_manager,
)
from projectbrain.graphify_engine.doc_versioning import (
    ingest_document_versioned,
    list_documents,
)
from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from projectbrain.graphify_engine.store import get_graph_store
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes


# =====================================================================
# Strictly Isolated Per-Test Master DB + Project DB Fixture
# =====================================================================


@pytest.fixture(autouse=True)
def isolated_multi_db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test to a fresh Master SQLite DB & pb_projects dir in tmp_path."""
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

    master_db_path = tmp_path / "master_control_plane.db"

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
    monkeypatch.delenv("PB_READ_ONLY", raising=False)
    monkeypatch.delenv("OM_READ_ONLY", raising=False)
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
            "projects_root": tmp_path / "pb_projects",
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
def client(isolated_multi_db_env) -> TestClient:
    """FastAPI TestClient wired to the isolated multi-source environment."""
    return TestClient(create_app())


# =====================================================================
# 1. 3-Part Tenant Spec Parser & Source Schema Naming
# =====================================================================


def test_three_part_parser_and_source_schema_naming():
    assert parse_tenant_spec("acme/erp@web-frontend:feat-ui") == (
        "acme/erp",
        "web-frontend",
        "feat-ui",
    )
    assert parse_tenant_spec("acme/erp@__shared__") == (
        "acme/erp",
        "__shared__",
        "main",
    )
    assert parse_tenant_spec("acme/erp") == ("acme/erp", None, "main")
    assert parse_tenant_spec("acme/erp:release-1") == ("acme/erp", None, "release-1")
    assert parse_tenant_spec(None) == ("default", None, "main")

    web_root = sanitize_source_schema_name("web-frontend", "main")
    assert re.match(r"^pb_src_web_frontend_[0-9a-f]{6}_root$", web_root)
    assert len(web_root.encode("utf-8")) <= 60
    assert sanitize_source_schema_name("web-frontend", "main") == web_root

    assert sanitize_source_schema_name("__shared__", "main") == "pb_root"
    assert sanitize_source_schema_name("__default__", "main") == "pb_root"
    assert sanitize_source_schema_name(None, "main") == "pb_root"

    web_feat = sanitize_source_schema_name("web-frontend", "feat-ui", root_branch="main")
    assert web_feat.startswith("pb_src_web_frontend_")
    assert "_br_feat_ui_" in web_feat
    assert len(web_feat.encode("utf-8")) <= 60

    long_src = "very-long-microservice-frontend-application-name"
    long_branch = "feature/multi-source-isolation-long-branch-name"
    long_schema = sanitize_source_schema_name(long_src, long_branch, root_branch="main")
    assert len(long_schema.encode("utf-8")) <= 60
    assert re.match(r"^[a-z0-9_]+$", long_schema)


# =====================================================================
# 2. Physical DB & Sub-Source Isolation, Shared Docs, Upgrade & Drop
# =====================================================================


@pytest.mark.asyncio
async def test_physical_db_sub_source_isolation_shared_docs_upgrade_and_drop(
    isolated_multi_db_env: Dict[str, Path],
):
    projects_root = isolated_multi_db_env["projects_root"]
    master_db_path = isolated_multi_db_env["master_db_path"]

    # Register project acme/erp and two independent sub-sources
    proj_info = tenant_manager.ensure_project_database("acme/erp", name="Acme ERP")
    erp_db = sanitize_project_db_name("acme/erp")
    assert proj_info["db_name"] == erp_db

    be_src = tenant_manager.register_project_source(
        "acme/erp",
        "backend-api",
        display_name="Backend API",
        source_type="service",
        tech_stack=["python", "fastapi"],
        current_version="1.0.0",
    )
    fe_src = tenant_manager.register_project_source(
        "acme/erp",
        "web-frontend",
        display_name="Web Frontend",
        source_type="web",
        tech_stack=["typescript", "react"],
        current_version="1.0.0",
    )
    assert be_src["root_schema"] == sanitize_source_schema_name("backend-api", "main")
    assert fe_src["root_schema"] == sanitize_source_schema_name("web-frontend", "main")

    # Verify distinct physical SQLite doc DBs on disk per sub-source + pb_root
    proj_dir = projects_root / erp_db
    be_dir = proj_dir / "sources" / _source_dir_slug("backend-api")
    fe_dir = proj_dir / "sources" / _source_dir_slug("web-frontend")
    assert (proj_dir / "pb_root.sqlite").is_file()
    assert (be_dir / f"{be_src['root_schema']}.sqlite").is_file()
    assert (fe_dir / f"{fe_src['root_schema']}.sqlite").is_file()

    # Sync GraphDelta into acme/erp@backend-api and acme/erp@web-frontend
    store = get_graph_store(db_path=str(master_db_path))
    try:
        be_delta = GraphDelta(
            project_id="acme/erp",
            branch="main",
            nodes=[
                GraphNode(
                    id="be_order_svc",
                    project_id="acme/erp",
                    label="OrderService.create_order",
                    file_type="code",
                    source_file="backend/orders.py",
                ),
                GraphNode(
                    id="be_auth_mw",
                    project_id="acme/erp",
                    label="verify_erp_jwt",
                    file_type="code",
                    source_file="backend/auth.py",
                ),
            ],
            edges=[
                GraphEdge(
                    project_id="acme/erp",
                    source="be_order_svc",
                    target="be_auth_mw",
                    relation="calls",
                    source_file="backend/orders.py",
                )
            ],
            synced_files=[
                {"file_path": "backend/orders.py"},
                {"file_path": "backend/auth.py"},
            ],
        )
        be_sync = store.sync_branch_delta("acme/erp@backend-api", "main", be_delta)
        assert be_sync["source_id"] == "backend-api"
        assert be_sync["schema_name"] == be_src["root_schema"]

        fe_delta_v1 = GraphDelta(
            project_id="acme/erp",
            branch="main",
            nodes=[
                GraphNode(
                    id="fe_checkout_v1",
                    project_id="acme/erp",
                    label="CheckoutPageV1",
                    file_type="code",
                    source_file="src/checkout.tsx",
                ),
                GraphNode(
                    id="fe_legacy_cart",
                    project_id="acme/erp",
                    label="LegacyCartWidget",
                    file_type="code",
                    source_file="src/legacy_cart.tsx",
                ),
            ],
            edges=[
                GraphEdge(
                    project_id="acme/erp",
                    source="fe_checkout_v1",
                    target="fe_legacy_cart",
                    relation="imports",
                    source_file="src/checkout.tsx",
                )
            ],
            synced_files=[
                {"file_path": "src/checkout.tsx"},
                {"file_path": "src/legacy_cart.tsx"},
            ],
        )
        fe_sync_v1 = store.sync_branch_delta("acme/erp@web-frontend", "main", fe_delta_v1)
        assert fe_sync_v1["source_id"] == "web-frontend"
        assert fe_sync_v1["schema_name"] == fe_src["root_schema"]
        assert (be_dir / "graph.sqlite").is_file()
        assert (fe_dir / "graph.sqlite").is_file()

        # Verify graph isolation before upgrade
        be_sub = store.get_resolved_subgraph("acme/erp@backend-api", "main")
        fe_sub = store.get_resolved_subgraph("acme/erp@web-frontend", "main")
        assert {n["id"] for n in be_sub["nodes"]} == {"be_order_svc", "be_auth_mw"}
        assert {n["id"] for n in fe_sub["nodes"]} == {"fe_checkout_v1", "fe_legacy_cart"}

        # Ingest project_shared doc into pb_root AND source-local docs into each sub-source
        shared_res = await ingest_document_versioned(
            filename="ERP_Architecture.md",
            markdown_content=(
                "# ERP Architecture\n"
                "Shared event bus, SSO tokens, and API gateway contracts for all ERP services.\n"
            ),
            project_id="acme/erp",
            branch="main",
            author="architect",
            scope="project_shared",
            doc_role="architecture",
        )
        assert shared_res["doc_schema"] == "pb_root"
        assert shared_res["scope"] == "project_shared"
        assert shared_res["source_id"] == "__shared__"

        fe_doc_res = await ingest_document_versioned(
            filename="Frontend_Guide.md",
            markdown_content="# Frontend Guide\nReact component conventions for web-frontend.\n",
            project_id="acme/erp",
            branch="main",
            author="fe-dev",
            source_id="web-frontend",
            scope="source",
        )
        assert fe_doc_res["doc_schema"] == fe_src["root_schema"]

        be_doc_res = await ingest_document_versioned(
            filename="Backend_Runbook.md",
            markdown_content="# Backend Runbook\nFastAPI worker and Postgres tuning for backend-api.\n",
            project_id="acme/erp",
            branch="main",
            author="be-dev",
            source_id="backend-api",
            scope="source",
        )
        assert be_doc_res["doc_schema"] == be_src["root_schema"]

        # Verify list_documents returns shared doc for web-frontend when include_shared=True
        fe_docs_with_shared = list_documents(
            "acme/erp", source_id="web-frontend", include_shared=True
        )
        fe_keys_with_shared = {d["doc_key"] for d in fe_docs_with_shared}
        assert "erp_architecture" in fe_keys_with_shared
        assert "frontend_guide" in fe_keys_with_shared
        assert "backend_runbook" not in fe_keys_with_shared

        fe_docs_local_only = list_documents(
            "acme/erp", source_id="web-frontend", include_shared=False
        )
        assert {d["doc_key"] for d in fe_docs_local_only} == {"frontend_guide"}

        be_docs_with_shared = list_documents(
            "acme/erp", source_id="backend-api", include_shared=True
        )
        assert {d["doc_key"] for d in be_docs_with_shared} == {
            "erp_architecture",
            "backend_runbook",
        }

        # Upgrade web-frontend with upgrade_mode="full_replace" (v2.0.0) + record_source_upgrade
        fe_delta_v2 = GraphDelta(
            project_id="acme/erp",
            branch="main",
            nodes=[
                GraphNode(
                    id="fe_checkout_v2",
                    project_id="acme/erp",
                    label="CheckoutAppV2",
                    file_type="code",
                    source_file="src/checkout_v2.tsx",
                ),
            ],
            edges=[],
            synced_files=[{"file_path": "src/checkout_v2.tsx"}],
        )
        fe_upgrade_sync = store.sync_branch_delta(
            "acme/erp@web-frontend",
            "main",
            fe_delta_v2,
            partial=True,  # overridden to full replacement by upgrade_mode="full_replace"
            upgrade_mode="full_replace",
        )
        assert fe_upgrade_sync["upgrade_mode"] == "full_replace"

        upg_rec = tenant_manager.record_source_upgrade(
            "acme/erp",
            "web-frontend",
            "main",
            "2.0.0",
            upgrade_mode="full_replace",
            nodes_delta=1,
            edges_delta=0,
            commit_sha="v200deadbeef",
            actor="ci-pipeline",
        )
        assert upg_rec["from_version"] == "1.0.0"
        assert upg_rec["to_version"] == "2.0.0"
        assert upg_rec["upgrade_mode"] == "full_replace"
        assert upg_rec["sync_generation"] == 1

        # web-frontend reflects ONLY v2.0.0 nodes; backend-api and pb_root shared docs remain intact
        fe_sub_after = store.get_resolved_subgraph("acme/erp@web-frontend", "main")
        assert {n["id"] for n in fe_sub_after["nodes"]} == {"fe_checkout_v2"}

        be_sub_after = store.get_resolved_subgraph("acme/erp@backend-api", "main")
        assert {n["id"] for n in be_sub_after["nodes"]} == {"be_order_svc", "be_auth_mw"}

        fe_meta_after = tenant_manager.get_project_source("acme/erp", "web-frontend")
        assert fe_meta_after is not None
        assert fe_meta_after["current_version"] == "2.0.0"
        assert fe_meta_after["sync_generation"] == 1

        shared_docs_after_upg = list_documents(
            "acme/erp", source_id="__shared__", include_shared=True
        )
        assert {d["doc_key"] for d in shared_docs_after_upg} == {"erp_architecture"}
    finally:
        store.close()

    # Drop web-frontend via drop_project_source
    drop_res = tenant_manager.drop_project_source("acme/erp", "web-frontend")
    assert drop_res["deleted"] is True
    assert drop_res["source_id"] == "web-frontend"
    assert not fe_dir.exists()
    assert tenant_manager.get_project_source("acme/erp", "web-frontend") is None
    assert tenant_manager.list_source_upgrades("acme/erp", source_id="web-frontend") == []

    # Verify backend-api physical files, graph nodes, local doc, and pb_root shared docs are untouched
    assert (be_dir / f"{be_src['root_schema']}.sqlite").is_file()
    assert (be_dir / "graph.sqlite").is_file()
    assert (proj_dir / "pb_root.sqlite").is_file()

    verify_store = get_graph_store(db_path=str(master_db_path))
    try:
        be_sub_final = verify_store.get_resolved_subgraph("acme/erp@backend-api", "main")
        assert {n["id"] for n in be_sub_final["nodes"]} == {"be_order_svc", "be_auth_mw"}
        assert len(be_sub_final["edges"]) == 1
    finally:
        verify_store.close()

    be_docs_final = list_documents(
        "acme/erp", source_id="backend-api", include_shared=True
    )
    assert {d["doc_key"] for d in be_docs_final} == {
        "erp_architecture",
        "backend_runbook",
    }

    with sqlite3.connect(proj_dir / "pb_root.sqlite") as conn_root:
        shared_rows = conn_root.execute(
            "SELECT doc_key, scope, source_id FROM document_versions "
            "WHERE project_id = ? AND status = 'active'",
            ("acme/erp",),
        ).fetchall()
    assert ("erp_architecture", "project_shared", "__shared__") in shared_rows


# =====================================================================
# 3. Multi-Source REST API & RBAC Verification
# =====================================================================


def test_rest_api_project_sources_relations_shared_bindings_and_rbac(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    # Create project acme/erp
    proj_res = client.post(
        "/master/projects",
        json={"project_id": "acme/erp", "name": "Acme ERP"},
    )
    assert proj_res.status_code == 200, proj_res.text

    # POST /master/sources (backend-api & web-frontend)
    r_be = client.post(
        "/master/sources",
        json={
            "project_id": "acme/erp",
            "source_id": "backend-api",
            "display_name": "Backend API",
            "source_type": "service",
            "tech_stack": ["python", "fastapi"],
            "current_version": "1.0.0",
        },
    )
    assert r_be.status_code == 200, r_be.text
    assert r_be.json()["source"]["source_id"] == "backend-api"

    r_fe = client.post(
        "/master/sources",
        json={
            "project_id": "acme/erp",
            "source_id": "web-frontend",
            "display_name": "Web Frontend",
            "source_type": "web",
            "tech_stack": ["react", "typescript"],
            "current_version": "1.0.0",
        },
    )
    assert r_fe.status_code == 200, r_fe.text
    assert r_fe.json()["source"]["source_id"] == "web-frontend"

    # GET /master/sources
    r_list = client.get("/master/sources", params={"project_id": "acme/erp"})
    assert r_list.status_code == 200, r_list.text
    assert {s["source_id"] for s in r_list.json()["sources"]} == {
        "backend-api",
        "web-frontend",
    }

    # PATCH /master/sources/{id}
    r_patch = client.patch(
        "/master/sources/web-frontend",
        params={"project_id": "acme/erp"},
        json={
            "display_name": "Web Frontend Portal",
            "tech_stack": ["react", "vite"],
            "metadata": {"owner": "ui-team"},
        },
    )
    assert r_patch.status_code == 200, r_patch.text
    patched_src = r_patch.json()["source"]
    assert patched_src["display_name"] == "Web Frontend Portal"
    assert patched_src["tech_stack"] == ["react", "vite"]
    assert patched_src["metadata"]["owner"] == "ui-team"

    # POST /master/sources/{id}/upgrade & GET /master/sources/{id}/upgrades
    r_upg = client.post(
        "/master/sources/web-frontend/upgrade",
        params={"project_id": "acme/erp"},
        json={
            "branch_name": "main",
            "to_version": "2.0.0",
            "upgrade_mode": "full_replace",
            "nodes_delta": 12,
            "edges_delta": 8,
            "commit_sha": "sha200",
            "actor": "release-bot",
        },
    )
    assert r_upg.status_code == 200, r_upg.text
    upg_body = r_upg.json()
    assert upg_body["upgrade"]["to_version"] == "2.0.0"
    assert upg_body["upgrade"]["upgrade_mode"] == "full_replace"
    assert upg_body["source"]["current_version"] == "2.0.0"
    assert upg_body["source"]["sync_generation"] == 1

    r_upgs = client.get(
        "/master/sources/web-frontend/upgrades",
        params={"project_id": "acme/erp"},
    )
    assert r_upgs.status_code == 200, r_upgs.text
    upgrades = r_upgs.json()["upgrades"]
    assert len(upgrades) == 1
    assert upgrades[0]["to_version"] == "2.0.0"
    assert upgrades[0]["commit_sha"] == "sha200"

    # POST /sources/upload with scope="project_shared"
    shared_md = (
        "# ERP Shared Architecture\n"
        "Shared API conventions and domain models across backend-api and web-frontend.\n"
    )
    r_upload = client.post(
        "/sources/upload",
        data={
            "project_id": "acme/erp",
            "branch": "main",
            "scope": "project_shared",
            "doc_role": "architecture",
        },
        files={
            "file": (
                "ERP_Architecture.md",
                io.BytesIO(shared_md.encode("utf-8")),
                "text/markdown",
            )
        },
    )
    assert r_upload.status_code == 200, r_upload.text
    up_data = r_upload.json()
    assert up_data["scope"] == "project_shared"
    assert up_data["doc_schema"] == "pb_root"
    assert up_data["doc_key"] == "erp_architecture"

    # GET /sources/shared-bindings & POST /sources/shared-bindings
    r_binds_init = client.get(
        "/sources/shared-bindings", params={"project_id": "acme/erp"}
    )
    assert r_binds_init.status_code == 200, r_binds_init.text
    assert any(
        b["doc_key"] == "erp_architecture" and b["target_source_id"] == "*"
        for b in r_binds_init.json()["bindings"]
    )

    r_bind_pin = client.post(
        "/sources/shared-bindings",
        json={
            "project_id": "acme/erp",
            "doc_key": "erp_architecture",
            "target_source_id": "web-frontend",
            "pinned_version": "v1",
            "binding_role": "contract",
            "priority": 2.5,
        },
    )
    assert r_bind_pin.status_code == 200, r_bind_pin.text
    assert r_bind_pin.json()["binding"]["target_source_id"] == "web-frontend"
    assert r_bind_pin.json()["binding"]["pinned_version"] == "v1"

    r_binds_fe = client.get(
        "/sources/shared-bindings",
        params={
            "project_id": "acme/erp",
            "target_source_id": "web-frontend",
            "include_wildcard": True,
        },
    )
    assert r_binds_fe.status_code == 200, r_binds_fe.text
    assert {b["target_source_id"] for b in r_binds_fe.json()["bindings"]} == {
        "web-frontend",
        "*",
    }

    # POST /master/relations, GET /master/relations, PATCH /master/relations/{id}, DELETE /master/relations/{id}
    r_rel_create = client.post(
        "/master/relations",
        json={
            "project_id": "acme/erp",
            "src_source_id": "web-frontend",
            "dst_source_id": "backend-api",
            "relation_type": "calls_api",
            "contract_key": "POST /api/v1/orders",
            "contract_metadata": {"auth": "bearer"},
            "pin": True,
            "created_by": "architect",
        },
    )
    assert r_rel_create.status_code == 200, r_rel_create.text
    rel_obj = r_rel_create.json()["relation"]
    rel_id = rel_obj["id"]
    assert rel_obj["status"] == "pinned"
    assert rel_obj["contract_key"] == "POST /api/v1/orders"

    r_rel_list = client.get("/master/relations", params={"project_id": "acme/erp"})
    assert r_rel_list.status_code == 200, r_rel_list.text
    assert [r["id"] for r in r_rel_list.json()["relations"]] == [rel_id]

    r_rel_patch = client.patch(
        f"/master/relations/{rel_id}",
        params={"project_id": "acme/erp"},
        json={
            "pin": False,
            "contract_metadata": {"auth": "bearer", "rate_limit": 100},
        },
    )
    assert r_rel_patch.status_code == 200, r_rel_patch.text
    patched_rel = r_rel_patch.json()["relation"]
    assert patched_rel["status"] == "active"
    assert patched_rel["contract_metadata"]["rate_limit"] == 100

    # Verify GET /master/topology reflects sources, shared_doc_count, and relation_count
    r_topo = client.get("/master/topology", params={"project_id": "acme/erp"})
    assert r_topo.status_code == 200, r_topo.text
    topo_proj = r_topo.json()["projects"][0]
    assert topo_proj["project_id"] == "acme/erp"
    assert {s["source_id"] for s in topo_proj["sources"]} == {
        "backend-api",
        "web-frontend",
    }
    assert topo_proj["shared_doc_count"] >= 1
    assert topo_proj["relation_count"] == 1

    r_rel_del = client.delete(
        f"/master/relations/{rel_id}",
        params={"project_id": "acme/erp"},
    )
    assert r_rel_del.status_code == 200, r_rel_del.text
    assert r_rel_del.json()["deleted"] is True

    r_rel_after_del = client.get("/master/relations", params={"project_id": "acme/erp"})
    assert r_rel_after_del.status_code == 200
    assert r_rel_after_del.json()["relations"] == []

    # -----------------------------------------------------------------
    # RBAC Enforcement with PB_REQUIRE_REST_AUTH=true
    # -----------------------------------------------------------------
    auth_routes.init_auth_system()
    assert (
        client.post(
            "/auth/accounts",
            json={
                "username": "erp_reader",
                "token": "pb_tok_erp_reader",
                "role": "reader",
                "allowed_tools": "*",
                "allowed_projects": "*",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/accounts",
            json={
                "username": "erp_collab",
                "token": "pb_tok_erp_collab",
                "role": "collaborator",
                "allowed_tools": "*",
                "allowed_projects": "acme/erp",
            },
        ).status_code
        == 200
    )

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    monkeypatch.setattr(env, "require_rest_auth", True)

    reader_headers = {"Authorization": "Bearer pb_tok_erp_reader"}
    collab_headers = {"Authorization": "Bearer pb_tok_erp_collab"}

    # Unauthenticated -> 401
    assert client.get("/master/sources", params={"project_id": "acme/erp"}).status_code == 401
    assert client.post("/master/sources", json={"project_id": "acme/erp", "source_id": "x"}).status_code == 401

    # Reader gets 200 on GET endpoints
    for get_url, get_params in (
        ("/master/sources", {"project_id": "acme/erp"}),
        ("/master/sources/web-frontend/upgrades", {"project_id": "acme/erp"}),
        ("/sources/shared-bindings", {"project_id": "acme/erp"}),
        ("/master/relations", {"project_id": "acme/erp"}),
        ("/master/topology", {"project_id": "acme/erp"}),
    ):
        resp = client.get(get_url, params=get_params, headers=reader_headers)
        assert resp.status_code == 200, f"Reader GET {get_url} failed: {resp.text}"

    # Reader gets 403 on all mutation endpoints
    assert (
        client.post(
            "/master/sources",
            json={"project_id": "acme/erp", "source_id": "blocked-src"},
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/master/sources/web-frontend",
            params={"project_id": "acme/erp"},
            json={"display_name": "Blocked"},
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/master/sources/web-frontend/upgrade",
            params={"project_id": "acme/erp"},
            json={"to_version": "9.9.9"},
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/sources/upload",
            data={"project_id": "acme/erp", "scope": "project_shared"},
            files={"file": ("blocked.md", io.BytesIO(b"# Blocked"), "text/markdown")},
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/sources/shared-bindings",
            json={"project_id": "acme/erp", "doc_key": "erp_architecture"},
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/master/relations",
            json={
                "project_id": "acme/erp",
                "src_source_id": "web-frontend",
                "dst_source_id": "backend-api",
                "relation_type": "calls_api",
            },
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/master/relations/{rel_id}",
            params={"project_id": "acme/erp"},
            json={"pin": True},
            headers=reader_headers,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/master/relations/{rel_id}",
            params={"project_id": "acme/erp"},
            headers=reader_headers,
        ).status_code
        == 403
    )

    # Collaborator with allowed_projects="acme/erp" is permitted on acme/erp@web-frontend
    r_collab_get = client.get(
        "/master/sources",
        params={"project_id": "acme/erp@web-frontend"},
        headers=collab_headers,
    )
    assert r_collab_get.status_code == 200, r_collab_get.text

    r_collab_patch = client.patch(
        "/master/sources/web-frontend",
        params={"project_id": "acme/erp@web-frontend"},
        json={"display_name": "Web Frontend Collab Updated"},
        headers=collab_headers,
    )
    assert r_collab_patch.status_code == 200, r_collab_patch.text
    assert r_collab_patch.json()["source"]["display_name"] == "Web Frontend Collab Updated"

    r_collab_upg = client.post(
        "/master/sources/web-frontend/upgrade",
        params={"project_id": "acme/erp@web-frontend"},
        json={"to_version": "2.1.0", "upgrade_mode": "delta"},
        headers=collab_headers,
    )
    assert r_collab_upg.status_code == 200, r_collab_upg.text
    assert r_collab_upg.json()["source"]["current_version"] == "2.1.0"

    r_collab_upload = client.post(
        "/sources/upload",
        params={"project_id": "acme/erp@web-frontend"},
        data={
            "project_id": "acme/erp",
            "source_id": "web-frontend",
            "branch": "main",
            "scope": "source",
        },
        files={
            "file": (
                "UI_Changelog.md",
                io.BytesIO(b"# UI Changelog\nAdded v2.1.0 checkout widget.\n"),
                "text/markdown",
            )
        },
        headers=collab_headers,
    )
    assert r_collab_upload.status_code == 200, r_collab_upload.text
    assert r_collab_upload.json()["source_id"] == "web-frontend"

    # Collaborator is denied (403) when targeting an unauthorized project
    r_collab_denied = client.get(
        "/master/sources",
        params={"project_id": "acme/other@web-frontend"},
        headers=collab_headers,
    )
    assert r_collab_denied.status_code == 403, r_collab_denied.text
