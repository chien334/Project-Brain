"""PostgreSQL Multi-Schema & SQLite Overlay GraphStore Core for Hybrid Graphify Engine.

Provides O(Delta) branch overlays, dynamic virtual views, and atomic branch promotion
with composite multi-project isolation and PostgreSQL 63-byte identifier safety.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.sql as psql

from .models import (
    BranchDiffResult,
    BranchDiffSummary,
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)

logger = logging.getLogger("graphify_store")
logger.setLevel(logging.INFO)

# Re-export models for convenient imports from store
__all__ = [
    "GraphNode",
    "GraphEdge",
    "GraphHyperedge",
    "GraphDelta",
    "BranchDiffSummary",
    "BranchDiffResult",
    "sanitize_branch_schema_name",
    "GraphStore",
    "PostgresSchemaGraphStore",
    "SqliteOverlayGraphStore",
    "get_graph_store",
]


def sanitize_branch_schema_name(project_id: str, branch_name: str) -> str:
    """Compute a deterministic, sanitized PostgreSQL schema identifier <= 60 bytes.

    Format: pb_br_{clean_proj[:16]}_{clean_branch[:28]}_{hash8}
    Guarantees regex conformance: ^[a-z_][a-z0-9_]{0,62}$ and prevents SQL injection.
    """
    clean_proj = re.sub(r"[^a-z0-9_]", "_", str(project_id).lower()).strip("_")
    if not clean_proj:
        clean_proj = "proj"
    clean_proj = clean_proj[:16].rstrip("_")
    if not clean_proj:
        clean_proj = "proj"

    clean_branch = re.sub(r"[^a-z0-9_]", "_", str(branch_name).lower()).strip("_")
    if not clean_branch:
        clean_branch = "branch"
    clean_branch = clean_branch[:28].rstrip("_")
    if not clean_branch:
        clean_branch = "branch"

    hash_material = f"{project_id}:{branch_name}".encode("utf-8")
    hash8 = hashlib.sha256(hash_material).hexdigest()[:8]

    schema_name = f"pb_br_{clean_proj}_{clean_branch}_{hash8}"
    if len(schema_name.encode("utf-8")) > 60:
        schema_name = schema_name[:60]

    pattern = r"^[a-z_][a-z0-9_]{0,62}$"
    if not re.match(pattern, schema_name):
        raise ValueError(f"Sanitized schema name '{schema_name}' violates identifier rules ({pattern})")
    return schema_name


class GraphStore(ABC):
    """Abstract Base Class for ProjectBrain Graph Storage backends."""

    def __init__(self, root_branch: str = "main"):
        self.root_branch = root_branch or "main"

    def is_root_branch(self, branch_name: Optional[str]) -> bool:
        """Check whether branch_name targets the canonical root schema."""
        if not branch_name:
            return True
        return branch_name.strip() == self.root_branch

    @abstractmethod
    def ensure_root_schema(self) -> None:
        """Create root schema and base tables (pb_root or root_*) if not existing."""
        pass

    @abstractmethod
    def ensure_branch_schema(self, project_id: str, branch_name: str, author: str = "system") -> str:
        """Create and register branch schema overlay. Returns schema name."""
        pass

    @abstractmethod
    def sync_branch_delta(
        self,
        project_id: str,
        branch_name: str,
        delta: GraphDelta,
        partial: bool = False,
        synced_files: Optional[List[str]] = None,
        deleted_files: Optional[List[str]] = None,
        author: str = "system",
    ) -> Dict[str, Any]:
        """Apply full or partial graph delta into the target branch overlay schema."""
        pass

    @abstractmethod
    def diff_branch_vs_root(
        self,
        project_id: str,
        branch_name: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        """O(Delta) structural diff between branch overlay and base root schema."""
        pass

    @abstractmethod
    def diff_branches(
        self,
        project_id: str,
        base_branch: str,
        target_branch: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        """O(Delta_A cup Delta_B) diff comparing two branch overlays."""
        pass

    @abstractmethod
    def promote_branch_to_root(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        """Atomically merge branch overlay mutations into canonical root schema and drop schema."""
        pass

    @abstractmethod
    def get_resolved_subgraph(
        self,
        project_id: str,
        branch_name: str,
        files: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Query resolved virtual graph (overlay over root) with optional file filter."""
        pass

    @abstractmethod
    def get_stats(self, project_id: str, branch_name: str = "main") -> Dict[str, Any]:
        """Return graph node, edge, and delta counts for a project branch."""
        pass

    @abstractmethod
    def list_projects(self) -> List[str]:
        """List all distinct projects present in graph storage."""
        pass

    @abstractmethod
    def delete_project(self, project_id: str) -> bool:
        """Delete all root data and active branch overlays for a project."""
        pass

    @abstractmethod
    def delete_branch(self, project_id: str, branch_name: str) -> bool:
        """Drop branch overlay schema and remove registration record."""
        pass

    @abstractmethod
    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """List active branch overlays for a project."""
        pass

    @abstractmethod
    def to_legacy_visjs(
        self,
        project_id: str,
        branch_name: str = "main",
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Format resolved graph into Vis.js compatible {nodes, edges} payload."""
        pass

    # Common unified diff helper methods shared across backends
    @abstractmethod
    def _get_branch_overlay(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        """Retrieve branch overlay dictionaries: node_upserts, node_tombstones, etc."""
        pass

    @abstractmethod
    def _get_root_nodes_by_ids(self, project_id: str, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch subset of canonical root nodes by ID."""
        pass

    @abstractmethod
    def _get_root_edges_by_keys(
        self, project_id: str, edge_keys: List[Tuple[str, str, str]]
    ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        """Fetch subset of canonical root edges by (source, target, relation)."""
        pass

    def _diff_branches_core(
        self,
        project_id: str,
        base_branch: str,
        target_branch: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        """Unified O(Delta_A cup Delta_B) branch comparison algorithm."""
        if base_branch == target_branch:
            return BranchDiffResult(summary=BranchDiffSummary())

        if self.is_root_branch(base_branch):
            return self.diff_branch_vs_root(project_id, target_branch, file_filter, community)

        if self.is_root_branch(target_branch):
            # Target is root, Base is feature branch.
            base_diff = self.diff_branch_vs_root(project_id, base_branch, file_filter, community)
            restored_nodes_dict = self._get_root_nodes_by_ids(project_id, base_diff.deleted_node_ids)
            restored_nodes = list(restored_nodes_dict.values())
            deleted_node_ids = [n["id"] for n in base_diff.new_nodes]

            modified_nodes = []
            for m in base_diff.modified_nodes:
                modified_nodes.append({
                    "id": m["id"],
                    "label": m.get("before", {}).get("label") if m.get("before") else m.get("label"),
                    "file_type": m.get("before", {}).get("file_type") if m.get("before") else m.get("file_type"),
                    "source_file": m.get("before", {}).get("source_file") if m.get("before") else m.get("source_file"),
                    "source_location": m.get("before", {}).get("source_location") if m.get("before") else m.get("source_location"),
                    "community": m.get("before", {}).get("community") if m.get("before") else m.get("community"),
                    "metadata": m.get("before", {}).get("metadata") if m.get("before") else m.get("metadata"),
                    "before": m.get("after"),
                    "after": m.get("before"),
                })

            del_edge_keys = [(e["source"], e["target"], e["relation"]) for e in base_diff.deleted_edges]
            restored_edges_dict = self._get_root_edges_by_keys(project_id, del_edge_keys)
            restored_edges = list(restored_edges_dict.values())
            deleted_edges = [
                {"project_id": project_id, "source": e["source"], "target": e["target"], "relation": e["relation"]}
                for e in base_diff.new_edges
            ]

            summary = BranchDiffSummary(
                new_nodes_count=len(restored_nodes),
                modified_nodes_count=len(modified_nodes),
                deleted_nodes_count=len(deleted_node_ids),
                new_edges_count=len(restored_edges),
                deleted_edges_count=len(deleted_edges),
            )
            return BranchDiffResult(
                summary=summary,
                new_nodes=restored_nodes,
                modified_nodes=modified_nodes,
                deleted_node_ids=deleted_node_ids,
                new_edges=restored_edges,
                deleted_edges=deleted_edges,
            )

        # Both base and target are non-root branches: O(Delta_A cup Delta_B)
        overlay_a = self._get_branch_overlay(project_id, base_branch)
        overlay_b = self._get_branch_overlay(project_id, target_branch)

        affected_node_ids = (
            set(overlay_a["node_upserts"].keys())
            | set(overlay_a["node_tombstones"])
            | set(overlay_b["node_upserts"].keys())
            | set(overlay_b["node_tombstones"])
        )

        needed_from_root_nodes: Set[str] = set()
        for nid in affected_node_ids:
            in_a = (nid in overlay_a["node_upserts"]) or (nid in overlay_a["node_tombstones"])
            in_b = (nid in overlay_b["node_upserts"]) or (nid in overlay_b["node_tombstones"])
            if not in_a or not in_b:
                needed_from_root_nodes.add(nid)

        root_nodes = self._get_root_nodes_by_ids(project_id, list(needed_from_root_nodes)) if needed_from_root_nodes else {}

        new_nodes: List[Dict[str, Any]] = []
        modified_nodes: List[Dict[str, Any]] = []
        deleted_node_ids: List[str] = []

        for nid in affected_node_ids:
            if nid in overlay_a["node_tombstones"]:
                state_a = None
            elif nid in overlay_a["node_upserts"]:
                state_a = overlay_a["node_upserts"][nid]
            else:
                state_a = root_nodes.get(nid)

            if nid in overlay_b["node_tombstones"]:
                state_b = None
            elif nid in overlay_b["node_upserts"]:
                state_b = overlay_b["node_upserts"][nid]
            else:
                state_b = root_nodes.get(nid)

            if state_a is None and state_b is not None:
                new_nodes.append(state_b)
            elif state_a is not None and state_b is None:
                deleted_node_ids.append(nid)
            elif state_a is not None and state_b is not None:
                attrs = ["label", "file_type", "source_file", "source_location", "community", "metadata"]
                if any(state_a.get(k) != state_b.get(k) for k in attrs):
                    modified_nodes.append({
                        "id": nid,
                        "label": state_b.get("label"),
                        "file_type": state_b.get("file_type"),
                        "source_file": state_b.get("source_file"),
                        "source_location": state_b.get("source_location"),
                        "community": state_b.get("community"),
                        "metadata": state_b.get("metadata"),
                        "before": state_a,
                        "after": state_b,
                    })

        affected_edge_keys = (
            set(overlay_a["edge_upserts"].keys())
            | set(overlay_a["edge_tombstones"])
            | set(overlay_b["edge_upserts"].keys())
            | set(overlay_b["edge_tombstones"])
        )

        needed_from_root_edges: Set[Tuple[str, str, str]] = set()
        for ekey in affected_edge_keys:
            in_a = (ekey in overlay_a["edge_upserts"]) or (ekey in overlay_a["edge_tombstones"])
            in_b = (ekey in overlay_b["edge_upserts"]) or (ekey in overlay_b["edge_tombstones"])
            if not in_a or not in_b:
                needed_from_root_edges.add(ekey)

        root_edges = self._get_root_edges_by_keys(project_id, list(needed_from_root_edges)) if needed_from_root_edges else {}

        new_edges: List[Dict[str, Any]] = []
        deleted_edges: List[Dict[str, Any]] = []

        for ekey in affected_edge_keys:
            if ekey in overlay_a["edge_tombstones"]:
                edge_a = None
            elif ekey in overlay_a["edge_upserts"]:
                edge_a = overlay_a["edge_upserts"][ekey]
            else:
                edge_a = root_edges.get(ekey)

            if ekey in overlay_b["edge_tombstones"]:
                edge_b = None
            elif ekey in overlay_b["edge_upserts"]:
                edge_b = overlay_b["edge_upserts"][ekey]
            else:
                edge_b = root_edges.get(ekey)

            if edge_a is None and edge_b is not None:
                new_edges.append(edge_b)
            elif edge_a is not None and edge_b is None:
                deleted_edges.append({"project_id": project_id, "source": ekey[0], "target": ekey[1], "relation": ekey[2]})
            elif edge_a is not None and edge_b is not None:
                attrs = ["confidence", "confidence_score", "source_file", "source_location", "weight"]
                if any(edge_a.get(k) != edge_b.get(k) for k in attrs):
                    new_edges.append(edge_b)

        # Filters
        if file_filter:
            new_nodes = [n for n in new_nodes if n.get("source_file", "").startswith(file_filter)]
            modified_nodes = [n for n in modified_nodes if n.get("source_file", "").startswith(file_filter)]
            # Deleted nodes check
            del_lookup = self._get_root_nodes_by_ids(project_id, deleted_node_ids)
            del_lookup.update({nid: n for nid, n in overlay_a["node_upserts"].items() if nid in deleted_node_ids})
            deleted_node_ids = [nid for nid in deleted_node_ids if nid in del_lookup and del_lookup[nid].get("source_file", "").startswith(file_filter)]
            new_edges = [e for e in new_edges if e.get("source_file", "").startswith(file_filter)]
            deleted_edges = [
                e for e in deleted_edges
                if (e.get("source_file", "").startswith(file_filter) or e.get("source") in deleted_node_ids or e.get("target") in deleted_node_ids)
            ]

        if community is not None:
            new_nodes = [n for n in new_nodes if n.get("community") == community]
            modified_nodes = [n for n in modified_nodes if n.get("community") == community or (n.get("before") and n["before"].get("community") == community)]
            del_lookup = self._get_root_nodes_by_ids(project_id, deleted_node_ids)
            del_lookup.update({nid: n for nid, n in overlay_a["node_upserts"].items() if nid in deleted_node_ids})
            deleted_node_ids = [nid for nid in deleted_node_ids if nid in del_lookup and del_lookup[nid].get("community") == community]

        summary = BranchDiffSummary(
            new_nodes_count=len(new_nodes),
            modified_nodes_count=len(modified_nodes),
            deleted_nodes_count=len(deleted_node_ids),
            new_edges_count=len(new_edges),
            deleted_edges_count=len(deleted_edges),
        )
        return BranchDiffResult(
            summary=summary,
            new_nodes=new_nodes,
            modified_nodes=modified_nodes,
            deleted_node_ids=deleted_node_ids,
            new_edges=new_edges,
            deleted_edges=deleted_edges,
        )

    def _format_legacy_visjs(
        self,
        subgraph: Dict[str, Any],
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        raw_nodes = subgraph.get("nodes", [])
        raw_edges = subgraph.get("edges", [])

        if query:
            q = query.lower()
            raw_nodes = [n for n in raw_nodes if q in n["id"].lower() or q in n.get("label", "").lower()]

        if kinds:
            kind_set = {k.strip().lower() for k in kinds.split(",") if k.strip()}
            raw_nodes = [n for n in raw_nodes if (n.get("file_type") or "code").lower() in kind_set]

        selected_nodes = raw_nodes[:limit]
        node_id_set = {n["id"] for n in selected_nodes}

        vis_nodes = []
        for n in selected_nodes:
            vis_nodes.append({
                "id": n["id"],
                "label": n.get("label", n["id"]),
                "title": f"{n.get('label', n['id'])} ({n.get('source_file', '')})",
                "group": n.get("file_type") or "code",
                "source_file": n.get("source_file"),
                "community": n.get("community"),
                "metadata": n.get("metadata", {}),
            })

        vis_edges = []
        for e in raw_edges:
            if e["source"] in node_id_set and e["target"] in node_id_set:
                vis_edges.append({
                    "id": f"{e['source']}->{e['target']}:{e['relation']}",
                    "from": e["source"],
                    "to": e["target"],
                    "label": e.get("relation", ""),
                    "relation": e.get("relation", ""),
                    "confidence": e.get("confidence", "EXTRACTED"),
                    "confidence_score": e.get("confidence_score", 1.0),
                    "weight": e.get("weight", 1.0),
                })

        return {
            "nodes": vis_nodes,
            "edges": vis_edges,
            "total_nodes": len(vis_nodes),
            "total_edges": len(vis_edges),
        }


# =====================================================================
# SQLite Overlay GraphStore Implementation
# =====================================================================

class SqliteOverlayGraphStore(GraphStore):
    """SQLite-backed GraphStore implementing overlay delta tables, dynamic views,

    and branch promotion for offline local execution and test determinism.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
        root_branch: str = "main",
    ):
        super().__init__(root_branch=root_branch)
        if db_path is None:
            db_path = os.getenv("OM_DB_PATH") or os.getenv("PB_DB_PATH") or None
        self.db_path = db_path
        self._external_conn = conn
        self._conn = conn
        if not self._conn:
            self._init_connection()
        self.ensure_root_schema()

    def _init_connection(self):
        path = self.db_path or ":memory:"
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")

    def get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._init_connection()
        return self._conn

    def ensure_root_schema(self) -> None:
        conn = self.get_conn()
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS root_nodes (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                file_type TEXT NOT NULL DEFAULT 'code',
                source_file TEXT NOT NULL,
                source_location TEXT,
                community INTEGER,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_root_nodes_proj ON root_nodes(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_root_nodes_file ON root_nodes(project_id, source_file)")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS root_edges (
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'EXTRACTED',
                confidence_score REAL NOT NULL DEFAULT 1.0,
                source_file TEXT,
                source_location TEXT,
                weight REAL DEFAULT 1.0,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, source, target, relation)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_root_edges_src ON root_edges(project_id, source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_root_edges_tgt ON root_edges(project_id, target)")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS root_hyperedges (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                nodes TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'EXTRACTED',
                confidence_score REAL NOT NULL DEFAULT 1.0,
                source_file TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, id)
            )""")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS graph_branches (
                project_id TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                schema_name TEXT NOT NULL,
                base_branch TEXT DEFAULT 'main',
                author TEXT DEFAULT 'system',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, branch_name)
            )""")

    def ensure_branch_schema(self, project_id: str, branch_name: str, author: str = "system") -> str:
        self.ensure_root_schema()
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        clean_proj = project_id.replace("'", "''")
        conn = self.get_conn()

        with conn:
            # Upsert registry record
            conn.execute(
                """
                INSERT INTO graph_branches (project_id, branch_name, schema_name, base_branch, author, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT (project_id, branch_name) DO UPDATE SET
                    schema_name = excluded.schema_name,
                    updated_at = datetime('now')
                """,
                (project_id, branch_name, schema_name, self.root_branch, author),
            )

            # Overlay tables for delta storage
            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_node_upserts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                file_type TEXT NOT NULL DEFAULT 'code',
                source_file TEXT NOT NULL,
                source_location TEXT,
                community INTEGER,
                metadata TEXT DEFAULT '{{}}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, id)
            )""")

            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_node_tombstones (
                project_id TEXT NOT NULL,
                id TEXT NOT NULL,
                deleted_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, id)
            )""")

            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_edge_upserts (
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'EXTRACTED',
                confidence_score REAL NOT NULL DEFAULT 1.0,
                source_file TEXT,
                source_location TEXT,
                weight REAL DEFAULT 1.0,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, source, target, relation)
            )""")

            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_edge_tombstones (
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                deleted_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, source, target, relation)
            )""")

            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_hyperedge_upserts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                nodes TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'EXTRACTED',
                confidence_score REAL NOT NULL DEFAULT 1.0,
                source_file TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, id)
            )""")

            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_hyperedge_tombstones (
                project_id TEXT NOT NULL,
                id TEXT NOT NULL,
                deleted_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, id)
            )""")

            conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {schema_name}_synced_files (
                project_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                sha256 TEXT,
                synced_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, file_path)
            )""")

            # Dynamic Resolved Views
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_nodes")
            conn.execute(f"""
            CREATE VIEW {schema_name}_v_resolved_nodes AS
            SELECT * FROM {schema_name}_node_upserts
            UNION ALL
            SELECT r.* FROM root_nodes r
            WHERE r.project_id = '{clean_proj}'
              AND r.id NOT IN (SELECT id FROM {schema_name}_node_tombstones WHERE project_id = '{clean_proj}')
              AND r.id NOT IN (SELECT id FROM {schema_name}_node_upserts WHERE project_id = '{clean_proj}')
            """)

            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_edges")
            conn.execute(f"""
            CREATE VIEW {schema_name}_v_resolved_edges AS
            SELECT * FROM {schema_name}_edge_upserts
            UNION ALL
            SELECT r.* FROM root_edges r
            WHERE r.project_id = '{clean_proj}'
              AND (r.source, r.target, r.relation) NOT IN (
                  SELECT source, target, relation FROM {schema_name}_edge_tombstones WHERE project_id = '{clean_proj}'
              )
              AND (r.source, r.target, r.relation) NOT IN (
                  SELECT source, target, relation FROM {schema_name}_edge_upserts WHERE project_id = '{clean_proj}'
              )
            """)

            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_hyperedges")
            conn.execute(f"""
            CREATE VIEW {schema_name}_v_resolved_hyperedges AS
            SELECT * FROM {schema_name}_hyperedge_upserts
            UNION ALL
            SELECT r.* FROM root_hyperedges r
            WHERE r.project_id = '{clean_proj}'
              AND r.id NOT IN (SELECT id FROM {schema_name}_hyperedge_tombstones WHERE project_id = '{clean_proj}')
              AND r.id NOT IN (SELECT id FROM {schema_name}_hyperedge_upserts WHERE project_id = '{clean_proj}')
            """)

        return schema_name

    def sync_branch_delta(
        self,
        project_id: str,
        branch_name: str,
        delta: GraphDelta,
        partial: bool = False,
        synced_files: Optional[List[str]] = None,
        deleted_files: Optional[List[str]] = None,
        author: str = "system",
    ) -> Dict[str, Any]:
        self.ensure_root_schema()
        conn = self.get_conn()

        if self.is_root_branch(branch_name):
            # Direct application into root schema
            return self._sync_to_root(project_id, delta, partial, synced_files, deleted_files)

        schema_name = self.ensure_branch_schema(project_id, branch_name, author)

        with conn:
            if not partial:
                # Full sync on branch: compute delta relative to root
                return self._sync_branch_full(conn, project_id, branch_name, schema_name, delta)
            else:
                # Partial sync on branch: scoped to synced_files & deleted_files
                return self._sync_branch_partial(
                    conn, project_id, branch_name, schema_name, delta, synced_files, deleted_files
                )

    def _sync_to_root(
        self,
        project_id: str,
        delta: GraphDelta,
        partial: bool,
        synced_files: Optional[List[str]],
        deleted_files: Optional[List[str]],
    ) -> Dict[str, Any]:
        conn = self.get_conn()
        nodes_upserted = 0
        nodes_deleted = 0
        edges_upserted = 0
        edges_deleted = 0
        hyperedges_upserted = 0
        hyperedges_deleted = 0

        with conn:
            if not partial:
                # Full replacement for this project_id on root
                conn.execute("DELETE FROM root_nodes WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM root_edges WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM root_hyperedges WHERE project_id = ?", (project_id,))
            else:
                # Handle deleted files
                del_files = deleted_files or []
                if del_files:
                    q_ph = ",".join("?" for _ in del_files)
                    c_n = conn.execute(
                        f"DELETE FROM root_nodes WHERE project_id = ? AND source_file IN ({q_ph})",
                        [project_id] + del_files,
                    )
                    nodes_deleted += c_n.rowcount
                    c_e = conn.execute(
                        f"DELETE FROM root_edges WHERE project_id = ? AND source_file IN ({q_ph})",
                        [project_id] + del_files,
                    )
                    edges_deleted += c_e.rowcount

                # Handle synced files: remove stale records for those files
                sync_files = synced_files or [
                    f["file_path"] if isinstance(f, dict) else f for f in delta.synced_files
                ]
                if sync_files:
                    q_ph = ",".join("?" for _ in sync_files)
                    conn.execute(
                        f"DELETE FROM root_nodes WHERE project_id = ? AND source_file IN ({q_ph})",
                        [project_id] + sync_files,
                    )
                    conn.execute(
                        f"DELETE FROM root_edges WHERE project_id = ? AND source_file IN ({q_ph})",
                        [project_id] + sync_files,
                    )

                # Explicit tombstones
                if delta.node_tombstones:
                    q_ph = ",".join("?" for _ in delta.node_tombstones)
                    c = conn.execute(
                        f"DELETE FROM root_nodes WHERE project_id = ? AND id IN ({q_ph})",
                        [project_id] + delta.node_tombstones,
                    )
                    nodes_deleted += c.rowcount

                for et in delta.edge_tombstones:
                    c = conn.execute(
                        "DELETE FROM root_edges WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                        (project_id, et["source"], et["target"], et["relation"]),
                    )
                    edges_deleted += c.rowcount

            # Upsert nodes
            for n in delta.nodes:
                meta_json = json.dumps(n.metadata) if isinstance(n.metadata, dict) else str(n.metadata or "{}")
                conn.execute(
                    """
                    INSERT INTO root_nodes (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = excluded.label,
                        file_type = excluded.file_type,
                        source_file = excluded.source_file,
                        source_location = excluded.source_location,
                        community = excluded.community,
                        metadata = excluded.metadata,
                        updated_at = datetime('now')
                    """,
                    (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                )
                nodes_upserted += 1

            # Upsert edges
            for e in delta.edges:
                conn.execute(
                    """
                    INSERT INTO root_edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = excluded.confidence,
                        confidence_score = excluded.confidence_score,
                        source_file = excluded.source_file,
                        source_location = excluded.source_location,
                        weight = excluded.weight
                    """,
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                )
                edges_upserted += 1

            # Upsert hyperedges
            for h in delta.hyperedges:
                nodes_json = json.dumps(h.nodes) if isinstance(h.nodes, list) else str(h.nodes)
                conn.execute(
                    """
                    INSERT INTO root_hyperedges (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = excluded.label,
                        nodes = excluded.nodes,
                        relation = excluded.relation,
                        confidence = excluded.confidence,
                        confidence_score = excluded.confidence_score,
                        source_file = excluded.source_file
                    """,
                    (h.id, project_id, h.label, nodes_json, h.relation, h.confidence, h.confidence_score, h.source_file),
                )
                hyperedges_upserted += 1

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": self.root_branch,
            "schema_name": "root",
            "nodes_upserted": nodes_upserted,
            "nodes_deleted": nodes_deleted,
            "edges_upserted": edges_upserted,
            "edges_deleted": edges_deleted,
            "hyperedges_upserted": hyperedges_upserted,
            "hyperedges_deleted": hyperedges_deleted,
            "partial": partial,
        }

    def _sync_branch_full(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        branch_name: str,
        schema_name: str,
        delta: GraphDelta,
    ) -> Dict[str, Any]:
        # Fetch root state
        root_nodes_rows = conn.execute("SELECT * FROM root_nodes WHERE project_id = ?", (project_id,)).fetchall()
        root_nodes = {r["id"]: dict(r) for r in root_nodes_rows}
        for r in root_nodes.values():
            if isinstance(r["metadata"], str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except Exception:
                    pass

        root_edges_rows = conn.execute("SELECT * FROM root_edges WHERE project_id = ?", (project_id,)).fetchall()
        root_edges = {(r["source"], r["target"], r["relation"]): dict(r) for r in root_edges_rows}

        # Clear existing branch overlay
        conn.execute(f"DELETE FROM {schema_name}_node_upserts WHERE project_id = ?", (project_id,))
        conn.execute(f"DELETE FROM {schema_name}_node_tombstones WHERE project_id = ?", (project_id,))
        conn.execute(f"DELETE FROM {schema_name}_edge_upserts WHERE project_id = ?", (project_id,))
        conn.execute(f"DELETE FROM {schema_name}_edge_tombstones WHERE project_id = ?", (project_id,))
        conn.execute(f"DELETE FROM {schema_name}_hyperedge_upserts WHERE project_id = ?", (project_id,))
        conn.execute(f"DELETE FROM {schema_name}_hyperedge_tombstones WHERE project_id = ?", (project_id,))
        conn.execute(f"DELETE FROM {schema_name}_synced_files WHERE project_id = ?", (project_id,))

        nodes_upserted = 0
        nodes_tombstoned = 0
        edges_upserted = 0
        edges_tombstoned = 0

        delta_node_ids = set()
        for n in delta.nodes:
            delta_node_ids.add(n.id)
            meta_dict = n.metadata if isinstance(n.metadata, dict) else {}
            meta_json = json.dumps(meta_dict)

            if n.id not in root_nodes:
                # Brand new node
                conn.execute(
                    f"""
                    INSERT INTO {schema_name}_node_upserts
                    (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                )
                nodes_upserted += 1
            else:
                # Compare attributes with root
                r = root_nodes[n.id]
                differs = (
                    r["label"] != n.label
                    or r["file_type"] != n.file_type
                    or r["source_file"] != n.source_file
                    or (r["source_location"] or None) != (n.source_location or None)
                    or r["community"] != n.community
                    or r["metadata"] != meta_dict
                )
                if differs:
                    conn.execute(
                        f"""
                        INSERT INTO {schema_name}_node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """,
                        (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                    )
                    nodes_upserted += 1

        # Root nodes missing from branch snapshot become tombstones
        for rid in root_nodes:
            if rid not in delta_node_ids:
                conn.execute(
                    f"INSERT INTO {schema_name}_node_tombstones (project_id, id) VALUES (?, ?)",
                    (project_id, rid),
                )
                nodes_tombstoned += 1

        # Edge comparison
        delta_edge_keys = set()
        for e in delta.edges:
            ekey = (e.source, e.target, e.relation)
            delta_edge_keys.add(ekey)

            if ekey not in root_edges:
                conn.execute(
                    f"""
                    INSERT INTO {schema_name}_edge_upserts
                    (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                )
                edges_upserted += 1
            else:
                r = root_edges[ekey]
                differs = (
                    r["confidence"] != e.confidence
                    or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                    or (r["source_file"] or None) != (e.source_file or None)
                    or (r["source_location"] or None) != (e.source_location or None)
                    or abs(r["weight"] - e.weight) > 1e-4
                )
                if differs:
                    conn.execute(
                        f"""
                        INSERT INTO {schema_name}_edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                    )
                    edges_upserted += 1

        # Missing root edges become tombstones
        for ekey in root_edges:
            if ekey not in delta_edge_keys:
                conn.execute(
                    f"INSERT INTO {schema_name}_edge_tombstones (project_id, source, target, relation) VALUES (?, ?, ?, ?)",
                    (project_id, ekey[0], ekey[1], ekey[2]),
                )
                edges_tombstoned += 1

        for htid in delta.hyperedge_tombstones:
            conn.execute(f"DELETE FROM {schema_name}_hyperedge_upserts WHERE project_id = ? AND id = ?", (project_id, htid))
            conn.execute(f"INSERT OR IGNORE INTO {schema_name}_hyperedge_tombstones (project_id, id) VALUES (?, ?)", (project_id, htid))

        for h in delta.hyperedges:
            conn.execute(f"DELETE FROM {schema_name}_hyperedge_tombstones WHERE project_id = ? AND id = ?", (project_id, h.id))
            nodes_val = json.dumps(h.nodes) if not isinstance(h.nodes, str) else h.nodes
            conn.execute(
                f"""
                INSERT INTO {schema_name}_hyperedge_upserts
                (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, id) DO UPDATE SET
                    label = excluded.label,
                    nodes = excluded.nodes,
                    relation = excluded.relation,
                    confidence = excluded.confidence,
                    confidence_score = excluded.confidence_score,
                    source_file = excluded.source_file
                """,
                (h.id, project_id, h.label, nodes_val, h.relation, h.confidence, h.confidence_score, h.source_file),
            )

        # Synced files record
        for sf in delta.synced_files:
            fp = sf.get("file_path") if isinstance(sf, dict) else str(sf)
            sh = sf.get("sha256", "") if isinstance(sf, dict) else ""
            if fp:
                conn.execute(
                    f"INSERT OR REPLACE INTO {schema_name}_synced_files (project_id, file_path, sha256) VALUES (?, ?, ?)",
                    (project_id, fp, sh),
                )

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": branch_name,
            "schema_name": schema_name,
            "nodes_upserted": nodes_upserted,
            "nodes_tombstoned": nodes_tombstoned,
            "edges_upserted": edges_upserted,
            "edges_tombstoned": edges_tombstoned,
            "partial": False,
        }

    def _sync_branch_partial(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        branch_name: str,
        schema_name: str,
        delta: GraphDelta,
        synced_files: Optional[List[str]],
        deleted_files: Optional[List[str]],
    ) -> Dict[str, Any]:
        synced_file_set: Set[str] = set(synced_files or [
            sf["file_path"] if isinstance(sf, dict) else sf for sf in delta.synced_files
        ])
        if not synced_file_set:
            synced_file_set = {n.source_file for n in delta.nodes if n.source_file}

        deleted_file_set: Set[str] = set(deleted_files or [])
        nodes_upserted = 0
        nodes_tombstoned = 0
        edges_upserted = 0
        edges_tombstoned = 0

        # 1. Process deleted files
        for fpath in deleted_file_set:
            # Drop previous branch upserts for this file
            conn.execute(f"DELETE FROM {schema_name}_node_upserts WHERE project_id = ? AND source_file = ?", (project_id, fpath))
            conn.execute(f"DELETE FROM {schema_name}_edge_upserts WHERE project_id = ? AND source_file = ?", (project_id, fpath))

            # Tombstone all root nodes belonging to this file
            root_nodes = conn.execute("SELECT id FROM root_nodes WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
            for r in root_nodes:
                conn.execute(
                    f"INSERT OR IGNORE INTO {schema_name}_node_tombstones (project_id, id) VALUES (?, ?)",
                    (project_id, r["id"]),
                )
                nodes_tombstoned += 1

            # Tombstone all root edges belonging to this file
            root_edges = conn.execute("SELECT source, target, relation FROM root_edges WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
            for r in root_edges:
                conn.execute(
                    f"INSERT OR IGNORE INTO {schema_name}_edge_tombstones (project_id, source, target, relation) VALUES (?, ?, ?, ?)",
                    (project_id, r["source"], r["target"], r["relation"]),
                )
                edges_tombstoned += 1

            conn.execute(f"DELETE FROM {schema_name}_synced_files WHERE project_id = ? AND file_path = ?", (project_id, fpath))

        # 2. Process synced files
        for fpath in synced_file_set:
            # Clear previous branch upserts for this file
            conn.execute(f"DELETE FROM {schema_name}_node_upserts WHERE project_id = ? AND source_file = ?", (project_id, fpath))
            conn.execute(f"DELETE FROM {schema_name}_edge_upserts WHERE project_id = ? AND source_file = ?", (project_id, fpath))

            root_nodes_rows = conn.execute("SELECT * FROM root_nodes WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
            root_nodes = {r["id"]: dict(r) for r in root_nodes_rows}
            for r in root_nodes.values():
                if isinstance(r["metadata"], str):
                    try:
                        r["metadata"] = json.loads(r["metadata"])
                    except Exception:
                        pass

            root_edges_rows = conn.execute("SELECT * FROM root_edges WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
            root_edges = {(r["source"], r["target"], r["relation"]): dict(r) for r in root_edges_rows}

            # File scoped delta entities
            delta_file_nodes = [n for n in delta.nodes if n.source_file == fpath]
            delta_node_ids = {n.id for n in delta_file_nodes}

            # Any root node in this file missing from delta is tombstoned
            for rid in root_nodes:
                if rid not in delta_node_ids:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {schema_name}_node_tombstones (project_id, id) VALUES (?, ?)",
                        (project_id, rid),
                    )
                    nodes_tombstoned += 1
                else:
                    # In delta: ensure tombstone removed
                    conn.execute(f"DELETE FROM {schema_name}_node_tombstones WHERE project_id = ? AND id = ?", (project_id, rid))

            for n in delta_file_nodes:
                meta_dict = n.metadata if isinstance(n.metadata, dict) else {}
                meta_json = json.dumps(meta_dict)
                conn.execute(f"DELETE FROM {schema_name}_node_tombstones WHERE project_id = ? AND id = ?", (project_id, n.id))

                if n.id not in root_nodes:
                    conn.execute(
                        f"""
                        INSERT INTO {schema_name}_node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """,
                        (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                    )
                    nodes_upserted += 1
                else:
                    r = root_nodes[n.id]
                    differs = (
                        r["label"] != n.label
                        or r["file_type"] != n.file_type
                        or (r["source_location"] or None) != (n.source_location or None)
                        or r["community"] != n.community
                        or r["metadata"] != meta_dict
                    )
                    if differs:
                        conn.execute(
                            f"""
                            INSERT INTO {schema_name}_node_upserts
                            (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            """,
                            (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                        )
                        nodes_upserted += 1

            # Edge file scoping
            delta_file_edges = [e for e in delta.edges if e.source_file == fpath]
            delta_edge_keys = {(e.source, e.target, e.relation) for e in delta_file_edges}

            for ekey in root_edges:
                if ekey not in delta_edge_keys:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {schema_name}_edge_tombstones (project_id, source, target, relation) VALUES (?, ?, ?, ?)",
                        (project_id, ekey[0], ekey[1], ekey[2]),
                    )
                    edges_tombstoned += 1
                else:
                    conn.execute(
                        f"DELETE FROM {schema_name}_edge_tombstones WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                        (project_id, ekey[0], ekey[1], ekey[2]),
                    )

            for e in delta_file_edges:
                ekey = (e.source, e.target, e.relation)
                conn.execute(
                    f"DELETE FROM {schema_name}_edge_tombstones WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                    (project_id, e.source, e.target, e.relation),
                )
                if ekey not in root_edges:
                    conn.execute(
                        f"""
                        INSERT INTO {schema_name}_edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                    )
                    edges_upserted += 1
                else:
                    r = root_edges[ekey]
                    differs = (
                        r["confidence"] != e.confidence
                        or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                        or (r["source_location"] or None) != (e.source_location or None)
                        or abs(r["weight"] - e.weight) > 1e-4
                    )
                    if differs:
                        conn.execute(
                            f"""
                            INSERT INTO {schema_name}_edge_upserts
                            (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                        )
                        edges_upserted += 1

            # Registry update
            conn.execute(
                f"INSERT OR REPLACE INTO {schema_name}_synced_files (project_id, file_path, sha256) VALUES (?, ?, ?)",
                (project_id, fpath, ""),
            )

        # 3. Explicit tombstones from delta
        for tid in delta.node_tombstones:
            conn.execute(f"DELETE FROM {schema_name}_node_upserts WHERE project_id = ? AND id = ?", (project_id, tid))
            conn.execute(f"INSERT OR IGNORE INTO {schema_name}_node_tombstones (project_id, id) VALUES (?, ?)", (project_id, tid))
            nodes_tombstoned += 1

        for et in delta.edge_tombstones:
            conn.execute(
                f"DELETE FROM {schema_name}_edge_upserts WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                (project_id, et["source"], et["target"], et["relation"]),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {schema_name}_edge_tombstones (project_id, source, target, relation) VALUES (?, ?, ?, ?)",
                (project_id, et["source"], et["target"], et["relation"]),
            )
            edges_tombstoned += 1

        for htid in delta.hyperedge_tombstones:
            conn.execute(f"DELETE FROM {schema_name}_hyperedge_upserts WHERE project_id = ? AND id = ?", (project_id, htid))
            conn.execute(f"INSERT OR IGNORE INTO {schema_name}_hyperedge_tombstones (project_id, id) VALUES (?, ?)", (project_id, htid))

        for h in delta.hyperedges:
            conn.execute(f"DELETE FROM {schema_name}_hyperedge_tombstones WHERE project_id = ? AND id = ?", (project_id, h.id))
            nodes_val = json.dumps(h.nodes) if not isinstance(h.nodes, str) else h.nodes
            conn.execute(
                f"""
                INSERT INTO {schema_name}_hyperedge_upserts
                (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, id) DO UPDATE SET
                    label = excluded.label,
                    nodes = excluded.nodes,
                    relation = excluded.relation,
                    confidence = excluded.confidence,
                    confidence_score = excluded.confidence_score,
                    source_file = excluded.source_file
                """,
                (h.id, project_id, h.label, nodes_val, h.relation, h.confidence, h.confidence_score, h.source_file),
            )

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": branch_name,
            "schema_name": schema_name,
            "nodes_upserted": nodes_upserted,
            "nodes_tombstoned": nodes_tombstoned,
            "edges_upserted": edges_upserted,
            "edges_tombstoned": edges_tombstoned,
            "partial": True,
        }

    def _get_branch_overlay(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        conn = self.get_conn()

        node_upserts: Dict[str, Dict[str, Any]] = {}
        node_tombstones: Set[str] = set()
        edge_upserts: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        edge_tombstones: Set[Tuple[str, str, str]] = set()

        try:
            for r in conn.execute(f"SELECT * FROM {schema_name}_node_upserts WHERE project_id = ?", (project_id,)).fetchall():
                d = dict(r)
                if isinstance(d.get("metadata"), str):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except Exception:
                        pass
                node_upserts[d["id"]] = d
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute(f"SELECT id FROM {schema_name}_node_tombstones WHERE project_id = ?", (project_id,)).fetchall():
                node_tombstones.add(r["id"])
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute(f"SELECT * FROM {schema_name}_edge_upserts WHERE project_id = ?", (project_id,)).fetchall():
                d = dict(r)
                edge_upserts[(d["source"], d["target"], d["relation"])] = d
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute(f"SELECT source, target, relation FROM {schema_name}_edge_tombstones WHERE project_id = ?", (project_id,)).fetchall():
                edge_tombstones.add((r["source"], r["target"], r["relation"]))
        except sqlite3.OperationalError:
            pass

        hyperedge_upserts: Dict[str, Dict[str, Any]] = {}
        hyperedge_tombstones: Set[str] = set()

        try:
            for r in conn.execute(f"SELECT * FROM {schema_name}_hyperedge_upserts WHERE project_id = ?", (project_id,)).fetchall():
                d = dict(r)
                if isinstance(d.get("nodes"), str):
                    try:
                        d["nodes"] = json.loads(d["nodes"])
                    except Exception:
                        pass
                hyperedge_upserts[d["id"]] = d
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute(f"SELECT id FROM {schema_name}_hyperedge_tombstones WHERE project_id = ?", (project_id,)).fetchall():
                hyperedge_tombstones.add(r["id"])
        except sqlite3.OperationalError:
            pass

        return {
            "schema_name": schema_name,
            "node_upserts": node_upserts,
            "node_tombstones": node_tombstones,
            "edge_upserts": edge_upserts,
            "edge_tombstones": edge_tombstones,
            "hyperedge_upserts": hyperedge_upserts,
            "hyperedge_tombstones": hyperedge_tombstones,
        }

    def _get_root_nodes_by_ids(self, project_id: str, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not node_ids:
            return {}
        conn = self.get_conn()
        q_ph = ",".join("?" for _ in node_ids)
        rows = conn.execute(
            f"SELECT * FROM root_nodes WHERE project_id = ? AND id IN ({q_ph})",
            [project_id] + node_ids,
        ).fetchall()
        res = {}
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except Exception:
                    pass
            res[d["id"]] = d
        return res

    def _get_root_edges_by_keys(
        self, project_id: str, edge_keys: List[Tuple[str, str, str]]
    ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        if not edge_keys:
            return {}
        conn = self.get_conn()
        res = {}
        for s, t, rel in edge_keys:
            r = conn.execute(
                "SELECT * FROM root_edges WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                (project_id, s, t, rel),
            ).fetchone()
            if r:
                res[(s, t, rel)] = dict(r)
        return res

    def diff_branch_vs_root(
        self,
        project_id: str,
        branch_name: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        if self.is_root_branch(branch_name):
            return BranchDiffResult(summary=BranchDiffSummary())

        overlay = self._get_branch_overlay(project_id, branch_name)
        upsert_ids = list(overlay["node_upserts"].keys())
        root_nodes = self._get_root_nodes_by_ids(project_id, upsert_ids)

        new_nodes: List[Dict[str, Any]] = []
        modified_nodes: List[Dict[str, Any]] = []

        for nid, n in overlay["node_upserts"].items():
            if nid in root_nodes:
                modified_nodes.append({
                    "id": nid,
                    "label": n.get("label"),
                    "file_type": n.get("file_type"),
                    "source_file": n.get("source_file"),
                    "source_location": n.get("source_location"),
                    "community": n.get("community"),
                    "metadata": n.get("metadata"),
                    "before": root_nodes[nid],
                    "after": n,
                })
            else:
                new_nodes.append(n)

        deleted_node_ids = list(overlay["node_tombstones"])

        upsert_edge_keys = list(overlay["edge_upserts"].keys())
        root_edges = self._get_root_edges_by_keys(project_id, upsert_edge_keys)

        new_edges: List[Dict[str, Any]] = []
        for ekey, e in overlay["edge_upserts"].items():
            if ekey not in root_edges:
                new_edges.append(e)

        deleted_edges = [
            {"project_id": project_id, "source": s, "target": t, "relation": r}
            for s, t, r in overlay["edge_tombstones"]
        ]

        # Filters
        if file_filter:
            new_nodes = [n for n in new_nodes if n.get("source_file", "").startswith(file_filter)]
            modified_nodes = [n for n in modified_nodes if n.get("source_file", "").startswith(file_filter)]
            del_lookup = self._get_root_nodes_by_ids(project_id, deleted_node_ids)
            deleted_node_ids = [nid for nid in deleted_node_ids if nid in del_lookup and del_lookup[nid].get("source_file", "").startswith(file_filter)]
            new_edges = [e for e in new_edges if e.get("source_file", "").startswith(file_filter)]
            deleted_edges = [
                e for e in deleted_edges
                if (e.get("source_file", "").startswith(file_filter) or e.get("source") in deleted_node_ids or e.get("target") in deleted_node_ids)
            ]

        if community is not None:
            new_nodes = [n for n in new_nodes if n.get("community") == community]
            modified_nodes = [n for n in modified_nodes if n.get("community") == community or (n.get("before") and n["before"].get("community") == community)]
            del_lookup = self._get_root_nodes_by_ids(project_id, deleted_node_ids)
            deleted_node_ids = [nid for nid in deleted_node_ids if nid in del_lookup and del_lookup[nid].get("community") == community]

        summary = BranchDiffSummary(
            new_nodes_count=len(new_nodes),
            modified_nodes_count=len(modified_nodes),
            deleted_nodes_count=len(deleted_node_ids),
            new_edges_count=len(new_edges),
            deleted_edges_count=len(deleted_edges),
        )
        return BranchDiffResult(
            summary=summary,
            new_nodes=new_nodes,
            modified_nodes=modified_nodes,
            deleted_node_ids=deleted_node_ids,
            new_edges=new_edges,
            deleted_edges=deleted_edges,
        )

    def diff_branches(
        self,
        project_id: str,
        base_branch: str,
        target_branch: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        return self._diff_branches_core(project_id, base_branch, target_branch, file_filter, community)

    def promote_branch_to_root(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        if self.is_root_branch(branch_name):
            return {"status": "noop", "message": "Already root branch"}

        overlay = self._get_branch_overlay(project_id, branch_name)
        schema_name = overlay["schema_name"]
        conn = self.get_conn()

        with conn:
            # 1. Apply node tombstones
            for tid in overlay["node_tombstones"]:
                conn.execute("DELETE FROM root_nodes WHERE project_id = ? AND id = ?", (project_id, tid))

            # 2. Apply node upserts
            for nid, n in overlay["node_upserts"].items():
                meta_json = json.dumps(n["metadata"]) if isinstance(n.get("metadata"), dict) else str(n.get("metadata", "{}"))
                conn.execute(
                    """
                    INSERT INTO root_nodes (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = excluded.label,
                        file_type = excluded.file_type,
                        source_file = excluded.source_file,
                        source_location = excluded.source_location,
                        community = excluded.community,
                        metadata = excluded.metadata,
                        updated_at = datetime('now')
                    """,
                    (n["id"], project_id, n["label"], n["file_type"], n["source_file"], n.get("source_location"), n.get("community"), meta_json),
                )

            # 3. Apply edge tombstones
            for s, t, rel in overlay["edge_tombstones"]:
                conn.execute("DELETE FROM root_edges WHERE project_id = ? AND source = ? AND target = ? AND relation = ?", (project_id, s, t, rel))

            # 4. Apply edge upserts
            for ekey, e in overlay["edge_upserts"].items():
                conn.execute(
                    """
                    INSERT INTO root_edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = excluded.confidence,
                        confidence_score = excluded.confidence_score,
                        source_file = excluded.source_file,
                        source_location = excluded.source_location,
                        weight = excluded.weight
                    """,
                    (project_id, e["source"], e["target"], e["relation"], e["confidence"], e["confidence_score"], e.get("source_file"), e.get("source_location"), e["weight"]),
                )

            # 4b. Apply hyperedge tombstones
            for htid in overlay.get("hyperedge_tombstones", set()):
                conn.execute("DELETE FROM root_hyperedges WHERE project_id = ? AND id = ?", (project_id, htid))

            # 4c. Apply hyperedge upserts
            for hid, h in overlay.get("hyperedge_upserts", {}).items():
                nodes_val = json.dumps(h["nodes"]) if not isinstance(h.get("nodes"), str) else h["nodes"]
                conn.execute(
                    """
                    INSERT INTO root_hyperedges (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = excluded.label,
                        nodes = excluded.nodes,
                        relation = excluded.relation,
                        confidence = excluded.confidence,
                        confidence_score = excluded.confidence_score,
                        source_file = excluded.source_file
                    """,
                    (h["id"], project_id, h["label"], nodes_val, h["relation"], h["confidence"], h["confidence_score"], h.get("source_file")),
                )

            # 5. Drop overlay tables & views
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_nodes")
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_edges")
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_hyperedges")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_node_upserts")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_node_tombstones")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_edge_upserts")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_edge_tombstones")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_hyperedge_upserts")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_hyperedge_tombstones")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_synced_files")

            # 6. Delete branch registration
            conn.execute("DELETE FROM graph_branches WHERE project_id = ? AND branch_name = ?", (project_id, branch_name))

        return {
            "status": "promoted",
            "project_id": project_id,
            "branch": branch_name,
            "upserted_nodes": len(overlay["node_upserts"]),
            "deleted_nodes": len(overlay["node_tombstones"]),
            "upserted_edges": len(overlay["edge_upserts"]),
            "deleted_edges": len(overlay["edge_tombstones"]),
            "upserted_hyperedges": len(overlay.get("hyperedge_upserts", {})),
            "deleted_hyperedges": len(overlay.get("hyperedge_tombstones", set())),
        }

    def get_resolved_subgraph(
        self,
        project_id: str,
        branch_name: str,
        files: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        conn = self.get_conn()
        if self.is_root_branch(branch_name):
            if files:
                q_ph = ",".join("?" for _ in files)
                nodes_rows = conn.execute(
                    f"SELECT * FROM root_nodes WHERE project_id = ? AND source_file IN ({q_ph}) LIMIT ?",
                    [project_id] + files + [limit],
                ).fetchall()
            else:
                nodes_rows = conn.execute(
                    "SELECT * FROM root_nodes WHERE project_id = ? LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            edges_rows = conn.execute("SELECT * FROM root_edges WHERE project_id = ?", (project_id,)).fetchall()
            hyperedges_rows = conn.execute("SELECT * FROM root_hyperedges WHERE project_id = ?", (project_id,)).fetchall()
        else:
            schema_name = sanitize_branch_schema_name(project_id, branch_name)
            # Query resolved views
            try:
                if files:
                    q_ph = ",".join("?" for _ in files)
                    nodes_rows = conn.execute(
                        f"SELECT * FROM {schema_name}_v_resolved_nodes WHERE project_id = ? AND source_file IN ({q_ph}) LIMIT ?",
                        [project_id] + files + [limit],
                    ).fetchall()
                else:
                    nodes_rows = conn.execute(
                        f"SELECT * FROM {schema_name}_v_resolved_nodes WHERE project_id = ? LIMIT ?",
                        (project_id, limit),
                    ).fetchall()
                edges_rows = conn.execute(f"SELECT * FROM {schema_name}_v_resolved_edges WHERE project_id = ?", (project_id,)).fetchall()
                hyperedges_rows = conn.execute(f"SELECT * FROM {schema_name}_v_resolved_hyperedges WHERE project_id = ?", (project_id,)).fetchall()
            except sqlite3.OperationalError:
                # View doesn't exist yet, fall back to root
                return self.get_resolved_subgraph(project_id, self.root_branch, files=files, limit=limit)

        nodes: List[Dict[str, Any]] = []
        for r in nodes_rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except Exception:
                    pass
            nodes.append(d)

        node_id_set = {n["id"] for n in nodes}
        edges: List[Dict[str, Any]] = []
        for r in edges_rows:
            d = dict(r)
            if d["source"] in node_id_set and d["target"] in node_id_set:
                edges.append(d)

        hyperedges: List[Dict[str, Any]] = []
        for r in hyperedges_rows:
            d = dict(r)
            if isinstance(d.get("nodes"), str):
                try:
                    d["nodes"] = json.loads(d["nodes"])
                except Exception:
                    pass
            hyperedges.append(d)

        return {
            "project_id": project_id,
            "branch": branch_name,
            "nodes": nodes,
            "edges": edges,
            "hyperedges": hyperedges,
        }

    def get_stats(self, project_id: str, branch_name: str = "main") -> Dict[str, Any]:
        subgraph = self.get_resolved_subgraph(project_id, branch_name, limit=1000000)
        res = {
            "project_id": project_id,
            "branch": branch_name,
            "node_count": len(subgraph.get("nodes", [])),
            "edge_count": len(subgraph.get("edges", [])),
            "hyperedge_count": len(subgraph.get("hyperedges", [])),
        }
        if not self.is_root_branch(branch_name):
            overlay = self._get_branch_overlay(project_id, branch_name)
            res["delta_nodes_upserted"] = len(overlay["node_upserts"])
            res["delta_nodes_tombstoned"] = len(overlay["node_tombstones"])
            res["delta_edges_upserted"] = len(overlay["edge_upserts"])
            res["delta_edges_tombstoned"] = len(overlay["edge_tombstones"])
        return res

    def list_projects(self) -> List[str]:
        conn = self.get_conn()
        r1 = conn.execute("SELECT DISTINCT project_id FROM root_nodes").fetchall()
        r2 = conn.execute("SELECT DISTINCT project_id FROM graph_branches").fetchall()
        projects = {r["project_id"] for r in r1} | {r["project_id"] for r in r2}
        return sorted(list(projects))

    def delete_project(self, project_id: str) -> bool:
        conn = self.get_conn()
        branches = self.list_branches(project_id)
        for b in branches:
            if not b.get("is_root"):
                self.delete_branch(project_id, b["branch_name"])

        with conn:
            conn.execute("DELETE FROM root_nodes WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM root_edges WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM root_hyperedges WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM graph_branches WHERE project_id = ?", (project_id,))
        return True

    def delete_branch(self, project_id: str, branch_name: str) -> bool:
        if self.is_root_branch(branch_name):
            return False
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        conn = self.get_conn()
        with conn:
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_nodes")
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_edges")
            conn.execute(f"DROP VIEW IF EXISTS {schema_name}_v_resolved_hyperedges")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_node_upserts")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_node_tombstones")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_edge_upserts")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_edge_tombstones")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_hyperedge_upserts")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_hyperedge_tombstones")
            conn.execute(f"DROP TABLE IF EXISTS {schema_name}_synced_files")
            conn.execute("DELETE FROM graph_branches WHERE project_id = ? AND branch_name = ?", (project_id, branch_name))
        return True

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT * FROM graph_branches WHERE project_id = ? ORDER BY branch_name",
            (project_id,),
        ).fetchall()
        branches: List[Dict[str, Any]] = [
            {"branch_name": self.root_branch, "schema_name": "root", "is_root": True}
        ]
        for r in rows:
            if r["branch_name"] != self.root_branch:
                branches.append({
                    "branch_name": r["branch_name"],
                    "schema_name": r["schema_name"],
                    "base_branch": r["base_branch"],
                    "author": r["author"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "is_root": False,
                })
        return branches

    def to_legacy_visjs(
        self,
        project_id: str,
        branch_name: str = "main",
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        subgraph = self.get_resolved_subgraph(project_id, branch_name, limit=limit * 2)
        return self._format_legacy_visjs(subgraph, query=query, kinds=kinds, limit=limit)


# =====================================================================
# PostgreSQL Multi-Schema GraphStore Implementation
# =====================================================================

class PostgresSchemaGraphStore(GraphStore):
    """PostgreSQL Multi-Schema GraphStore using isolated schemas for branch overlays,

    dynamic SQL views for virtual merging, and parameterized psycopg2 queries.
    """

    def __init__(
        self,
        connection_url: Optional[str] = None,
        conn: Optional[Any] = None,
        root_branch: str = "main",
    ):
        super().__init__(root_branch=root_branch)
        self.connection_url = connection_url
        self.conn = conn

    def get_conn(self):
        if self.conn and not getattr(self.conn, "closed", False):
            return self.conn
        if self.connection_url:
            self.conn = psycopg2.connect(self.connection_url)
            self.conn.autocommit = True
            return self.conn
        try:
            from ..core.config import env
            self.conn = psycopg2.connect(env.db_url)
            self.conn.autocommit = True
            return self.conn
        except Exception as e:
            raise RuntimeError(f"Failed to connect to PostgreSQL: {e}")

    # DDL Generation helpers for testing and verification
    def generate_root_schema_ddl(self, as_string: bool = False) -> List[Any]:
        """Generate parameterized psycopg2.sql statements for canonical root schema."""
        statements = [
            psql.SQL("CREATE SCHEMA IF NOT EXISTS pb_root;"),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS pb_root.nodes (
                id VARCHAR(255) NOT NULL,
                project_id VARCHAR(100) NOT NULL,
                label VARCHAR(255) NOT NULL,
                file_type VARCHAR(50) NOT NULL DEFAULT 'code',
                source_file TEXT NOT NULL,
                source_location TEXT,
                community INT,
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, id)
            );
            """),
            psql.SQL("CREATE INDEX IF NOT EXISTS idx_pb_root_nodes_proj ON pb_root.nodes(project_id);"),
            psql.SQL("CREATE INDEX IF NOT EXISTS idx_pb_root_nodes_file ON pb_root.nodes(project_id, source_file);"),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS pb_root.edges (
                project_id VARCHAR(100) NOT NULL,
                source VARCHAR(255) NOT NULL,
                target VARCHAR(255) NOT NULL,
                relation VARCHAR(100) NOT NULL,
                confidence VARCHAR(20) NOT NULL DEFAULT 'EXTRACTED',
                confidence_score FLOAT NOT NULL DEFAULT 1.0,
                source_file TEXT,
                source_location TEXT,
                weight FLOAT DEFAULT 1.0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, source, target, relation)
            );
            """),
            psql.SQL("CREATE INDEX IF NOT EXISTS idx_pb_root_edges_src ON pb_root.edges(project_id, source);"),
            psql.SQL("CREATE INDEX IF NOT EXISTS idx_pb_root_edges_tgt ON pb_root.edges(project_id, target);"),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS pb_root.hyperedges (
                id VARCHAR(255) NOT NULL,
                project_id VARCHAR(100) NOT NULL,
                label VARCHAR(255) NOT NULL,
                nodes TEXT[] NOT NULL,
                relation VARCHAR(100) NOT NULL,
                confidence VARCHAR(20) NOT NULL DEFAULT 'EXTRACTED',
                confidence_score FLOAT NOT NULL DEFAULT 1.0,
                source_file TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, id)
            );
            """),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS pb_root.graph_branches (
                project_id VARCHAR(100) NOT NULL,
                branch_name VARCHAR(255) NOT NULL,
                schema_name VARCHAR(63) NOT NULL,
                base_branch VARCHAR(255) DEFAULT 'main',
                author VARCHAR(100) DEFAULT 'system',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, branch_name)
            );
            """),
        ]
        return statements

    def generate_branch_schema_ddl(self, project_id: str, branch_name: str) -> List[Any]:
        """Generate psycopg2.sql statements for an isolated branch schema and resolved views."""
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        schema_id = psql.Identifier(schema_name)
        proj_lit = psql.Literal(project_id)

        statements = [
            psql.SQL("CREATE SCHEMA IF NOT EXISTS {schema};").format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.node_upserts (
                id VARCHAR(255) NOT NULL,
                project_id VARCHAR(100) NOT NULL,
                label VARCHAR(255) NOT NULL,
                file_type VARCHAR(50) NOT NULL DEFAULT 'code',
                source_file TEXT NOT NULL,
                source_location TEXT,
                community INT,
                metadata JSONB DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, id)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.node_tombstones (
                project_id VARCHAR(100) NOT NULL,
                id VARCHAR(255) NOT NULL,
                deleted_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, id)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.edge_upserts (
                project_id VARCHAR(100) NOT NULL,
                source VARCHAR(255) NOT NULL,
                target VARCHAR(255) NOT NULL,
                relation VARCHAR(100) NOT NULL,
                confidence VARCHAR(20) NOT NULL DEFAULT 'EXTRACTED',
                confidence_score FLOAT NOT NULL DEFAULT 1.0,
                source_file TEXT,
                source_location TEXT,
                weight FLOAT DEFAULT 1.0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, source, target, relation)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.edge_tombstones (
                project_id VARCHAR(100) NOT NULL,
                source VARCHAR(255) NOT NULL,
                target VARCHAR(255) NOT NULL,
                relation VARCHAR(100) NOT NULL,
                deleted_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, source, target, relation)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.hyperedge_upserts (
                id VARCHAR(255) NOT NULL,
                project_id VARCHAR(100) NOT NULL,
                label VARCHAR(255) NOT NULL,
                nodes TEXT[] NOT NULL,
                relation VARCHAR(100) NOT NULL,
                confidence VARCHAR(20) NOT NULL DEFAULT 'EXTRACTED',
                confidence_score FLOAT NOT NULL DEFAULT 1.0,
                source_file TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, id)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.hyperedge_tombstones (
                project_id VARCHAR(100) NOT NULL,
                id VARCHAR(255) NOT NULL,
                deleted_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, id)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE TABLE IF NOT EXISTS {schema}.synced_files (
                project_id VARCHAR(100) NOT NULL,
                file_path TEXT NOT NULL,
                sha256 VARCHAR(64),
                synced_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, file_path)
            );
            """).format(schema=schema_id),
            psql.SQL("""
            CREATE OR REPLACE VIEW {schema}.v_resolved_nodes AS
            SELECT * FROM {schema}.node_upserts
            UNION ALL
            SELECT r.* FROM pb_root.nodes r
            WHERE r.project_id = {proj}
              AND (r.project_id, r.id) NOT IN (SELECT project_id, id FROM {schema}.node_tombstones)
              AND (r.project_id, r.id) NOT IN (SELECT project_id, id FROM {schema}.node_upserts);
            """).format(schema=schema_id, proj=proj_lit),
            psql.SQL("""
            CREATE OR REPLACE VIEW {schema}.v_resolved_edges AS
            SELECT * FROM {schema}.edge_upserts
            UNION ALL
            SELECT r.* FROM pb_root.edges r
            WHERE r.project_id = {proj}
              AND (r.project_id, r.source, r.target, r.relation) NOT IN (
                  SELECT project_id, source, target, relation FROM {schema}.edge_tombstones
              )
              AND (r.project_id, r.source, r.target, r.relation) NOT IN (
                  SELECT project_id, source, target, relation FROM {schema}.edge_upserts
              );
            """).format(schema=schema_id, proj=proj_lit),
            psql.SQL("""
            CREATE OR REPLACE VIEW {schema}.v_resolved_hyperedges AS
            SELECT * FROM {schema}.hyperedge_upserts
            UNION ALL
            SELECT r.* FROM pb_root.hyperedges r
            WHERE r.project_id = {proj}
              AND (r.project_id, r.id) NOT IN (SELECT project_id, id FROM {schema}.hyperedge_tombstones)
              AND (r.project_id, r.id) NOT IN (SELECT project_id, id FROM {schema}.hyperedge_upserts);
            """).format(schema=schema_id, proj=proj_lit),
        ]
        return statements

    def ensure_root_schema(self) -> None:
        conn = self.get_conn()
        with conn.cursor() as cur:
            for stmt in self.generate_root_schema_ddl():
                cur.execute(stmt)

    def ensure_branch_schema(self, project_id: str, branch_name: str, author: str = "system") -> str:
        self.ensure_root_schema()
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        conn = self.get_conn()

        with conn.cursor() as cur:
            # Register branch
            cur.execute(
                """
                INSERT INTO pb_root.graph_branches (project_id, branch_name, schema_name, base_branch, author, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (project_id, branch_name) DO UPDATE SET
                    schema_name = EXCLUDED.schema_name,
                    updated_at = NOW()
                """,
                (project_id, branch_name, schema_name, self.root_branch, author),
            )

            # Apply DDL
            for stmt in self.generate_branch_schema_ddl(project_id, branch_name):
                cur.execute(stmt)

        return schema_name

    def sync_branch_delta(
        self,
        project_id: str,
        branch_name: str,
        delta: GraphDelta,
        partial: bool = False,
        synced_files: Optional[List[str]] = None,
        deleted_files: Optional[List[str]] = None,
        author: str = "system",
    ) -> Dict[str, Any]:
        self.ensure_root_schema()
        conn = self.get_conn()

        if self.is_root_branch(branch_name):
            return self._sync_to_root_pg(conn, project_id, delta, partial, synced_files, deleted_files)

        schema_name = self.ensure_branch_schema(project_id, branch_name, author)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not partial:
                return self._sync_branch_full_pg(cur, project_id, branch_name, schema_name, delta)
            else:
                return self._sync_branch_partial_pg(cur, project_id, branch_name, schema_name, delta, synced_files, deleted_files)

    def _sync_to_root_pg(
        self,
        conn: Any,
        project_id: str,
        delta: GraphDelta,
        partial: bool,
        synced_files: Optional[List[str]],
        deleted_files: Optional[List[str]],
    ) -> Dict[str, Any]:
        nodes_upserted = 0
        nodes_deleted = 0
        edges_upserted = 0
        edges_deleted = 0

        with conn.cursor() as cur:
            if not partial:
                cur.execute("DELETE FROM pb_root.nodes WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM pb_root.edges WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM pb_root.hyperedges WHERE project_id = %s", (project_id,))
            else:
                del_files = deleted_files or []
                if del_files:
                    cur.execute(
                        "DELETE FROM pb_root.nodes WHERE project_id = %s AND source_file = ANY(%s)",
                        (project_id, del_files),
                    )
                    nodes_deleted += cur.rowcount
                    cur.execute(
                        "DELETE FROM pb_root.edges WHERE project_id = %s AND source_file = ANY(%s)",
                        (project_id, del_files),
                    )
                    edges_deleted += cur.rowcount

                sync_files = synced_files or [
                    sf["file_path"] if isinstance(sf, dict) else sf for sf in delta.synced_files
                ]
                if sync_files:
                    cur.execute(
                        "DELETE FROM pb_root.nodes WHERE project_id = %s AND source_file = ANY(%s)",
                        (project_id, sync_files),
                    )
                    cur.execute(
                        "DELETE FROM pb_root.edges WHERE project_id = %s AND source_file = ANY(%s)",
                        (project_id, sync_files),
                    )

                if delta.node_tombstones:
                    cur.execute(
                        "DELETE FROM pb_root.nodes WHERE project_id = %s AND id = ANY(%s)",
                        (project_id, delta.node_tombstones),
                    )
                    nodes_deleted += cur.rowcount

                for et in delta.edge_tombstones:
                    cur.execute(
                        "DELETE FROM pb_root.edges WHERE project_id = %s AND source = %s AND target = %s AND relation = %s",
                        (project_id, et["source"], et["target"], et["relation"]),
                    )
                    edges_deleted += cur.rowcount

            # Upsert nodes
            for n in delta.nodes:
                cur.execute(
                    """
                    INSERT INTO pb_root.nodes (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = EXCLUDED.label,
                        file_type = EXCLUDED.file_type,
                        source_file = EXCLUDED.source_file,
                        source_location = EXCLUDED.source_location,
                        community = EXCLUDED.community,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    """,
                    (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, json.dumps(n.metadata or {})),
                )
                nodes_upserted += 1

            # Upsert edges
            for e in delta.edges:
                cur.execute(
                    """
                    INSERT INTO pb_root.edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = EXCLUDED.confidence,
                        confidence_score = EXCLUDED.confidence_score,
                        source_file = EXCLUDED.source_file,
                        source_location = EXCLUDED.source_location,
                        weight = EXCLUDED.weight
                    """,
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                )
                edges_upserted += 1

            for h in delta.hyperedges:
                cur.execute(
                    """
                    INSERT INTO pb_root.hyperedges (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = EXCLUDED.label,
                        nodes = EXCLUDED.nodes,
                        relation = EXCLUDED.relation,
                        confidence = EXCLUDED.confidence,
                        confidence_score = EXCLUDED.confidence_score,
                        source_file = EXCLUDED.source_file
                    """,
                    (h.id, project_id, h.label, h.nodes, h.relation, h.confidence, h.confidence_score, h.source_file),
                )

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": self.root_branch,
            "schema_name": "pb_root",
            "nodes_upserted": nodes_upserted,
            "nodes_deleted": nodes_deleted,
            "edges_upserted": edges_upserted,
            "edges_deleted": edges_deleted,
            "partial": partial,
        }

    def _sync_branch_full_pg(
        self,
        cur: Any,
        project_id: str,
        branch_name: str,
        schema_name: str,
        delta: GraphDelta,
    ) -> Dict[str, Any]:
        schema_id = psql.Identifier(schema_name)

        # Clear overlay
        for tbl in ["node_upserts", "node_tombstones", "edge_upserts", "edge_tombstones", "hyperedge_upserts", "hyperedge_tombstones", "synced_files"]:
            cur.execute(psql.SQL("DELETE FROM {schema}.{table} WHERE project_id = %s;").format(schema=schema_id, table=psql.Identifier(tbl)), (project_id,))

        cur.execute("SELECT * FROM pb_root.nodes WHERE project_id = %s", (project_id,))
        root_nodes = {r["id"]: dict(r) for r in cur.fetchall()}

        cur.execute("SELECT * FROM pb_root.edges WHERE project_id = %s", (project_id,))
        root_edges = {(r["source"], r["target"], r["relation"]): dict(r) for r in cur.fetchall()}

        nodes_upserted = 0
        nodes_tombstoned = 0
        edges_upserted = 0
        edges_tombstoned = 0

        delta_node_ids = set()
        for n in delta.nodes:
            delta_node_ids.add(n.id)
            meta_json = json.dumps(n.metadata or {})
            if n.id not in root_nodes:
                cur.execute(
                    psql.SQL("""
                    INSERT INTO {schema}.node_upserts
                    (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW());
                    """).format(schema=schema_id),
                    (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                )
                nodes_upserted += 1
            else:
                r = root_nodes[n.id]
                differs = (
                    r["label"] != n.label
                    or r["file_type"] != n.file_type
                    or r["source_file"] != n.source_file
                    or (r["source_location"] or None) != (n.source_location or None)
                    or r["community"] != n.community
                    or r["metadata"] != (n.metadata or {})
                )
                if differs:
                    cur.execute(
                        psql.SQL("""
                        INSERT INTO {schema}.node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW());
                        """).format(schema=schema_id),
                        (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                    )
                    nodes_upserted += 1

        for rid in root_nodes:
            if rid not in delta_node_ids:
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s);").format(schema=schema_id),
                    (project_id, rid),
                )
                nodes_tombstoned += 1

        delta_edge_keys = set()
        for e in delta.edges:
            ekey = (e.source, e.target, e.relation)
            delta_edge_keys.add(ekey)
            if ekey not in root_edges:
                cur.execute(
                    psql.SQL("""
                    INSERT INTO {schema}.edge_upserts
                    (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """).format(schema=schema_id),
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                )
                edges_upserted += 1
            else:
                r = root_edges[ekey]
                differs = (
                    r["confidence"] != e.confidence
                    or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                    or (r["source_file"] or None) != (e.source_file or None)
                    or (r["source_location"] or None) != (e.source_location or None)
                    or abs(r["weight"] - e.weight) > 1e-4
                )
                if differs:
                    cur.execute(
                        psql.SQL("""
                        INSERT INTO {schema}.edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                        """).format(schema=schema_id),
                        (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                    )
                    edges_upserted += 1

        for ekey in root_edges:
            if ekey not in delta_edge_keys:
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s);").format(schema=schema_id),
                    (project_id, ekey[0], ekey[1], ekey[2]),
                )
                edges_tombstoned += 1

        for htid in delta.hyperedge_tombstones:
            cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_upserts WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, htid))
            cur.execute(psql.SQL("INSERT INTO {schema}.hyperedge_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id), (project_id, htid))

        for h in delta.hyperedges:
            cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_tombstones WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, h.id))
            nodes_list = h.nodes if isinstance(h.nodes, list) else json.loads(h.nodes)
            cur.execute(
                psql.SQL("""
                INSERT INTO {schema}.hyperedge_upserts (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, id) DO UPDATE SET
                    label = EXCLUDED.label,
                    nodes = EXCLUDED.nodes,
                    relation = EXCLUDED.relation,
                    confidence = EXCLUDED.confidence,
                    confidence_score = EXCLUDED.confidence_score,
                    source_file = EXCLUDED.source_file;
                """).format(schema=schema_id),
                (h.id, project_id, h.label, nodes_list, h.relation, h.confidence, h.confidence_score, h.source_file),
            )

        for sf in delta.synced_files:
            fp = sf.get("file_path") if isinstance(sf, dict) else str(sf)
            sh = sf.get("sha256", "") if isinstance(sf, dict) else ""
            if fp:
                cur.execute(
                    psql.SQL("""
                    INSERT INTO {schema}.synced_files (project_id, file_path, sha256)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (project_id, file_path) DO UPDATE SET sha256 = EXCLUDED.sha256;
                    """).format(schema=schema_id),
                    (project_id, fp, sh),
                )

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": branch_name,
            "schema_name": schema_name,
            "nodes_upserted": nodes_upserted,
            "nodes_tombstoned": nodes_tombstoned,
            "edges_upserted": edges_upserted,
            "edges_tombstoned": edges_tombstoned,
            "partial": False,
        }

    def _sync_branch_partial_pg(
        self,
        cur: Any,
        project_id: str,
        branch_name: str,
        schema_name: str,
        delta: GraphDelta,
        synced_files: Optional[List[str]],
        deleted_files: Optional[List[str]],
    ) -> Dict[str, Any]:
        schema_id = psql.Identifier(schema_name)
        synced_file_set: Set[str] = set(synced_files or [
            sf["file_path"] if isinstance(sf, dict) else sf for sf in delta.synced_files
        ])
        if not synced_file_set:
            synced_file_set = {n.source_file for n in delta.nodes if n.source_file}

        deleted_file_set: Set[str] = set(deleted_files or [])
        nodes_upserted = 0
        nodes_tombstoned = 0
        edges_upserted = 0
        edges_tombstoned = 0

        for fpath in deleted_file_set:
            cur.execute(psql.SQL("DELETE FROM {schema}.node_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))
            cur.execute(psql.SQL("DELETE FROM {schema}.edge_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))

            cur.execute("SELECT id FROM pb_root.nodes WHERE project_id = %s AND source_file = %s", (project_id, fpath))
            for r in cur.fetchall():
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                    (project_id, r["id"]),
                )
                nodes_tombstoned += 1

            cur.execute("SELECT source, target, relation FROM pb_root.edges WHERE project_id = %s AND source_file = %s", (project_id, fpath))
            for r in cur.fetchall():
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                    (project_id, r["source"], r["target"], r["relation"]),
                )
                edges_tombstoned += 1

            cur.execute(psql.SQL("DELETE FROM {schema}.synced_files WHERE project_id = %s AND file_path = %s;").format(schema=schema_id), (project_id, fpath))

        for fpath in synced_file_set:
            cur.execute(psql.SQL("DELETE FROM {schema}.node_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))
            cur.execute(psql.SQL("DELETE FROM {schema}.edge_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))

            cur.execute("SELECT * FROM pb_root.nodes WHERE project_id = %s AND source_file = %s", (project_id, fpath))
            root_nodes = {r["id"]: dict(r) for r in cur.fetchall()}

            cur.execute("SELECT * FROM pb_root.edges WHERE project_id = %s AND source_file = %s", (project_id, fpath))
            root_edges = {(r["source"], r["target"], r["relation"]): dict(r) for r in cur.fetchall()}

            delta_file_nodes = [n for n in delta.nodes if n.source_file == fpath]
            delta_node_ids = {n.id for n in delta_file_nodes}

            for rid in root_nodes:
                if rid not in delta_node_ids:
                    cur.execute(
                        psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                        (project_id, rid),
                    )
                    nodes_tombstoned += 1
                else:
                    cur.execute(psql.SQL("DELETE FROM {schema}.node_tombstones WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, rid))

            for n in delta_file_nodes:
                meta_json = json.dumps(n.metadata or {})
                cur.execute(psql.SQL("DELETE FROM {schema}.node_tombstones WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, n.id))

                if n.id not in root_nodes:
                    cur.execute(
                        psql.SQL("""
                        INSERT INTO {schema}.node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW());
                        """).format(schema=schema_id),
                        (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                    )
                    nodes_upserted += 1
                else:
                    r = root_nodes[n.id]
                    differs = (
                        r["label"] != n.label
                        or r["file_type"] != n.file_type
                        or (r["source_location"] or None) != (n.source_location or None)
                        or r["community"] != n.community
                        or r["metadata"] != (n.metadata or {})
                    )
                    if differs:
                        cur.execute(
                            psql.SQL("""
                            INSERT INTO {schema}.node_upserts
                            (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW());
                            """).format(schema=schema_id),
                            (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                        )
                        nodes_upserted += 1

            delta_file_edges = [e for e in delta.edges if e.source_file == fpath]
            delta_edge_keys = {(e.source, e.target, e.relation) for e in delta_file_edges}

            for ekey in root_edges:
                if ekey not in delta_edge_keys:
                    cur.execute(
                        psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                        (project_id, ekey[0], ekey[1], ekey[2]),
                    )
                    edges_tombstoned += 1
                else:
                    cur.execute(
                        psql.SQL("DELETE FROM {schema}.edge_tombstones WHERE project_id = %s AND source = %s AND target = %s AND relation = %s;").format(schema=schema_id),
                        (project_id, ekey[0], ekey[1], ekey[2]),
                    )

            for e in delta_file_edges:
                ekey = (e.source, e.target, e.relation)
                cur.execute(
                    psql.SQL("DELETE FROM {schema}.edge_tombstones WHERE project_id = %s AND source = %s AND target = %s AND relation = %s;").format(schema=schema_id),
                    (project_id, e.source, e.target, e.relation),
                )
                if ekey not in root_edges:
                    cur.execute(
                        psql.SQL("""
                        INSERT INTO {schema}.edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                        """).format(schema=schema_id),
                        (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                    )
                    edges_upserted += 1
                else:
                    r = root_edges[ekey]
                    differs = (
                        r["confidence"] != e.confidence
                        or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                        or (r["source_location"] or None) != (e.source_location or None)
                        or abs(r["weight"] - e.weight) > 1e-4
                    )
                    if differs:
                        cur.execute(
                            psql.SQL("""
                            INSERT INTO {schema}.edge_upserts
                            (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                            """).format(schema=schema_id),
                            (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight),
                        )
                        edges_upserted += 1

            cur.execute(
                psql.SQL("""
                INSERT INTO {schema}.synced_files (project_id, file_path, sha256)
                VALUES (%s, %s, %s)
                ON CONFLICT (project_id, file_path) DO NOTHING;
                """).format(schema=schema_id),
                (project_id, fpath, ""),
            )

        for tid in delta.node_tombstones:
            cur.execute(psql.SQL("DELETE FROM {schema}.node_upserts WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, tid))
            cur.execute(psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id), (project_id, tid))
            nodes_tombstoned += 1

        for et in delta.edge_tombstones:
            cur.execute(
                psql.SQL("DELETE FROM {schema}.edge_upserts WHERE project_id = %s AND source = %s AND target = %s AND relation = %s;").format(schema=schema_id),
                (project_id, et["source"], et["target"], et["relation"]),
            )
            cur.execute(
                psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                (project_id, et["source"], et["target"], et["relation"]),
            )
            edges_tombstoned += 1

        for htid in delta.hyperedge_tombstones:
            cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_upserts WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, htid))
            cur.execute(psql.SQL("INSERT INTO {schema}.hyperedge_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id), (project_id, htid))

        for h in delta.hyperedges:
            cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_tombstones WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, h.id))
            nodes_list = h.nodes if isinstance(h.nodes, list) else json.loads(h.nodes)
            cur.execute(
                psql.SQL("""
                INSERT INTO {schema}.hyperedge_upserts (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, id) DO UPDATE SET
                    label = EXCLUDED.label,
                    nodes = EXCLUDED.nodes,
                    relation = EXCLUDED.relation,
                    confidence = EXCLUDED.confidence,
                    confidence_score = EXCLUDED.confidence_score,
                    source_file = EXCLUDED.source_file;
                """).format(schema=schema_id),
                (h.id, project_id, h.label, nodes_list, h.relation, h.confidence, h.confidence_score, h.source_file),
            )

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": branch_name,
            "schema_name": schema_name,
            "nodes_upserted": nodes_upserted,
            "nodes_tombstoned": nodes_tombstoned,
            "edges_upserted": edges_upserted,
            "edges_tombstoned": edges_tombstoned,
            "partial": True,
        }

    def _get_branch_overlay(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        schema_id = psql.Identifier(schema_name)
        conn = self.get_conn()

        node_upserts: Dict[str, Dict[str, Any]] = {}
        node_tombstones: Set[str] = set()
        edge_upserts: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        edge_tombstones: Set[Tuple[str, str, str]] = set()
        hyperedge_upserts: Dict[str, Dict[str, Any]] = {}
        hyperedge_tombstones: Set[str] = set()

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(psql.SQL("SELECT * FROM {schema}.node_upserts WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                for r in cur.fetchall():
                    node_upserts[r["id"]] = dict(r)
            except psycopg2.Error:
                pass

            try:
                cur.execute(psql.SQL("SELECT id FROM {schema}.node_tombstones WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                for r in cur.fetchall():
                    node_tombstones.add(r["id"])
            except psycopg2.Error:
                pass

            try:
                cur.execute(psql.SQL("SELECT * FROM {schema}.edge_upserts WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                for r in cur.fetchall():
                    edge_upserts[(r["source"], r["target"], r["relation"])] = dict(r)
            except psycopg2.Error:
                pass

            try:
                cur.execute(psql.SQL("SELECT source, target, relation FROM {schema}.edge_tombstones WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                for r in cur.fetchall():
                    edge_tombstones.add((r["source"], r["target"], r["relation"]))
            except psycopg2.Error:
                pass

            try:
                cur.execute(psql.SQL("SELECT * FROM {schema}.hyperedge_upserts WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                for r in cur.fetchall():
                    d = dict(r)
                    if "id" in d:
                        hyperedge_upserts[d["id"]] = d
            except (psycopg2.Error, Exception):
                pass

            try:
                cur.execute(psql.SQL("SELECT id FROM {schema}.hyperedge_tombstones WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                for r in cur.fetchall():
                    hyperedge_tombstones.add(r["id"])
            except psycopg2.Error:
                pass

        return {
            "schema_name": schema_name,
            "node_upserts": node_upserts,
            "node_tombstones": node_tombstones,
            "edge_upserts": edge_upserts,
            "edge_tombstones": edge_tombstones,
            "hyperedge_upserts": hyperedge_upserts,
            "hyperedge_tombstones": hyperedge_tombstones,
        }

    def _get_root_nodes_by_ids(self, project_id: str, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not node_ids:
            return {}
        conn = self.get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pb_root.nodes WHERE project_id = %s AND id = ANY(%s)", (project_id, node_ids))
            return {r["id"]: dict(r) for r in cur.fetchall()}

    def _get_root_edges_by_keys(
        self, project_id: str, edge_keys: List[Tuple[str, str, str]]
    ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        if not edge_keys:
            return {}
        conn = self.get_conn()
        res = {}
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for s, t, rel in edge_keys:
                cur.execute(
                    "SELECT * FROM pb_root.edges WHERE project_id = %s AND source = %s AND target = %s AND relation = %s",
                    (project_id, s, t, rel),
                )
                r = cur.fetchone()
                if r:
                    res[(s, t, rel)] = dict(r)
        return res

    def diff_branch_vs_root(
        self,
        project_id: str,
        branch_name: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        if self.is_root_branch(branch_name):
            return BranchDiffResult(summary=BranchDiffSummary())

        overlay = self._get_branch_overlay(project_id, branch_name)
        upsert_ids = list(overlay["node_upserts"].keys())
        root_nodes = self._get_root_nodes_by_ids(project_id, upsert_ids)

        new_nodes: List[Dict[str, Any]] = []
        modified_nodes: List[Dict[str, Any]] = []

        for nid, n in overlay["node_upserts"].items():
            if nid in root_nodes:
                modified_nodes.append({
                    "id": nid,
                    "label": n.get("label"),
                    "file_type": n.get("file_type"),
                    "source_file": n.get("source_file"),
                    "source_location": n.get("source_location"),
                    "community": n.get("community"),
                    "metadata": n.get("metadata"),
                    "before": root_nodes[nid],
                    "after": n,
                })
            else:
                new_nodes.append(n)

        deleted_node_ids = list(overlay["node_tombstones"])
        upsert_edge_keys = list(overlay["edge_upserts"].keys())
        root_edges = self._get_root_edges_by_keys(project_id, upsert_edge_keys)

        new_edges = [e for ekey, e in overlay["edge_upserts"].items() if ekey not in root_edges]
        deleted_edges = [
            {"project_id": project_id, "source": s, "target": t, "relation": r}
            for s, t, r in overlay["edge_tombstones"]
        ]

        # Filters
        if file_filter:
            new_nodes = [n for n in new_nodes if n.get("source_file", "").startswith(file_filter)]
            modified_nodes = [n for n in modified_nodes if n.get("source_file", "").startswith(file_filter)]
            del_lookup = self._get_root_nodes_by_ids(project_id, deleted_node_ids)
            deleted_node_ids = [nid for nid in deleted_node_ids if nid in del_lookup and del_lookup[nid].get("source_file", "").startswith(file_filter)]
            new_edges = [e for e in new_edges if e.get("source_file", "").startswith(file_filter)]
            deleted_edges = [
                e for e in deleted_edges
                if (e.get("source_file", "").startswith(file_filter) or e.get("source") in deleted_node_ids or e.get("target") in deleted_node_ids)
            ]

        if community is not None:
            new_nodes = [n for n in new_nodes if n.get("community") == community]
            modified_nodes = [n for n in modified_nodes if n.get("community") == community or (n.get("before") and n["before"].get("community") == community)]
            del_lookup = self._get_root_nodes_by_ids(project_id, deleted_node_ids)
            deleted_node_ids = [nid for nid in deleted_node_ids if nid in del_lookup and del_lookup[nid].get("community") == community]

        summary = BranchDiffSummary(
            new_nodes_count=len(new_nodes),
            modified_nodes_count=len(modified_nodes),
            deleted_nodes_count=len(deleted_node_ids),
            new_edges_count=len(new_edges),
            deleted_edges_count=len(deleted_edges),
        )
        return BranchDiffResult(
            summary=summary,
            new_nodes=new_nodes,
            modified_nodes=modified_nodes,
            deleted_node_ids=deleted_node_ids,
            new_edges=new_edges,
            deleted_edges=deleted_edges,
        )

    def diff_branches(
        self,
        project_id: str,
        base_branch: str,
        target_branch: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        return self._diff_branches_core(project_id, base_branch, target_branch, file_filter, community)

    def promote_branch_to_root(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        if self.is_root_branch(branch_name):
            return {"status": "noop", "message": "Already root branch"}

        overlay = self._get_branch_overlay(project_id, branch_name)
        schema_name = overlay["schema_name"]
        schema_id = psql.Identifier(schema_name)
        conn = self.get_conn()
        is_autocommit = getattr(conn, "autocommit", False)

        try:
            with conn.cursor() as cur:
                if is_autocommit:
                    cur.execute("BEGIN;")

                # 1. Apply node tombstones
                if overlay["node_tombstones"]:
                    cur.execute(
                        "DELETE FROM pb_root.nodes WHERE project_id = %s AND id = ANY(%s)",
                        (project_id, list(overlay["node_tombstones"])),
                    )

                # 2. Apply node upserts
                for nid, n in overlay["node_upserts"].items():
                    cur.execute(
                        """
                        INSERT INTO pb_root.nodes (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (project_id, id) DO UPDATE SET
                            label = EXCLUDED.label,
                            file_type = EXCLUDED.file_type,
                            source_file = EXCLUDED.source_file,
                            source_location = EXCLUDED.source_location,
                            community = EXCLUDED.community,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW()
                        """,
                        (n["id"], project_id, n["label"], n["file_type"], n["source_file"], n.get("source_location"), n.get("community"), json.dumps(n.get("metadata", {}))),
                    )

                # 3. Apply edge tombstones
                for s, t, rel in overlay["edge_tombstones"]:
                    cur.execute(
                        "DELETE FROM pb_root.edges WHERE project_id = %s AND source = %s AND target = %s AND relation = %s",
                        (project_id, s, t, rel),
                    )

                # 4. Apply edge upserts
                for ekey, e in overlay["edge_upserts"].items():
                    cur.execute(
                        """
                        INSERT INTO pb_root.edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                            confidence = EXCLUDED.confidence,
                            confidence_score = EXCLUDED.confidence_score,
                            source_file = EXCLUDED.source_file,
                            source_location = EXCLUDED.source_location,
                            weight = EXCLUDED.weight
                        """,
                        (project_id, e["source"], e["target"], e["relation"], e["confidence"], e["confidence_score"], e.get("source_file"), e.get("source_location"), e["weight"]),
                    )

                # 4b. Apply hyperedge tombstones
                if overlay.get("hyperedge_tombstones"):
                    cur.execute(
                        "DELETE FROM pb_root.hyperedges WHERE project_id = %s AND id = ANY(%s)",
                        (project_id, list(overlay["hyperedge_tombstones"])),
                    )

                # 4c. Apply hyperedge upserts
                for hid, h in overlay.get("hyperedge_upserts", {}).items():
                    nodes_list = h["nodes"] if isinstance(h["nodes"], list) else json.loads(h["nodes"])
                    cur.execute(
                        """
                        INSERT INTO pb_root.hyperedges (id, project_id, label, nodes, relation, confidence, confidence_score, source_file)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, id) DO UPDATE SET
                            label = EXCLUDED.label,
                            nodes = EXCLUDED.nodes,
                            relation = EXCLUDED.relation,
                            confidence = EXCLUDED.confidence,
                            confidence_score = EXCLUDED.confidence_score,
                            source_file = EXCLUDED.source_file
                        """,
                        (h["id"], project_id, h["label"], nodes_list, h["relation"], h["confidence"], h["confidence_score"], h.get("source_file")),
                    )

                # 5. Drop schema
                cur.execute(psql.SQL("DROP SCHEMA IF EXISTS {schema} CASCADE;").format(schema=schema_id))

                # 6. Delete branch registration
                cur.execute("DELETE FROM pb_root.graph_branches WHERE project_id = %s AND branch_name = %s", (project_id, branch_name))

                if is_autocommit:
                    cur.execute("COMMIT;")
                elif hasattr(conn, "commit"):
                    conn.commit()
        except Exception:
            try:
                if is_autocommit:
                    with conn.cursor() as cur:
                        cur.execute("ROLLBACK;")
                elif hasattr(conn, "rollback"):
                    conn.rollback()
            except Exception:
                pass
            raise

        return {
            "status": "promoted",
            "project_id": project_id,
            "branch": branch_name,
            "upserted_nodes": len(overlay["node_upserts"]),
            "deleted_nodes": len(overlay["node_tombstones"]),
            "upserted_edges": len(overlay["edge_upserts"]),
            "deleted_edges": len(overlay["edge_tombstones"]),
            "upserted_hyperedges": len(overlay.get("hyperedge_upserts", {})),
            "deleted_hyperedges": len(overlay.get("hyperedge_tombstones", set())),
        }

    def get_resolved_subgraph(
        self,
        project_id: str,
        branch_name: str,
        files: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        conn = self.get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if self.is_root_branch(branch_name):
                if files:
                    cur.execute(
                        "SELECT * FROM pb_root.nodes WHERE project_id = %s AND source_file = ANY(%s) LIMIT %s",
                        (project_id, files, limit),
                    )
                else:
                    cur.execute("SELECT * FROM pb_root.nodes WHERE project_id = %s LIMIT %s", (project_id, limit))
                nodes_rows = cur.fetchall()
                cur.execute("SELECT * FROM pb_root.edges WHERE project_id = %s", (project_id,))
                edges_rows = cur.fetchall()
                cur.execute("SELECT * FROM pb_root.hyperedges WHERE project_id = %s", (project_id,))
                hyperedges_rows = cur.fetchall()
            else:
                schema_name = sanitize_branch_schema_name(project_id, branch_name)
                schema_id = psql.Identifier(schema_name)
                try:
                    if files:
                        cur.execute(
                            psql.SQL("SELECT * FROM {schema}.v_resolved_nodes WHERE project_id = %s AND source_file = ANY(%s) LIMIT %s;").format(schema=schema_id),
                            (project_id, files, limit),
                        )
                    else:
                        cur.execute(
                            psql.SQL("SELECT * FROM {schema}.v_resolved_nodes WHERE project_id = %s LIMIT %s;").format(schema=schema_id),
                            (project_id, limit),
                        )
                    nodes_rows = cur.fetchall()
                    cur.execute(psql.SQL("SELECT * FROM {schema}.v_resolved_edges WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                    edges_rows = cur.fetchall()
                    cur.execute(psql.SQL("SELECT * FROM {schema}.v_resolved_hyperedges WHERE project_id = %s;").format(schema=schema_id), (project_id,))
                    hyperedges_rows = cur.fetchall()
                except psycopg2.Error:
                    return self.get_resolved_subgraph(project_id, self.root_branch, files=files, limit=limit)

        nodes = [dict(r) for r in nodes_rows]
        node_id_set = {n["id"] for n in nodes}
        edges = [dict(r) for r in edges_rows if r["source"] in node_id_set and r["target"] in node_id_set]
        hyperedges = [dict(r) for r in hyperedges_rows]

        return {
            "project_id": project_id,
            "branch": branch_name,
            "nodes": nodes,
            "edges": edges,
            "hyperedges": hyperedges,
        }

    def get_stats(self, project_id: str, branch_name: str = "main") -> Dict[str, Any]:
        subgraph = self.get_resolved_subgraph(project_id, branch_name, limit=1000000)
        res = {
            "project_id": project_id,
            "branch": branch_name,
            "node_count": len(subgraph.get("nodes", [])),
            "edge_count": len(subgraph.get("edges", [])),
            "hyperedge_count": len(subgraph.get("hyperedges", [])),
        }
        if not self.is_root_branch(branch_name):
            overlay = self._get_branch_overlay(project_id, branch_name)
            res["delta_nodes_upserted"] = len(overlay["node_upserts"])
            res["delta_nodes_tombstoned"] = len(overlay["node_tombstones"])
            res["delta_edges_upserted"] = len(overlay["edge_upserts"])
            res["delta_edges_tombstoned"] = len(overlay["edge_tombstones"])
        return res

    def list_projects(self) -> List[str]:
        conn = self.get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT project_id FROM pb_root.nodes UNION SELECT DISTINCT project_id FROM pb_root.graph_branches")
            rows = cur.fetchall()
            return sorted([r[0] for r in rows if r[0]])

    def delete_project(self, project_id: str) -> bool:
        conn = self.get_conn()
        branches = self.list_branches(project_id)
        for b in branches:
            if not b.get("is_root"):
                self.delete_branch(project_id, b["branch_name"])

        with conn.cursor() as cur:
            cur.execute("DELETE FROM pb_root.nodes WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM pb_root.edges WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM pb_root.hyperedges WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM pb_root.graph_branches WHERE project_id = %s", (project_id,))
        return True

    def delete_branch(self, project_id: str, branch_name: str) -> bool:
        if self.is_root_branch(branch_name):
            return False
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        schema_id = psql.Identifier(schema_name)
        conn = self.get_conn()
        with conn.cursor() as cur:
            cur.execute(psql.SQL("DROP SCHEMA IF EXISTS {schema} CASCADE;").format(schema=schema_id))
            cur.execute("DELETE FROM pb_root.graph_branches WHERE project_id = %s AND branch_name = %s", (project_id, branch_name))
        return True

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pb_root.graph_branches WHERE project_id = %s ORDER BY branch_name", (project_id,))
            rows = cur.fetchall()
            branches = [{"branch_name": self.root_branch, "schema_name": "pb_root", "is_root": True}]
            for r in rows:
                if r["branch_name"] != self.root_branch:
                    branches.append({
                        "branch_name": r["branch_name"],
                        "schema_name": r["schema_name"],
                        "base_branch": r["base_branch"],
                        "author": r["author"],
                        "created_at": str(r["created_at"]) if r["created_at"] else None,
                        "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
                        "is_root": False,
                    })
            return branches

    def to_legacy_visjs(
        self,
        project_id: str,
        branch_name: str = "main",
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        subgraph = self.get_resolved_subgraph(project_id, branch_name, limit=limit * 2)
        return self._format_legacy_visjs(subgraph, query=query, kinds=kinds, limit=limit)


# =====================================================================
# GraphStore Factory
# =====================================================================

def get_graph_store(
    db_path: Optional[str] = None,
    backend: Optional[str] = None,
    connection_url: Optional[str] = None,
    root_branch: str = "main",
) -> GraphStore:
    """Factory creating configured GraphStore instance based on environment or parameters."""
    if not db_path:
        db_path = os.getenv("OM_DB_PATH") or os.getenv("PB_DB_PATH") or None
    backend_val = (backend or "").lower()
    if not backend_val:
        backend_val = (
            os.getenv("PB_METADATA_BACKEND")
            or os.getenv("OM_METADATA_BACKEND")
            or ""
        ).lower()
        if not backend_val:
            try:
                from ..core.config import env
                backend_val = (getattr(env, "db_backend", None) or "sqlite").lower()
            except Exception:
                backend_val = "sqlite"

    if backend_val in ("postgres", "postgresql"):
        return PostgresSchemaGraphStore(
            connection_url=connection_url,
            root_branch=root_branch,
        )
    else:
        return SqliteOverlayGraphStore(
            db_path=db_path,
            root_branch=root_branch,
        )
