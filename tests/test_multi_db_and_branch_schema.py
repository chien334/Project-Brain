"""Comprehensive Verification Suite for 3-Tier Multi-DB & Per-Branch Schema Isolation.

Covers:
1. Master Control-Plane Catalog & Per-Project Physical Database Provisioning
   (`pb_projects/<db_name>/pb_root.sqlite` & `graph.sqlite`, `GET /master/topology` UI contract)
2. Per-Branch Schema Isolation for BOTH Graphify AND Documents/Memories
   (`pb_root` vs `pb_br_*`, cross-schema unit reuse, zero cross-project leakage)
3. Branch Schema Drop & Project Database Drop Lifecycle
   (400 on root branch delete, branch schema drop, full project DB drop)
4. PostgreSQL DDL & `CREATE DATABASE` / `CREATE SCHEMA` / `SET search_path` Verification
   (mocked `psycopg2.connect`)
5. FastMCP `projectbrain_database_topology` & REST/MCP RBAC Verification
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for p in (str(REPO_ROOT), str(SRC_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from projectbrain.ai.mcp import projectbrain_database_topology
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import (
    ProjectDatabaseManager,
    sanitize_branch_schema_for_project,
    sanitize_project_db_name,
    tenant_manager,
)
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.server.api import _is_write_tool, create_app
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
    """FastAPI TestClient wired to the isolated 3-tier multi-DB environment."""
    return TestClient(create_app())


# =====================================================================
# 1. Master Control-Plane Catalog & Physical Project DB Provisioning
# =====================================================================


def test_master_catalog_and_per_project_physical_databases(
    client: TestClient, isolated_multi_db_env: Dict[str, Path]
):
    projects_root = isolated_multi_db_env["projects_root"]

    # Create acme/billing via /codegraph/projects
    r1 = client.post(
        "/codegraph/projects",
        json={
            "id": "acme/billing",
            "name": "Acme Billing",
            "description": "Billing service workspace",
        },
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    expected_billing_db = sanitize_project_db_name("acme/billing")
    assert b1["db_name"] == expected_billing_db
    assert b1["db_name"].startswith("pb_proj_acme_billing_")
    assert b1["schema_name"] == "pb_root"

    # Create acme/inventory via /master/projects
    r2 = client.post(
        "/master/projects",
        json={
            "project_id": "acme/inventory",
            "name": "Acme Inventory",
            "description": "Inventory service workspace",
        },
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    expected_inv_db = sanitize_project_db_name("acme/inventory")
    assert b2["status"] == "ok"
    assert b2["project"]["project_id"] == "acme/inventory"
    assert b2["project"]["db_name"] == expected_inv_db
    assert b2["project"]["db_name"].startswith("pb_proj_acme_inventory_")
    assert b2["project"]["root_schema"] == "pb_root"
    assert expected_billing_db != expected_inv_db

    # Verify two distinct physical project directories & SQLite DB files on disk
    for db_name in (expected_billing_db, expected_inv_db):
        proj_dir = projects_root / db_name
        assert proj_dir.is_dir(), f"Missing physical project DB directory: {proj_dir}"
        assert (proj_dir / "pb_root.sqlite").is_file()
        assert (proj_dir / "graph.sqlite").is_file()

    # Verify GET /master/topology payload & UI contract keys (app.js fetchMergedTopology)
    topo_res = client.get("/master/topology")
    assert topo_res.status_code == 200, topo_res.text
    topo = topo_res.json()

    assert topo["status"] == "ok"
    assert topo["multi_db_enabled"] is True
    assert topo["backend_type"] == "sqlite"
    assert topo["master_database"]["project_count"] == 2
    assert topo["master_database"]["branch_schema_count"] == 2
    assert "descriptor" in topo["master_database"]

    by_id = {p["project_id"]: p for p in topo["projects"]}
    assert set(by_id.keys()) == {"acme/billing", "acme/inventory"}

    for pid, expected_db in (
        ("acme/billing", expected_billing_db),
        ("acme/inventory", expected_inv_db),
    ):
        proj_obj = by_id[pid]
        assert proj_obj["db_name"] == expected_db
        assert proj_obj["backend_type"] == "sqlite"
        assert proj_obj["root_branch"] == "main"
        assert proj_obj["root_schema"] == "pb_root"
        assert len(proj_obj["branches"]) == 1
        root_br = proj_obj["branches"][0]
        assert root_br["branch_name"] == "main"
        assert root_br["schema_name"] == "pb_root"
        assert root_br["is_root"] is True
        assert root_br["has_graph_schema"] is True
        assert root_br["has_doc_schema"] is True
        # UI contract keys expected by app.js loadProjectsList()
        assert "node_count" in root_br
        assert "doc_count" in root_br
        assert "memory_count" in root_br


# =====================================================================
# 2. Per-Branch Schema Isolation (Graphify + Documents & Memories)
# =====================================================================


def test_per_branch_schema_isolation_graphify_and_documents(
    client: TestClient, isolated_multi_db_env: Dict[str, Path]
):
    projects_root = isolated_multi_db_env["projects_root"]

    # Provision two projects
    assert client.post(
        "/master/projects",
        json={"project_id": "acme/billing", "name": "Billing"},
    ).status_code == 200
    assert client.post(
        "/master/projects",
        json={"project_id": "acme/inventory", "name": "Inventory"},
    ).status_code == 200

    billing_db = sanitize_project_db_name("acme/billing")
    inventory_db = sanitize_project_db_name("acme/inventory")

    # Provision branch feat/invoice-v2 on acme/billing
    br_res = client.post(
        "/master/projects/acme%2Fbilling/branches",
        json={
            "branch_name": "feat/invoice-v2",
            "base_branch": "main",
            "author": "alice",
        },
    )
    assert br_res.status_code == 200, br_res.text
    br_data = br_res.json()
    expected_br_schema = sanitize_branch_schema_for_project("feat/invoice-v2", "main")
    assert expected_br_schema.startswith("pb_br_feat_invoice_v2_")
    assert br_data["db_name"] == billing_db
    assert br_data["branch"]["schema_name"] == expected_br_schema
    assert br_data["branch"]["is_root"] is False

    # Branch SQLite DB file created inside acme/billing's DB directory, NOT acme/inventory's
    assert (projects_root / billing_db / f"{expected_br_schema}.sqlite").is_file()
    assert not (projects_root / inventory_db / f"{expected_br_schema}.sqlite").exists()

    # --- Graphify branch isolation ---
    sync_main = client.post(
        "/graphify/sync",
        json={
            "project_id": "acme/billing",
            "branch": "main",
            "author": "alice",
            "nodes": [
                {
                    "id": "fn_invoice_v1",
                    "project_id": "acme/billing",
                    "label": "create_invoice_v1",
                    "file_type": "code",
                    "source_file": "billing/invoice.py",
                }
            ],
            "edges": [],
        },
    )
    assert sync_main.status_code == 200, sync_main.text
    assert sync_main.json()["db_name"] == billing_db
    assert sync_main.json()["schema_name"] == "pb_root"

    sync_feat = client.post(
        "/graphify/sync",
        json={
            "project_id": "acme/billing",
            "branch": "feat/invoice-v2",
            "base_branch": "main",
            "author": "alice",
            "partial": True,
            "synced_files_filter": ["billing/tax.py"],
            "nodes": [
                {
                    "id": "fn_tax_v2",
                    "project_id": "acme/billing",
                    "label": "compute_vat_v2",
                    "file_type": "code",
                    "source_file": "billing/tax.py",
                }
            ],
            "edges": [],
        },
    )
    assert sync_feat.status_code == 200, sync_feat.text
    assert sync_feat.json()["db_name"] == billing_db
    assert sync_feat.json()["schema_name"] == expected_br_schema

    # Resolved subgraph: main only sees v1, feat/invoice-v2 sees inherited v1 + v2, inventory sees 0
    sub_main = client.get(
        "/graphify/subgraph", params={"project_id": "acme/billing", "branch": "main"}
    ).json()
    sub_feat = client.get(
        "/graphify/subgraph",
        params={"project_id": "acme/billing", "branch": "feat/invoice-v2"},
    ).json()
    sub_inv = client.get(
        "/graphify/subgraph", params={"project_id": "acme/inventory", "branch": "main"}
    ).json()

    main_node_ids = {n["id"] for n in sub_main["nodes"]}
    feat_node_ids = {n["id"] for n in sub_feat["nodes"]}
    assert "fn_invoice_v1" in main_node_ids
    assert "fn_tax_v2" not in main_node_ids
    assert {"fn_invoice_v1", "fn_tax_v2"}.issubset(feat_node_ids)
    assert sub_inv["nodes"] == []

    # --- Documents & Memories per-branch schema isolation + cross-schema unit reuse ---
    md_v1 = "# Overview\nBilling overview unchanged.\n\n# Invoice Schema\nInvoice v1 payload."
    md_v2 = "# Overview\nBilling overview unchanged.\n\n# Invoice Schema\nInvoice v2 payload with VAT."

    up_v1 = client.post(
        "/sources/upload",
        data={"project_id": "acme/billing", "branch": "main"},
        files={"file": ("spec.md", io.BytesIO(md_v1.encode("utf-8")), "text/markdown")},
    )
    assert up_v1.status_code == 200, up_v1.text
    d1 = up_v1.json()
    assert d1["db_name"] == billing_db
    assert d1["doc_schema"] == "pb_root"
    assert d1["doc_key"] == "spec"
    assert d1["doc_version"] == "v1"
    assert d1["new_units"] == 2
    assert d1["reused_units"] == 0

    up_v2 = client.post(
        "/sources/upload",
        data={
            "project_id": "acme/billing",
            "branch": "feat/invoice-v2",
            "base_branch": "main",
        },
        files={"file": ("spec.md", io.BytesIO(md_v2.encode("utf-8")), "text/markdown")},
    )
    assert up_v2.status_code == 200, up_v2.text
    d2 = up_v2.json()
    assert d2["db_name"] == billing_db
    assert d2["doc_schema"] == expected_br_schema
    assert d2["doc_key"] == "spec"
    assert d2["doc_version"] == "v2"
    assert d2["reused_units"] >= 1
    assert d2["new_units"] >= 1

    # Verify REST document resolution per branch & zero leakage to acme/inventory
    docs_main = client.get(
        "/sources/documents", params={"project_id": "acme/billing", "branch": "main"}
    ).json()["documents"]
    docs_feat = client.get(
        "/sources/documents",
        params={"project_id": "acme/billing", "branch": "feat/invoice-v2"},
    ).json()["documents"]
    docs_inv = client.get(
        "/sources/documents", params={"project_id": "acme/inventory", "branch": "main"}
    ).json()["documents"]

    assert [(d["doc_key"], d["version"]) for d in docs_main] == [("spec", "v1")]
    assert [(d["doc_key"], d["version"]) for d in docs_feat] == [("spec", "v2")]
    assert docs_inv == []

    # Direct physical SQLite DB inspection across pb_root.sqlite, pb_br_*.sqlite, and inventory pb_root.sqlite
    with sqlite3.connect(projects_root / billing_db / "pb_root.sqlite") as conn_root:
        vers_root = [
            r[0]
            for r in conn_root.execute(
                "SELECT version FROM document_versions WHERE doc_key='spec'"
            ).fetchall()
        ]
        mem_root_cnt = conn_root.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    with sqlite3.connect(
        projects_root / billing_db / f"{expected_br_schema}.sqlite"
    ) as conn_br:
        vers_br = [
            r[0]
            for r in conn_br.execute(
                "SELECT version FROM document_versions WHERE doc_key='spec'"
            ).fetchall()
        ]
        mem_br_cnt = conn_br.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    with sqlite3.connect(projects_root / inventory_db / "pb_root.sqlite") as conn_inv:
        vers_inv = conn_inv.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0]
        mem_inv_cnt = conn_inv.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    assert vers_root == ["v1"]
    assert vers_br == ["v2"]
    assert mem_root_cnt >= 2
    assert mem_br_cnt >= 2
    assert vers_inv == 0
    assert mem_inv_cnt == 0

    # Topology endpoint reflects live per-branch counts
    topo_billing = client.get(
        "/master/topology", params={"project_id": "acme/billing"}
    ).json()
    assert topo_billing["master_database"]["project_count"] == 1
    assert topo_billing["master_database"]["branch_schema_count"] == 2
    br_map = {b["branch_name"]: b for b in topo_billing["projects"][0]["branches"]}
    assert br_map["main"]["doc_count"] >= 1
    assert br_map["main"]["memory_count"] >= 2
    assert br_map["main"]["node_count"] >= 1
    assert br_map["feat/invoice-v2"]["doc_count"] >= 1
    assert br_map["feat/invoice-v2"]["memory_count"] >= 2
    assert br_map["feat/invoice-v2"]["node_count"] >= 2


# =====================================================================
# 3. Branch Schema Drop & Project Database Drop Lifecycle
# =====================================================================


def test_branch_schema_drop_and_project_database_drop_lifecycle(
    client: TestClient, isolated_multi_db_env: Dict[str, Path]
):
    projects_root = isolated_multi_db_env["projects_root"]

    assert client.post(
        "/master/projects",
        json={"project_id": "acme/billing", "name": "Billing"},
    ).status_code == 200
    assert client.post(
        "/master/projects",
        json={"project_id": "acme/inventory", "name": "Inventory"},
    ).status_code == 200
    assert client.post(
        "/master/projects/acme%2Fbilling/branches",
        json={"branch_name": "feat/invoice-v2", "base_branch": "main"},
    ).status_code == 200

    billing_db = sanitize_project_db_name("acme/billing")
    inventory_db = sanitize_project_db_name("acme/inventory")
    feat_schema = sanitize_branch_schema_for_project("feat/invoice-v2", "main")
    feat_sqlite = projects_root / billing_db / f"{feat_schema}.sqlite"
    assert feat_sqlite.is_file()

    # 3a. Cannot drop canonical root branch 'main'
    del_root = client.delete("/master/projects/acme%2Fbilling/branches/main")
    assert del_root.status_code == 400
    assert "root branch" in del_root.json()["detail"].lower()

    # 3b. Drop non-root branch feat/invoice-v2 -> deletes branch schema, preserves pb_root
    del_feat = client.delete(
        "/master/projects/acme%2Fbilling/branches/feat%2Finvoice-v2"
    )
    assert del_feat.status_code == 200, del_feat.text
    del_feat_body = del_feat.json()
    assert del_feat_body["deleted"] is True
    assert del_feat_body["schema_dropped"] == feat_schema
    assert not feat_sqlite.exists()
    assert (projects_root / billing_db / "pb_root.sqlite").is_file()

    remaining_branches = tenant_manager.list_project_branches("acme/billing")
    assert remaining_branches == ["main"]

    # 3c. Drop acme/inventory via DELETE /codegraph/projects/acme%2Finventory
    inv_dir = projects_root / inventory_db
    assert inv_dir.is_dir()

    del_proj = client.delete("/codegraph/projects/acme%2Finventory")
    assert del_proj.status_code == 200, del_proj.text
    assert not inv_dir.exists()

    mp_rows = tenant_manager._master_fetchall(
        "SELECT * FROM master_projects WHERE project_id = ?", ("acme/inventory",)
    )
    mb_rows = tenant_manager._master_fetchall(
        "SELECT * FROM master_branches WHERE project_id = ?", ("acme/inventory",)
    )
    assert mp_rows == []
    assert mb_rows == []

    # acme/billing remains intact in Master topology
    topo = client.get("/master/topology").json()
    assert [p["project_id"] for p in topo["projects"]] == ["acme/billing"]


# =====================================================================
# 4. PostgreSQL DDL, CREATE DATABASE, CREATE SCHEMA & SET search_path
# =====================================================================


def _render_psql_statement(stmt: Any) -> str:
    """Render psycopg2.sql.SQL / Identifier / Composed without a live PG connection."""
    if isinstance(stmt, str):
        return stmt
    if hasattr(stmt, "seq"):
        return "".join(_render_psql_statement(part) for part in stmt.seq)
    if hasattr(stmt, "string"):
        if stmt.__class__.__name__ == "Identifier":
            return f'"{stmt.string}"'
        return str(stmt.string)
    return str(stmt)


class _FakePgCursor:
    def __init__(self, conn: "_FakePgConnection") -> None:
        self.conn = conn
        self.description: Optional[List[Tuple[str]]] = None
        self._last_results: List[Tuple[Any, ...]] = []

    def __enter__(self) -> "_FakePgCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        sql_text = _render_psql_statement(query).strip()
        self.conn.executed_sql.append((sql_text, params))
        upper = sql_text.upper()

        if upper.startswith("SELECT 1 FROM PG_DATABASE"):
            self._last_results = []
            self.description = [("exists",)]
        elif "FROM INFORMATION_SCHEMA.TABLES" in upper:
            self._last_results = []
            self.description = [("ok",)]
        elif "FROM MASTER_PROJECTS" in upper:
            self._last_results = []
            self.description = [("root_branch",)]
        elif "FROM MASTER_BRANCHES" in upper:
            self._last_results = []
            self.description = [("base_branch",)]
        else:
            self._last_results = []
            self.description = None

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self._last_results[0] if self._last_results else None

    def fetchall(self) -> List[Tuple[Any, ...]]:
        return list(self._last_results)


class _FakePgConnection:
    def __init__(self, dsn: str, shared_log: List[Tuple[str, str, Any]]) -> None:
        self.dsn = dsn
        self.autocommit = False
        self.closed = 0
        self.executed_sql: List[Tuple[str, Any]] = []
        self._shared_log = shared_log

    def cursor(self, *args, **kwargs) -> _FakePgCursor:
        parent = self

        class _RecordingCursor(_FakePgCursor):
            def execute(self, query: Any, params: Any = None) -> None:
                super().execute(query, params)
                parent._shared_log.append(
                    (parent.dsn, _render_psql_statement(query).strip(), params)
                )

        return _RecordingCursor(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = 1


class _FakeMasterPgFacade:
    def __init__(self, conn: _FakePgConnection) -> None:
        self.conn = conn
        self.is_postgres = True

    def connect(self) -> None:
        pass


def test_postgres_ddl_create_database_schema_views_and_search_path(
    monkeypatch: pytest.MonkeyPatch,
):
    pg_url = "postgresql://postgres:secret@localhost:5432/projectbrain_master"
    executed_log: List[Tuple[str, str, Any]] = []

    def fake_pg_connect(dsn: str, *args, **kwargs) -> _FakePgConnection:
        return _FakePgConnection(dsn, executed_log)

    monkeypatch.setenv("PB_MULTI_DB_ENABLED", "true")
    monkeypatch.setattr(env, "database_url", pg_url)
    monkeypatch.setattr("projectbrain.core.tenant_manager.psycopg2.connect", fake_pg_connect)

    master_conn = _FakePgConnection(pg_url, executed_log)
    master_facade = _FakeMasterPgFacade(master_conn)

    mgr = ProjectDatabaseManager()
    mgr.bind_master_db(master_facade)

    # 1. Provision project DB + pb_root schema
    proj_info = mgr.ensure_project_database("acme/billing", name="Acme Billing")
    expected_db = sanitize_project_db_name("acme/billing")
    assert proj_info["backend_type"] == "postgresql"
    assert proj_info["db_name"] == expected_db

    # 2. Provision branch schema + v_resolved_* views + SET search_path
    br_info = mgr.ensure_branch_schema(
        "acme/billing", "feat/invoice-v2", base_branch="main", author="bob"
    )
    expected_schema = sanitize_branch_schema_for_project("feat/invoice-v2", "main")
    assert br_info["schema_name"] == expected_schema

    # 3. Acquire tenant connection for branch -> verifies SET search_path
    _, is_pg, got_db, got_schema = mgr.get_tenant_connection(
        "acme/billing", "feat/invoice-v2"
    )
    assert is_pg is True
    assert got_db == expected_db
    assert got_schema == expected_schema

    all_sql = [sql for _, sql, _ in executed_log]
    assert any(
        f'CREATE DATABASE "{expected_db}" ENCODING \'UTF8\'' in s for s in all_sql
    ), f"Expected CREATE DATABASE for {expected_db}, got: {all_sql[:10]}"
    assert any("CREATE SCHEMA IF NOT EXISTS pb_root" in s for s in all_sql)
    assert any(
        f'CREATE SCHEMA IF NOT EXISTS "{expected_schema}"' in s for s in all_sql
    )
    assert any(
        f'CREATE OR REPLACE VIEW "{expected_schema}".v_resolved_document_versions' in s
        for s in all_sql
    )
    assert any(
        f'SET search_path TO "{expected_schema}", public;' in s for s in all_sql
    )

    # 4. Drop branch schema & drop project DB
    mgr.drop_branch_schema("acme/billing", "feat/invoice-v2")
    mgr.drop_project_database("acme/billing")

    all_sql_after = [sql for _, sql, _ in executed_log]
    assert any(
        f'DROP SCHEMA IF EXISTS "{expected_schema}" CASCADE;' in s
        for s in all_sql_after
    )
    assert any(
        f'DROP DATABASE IF EXISTS "{expected_db}";' in s for s in all_sql_after
    )


# =====================================================================
# 5. FastMCP & REST RBAC Verification
# =====================================================================


@pytest.mark.asyncio
async def test_fastmcp_topology_tool_and_rest_rbac(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    # Provision a sample project & branch while auth is off
    assert client.post(
        "/master/projects",
        json={"project_id": "acme/billing", "name": "Billing"},
    ).status_code == 200
    assert client.post(
        "/master/projects/acme%2Fbilling/branches",
        json={"branch_name": "feat/rbac", "base_branch": "main"},
    ).status_code == 200

    # 5a. FastMCP tool returns 3-tier topology and is read-only for reader role
    assert _is_write_tool("projectbrain_database_topology") is False
    raw_mcp_out = await projectbrain_database_topology(project_id="acme/billing")
    mcp_topo = json.loads(raw_mcp_out)
    assert mcp_topo["status"] == "ok"
    assert mcp_topo["multi_db_enabled"] is True
    assert mcp_topo["master_database"]["project_count"] == 1
    assert mcp_topo["projects"][0]["project_id"] == "acme/billing"
    assert {b["branch_name"] for b in mcp_topo["projects"][0]["branches"]} == {
        "main",
        "feat/rbac",
    }

    # 5b. Enable PB_REQUIRE_REST_AUTH=true and create reader + collaborator accounts
    auth_routes.init_auth_system()
    res_reader = client.post(
        "/auth/accounts",
        json={
            "username": "topo_reader",
            "token": "pb_tok_topo_reader",
            "role": "reader",
            "allowed_tools": "*",
        },
    )
    assert res_reader.status_code == 200, res_reader.text
    res_collab = client.post(
        "/auth/accounts",
        json={
            "username": "topo_collab",
            "token": "pb_tok_topo_collab",
            "role": "collaborator",
            "allowed_tools": "*",
        },
    )
    assert res_collab.status_code == 200, res_collab.text

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    monkeypatch.setattr(env, "require_rest_auth", True)

    # Unauthenticated request -> 401
    assert client.get("/master/topology").status_code == 401
    assert (
        client.post("/master/projects", json={"project_id": "acme/unauth"}).status_code
        == 401
    )

    # Reader can GET /master/topology (200), but POST /master/projects -> 403
    reader_headers = {"Authorization": "Bearer pb_tok_topo_reader"}
    r_get = client.get("/master/topology", headers=reader_headers)
    assert r_get.status_code == 200, r_get.text
    r_post_reader = client.post(
        "/master/projects",
        json={"project_id": "acme/blocked"},
        headers=reader_headers,
    )
    assert r_post_reader.status_code == 403, r_post_reader.text

    # Collaborator can POST /master/projects (200)
    collab_headers = {"Authorization": "Bearer pb_tok_topo_collab"}
    r_post_collab = client.post(
        "/master/projects",
        json={"project_id": "acme/allowed", "name": "Allowed"},
        headers=collab_headers,
    )
    assert r_post_collab.status_code == 200, r_post_collab.text
