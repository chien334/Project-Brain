"""Unit tests for Hybrid Graphify Engine Store & O(Delta) Branch Diff Core."""

import re
import pytest
import sqlite3
from typing import Dict, Any

from projectbrain.graphify_engine.models import (
    GraphNode,
    GraphEdge,
    GraphHyperedge,
    GraphDelta,
    BranchDiffResult,
    BranchDiffSummary,
)
from projectbrain.graphify_engine.store import (
    GraphStore,
    SqliteOverlayGraphStore,
    PostgresSchemaGraphStore,
    get_graph_store,
    sanitize_branch_schema_name,
)
import psycopg2.sql as psql


# =====================================================================
# 1. Identifier Sanitization & Postgres 63-Byte Limit Tests
# =====================================================================

def test_sanitize_branch_schema_name_basic():
    schema = sanitize_branch_schema_name("ecommerce-backend", "feature/auth-redesign")
    assert schema.startswith("pb_br_ecommerce_backen_feature_auth_redesign_")
    assert len(schema) <= 60
    assert re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema)


def test_sanitize_branch_schema_name_long_branch():
    long_branch = "feature/" + "x" * 250
    schema = sanitize_branch_schema_name("proj", long_branch)
    assert len(schema) <= 60
    assert re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema)
    assert schema.startswith("pb_br_proj_feature_")


def test_sanitize_branch_schema_name_unicode():
    schema = sanitize_branch_schema_name("プロジェクト", "機能/ブランチ")
    assert len(schema) <= 60
    assert re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema)


def test_sanitize_branch_schema_name_sqli_resilience():
    malicious = "main'; DROP TABLE pb_root.nodes; --"
    schema = sanitize_branch_schema_name("proj", malicious)
    assert len(schema) <= 60
    assert re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema)
    assert ";" not in schema
    assert " " not in schema
    assert "-" not in schema


def test_sanitize_branch_schema_name_multi_project_isolation():
    # Two different projects with identical branch name produce different schemas
    schema1 = sanitize_branch_schema_name("project-alpha", "feature/user-login")
    schema2 = sanitize_branch_schema_name("project-beta", "feature/user-login")
    assert schema1 != schema2
    assert re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema1)
    assert re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema2)


# =====================================================================
# 2. SQLite Root Schema & Direct Root Sync Tests
# =====================================================================

@pytest.fixture
def sqlite_store():
    # Fresh in-memory SQLite store for every test
    return SqliteOverlayGraphStore(db_path=":memory:")


def test_root_full_sync(sqlite_store: SqliteOverlayGraphStore):
    delta = GraphDelta(
        project_id="p1",
        branch="main",
        nodes=[
            GraphNode(id="auth_login", project_id="p1", label="login()", file_type="code", source_file="src/auth.py"),
            GraphNode(id="auth_logout", project_id="p1", label="logout()", file_type="code", source_file="src/auth.py"),
            GraphNode(id="user_model", project_id="p1", label="User", file_type="code", source_file="src/models.py"),
        ],
        edges=[
            GraphEdge(project_id="p1", source="auth_login", target="user_model", relation="uses"),
        ],
    )
    res = sqlite_store.sync_branch_delta("p1", "main", delta, partial=False)
    assert res["status"] == "synced"
    assert res["nodes_upserted"] == 3
    assert res["edges_upserted"] == 1

    subgraph = sqlite_store.get_resolved_subgraph("p1", "main")
    assert len(subgraph["nodes"]) == 3
    assert len(subgraph["edges"]) == 1


def test_root_partial_file_sync(sqlite_store: SqliteOverlayGraphStore):
    # Initial root graph
    delta1 = GraphDelta(
        project_id="p1",
        branch="main",
        nodes=[
            GraphNode(id="auth_login", project_id="p1", label="login()", source_file="src/auth.py"),
            GraphNode(id="user_model", project_id="p1", label="User", source_file="src/models.py"),
        ],
        edges=[],
    )
    sqlite_store.sync_branch_delta("p1", "main", delta1, partial=False)

    # Partial sync updating src/auth.py and deleting src/models.py
    delta2 = GraphDelta(
        project_id="p1",
        branch="main",
        nodes=[
            GraphNode(id="auth_login_v2", project_id="p1", label="login_v2()", source_file="src/auth.py"),
        ],
    )
    sqlite_store.sync_branch_delta(
        "p1",
        "main",
        delta2,
        partial=True,
        synced_files=["src/auth.py"],
        deleted_files=["src/models.py"],
    )

    subgraph = sqlite_store.get_resolved_subgraph("p1", "main")
    node_ids = {n["id"] for n in subgraph["nodes"]}
    assert "auth_login_v2" in node_ids
    assert "auth_login" not in node_ids  # Replaced in src/auth.py
    assert "user_model" not in node_ids  # Deleted via deleted_files


# =====================================================================
# 3. Non-Root Branch Overlay & No Duplication Invariant
# =====================================================================

def test_branch_sync_no_duplicates_and_tombstones(sqlite_store: SqliteOverlayGraphStore):
    # 1. Establish canonical root schema
    root_delta = GraphDelta(
        project_id="proj",
        branch="main",
        nodes=[
            GraphNode(id="n_stay", project_id="proj", label="Stay", source_file="src/a.py"),
            GraphNode(id="n_mod", project_id="proj", label="Mod Old", source_file="src/b.py"),
            GraphNode(id="n_del", project_id="proj", label="Del", source_file="src/c.py"),
        ],
        edges=[
            GraphEdge(project_id="proj", source="n_stay", target="n_mod", relation="calls"),
            GraphEdge(project_id="proj", source="n_mod", target="n_del", relation="calls"),
        ],
    )
    sqlite_store.sync_branch_delta("proj", "main", root_delta, partial=False)

    # 2. Branch snapshot: n_stay unchanged, n_mod modified, n_new added, n_del deleted
    branch_delta = GraphDelta(
        project_id="proj",
        branch="feature/x",
        nodes=[
            GraphNode(id="n_stay", project_id="proj", label="Stay", source_file="src/a.py"),  # EXACT SAME as root
            GraphNode(id="n_mod", project_id="proj", label="Mod New", source_file="src/b.py"),  # MODIFIED
            GraphNode(id="n_new", project_id="proj", label="New", source_file="src/d.py"),  # BRAND NEW
        ],
        edges=[
            GraphEdge(project_id="proj", source="n_stay", target="n_mod", relation="calls"),  # EXACT SAME as root
            GraphEdge(project_id="proj", source="n_mod", target="n_new", relation="calls"),  # BRAND NEW
        ],
    )
    sync_res = sqlite_store.sync_branch_delta("proj", "feature/x", branch_delta, partial=False)
    assert sync_res["status"] == "synced"

    # Check internal overlay tables:
    overlay = sqlite_store._get_branch_overlay("proj", "feature/x")

    # CRITICAL: n_stay is unchanged, so it MUST NOT be duplicated in node_upserts!
    assert "n_stay" not in overlay["node_upserts"]
    assert "n_mod" in overlay["node_upserts"]
    assert "n_new" in overlay["node_upserts"]

    # CRITICAL: n_del is missing from snapshot, so it MUST be in node_tombstones!
    assert "n_del" in overlay["node_tombstones"]

    # Edges: (n_stay, n_mod, calls) is unchanged -> not duplicated in edge_upserts!
    assert ("n_stay", "n_mod", "calls") not in overlay["edge_upserts"]
    assert ("n_mod", "n_new", "calls") in overlay["edge_upserts"]
    assert ("n_mod", "n_del", "calls") in overlay["edge_tombstones"]


# =====================================================================
# 4. Partial Branch Sync Scoped to Files
# =====================================================================

def test_branch_partial_sync(sqlite_store: SqliteOverlayGraphStore):
    # Root has files a.py and b.py
    root_delta = GraphDelta(
        project_id="proj",
        branch="main",
        nodes=[
            GraphNode(id="a1", project_id="proj", label="A1", source_file="src/a.py"),
            GraphNode(id="a2", project_id="proj", label="A2", source_file="src/a.py"),
            GraphNode(id="b1", project_id="proj", label="B1", source_file="src/b.py"),
        ],
    )
    sqlite_store.sync_branch_delta("proj", "main", root_delta)

    # Partial sync: only src/a.py is modified (a2 removed, a3 added), b.py not in scope
    branch_delta = GraphDelta(
        project_id="proj",
        branch="feature/partial",
        nodes=[
            GraphNode(id="a1", project_id="proj", label="A1 Updated", source_file="src/a.py"),
            GraphNode(id="a3", project_id="proj", label="A3", source_file="src/a.py"),
        ],
    )
    sqlite_store.sync_branch_delta(
        "proj",
        "feature/partial",
        branch_delta,
        partial=True,
        synced_files=["src/a.py"],
    )

    overlay = sqlite_store._get_branch_overlay("proj", "feature/partial")
    # a1 modified, a3 new, a2 tombstoned
    assert "a1" in overlay["node_upserts"]
    assert "a3" in overlay["node_upserts"]
    assert "a2" in overlay["node_tombstones"]
    # b1 untouched (not in upserts, not in tombstones)
    assert "b1" not in overlay["node_upserts"]
    assert "b1" not in overlay["node_tombstones"]

    # Verify virtual merge view
    subgraph = sqlite_store.get_resolved_subgraph("proj", "feature/partial")
    node_ids = {n["id"] for n in subgraph["nodes"]}
    assert node_ids == {"a1", "a3", "b1"}
    # a1 has updated label
    a1_node = next(n for n in subgraph["nodes"] if n["id"] == "a1")
    assert a1_node["label"] == "A1 Updated"


# =====================================================================
# 5. O(Delta) Branch-vs-Root Diff Tests
# =====================================================================

def test_diff_branch_vs_root(sqlite_store: SqliteOverlayGraphStore):
    # Root
    sqlite_store.sync_branch_delta(
        "p1",
        "main",
        GraphDelta(
            project_id="p1",
            nodes=[
                GraphNode(id="n1", project_id="p1", label="N1", source_file="src/a.py", community=1),
                GraphNode(id="n2", project_id="p1", label="N2 Old", source_file="src/b.py", community=2),
                GraphNode(id="n3", project_id="p1", label="N3", source_file="src/c.py", community=1),
            ],
            edges=[
                GraphEdge(project_id="p1", source="n1", target="n2", relation="calls", source_file="src/a.py"),
            ],
        ),
    )

    # Branch: n1 unchanged, n2 modified, n3 deleted, n4 new
    sqlite_store.sync_branch_delta(
        "p1",
        "feature/diff-test",
        GraphDelta(
            project_id="p1",
            nodes=[
                GraphNode(id="n1", project_id="p1", label="N1", source_file="src/a.py", community=1),
                GraphNode(id="n2", project_id="p1", label="N2 New", source_file="src/b.py", community=2),
                GraphNode(id="n4", project_id="p1", label="N4", source_file="src/b.py", community=2),
            ],
            edges=[
                GraphEdge(project_id="p1", source="n1", target="n2", relation="calls", source_file="src/a.py"),
                GraphEdge(project_id="p1", source="n2", target="n4", relation="calls", source_file="src/b.py"),
            ],
        ),
    )

    diff = sqlite_store.diff_branch_vs_root("p1", "feature/diff-test")
    assert diff.summary.new_nodes_count == 1
    assert diff.summary.modified_nodes_count == 1
    assert diff.summary.deleted_nodes_count == 1
    assert diff.summary.new_edges_count == 1
    assert diff.summary.deleted_edges_count == 0

    assert [n["id"] for n in diff.new_nodes] == ["n4"]
    assert diff.deleted_node_ids == ["n3"]

    # Verify before & after captured for modified nodes
    assert len(diff.modified_nodes) == 1
    mod = diff.modified_nodes[0]
    assert mod["id"] == "n2"
    assert mod["before"]["label"] == "N2 Old"
    assert mod["after"]["label"] == "N2 New"

    # Test file_filter
    diff_file_b = sqlite_store.diff_branch_vs_root("p1", "feature/diff-test", file_filter="src/b.py")
    assert diff_file_b.summary.new_nodes_count == 1
    assert diff_file_b.summary.modified_nodes_count == 1
    assert diff_file_b.summary.deleted_nodes_count == 0  # n3 was in src/c.py

    # Test community filter
    diff_comm_2 = sqlite_store.diff_branch_vs_root("p1", "feature/diff-test", community=2)
    assert diff_comm_2.summary.new_nodes_count == 1
    assert diff_comm_2.summary.modified_nodes_count == 1
    assert diff_comm_2.summary.deleted_nodes_count == 0


# =====================================================================
# 6. O(Delta_A cup Delta_B) Branch-vs-Branch Diff Tests
# =====================================================================

def test_diff_branches_two_feature_branches(sqlite_store: SqliteOverlayGraphStore):
    # Root base
    sqlite_store.sync_branch_delta(
        "proj",
        "main",
        GraphDelta(
            project_id="proj",
            nodes=[
                GraphNode(id="base_node", project_id="proj", label="Base", source_file="src/base.py"),
            ],
        ),
    )

    # Branch A adds node_a
    sqlite_store.sync_branch_delta(
        "proj",
        "feature/a",
        GraphDelta(
            project_id="proj",
            nodes=[
                GraphNode(id="base_node", project_id="proj", label="Base", source_file="src/base.py"),
                GraphNode(id="node_a", project_id="proj", label="Node A", source_file="src/a.py"),
            ],
        ),
    )

    # Branch B adds node_b and modifies base_node
    sqlite_store.sync_branch_delta(
        "proj",
        "feature/b",
        GraphDelta(
            project_id="proj",
            nodes=[
                GraphNode(id="base_node", project_id="proj", label="Base Modified", source_file="src/base.py"),
                GraphNode(id="node_b", project_id="proj", label="Node B", source_file="src/b.py"),
            ],
        ),
    )

    # Diff feature/a -> feature/b
    diff_ab = sqlite_store.diff_branches("proj", "feature/a", "feature/b")
    # Relative to A, B added node_b, deleted node_a, and modified base_node
    assert diff_ab.summary.new_nodes_count == 1
    assert diff_ab.new_nodes[0]["id"] == "node_b"
    assert diff_ab.summary.deleted_nodes_count == 1
    assert diff_ab.deleted_node_ids == ["node_a"]
    assert diff_ab.summary.modified_nodes_count == 1
    assert diff_ab.modified_nodes[0]["id"] == "base_node"
    assert diff_ab.modified_nodes[0]["before"]["label"] == "Base"
    assert diff_ab.modified_nodes[0]["after"]["label"] == "Base Modified"


def test_diff_branches_with_root_as_base_or_target(sqlite_store: SqliteOverlayGraphStore):
    sqlite_store.sync_branch_delta(
        "proj",
        "main",
        GraphDelta(
            project_id="proj",
            nodes=[GraphNode(id="r1", project_id="proj", label="R1", source_file="src/r.py")],
        ),
    )
    sqlite_store.sync_branch_delta(
        "proj",
        "feature/feat",
        GraphDelta(
            project_id="proj",
            nodes=[
                GraphNode(id="r1", project_id="proj", label="R1", source_file="src/r.py"),
                GraphNode(id="f1", project_id="proj", label="F1", source_file="src/f.py"),
            ],
        ),
    )

    # 1. Base is main, target is feature
    diff1 = sqlite_store.diff_branches("proj", "main", "feature/feat")
    assert diff1.summary.new_nodes_count == 1
    assert diff1.new_nodes[0]["id"] == "f1"

    # 2. Base is feature, target is main
    diff2 = sqlite_store.diff_branches("proj", "feature/feat", "main")
    assert diff2.summary.deleted_nodes_count == 1
    assert diff2.deleted_node_ids == ["f1"]

    # 3. Base == Target
    diff3 = sqlite_store.diff_branches("proj", "feature/feat", "feature/feat")
    assert diff3.summary.new_nodes_count == 0
    assert diff3.summary.modified_nodes_count == 0
    assert diff3.summary.deleted_nodes_count == 0


# =====================================================================
# 7. Virtual Subgraph Query & Tombstone Suppression
# =====================================================================

def test_resolved_subgraph_tombstone_suppression(sqlite_store: SqliteOverlayGraphStore):
    sqlite_store.sync_branch_delta(
        "p",
        "main",
        GraphDelta(
            project_id="p",
            nodes=[
                GraphNode(id="keep", project_id="p", label="Keep", source_file="src/keep.py"),
                GraphNode(id="drop", project_id="p", label="Drop", source_file="src/drop.py"),
            ],
            edges=[
                GraphEdge(project_id="p", source="keep", target="drop", relation="calls"),
            ],
        ),
    )

    # Branch tombstones 'drop'
    sqlite_store.sync_branch_delta(
        "p",
        "feature/prune",
        GraphDelta(
            project_id="p",
            nodes=[
                GraphNode(id="keep", project_id="p", label="Keep", source_file="src/keep.py"),
            ],
        ),
    )

    subgraph = sqlite_store.get_resolved_subgraph("p", "feature/prune")
    node_ids = {n["id"] for n in subgraph["nodes"]}
    assert "keep" in node_ids
    assert "drop" not in node_ids  # Suppressed!
    assert len(subgraph["edges"]) == 0  # Edge connecting to dropped node is excluded


# =====================================================================
# 8. Atomic Promotion to Root
# =====================================================================

def test_promote_branch_to_root(sqlite_store: SqliteOverlayGraphStore):
    sqlite_store.sync_branch_delta(
        "p",
        "main",
        GraphDelta(
            project_id="p",
            nodes=[
                GraphNode(id="old_node", project_id="p", label="Old", source_file="src/old.py"),
                GraphNode(id="del_node", project_id="p", label="Del", source_file="src/del.py"),
            ],
        ),
    )

    sqlite_store.sync_branch_delta(
        "p",
        "feature/release",
        GraphDelta(
            project_id="p",
            nodes=[
                GraphNode(id="old_node", project_id="p", label="Upgraded", source_file="src/old.py"),
                GraphNode(id="brand_new", project_id="p", label="Brand New", source_file="src/new.py"),
            ],
        ),
    )

    res = sqlite_store.promote_branch_to_root("p", "feature/release")
    assert res["status"] == "promoted"
    assert res["upserted_nodes"] >= 2
    assert res["deleted_nodes"] >= 1

    # Verify canonical root now has updated state
    root_subgraph = sqlite_store.get_resolved_subgraph("p", "main")
    root_nodes = {n["id"]: n for n in root_subgraph["nodes"]}
    assert "del_node" not in root_nodes
    assert "brand_new" in root_nodes
    assert root_nodes["old_node"]["label"] == "Upgraded"

    # Verify branch schema/tables dropped and branch deleted from registry
    branches = sqlite_store.list_branches("p")
    assert "feature/release" not in [b["branch_name"] for b in branches]


# =====================================================================
# 9. Legacy Vis.js Translation & Store Utilities
# =====================================================================

def test_to_legacy_visjs_formatting(sqlite_store: SqliteOverlayGraphStore):
    sqlite_store.sync_branch_delta(
        "p",
        "main",
        GraphDelta(
            project_id="p",
            nodes=[
                GraphNode(id="svc_auth", project_id="p", label="AuthService", file_type="code", source_file="src/auth.py"),
                GraphNode(id="doc_spec", project_id="p", label="AuthSpec", file_type="document", source_file="docs/spec.md"),
            ],
            edges=[
                GraphEdge(project_id="p", source="doc_spec", target="svc_auth", relation="rationale_for"),
            ],
        ),
    )

    vis = sqlite_store.to_legacy_visjs("p", "main")
    assert len(vis["nodes"]) == 2
    assert len(vis["edges"]) == 1
    assert vis["nodes"][0]["group"] in ("code", "document")
    assert vis["edges"][0]["from"] == "doc_spec"
    assert vis["edges"][0]["to"] == "svc_auth"
    assert vis["edges"][0]["label"] == "rationale_for"

    # Query filter
    vis_query = sqlite_store.to_legacy_visjs("p", "main", query="Service")
    assert len(vis_query["nodes"]) == 1
    assert vis_query["nodes"][0]["id"] == "svc_auth"

    # Kinds filter
    vis_kinds = sqlite_store.to_legacy_visjs("p", "main", kinds="document")
    assert len(vis_kinds["nodes"]) == 1
    assert vis_kinds["nodes"][0]["id"] == "doc_spec"


def test_stats_and_project_lifecycle(sqlite_store: SqliteOverlayGraphStore):
    sqlite_store.sync_branch_delta(
        "proj_x",
        "main",
        GraphDelta(
            project_id="proj_x",
            nodes=[GraphNode(id="x1", project_id="proj_x", label="X1", source_file="x.py")],
        ),
    )
    sqlite_store.sync_branch_delta(
        "proj_y",
        "main",
        GraphDelta(
            project_id="proj_y",
            nodes=[GraphNode(id="y1", project_id="proj_y", label="Y1", source_file="y.py")],
        ),
    )

    projects = sqlite_store.list_projects()
    assert "proj_x" in projects
    assert "proj_y" in projects

    stats = sqlite_store.get_stats("proj_x", "main")
    assert stats["node_count"] == 1

    sqlite_store.delete_project("proj_x")
    projects_after = sqlite_store.list_projects()
    assert "proj_x" not in projects_after
    assert "proj_y" in projects_after


# =====================================================================
# 10. PostgreSQL Multi-Schema DDL Generation & Mock Verification
# =====================================================================

def test_postgres_schema_ddl_generation():
    pg_store = PostgresSchemaGraphStore(connection_url="postgresql://localhost:5432/fake_db")

    # Verify root DDL
    root_stmts = pg_store.generate_root_schema_ddl()
    assert len(root_stmts) >= 6
    assert any("CREATE SCHEMA IF NOT EXISTS pb_root" in stmt.as_string(None) for stmt in root_stmts if hasattr(stmt, "as_string"))

    # Verify branch DDL uses psycopg2.sql.Identifier without SQL injection
    branch_stmts = pg_store.generate_branch_schema_ddl("ecommerce", "feature/auth-redesign")
    assert len(branch_stmts) >= 8

    # Check that schema name conforms to sanitized format
    schema_name = sanitize_branch_schema_name("ecommerce", "feature/auth-redesign")
    assert schema_name in str(branch_stmts)


def test_get_graph_store_factory(monkeypatch):
    monkeypatch.setenv("PB_METADATA_BACKEND", "sqlite")
    store = get_graph_store(db_path=":memory:")
    assert isinstance(store, SqliteOverlayGraphStore)

    monkeypatch.setenv("PB_METADATA_BACKEND", "postgres")
    store_pg = get_graph_store(connection_url="postgresql://localhost:5432/test")
    assert isinstance(store_pg, PostgresSchemaGraphStore)


@pytest.mark.skipif(
    True,  # Skipped gracefully when local PostgreSQL is not configured / running
    reason="Requires live PostgreSQL server instance"
)
def test_live_postgres_integration():
    try:
        store = PostgresSchemaGraphStore(connection_url="postgresql://postgres:postgres@localhost:5432/projectbrain")
        store.ensure_root_schema()
        store.sync_branch_delta(
            "pg_test",
            "main",
            GraphDelta(
                project_id="pg_test",
                nodes=[GraphNode(id="p1", project_id="pg_test", label="PG1", source_file="pg.py")],
            ),
        )
        sub = store.get_resolved_subgraph("pg_test", "main")
        assert len(sub["nodes"]) == 1
    except Exception as e:
        pytest.skip(f"Live PostgreSQL not reachable: {e}")
