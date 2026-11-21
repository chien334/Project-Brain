"""
Unit tests for 3-Tier Hybrid Knowledge Base Resolver & Local-vs-Server Diff Engine.

Tests:
- 3-Tier Precedence (local > branch > root)
- Tombstone suppression (nodes & edges)
- Automatic stale-symbol pruning on locally edited files
- Scoped partial local-vs-remote diff in O(Delta_local)
- Hybrid traversals: BFS/DFS query, path tracing, impact radius, callers/callees
- Degree centrality god nodes & Leiden community retrieval
- Remote HTTP client mocking with Bearer token & scoped file queries
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from projectbrain.graphify_engine.hybrid_resolver import (
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
    HybridResolver,
)


class MockGraphStore:
    """Deterministic in-memory mock of GraphStore for testing hybrid resolution."""

    def __init__(
        self,
        root_nodes: Optional[List[Dict[str, Any]]] = None,
        root_edges: Optional[List[Dict[str, Any]]] = None,
        branch_nodes: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        branch_edges: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        branch_tombstones: Optional[Dict[str, List[str]]] = None,
    ):
        self.root_nodes = root_nodes or []
        self.root_edges = root_edges or []
        self.branch_nodes = branch_nodes or {}
        self.branch_edges = branch_edges or {}
        self.branch_tombstones = branch_tombstones or {}
        self.last_query_files: Optional[List[str]] = None

    def get_resolved_subgraph(
        self,
        project_id: str,
        branch_name: str = "main",
        files: Optional[List[str]] = None,
        limit: int = 10000,
    ) -> Dict[str, Any]:
        """Simulate PostgreSQL/SQLite resolved views (overlay over root)."""
        self.last_query_files = files

        # 1. Base root entities
        nodes_dict: Dict[str, Dict[str, Any]] = {
            n["id"]: {**n, "tier": "root"} for n in self.root_nodes
        }
        edges_dict: Dict[tuple, Dict[str, Any]] = {
            (e["source"], e["target"], e.get("relation", "calls")): {**e, "tier": "root"}
            for e in self.root_edges
        }

        # 2. Apply branch overlay if not main
        if branch_name != "main":
            # Suppress branch tombstones
            tombstones = set(self.branch_tombstones.get(branch_name, []))
            for tid in tombstones:
                nodes_dict.pop(tid, None)

            # Upsert branch nodes
            for bn in self.branch_nodes.get(branch_name, []):
                nodes_dict[bn["id"]] = {**bn, "tier": "branch"}

            # Upsert branch edges
            for be in self.branch_edges.get(branch_name, []):
                key = (be["source"], be["target"], be.get("relation", "calls"))
                edges_dict[key] = {**be, "tier": "branch"}

        # 3. Filter by files if specified
        if files:
            files_set = {f.lstrip("./") for f in files}
            nodes_dict = {
                nid: n for nid, n in nodes_dict.items()
                if (n.get("source_file") or "").lstrip("./") in files_set
            }
            edges_dict = {
                ek: e for ek, e in edges_dict.items()
                if (e.get("source_file") or "").lstrip("./") in files_set
            }

        return {
            "project_id": project_id,
            "branch": branch_name,
            "nodes": list(nodes_dict.values())[:limit],
            "edges": list(edges_dict.values())[:limit],
            "hyperedges": [],
        }


# ===========================================================================
# 1. Local Graph Ingestion Tests
# ===========================================================================
def test_load_local_graph_delta_format(tmp_path: Path):
    """Test loading native GraphDelta JSON format."""
    graph_file = tmp_path / "partial_graph.json"
    payload = {
        "project_id": "test-p1",
        "branch": "feat-auth",
        "nodes": [
            {
                "id": "auth_login",
                "label": "auth_login",
                "file_type": "code",
                "source_file": "src/auth.py",
                "source_location": "src/auth.py:42",
                "community": 1,
                "metadata": {"docstring": "Authenticates user session."},
            }
        ],
        "edges": [
            {
                "source": "auth_login",
                "target": "user_repo_find",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            }
        ],
        "hyperedges": [
            {
                "id": "h_auth_flow",
                "label": "Auth Flow",
                "nodes": ["auth_login", "user_repo_find"],
                "relation": "orchestrates",
            }
        ],
        "node_tombstones": ["legacy_login"],
        "edge_tombstones": [{"source": "auth_login", "target": "old_db", "relation": "calls"}],
        "synced_files": [{"file_path": "src/auth.py", "sha256": "abcdef123"}],
    }
    graph_file.write_text(json.dumps(payload), encoding="utf-8")

    resolver = HybridResolver(store=MockGraphStore())
    delta = resolver.load_local_graph(local_path=str(graph_file))

    assert delta is not None
    assert delta.project_id == "test-p1"
    assert delta.branch == "feat-auth"
    assert len(delta.nodes) == 1
    assert delta.nodes[0].id == "auth_login"
    assert delta.nodes[0].metadata["docstring"] == "Authenticates user session."
    assert len(delta.edges) == 1
    assert delta.edges[0].relation == "calls"
    assert len(delta.hyperedges) == 1
    assert delta.hyperedges[0].id == "h_auth_flow"
    assert delta.node_tombstones == ["legacy_login"]
    assert len(delta.edge_tombstones) == 1
    assert delta.edge_tombstones[0]["target"] == "old_db"


def test_load_local_graph_graphify_format(tmp_path: Path):
    """Test loading standard graphify output format (`graphify-out/graph.json` with `links`)."""
    graph_file = tmp_path / "graph.json"
    payload = {
        "directed": True,
        "multigraph": False,
        "graph": {
            "hyperedges": [
                {
                    "id": "sys_pipeline",
                    "label": "System Pipeline",
                    "nodes": ["step1", "step2"],
                    "relation": "pipeline",
                }
            ]
        },
        "nodes": [
            {
                "id": "step1",
                "label": "IngestStep",
                "file_type": "code",
                "source_file": "src/pipeline.py",
                "community": 2,
            },
            {
                "id": "step2",
                "label": "ProcessStep",
                "file_type": "code",
                "source_file": "src/pipeline.py",
                "community": 2,
            },
        ],
        "links": [
            {
                "source": "step1",
                "target": "step2",
                "relation": "pipes_into",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            }
        ],
    }
    graph_file.write_text(json.dumps(payload), encoding="utf-8")

    resolver = HybridResolver(store=MockGraphStore())
    delta = resolver.load_local_graph(local_path=str(graph_file), project_id="proj-graphify")

    assert delta is not None
    assert delta.project_id == "proj-graphify"
    assert len(delta.nodes) == 2
    assert len(delta.edges) == 1
    assert delta.edges[0].relation == "pipes_into"
    assert len(delta.hyperedges) == 1
    # Auto-derived synced_files
    assert len(delta.synced_files) == 1
    assert delta.synced_files[0]["file_path"] == "src/pipeline.py"


def test_load_local_graph_nonexistent():
    """Test returns None when file does not exist."""
    resolver = HybridResolver(store=MockGraphStore())
    assert resolver.load_local_graph(local_path="/path/does/not/exist/partial.json") is None


# ===========================================================================
# 2. 3-Tier Precedence & Resolution Tests
# ===========================================================================
def test_3_tier_precedence(tmp_path: Path):
    """
    Test 3-Tier Precedence:
    - `service_core` exists in root, overridden in branch, and overridden in local -> Local wins!
    - `auth_helper` exists in root and branch -> Branch wins!
    - `db_pool` exists only in root -> Root wins!
    """
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "service_core", "label": "ServiceCore v1 (Root)", "source_file": "src/core.py"},
            {"id": "auth_helper", "label": "AuthHelper v1 (Root)", "source_file": "src/auth.py"},
            {"id": "db_pool", "label": "DBPool (Root)", "source_file": "src/db.py"},
        ],
        root_edges=[
            {"source": "service_core", "target": "db_pool", "relation": "uses"},
        ],
        branch_nodes={
            "feature-x": [
                {"id": "service_core", "label": "ServiceCore v2 (Branch)", "source_file": "src/core.py"},
                {"id": "auth_helper", "label": "AuthHelper v2 (Branch)", "source_file": "src/auth.py"},
            ]
        },
        branch_edges={
            "feature-x": [
                {"source": "service_core", "target": "auth_helper", "relation": "calls"},
            ]
        },
    )

    # Local Tier 1 modifies service_core
    local_file = tmp_path / "local.json"
    local_file.write_text(json.dumps({
        "project_id": "test-p",
        "branch": "feature-x",
        "nodes": [
            {
                "id": "service_core",
                "label": "ServiceCore v3 (Local Uncommitted)",
                "source_file": "src/core.py",
                "source_location": "src/core.py:10",
            }
        ],
        "edges": [
            {"source": "service_core", "target": "db_pool", "relation": "uses_locally"},
        ],
        "synced_files": ["src/core.py"],
    }), encoding="utf-8")

    resolver = HybridResolver(store=mock_store)
    g = resolver.resolve_hybrid_graph(
        project_id="test-p",
        branch="feature-x",
        local_path=str(local_file),
    )

    # Assert service_core: Local wins
    assert g.has_node("service_core")
    assert g.nodes["service_core"]["tier"] == "local"
    assert g.nodes["service_core"]["label"] == "ServiceCore v3 (Local Uncommitted)"

    # Assert auth_helper: Branch wins
    assert g.has_node("auth_helper")
    assert g.nodes["auth_helper"]["tier"] == "branch"
    assert g.nodes["auth_helper"]["label"] == "AuthHelper v2 (Branch)"

    # Assert db_pool: Root wins
    assert g.has_node("db_pool")
    assert g.nodes["db_pool"]["tier"] == "root"
    assert g.nodes["db_pool"]["label"] == "DBPool (Root)"


# ===========================================================================
# 3. Tombstone Suppression & Stale Symbol Pruning
# ===========================================================================
def test_tombstone_suppression(tmp_path: Path):
    """Test explicit tombstones in local graph hide server root & branch entities."""
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "legacy_api", "label": "Legacy API", "source_file": "src/api.py"},
            {"id": "active_client", "label": "Active Client", "source_file": "src/client.py"},
        ],
        root_edges=[
            {"source": "active_client", "target": "legacy_api", "relation": "calls"},
        ],
    )

    local_file = tmp_path / "local_tombstone.json"
    local_file.write_text(json.dumps({
        "project_id": "test-p",
        "branch": "main",
        "nodes": [],
        "edges": [],
        "node_tombstones": ["legacy_api"],
        "edge_tombstones": [{"source": "active_client", "target": "legacy_api", "relation": "calls"}],
    }), encoding="utf-8")

    resolver = HybridResolver(store=mock_store)
    g = resolver.resolve_hybrid_graph(
        project_id="test-p",
        branch="main",
        local_path=str(local_file),
    )

    # legacy_api must be suppressed
    assert not g.has_node("legacy_api")
    assert not g.has_edge("active_client", "legacy_api")
    # active_client remains
    assert g.has_node("active_client")


def test_stale_symbol_pruning_on_locally_edited_files(tmp_path: Path):
    """
    Test automatic stale symbol pruning:
    Server has `fn_a`, `fn_b`, `fn_c` in `src/service.py`.
    Local workspace edits `src/service.py` and removes `fn_c`.
    The hybrid resolver must prune `fn_c` automatically without requiring an explicit tombstone.
    """
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "fn_a", "label": "fn_a", "source_file": "src/service.py"},
            {"id": "fn_b", "label": "fn_b", "source_file": "src/service.py"},
            {"id": "fn_c", "label": "fn_c (stale)", "source_file": "src/service.py"},
            {"id": "other_fn", "label": "other_fn", "source_file": "src/other.py"},
        ],
        root_edges=[
            {"source": "fn_a", "target": "fn_c", "relation": "calls", "source_file": "src/service.py"},
            {"source": "other_fn", "target": "fn_b", "relation": "calls", "source_file": "src/other.py"},
        ],
    )

    local_file = tmp_path / "local_stale.json"
    local_file.write_text(json.dumps({
        "project_id": "test-p",
        "branch": "main",
        "nodes": [
            {"id": "fn_a", "label": "fn_a (modified)", "source_file": "src/service.py"},
            {"id": "fn_b", "label": "fn_b (modified)", "source_file": "src/service.py"},
        ],
        "edges": [],
        "synced_files": ["src/service.py"],
    }), encoding="utf-8")

    resolver = HybridResolver(store=mock_store)
    g = resolver.resolve_hybrid_graph(
        project_id="test-p",
        branch="main",
        local_path=str(local_file),
    )

    # fn_a and fn_b present as local
    assert g.has_node("fn_a")
    assert g.nodes["fn_a"]["tier"] == "local"
    assert g.has_node("fn_b")
    assert g.nodes["fn_b"]["tier"] == "local"

    # fn_c was pruned!
    assert not g.has_node("fn_c")
    assert not g.has_edge("fn_a", "fn_c")

    # other_fn untouched in other file remains root
    assert g.has_node("other_fn")
    assert g.nodes["other_fn"]["tier"] == "root"


# ===========================================================================
# 4. Scoped Local-vs-Remote Diff Engine Tests (O(Delta_local))
# ===========================================================================
def test_diff_local_vs_remote_scoped(tmp_path: Path):
    """
    Test diff_local_vs_remote:
    - Scoped comparison queries server only for files touched in local_graph
    - Detects added nodes, modified nodes (with before/after), deleted nodes, added edges, deleted edges
    """
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "login", "label": "login_v1", "source_file": "src/auth.py", "source_location": "src/auth.py:10"},
            {"id": "old_token", "label": "old_token", "source_file": "src/auth.py"},
            {"id": "math_calc", "label": "math_calc", "source_file": "src/math.py"},
        ],
        root_edges=[
            {"source": "login", "target": "old_token", "relation": "generates", "source_file": "src/auth.py"},
        ],
    )

    local_file = tmp_path / "diff_local.json"
    local_file.write_text(json.dumps({
        "project_id": "proj-diff",
        "branch": "main",
        "nodes": [
            {
                "id": "login",
                "label": "login_v2",
                "source_file": "src/auth.py",
                "source_location": "src/auth.py:25",
            },
            {
                "id": "new_jwt",
                "label": "new_jwt",
                "file_type": "code",
                "source_file": "src/auth.py",
            },
        ],
        "edges": [
            {
                "source": "login",
                "target": "new_jwt",
                "relation": "generates",
                "source_file": "src/auth.py",
            }
        ],
        "synced_files": ["src/auth.py"],
    }), encoding="utf-8")

    resolver = HybridResolver(store=mock_store)
    diff = resolver.diff_local_vs_remote(
        project_id="proj-diff",
        branch="main",
        local_path=str(local_file),
        scoped_only=True,
    )

    # 1. Assert only touched file was fetched from server (O(Delta) scoped verification)
    assert mock_store.last_query_files == ["src/auth.py"]

    # 2. Assert stats
    stats = diff["stats"]
    assert stats["added_nodes_count"] == 1  # new_jwt
    assert stats["modified_nodes_count"] == 1  # login
    assert stats["deleted_nodes_count"] == 1  # old_token (pruned stale)
    assert stats["added_edges_count"] == 1  # login -> new_jwt
    assert stats["deleted_edges_count"] == 1  # login -> old_token
    assert stats["touched_files_count"] == 1

    # 3. Assert modified node before/after detail
    mod = diff["modified_nodes"][0]
    assert mod["id"] == "login"
    assert "label" in mod["changed_fields"]
    assert "source_location" in mod["changed_fields"]
    assert mod["before"]["label"] == "login_v1"
    assert mod["after"]["label"] == "login_v2"

    # 4. Assert human readable summary
    assert "Diff:" in diff["summary"]
    assert "+1 added" in diff["summary"]
    assert "~1 modified" in diff["summary"]
    assert "-1 deleted" in diff["summary"]


# ===========================================================================
# 5. Hybrid Traversal & Query Tests (BFS, DFS, Confidence, Budget)
# ===========================================================================
def test_hybrid_query_traversal(tmp_path: Path):
    """
    Test hybrid BFS & DFS query:
    - Traverses across tiers (local function calling server root repository)
    - Verifies tier breakdown
    - Verifies confidence threshold filtering
    - Verifies token budget truncation
    """
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "UserRepo", "label": "UserRepository", "file_type": "code", "source_file": "src/db/repo.py"},
            {"id": "SQLDatabase", "label": "SQLDatabase", "file_type": "code", "source_file": "src/db/sql.py"},
        ],
        root_edges=[
            {"source": "UserRepo", "target": "SQLDatabase", "relation": "queries", "confidence": "EXTRACTED"},
        ],
    )

    # Local uncommitted controller calling server UserRepo
    local_file = tmp_path / "local_query.json"
    local_file.write_text(json.dumps({
        "project_id": "proj-query",
        "branch": "main",
        "nodes": [
            {
                "id": "AuthController",
                "label": "AuthController",
                "file_type": "code",
                "source_file": "src/auth/controller.py",
                "source_location": "src/auth/controller.py:15",
                "metadata": {"docstring": "Handles authentication requests."},
            }
        ],
        "edges": [
            {
                "source": "AuthController",
                "target": "UserRepo",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            },
            {
                "source": "AuthController",
                "target": "SQLDatabase",
                "relation": "speculative_cache",
                "confidence": "AMBIGUOUS",
                "confidence_score": 0.2,
            },
        ],
        "synced_files": ["src/auth/controller.py"],
    }), encoding="utf-8")

    resolver = HybridResolver(store=mock_store)

    # 1. BFS Query with min_confidence="EXTRACTED"
    res_bfs = resolver.query(
        project_id="proj-query",
        query_text="auth",
        mode="bfs",
        min_confidence="EXTRACTED",
        local_path=str(local_file),
    )

    assert len(res_bfs["nodes"]) >= 2
    assert res_bfs["tier_breakdown"]["local"] >= 1  # AuthController
    assert res_bfs["tier_breakdown"]["root"] >= 1  # UserRepo

    # Ambiguous edge should NOT be traversed under EXTRACTED filter
    edge_rels = [e["relation"] for e in res_bfs["edges"]]
    assert "calls" in edge_rels
    assert "speculative_cache" not in edge_rels

    # Markdown context check
    assert "# Hybrid Knowledge Base Context" in res_bfs["context_markdown"]
    assert "AuthController" in res_bfs["context_markdown"]

    # 2. DFS Query with min_confidence="AMBIGUOUS" (includes speculative edge)
    res_dfs = resolver.query(
        project_id="proj-query",
        query_text="auth",
        mode="dfs",
        min_confidence="AMBIGUOUS",
        local_path=str(local_file),
    )
    dfs_rels = [e["relation"] for e in res_dfs["edges"]]
    assert "calls" in dfs_rels
    assert "speculative_cache" in dfs_rels

    # 3. Budget Truncation Check
    res_budget = resolver.query(
        project_id="proj-query",
        query_text="auth",
        budget=15,  # Tiny budget
        local_path=str(local_file),
    )
    assert "[Context truncated" in res_budget["context_markdown"]


# ===========================================================================
# 6. Trace Path (Shortest Path Across Tiers)
# ===========================================================================
def test_trace_path_directed_and_undirected():
    """Test shortest path tracing with directed path and undirected fallback."""
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "node_a", "label": "Node A"},
            {"id": "node_b", "label": "Node B"},
            {"id": "node_c", "label": "Node C"},
            {"id": "island_x", "label": "Island X"},
            {"id": "island_y", "label": "Island Y"},
        ],
        root_edges=[
            {"source": "node_a", "target": "node_b", "relation": "calls"},
            {"source": "node_b", "target": "node_c", "relation": "calls"},
            # Undirected semantic bridge
            {"source": "island_x", "target": "node_c", "relation": "cites"},
        ],
    )

    resolver = HybridResolver(store=mock_store)

    # 1. Directed shortest path
    hops_directed = resolver.trace_path("test-p", "node_a", "node_c")
    assert len(hops_directed) == 2
    assert hops_directed[0]["source"] == "node_a"
    assert hops_directed[0]["target"] == "node_b"
    assert hops_directed[1]["source"] == "node_b"
    assert hops_directed[1]["target"] == "node_c"
    assert hops_directed[0]["directed_path"] is True

    # 2. Undirected fallback (island_x -> node_c -> node_b)
    hops_undirected = resolver.trace_path("test-p", "island_x", "node_b")
    assert len(hops_undirected) == 2
    assert hops_undirected[0]["directed_path"] is False

    # 3. Nonexistent path
    assert resolver.trace_path("test-p", "node_a", "island_y") == []


# ===========================================================================
# 7. Impact Radius & Ego-Subgraph Analysis
# ===========================================================================
def test_get_impact_radius():
    """Test blast radius calculation up to max_hops."""
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "caller1", "label": "Caller 1"},
            {"id": "caller2", "label": "Caller 2"},
            {"id": "target_service", "label": "Target Service"},
            {"id": "dep1", "label": "Dependency 1"},
            {"id": "sub_dep", "label": "Sub Dependency"},
        ],
        root_edges=[
            {"source": "caller1", "target": "target_service", "relation": "calls"},
            {"source": "caller2", "target": "caller1", "relation": "calls"},
            {"source": "target_service", "target": "dep1", "relation": "calls"},
            {"source": "dep1", "target": "sub_dep", "relation": "queries"},
        ],
    )

    resolver = HybridResolver(store=mock_store)
    impact = resolver.get_impact_radius("test-p", "target_service", max_hops=2)

    assert impact["found"] is True
    assert impact["max_hops"] == 2
    up_ids = [u["node_id"] for u in impact["upstream_dependents"]]
    down_ids = [d["node_id"] for d in impact["downstream_dependencies"]]

    assert "caller1" in up_ids
    assert "caller2" in up_ids
    assert "dep1" in down_ids
    assert "sub_dep" in down_ids
    assert impact["affected_nodes_count"] == 5


# ===========================================================================
# 8. Callers, Callees, Node Details, God Nodes, Community
# ===========================================================================
def test_callers_callees_and_node_details():
    """Test direct callers and callees navigation and node details."""
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "A", "label": "Node A"},
            {"id": "B", "label": "Node B"},
            {"id": "C", "label": "Node C"},
        ],
        root_edges=[
            {"source": "A", "target": "B", "relation": "calls"},
            {"source": "B", "target": "C", "relation": "calls"},
        ],
    )

    resolver = HybridResolver(store=mock_store)
    callers = resolver.get_callers("test-p", "B")
    callees = resolver.get_callees("test-p", "B")

    assert len(callers) == 1
    assert callers[0]["node_id"] == "A"
    assert len(callees) == 1
    assert callees[0]["node_id"] == "C"

    details = resolver.get_node_details("test-p", "B")
    assert details is not None
    assert details["id"] == "B"
    assert details["in_degree"] == 1
    assert details["out_degree"] == 1
    assert details["callers_count"] == 1
    assert details["callees_count"] == 1


def test_get_god_nodes_and_community():
    """Test god node identification via centrality and Leiden community retrieval."""
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "hub", "label": "Central Hub", "community": 5},
            {"id": "spoke1", "label": "Spoke 1", "community": 5},
            {"id": "spoke2", "label": "Spoke 2", "community": 5},
            {"id": "spoke3", "label": "Spoke 3", "community": 9},
        ],
        root_edges=[
            {"source": "spoke1", "target": "hub", "relation": "calls"},
            {"source": "spoke2", "target": "hub", "relation": "calls"},
            {"source": "spoke3", "target": "hub", "relation": "calls"},
        ],
    )

    resolver = HybridResolver(store=mock_store)

    gods = resolver.get_god_nodes("test-p", top_k=2)
    assert len(gods) >= 1
    assert gods[0]["id"] == "hub"
    assert gods[0]["in_degree"] == 3

    comm = resolver.get_community("test-p", community_id=5)
    assert comm["community_id"] == 5
    assert comm["nodes_count"] == 3
    comm_node_ids = {n["id"] for n in comm["nodes"]}
    assert comm_node_ids == {"hub", "spoke1", "spoke2"}


# ===========================================================================
# 9. Remote HTTP Subgraph Client Mocking
# ===========================================================================
def test_remote_http_subgraph_client():
    """Test HybridResolver when configured with remote_url & api_token."""
    resolver = HybridResolver(
        remote_url="http://remote-brain:8080",
        api_token="pb_test_token_secret",
    )

    fake_response_data = {
        "nodes": [
            {"id": "remote_entry", "label": "Remote Entry", "source_file": "src/api.py", "tier": "root"}
        ],
        "edges": [],
        "hyperedges": [],
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_response_data
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        g = resolver.resolve_hybrid_graph(
            project_id="remote-proj",
            branch="main",
            files=["src/api.py"],
        )

        assert mock_get.called
        call_args = mock_get.call_args
        assert "http://remote-brain:8080/graphify/subgraph" in call_args[0]
        assert call_args[1]["params"]["project_id"] == "remote-proj"
        assert call_args[1]["params"]["branch"] == "main"
        assert call_args[1]["params"]["files"] == "src/api.py"
        assert call_args[1]["headers"]["Authorization"] == "Bearer pb_test_token_secret"

        assert g.has_node("remote_entry")
        assert g.nodes["remote_entry"]["label"] == "Remote Entry"


def test_diff_local_vs_remote_no_local_graph():
    """Test diff_local_vs_remote when local file does not exist."""
    resolver = HybridResolver(store=MockGraphStore())
    diff = resolver.diff_local_vs_remote("test-p", "main", local_path="/nonexistent/path.json")
    assert diff["stats"]["added_nodes_count"] == 0
    assert "No local graph found" in diff["summary"]


def test_diff_local_vs_remote_unscoped_and_tombstones(tmp_path: Path):
    """Test diff_local_vs_remote when scoped_only=False and explicit tombstones exist."""
    mock_store = MockGraphStore(
        root_nodes=[
            {"id": "node_x", "label": "Node X", "source_file": "src/x.py"},
            {"id": "node_y", "label": "Node Y", "source_file": "src/y.py"},
        ],
        root_edges=[
            {"source": "node_x", "target": "node_y", "relation": "calls"},
        ],
    )

    local_file = tmp_path / "local_diff_tomb.json"
    local_file.write_text(json.dumps({
        "project_id": "test-p",
        "branch": "main",
        "nodes": [],
        "edges": [],
        "node_tombstones": ["node_x"],
        "edge_tombstones": [{"source": "node_x", "target": "node_y", "relation": "calls"}],
        "synced_files": ["src/x.py"],
    }), encoding="utf-8")

    resolver = HybridResolver(store=mock_store)

    # 1. scoped_only=False
    diff = resolver.diff_local_vs_remote(
        project_id="test-p",
        branch="main",
        local_path=str(local_file),
        scoped_only=False,
    )
    assert mock_store.last_query_files is None  # Full graph queried
    assert any(n["id"] == "node_x" for n in diff["deleted_nodes"])
    assert any(e["source"] == "node_x" and e["target"] == "node_y" for e in diff["deleted_edges"])


def test_empty_graph_edge_cases():
    """Test edge cases for empty graph across query, impact radius, callers, and god nodes."""
    resolver = HybridResolver(store=MockGraphStore())

    # 1. Query on empty graph
    q_res = resolver.query("empty-p", "anything", local_path="/nonexistent.json")
    assert len(q_res["nodes"]) == 0
    assert "No entities found" in q_res["context_markdown"]

    # 2. Impact radius on nonexistent node
    impact = resolver.get_impact_radius("empty-p", "nonexistent_node")
    assert impact["found"] is False
    assert impact["affected_nodes_count"] == 0

    # 3. Callers and callees on nonexistent node
    assert resolver.get_callers("empty-p", "nonexistent_node") == []
    assert resolver.get_callees("empty-p", "nonexistent_node") == []
    assert resolver.get_node_details("empty-p", "nonexistent_node") is None

    # 4. God nodes on empty graph
    assert resolver.get_god_nodes("empty-p") == []

