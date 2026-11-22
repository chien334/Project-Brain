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
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
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
    "sanitize_project_branch_schema_name",
    "GraphStore",
    "PostgresSchemaGraphStore",
    "SqliteOverlayGraphStore",
    "MultiProjectGraphStore",
    "get_graph_store",
]


def _parse_edge_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_edge_metadata_dict(edge_or_meta: Any) -> Dict[str, Any]:
    if isinstance(edge_or_meta, dict):
        return dict(edge_or_meta)
    if isinstance(edge_or_meta, str):
        return _parse_edge_metadata(edge_or_meta)
    if edge_or_meta is None:
        return {}
    raw = getattr(edge_or_meta, "metadata", None)
    if raw is None and hasattr(edge_or_meta, "to_dict"):
        raw = edge_or_meta.to_dict().get("metadata")
    return _parse_edge_metadata(raw)


def _serialize_edge_metadata(edge_or_meta: Any) -> str:
    return json.dumps(_extract_edge_metadata_dict(edge_or_meta))


def _dedup_nodes(nodes: List[GraphNode]) -> List[GraphNode]:
    """Deduplicate nodes by primary key (id), keeping the last occurrence."""
    return list({n.id: n for n in nodes}.values())


def _dedup_edges(edges: List[GraphEdge]) -> List[GraphEdge]:
    """Deduplicate edges by primary key (source, target, relation), keeping the last occurrence."""
    return list({(e.source, e.target, e.relation): e for e in edges}.values())


def _dedup_hyperedges(hyperedges: List[GraphHyperedge]) -> List[GraphHyperedge]:
    """Deduplicate hyperedges by primary key (id), keeping the last occurrence."""
    return list({h.id: h for h in hyperedges}.values())


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


def sanitize_project_branch_schema_name(
    branch_name: str, root_branch: str = "main"
) -> str:
    """Compute deterministic branch schema identifier inside a dedicated project DB (<= 60 bytes)."""
    from ..core.tenant_manager import sanitize_branch_schema_for_project

    return sanitize_branch_schema_for_project(branch_name, root_branch=root_branch)


class GraphStore(ABC):
    """Abstract Base Class for ProjectBrain Graph Storage backends."""

    def __init__(self, root_branch: str = "main", project_scoped_schemas: bool = False):
        self.root_branch = root_branch or "main"
        self.project_scoped_schemas = bool(project_scoped_schemas)

    def _branch_schema_name(self, project_id: str, branch_name: str) -> str:
        if getattr(self, "project_scoped_schemas", False):
            return sanitize_project_branch_schema_name(
                branch_name, root_branch=self.root_branch
            )
        return sanitize_branch_schema_name(project_id, branch_name)

    def close(self) -> None:
        """Close underlying database connection if owned by this store."""
        pass

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def is_root_branch(self, branch_name: Optional[str]) -> bool:
        """Check whether branch_name targets the canonical root schema."""
        if not branch_name:
            return True
        return branch_name.strip() == self.root_branch

    def get_branch_lineage(self, project_id: str, branch_name: str, max_depth: int = 8) -> List[str]:
        """Return ordered ancestor lineage [root_branch, ..., parent_branch, branch_name]."""
        if self.is_root_branch(branch_name):
            return [self.root_branch]
        return [self.root_branch, branch_name.strip()]

    def _validate_branch_hierarchy(
        self,
        branch_name: str,
        resolved_base: str,
        existing_parents: Dict[str, str],
        max_depth: int = 8,
    ) -> None:
        """Validate that setting branch_name -> resolved_base creates no cycle and depth <= max_depth."""
        visited: Set[str] = {branch_name}
        cur: Optional[str] = resolved_base
        depth = 1
        while cur and not self.is_root_branch(cur):
            if cur in visited:
                raise ValueError(f"Cycle detected in branch hierarchy: '{branch_name}' -> '{resolved_base}'")
            visited.add(cur)
            depth += 1
            if depth > max_depth:
                raise ValueError(f"Branch hierarchy depth exceeds maximum of {max_depth} for branch '{branch_name}'")
            cur = existing_parents.get(cur)

    def _walk_lineage(
        self,
        branch_name: str,
        parent_map: Dict[str, str],
        max_depth: int = 8,
    ) -> List[str]:
        """Walk parent_map from branch_name up to root_branch, returning [root, ..., branch_name]."""
        if self.is_root_branch(branch_name):
            return [self.root_branch]
        chain: List[str] = []
        visited: Set[str] = set()
        cur: Optional[str] = branch_name.strip()
        while cur and len(chain) <= max_depth:
            if cur in visited:
                break
            visited.add(cur)
            chain.append(cur)
            if self.is_root_branch(cur):
                break
            parent = parent_map.get(cur)
            if not parent or parent.strip() == cur:
                break
            cur = parent.strip()
        if not chain or not self.is_root_branch(chain[-1]):
            chain.append(self.root_branch)
        chain.reverse()
        return chain

    def _resolve_multi_level_subgraph(
        self,
        project_id: str,
        branch_name: str,
        lineage: List[str],
        files: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Resolve multi-level branch lineage by applying overlays sequentially over canonical root."""
        base = self.get_resolved_subgraph(project_id, self.root_branch, files=None, limit=1_000_000)
        nodes_map: Dict[str, Dict[str, Any]] = {n["id"]: dict(n) for n in base.get("nodes", [])}
        edges_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {
            (e["source"], e["target"], e["relation"]): dict(e) for e in base.get("edges", [])
        }
        hyperedges_map: Dict[str, Dict[str, Any]] = {h["id"]: dict(h) for h in base.get("hyperedges", [])}

        for b_name in lineage[1:]:
            overlay = self._get_branch_overlay(project_id, b_name)
            for tid in overlay.get("node_tombstones", set()):
                nodes_map.pop(tid, None)
            for nid, nd in overlay.get("node_upserts", {}).items():
                nodes_map[nid] = dict(nd)
            for ekey in overlay.get("edge_tombstones", set()):
                edges_map.pop(ekey, None)
            for ekey, ed in overlay.get("edge_upserts", {}).items():
                edges_map[ekey] = dict(ed)
            for htid in overlay.get("hyperedge_tombstones", set()):
                hyperedges_map.pop(htid, None)
            for hid, hd in overlay.get("hyperedge_upserts", {}).items():
                hyperedges_map[hid] = dict(hd)

        resolved_nodes = list(nodes_map.values())
        if files:
            file_set = set(files)
            resolved_nodes = [n for n in resolved_nodes if n.get("source_file") in file_set]
        resolved_nodes = resolved_nodes[:limit]
        surviving_ids = {n["id"] for n in resolved_nodes}
        resolved_edges = [
            e for e in edges_map.values()
            if e["source"] in surviving_ids and e["target"] in surviving_ids
        ]
        resolved_hyperedges = list(hyperedges_map.values())
        return {
            "project_id": project_id,
            "branch": branch_name,
            "nodes": resolved_nodes,
            "edges": resolved_edges,
            "hyperedges": resolved_hyperedges,
        }

    def _diff_resolved_subgraphs(
        self,
        project_id: str,
        base_branch: str,
        target_branch: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        """Compute exact structural diff by comparing fully resolved subgraphs of base_branch and target_branch."""
        sub_a = self.get_resolved_subgraph(project_id, base_branch, limit=1_000_000)
        sub_b = self.get_resolved_subgraph(project_id, target_branch, limit=1_000_000)

        nodes_a: Dict[str, Dict[str, Any]] = {n["id"]: n for n in sub_a.get("nodes", [])}
        nodes_b: Dict[str, Dict[str, Any]] = {n["id"]: n for n in sub_b.get("nodes", [])}
        edges_a: Dict[Tuple[str, str, str], Dict[str, Any]] = {
            (e["source"], e["target"], e["relation"]): e for e in sub_a.get("edges", [])
        }
        edges_b: Dict[Tuple[str, str, str], Dict[str, Any]] = {
            (e["source"], e["target"], e["relation"]): e for e in sub_b.get("edges", [])
        }

        new_nodes: List[Dict[str, Any]] = []
        modified_nodes: List[Dict[str, Any]] = []
        deleted_node_ids: List[str] = []

        node_attrs = ["label", "file_type", "source_file", "source_location", "community", "metadata"]
        for nid in set(nodes_a.keys()) | set(nodes_b.keys()):
            state_a = nodes_a.get(nid)
            state_b = nodes_b.get(nid)
            if state_a is None and state_b is not None:
                new_nodes.append(state_b)
            elif state_a is not None and state_b is None:
                deleted_node_ids.append(nid)
            elif state_a is not None and state_b is not None:
                if any(state_a.get(k) != state_b.get(k) for k in node_attrs):
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

        new_edges: List[Dict[str, Any]] = []
        deleted_edges: List[Dict[str, Any]] = []
        edge_attrs = ["confidence", "confidence_score", "source_file", "source_location", "weight", "metadata"]
        for ekey in set(edges_a.keys()) | set(edges_b.keys()):
            edge_a = edges_a.get(ekey)
            edge_b = edges_b.get(ekey)
            if edge_a is None and edge_b is not None:
                new_edges.append(edge_b)
            elif edge_a is not None and edge_b is None:
                deleted_edges.append({"project_id": project_id, "source": ekey[0], "target": ekey[1], "relation": ekey[2]})
            elif edge_a is not None and edge_b is not None:
                if any(
                    (_parse_edge_metadata(edge_a.get(k)) != _parse_edge_metadata(edge_b.get(k)))
                    if k == "metadata"
                    else (edge_a.get(k) != edge_b.get(k))
                    for k in edge_attrs
                ):
                    new_edges.append(edge_b)

        if file_filter:
            new_nodes = [n for n in new_nodes if (n.get("source_file") or "").startswith(file_filter)]
            modified_nodes = [n for n in modified_nodes if (n.get("source_file") or "").startswith(file_filter)]
            deleted_node_ids = [
                nid for nid in deleted_node_ids
                if nid in nodes_a and (nodes_a[nid].get("source_file") or "").startswith(file_filter)
            ]
            new_edges = [e for e in new_edges if (e.get("source_file") or "").startswith(file_filter)]
            deleted_edges = [
                e for e in deleted_edges
                if (
                    (edges_a.get((e["source"], e["target"], e["relation"]), {}).get("source_file") or "").startswith(file_filter)
                    or e.get("source") in deleted_node_ids
                    or e.get("target") in deleted_node_ids
                )
            ]

        if community is not None:
            new_nodes = [n for n in new_nodes if n.get("community") == community]
            modified_nodes = [
                n for n in modified_nodes
                if n.get("community") == community or (n.get("before") and n["before"].get("community") == community)
            ]
            deleted_node_ids = [
                nid for nid in deleted_node_ids
                if nid in nodes_a and nodes_a[nid].get("community") == community
            ]

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

    @abstractmethod
    def ensure_root_schema(self) -> None:
        """Create root schema and base tables (pb_root or root_*) if not existing."""
        pass

    @abstractmethod
    def ensure_branch_schema(
        self,
        project_id: str,
        branch_name: str,
        author: str = "system",
        base_branch: Optional[str] = None,
    ) -> str:
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
        base_branch: Optional[str] = None,
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
    def promote_branch_to_root(
        self,
        project_id: str,
        branch_name: str,
        target_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically merge branch overlay mutations into canonical root or target parent branch and drop schema."""
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

        lineage_a = self.get_branch_lineage(project_id, base_branch)
        lineage_b = self.get_branch_lineage(project_id, target_branch)
        if len(lineage_a) > 2 or len(lineage_b) > 2:
            return self._diff_resolved_subgraphs(project_id, base_branch, target_branch, file_filter, community)

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
                attrs = ["confidence", "confidence_score", "source_file", "source_location", "weight", "metadata"]
                if any(
                    (_parse_edge_metadata(edge_a.get(k)) != _parse_edge_metadata(edge_b.get(k)))
                    if k == "metadata"
                    else (edge_a.get(k) != edge_b.get(k))
                    for k in attrs
                ):
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

    def _resolve_subgraph_for_visjs(
        self,
        project_id: str,
        branch_name: Optional[str] = "main",
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Resolve full enough subgraph for Vis.js filtering and merge branches on wildcard."""
        has_query = bool(query and str(query).strip())
        has_kinds = bool(kinds and str(kinds).strip().lower() not in ("all", "*"))
        subgraph_limit = 100_000 if (has_query or has_kinds) else max(limit * 5, 2000)

        br = (branch_name or "").strip()
        if br in ("%", "*", ""):
            subgraph = self.get_resolved_subgraph(
                project_id, self.root_branch, limit=subgraph_limit
            )
            try:
                branches = self.list_branches(project_id) or []
            except Exception:
                branches = []
            non_root = [
                b.get("branch_name")
                for b in branches
                if b.get("branch_name")
                and not b.get("is_root")
                and not self.is_root_branch(b.get("branch_name"))
            ]
            if non_root:
                nodes_by_id: Dict[str, Dict[str, Any]] = {
                    n["id"]: n for n in subgraph.get("nodes", [])
                }
                edges_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {
                    (e["source"], e["target"], e["relation"]): e
                    for e in subgraph.get("edges", [])
                }
                for b_name in non_root:
                    if len(nodes_by_id) >= subgraph_limit and not (has_query or has_kinds):
                        break
                    b_sub = self.get_resolved_subgraph(
                        project_id, str(b_name), limit=subgraph_limit
                    )
                    for n in b_sub.get("nodes", []):
                        nodes_by_id.setdefault(n["id"], n)
                    for e in b_sub.get("edges", []):
                        edges_by_key.setdefault(
                            (e["source"], e["target"], e["relation"]), e
                        )
                subgraph = {
                    "project_id": project_id,
                    "branch": self.root_branch,
                    "nodes": list(nodes_by_id.values()),
                    "edges": list(edges_by_key.values()),
                }
            return subgraph

        return self.get_resolved_subgraph(project_id, br, limit=subgraph_limit)

    @staticmethod
    def _infer_visjs_sym_kind(node: Dict[str, Any], meta: Dict[str, Any]) -> str:
        """Derive normalized symbol kind from node metadata and AST/doc naming cues."""
        sym_kind = str(
            meta.get("kind")
            or meta.get("symbol_type")
            or meta.get("node_type")
            or meta.get("type")
            or ""
        ).strip().lower()
        if sym_kind:
            return sym_kind

        nid = str(node.get("id") or "")
        label = str(node.get("label") or "")
        src_file = str(node.get("source_file") or "")
        file_type = (node.get("file_type") or "code").strip().lower()

        if "_rationale_" in nid:
            return "rationale"
        if nid == src_file or label.endswith(
            (".py", ".js", ".ts", ".tsx", ".sql", ".md", ".sh", ".json", ".toml")
        ):
            return "file"
        if file_type in ("doc", "document", "markdown"):
            return "section"
        if meta.get("_callable_class") or (
            file_type == "code"
            and label[:1].isupper()
            and " " not in label
            and not label.endswith("()")
        ):
            return "class"
        if file_type == "code" and label.startswith(".") and label.endswith("()"):
            return "method"
        if file_type == "code":
            return "function"
        return file_type

    def _format_legacy_visjs(
        self,
        subgraph: Dict[str, Any],
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        raw_nodes = subgraph.get("nodes", [])
        raw_edges = subgraph.get("edges", [])

        kinds_str = (kinds or "").strip().lower()
        has_kind_filter = bool(kinds_str) and kinds_str not in ("all", "*")
        kind_set = (
            {k.strip().lower() for k in kinds_str.split(",") if k.strip()}
            if has_kind_filter
            else set()
        )

        # Precompute parsed metadata and symbol kinds once per node
        parsed_meta_by_id: Dict[str, Dict[str, Any]] = {}
        sym_kind_by_id: Dict[str, str] = {}
        node_by_id: Dict[str, Dict[str, Any]] = {}
        for n in raw_nodes:
            nid = n["id"]
            node_by_id[nid] = n
            meta = _parse_edge_metadata(n.get("metadata"))
            parsed_meta_by_id[nid] = meta
            sym_kind_by_id[nid] = self._infer_visjs_sym_kind(n, meta)

        def _passes_kinds(n: Dict[str, Any]) -> bool:
            if not has_kind_filter:
                return True
            nid = n["id"]
            ft = (n.get("file_type") or "code").strip().lower()
            return sym_kind_by_id[nid] in kind_set or ft in kind_set

        q = (query or "").strip().lower()
        if q:
            matched: List[Dict[str, Any]] = []
            for n in raw_nodes:
                if not _passes_kinds(n):
                    continue
                nid = n["id"]
                meta = parsed_meta_by_id[nid]
                qname = str(meta.get("qualified_name") or "").lower()
                src_file = str(n.get("source_file") or "").lower()
                label = str(n.get("label") or "").lower()
                if (
                    q in nid.lower()
                    or q in label
                    or q in src_file
                    or q in qname
                ):
                    matched.append(n)

            selected_nodes = matched[:limit]
            # Include true 1-hop connected neighbors when query results are under limit
            if selected_nodes and len(selected_nodes) < limit:
                seed_ids = {n["id"] for n in selected_nodes}
                selected_ids = set(seed_ids)
                seed_file_types = {
                    (n.get("file_type") or "code").strip().lower()
                    for n in selected_nodes
                }
                explicit_kinds_arg = bool(kinds_str)
                for e in raw_edges:
                    if len(selected_nodes) >= limit:
                        break
                    src, tgt = e.get("source"), e.get("target")
                    neighbor_id = None
                    if src in seed_ids and tgt not in selected_ids:
                        neighbor_id = tgt
                    elif tgt in seed_ids and src not in selected_ids:
                        neighbor_id = src
                    if not neighbor_id or neighbor_id not in node_by_id:
                        continue
                    nb = node_by_id[neighbor_id]
                    if not _passes_kinds(nb):
                        continue
                    # When kinds is omitted, avoid pulling unrelated cross-domain doc nodes
                    if not explicit_kinds_arg:
                        nb_ft = (nb.get("file_type") or "code").strip().lower()
                        if nb_ft not in seed_file_types:
                            continue
                    selected_ids.add(neighbor_id)
                    selected_nodes.append(nb)
        else:
            selected_nodes = [n for n in raw_nodes if _passes_kinds(n)][:limit]

        node_id_set = {n["id"] for n in selected_nodes}

        vis_nodes = []
        for n in selected_nodes:
            nid = n["id"]
            meta = parsed_meta_by_id[nid]
            sym_kind = sym_kind_by_id[nid]
            label = n.get("label", nid)
            vis_nodes.append({
                "id": nid,
                "label": label,
                "name": label,
                "qualified_name": meta.get("qualified_name") or nid,
                "kind": sym_kind,
                "title": f"{label} ({n.get('source_file', '')})",
                "group": n.get("file_type") or "code",
                "source_file": n.get("source_file"),
                "file_path": n.get("source_file"),
                "source_location": n.get("source_location"),
                "start_line": meta.get("start_line"),
                "end_line": meta.get("end_line"),
                "signature": meta.get("signature"),
                "docstring": meta.get("docstring") or meta.get("summary"),
                "community": n.get("community"),
                "metadata": meta,
            })

        vis_edges = []
        for e in raw_edges:
            if e["source"] in node_id_set and e["target"] in node_id_set:
                rel = e.get("relation", "")
                vis_edges.append({
                    "id": f"{e['source']}->{e['target']}:{rel}",
                    "from": e["source"],
                    "to": e["target"],
                    "source": e["source"],
                    "target": e["target"],
                    "label": rel,
                    "relation": rel,
                    "type": rel,
                    "kind": rel,
                    "confidence": e.get("confidence", "EXTRACTED"),
                    "confidence_score": e.get("confidence_score", 1.0),
                    "weight": e.get("weight", 1.0),
                    "metadata": _parse_edge_metadata(e.get("metadata")),
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
        project_scoped_schemas: bool = False,
    ):
        super().__init__(root_branch=root_branch, project_scoped_schemas=project_scoped_schemas)
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

    def close(self) -> None:
        if self._external_conn is None and self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

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
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, source, target, relation)
            )""")
            existing_edge_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(root_edges)").fetchall()
            }
            if "metadata" not in existing_edge_cols:
                conn.execute("ALTER TABLE root_edges ADD COLUMN metadata TEXT DEFAULT '{}'")
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

    def get_branch_lineage(self, project_id: str, branch_name: str, max_depth: int = 8) -> List[str]:
        if self.is_root_branch(branch_name):
            return [self.root_branch]
        conn = self.get_conn()
        parent_map: Dict[str, str] = {}
        try:
            rows = conn.execute(
                "SELECT branch_name, base_branch FROM graph_branches WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            for r in rows:
                if r["branch_name"] and r["base_branch"]:
                    parent_map[r["branch_name"]] = r["base_branch"]
        except sqlite3.OperationalError:
            pass
        return self._walk_lineage(branch_name, parent_map, max_depth=max_depth)

    def ensure_branch_schema(
        self,
        project_id: str,
        branch_name: str,
        author: str = "system",
        base_branch: Optional[str] = None,
    ) -> str:
        self.ensure_root_schema()
        schema_name = self._branch_schema_name(project_id, branch_name)
        clean_proj = project_id.replace("'", "''")
        conn = self.get_conn()

        existing_rows = conn.execute(
            "SELECT branch_name, base_branch FROM graph_branches WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        existing_parents = {r["branch_name"]: r["base_branch"] for r in existing_rows if r["branch_name"]}

        if base_branch and base_branch.strip() and base_branch.strip() != branch_name:
            resolved_base = base_branch.strip()
            self._validate_branch_hierarchy(branch_name, resolved_base, existing_parents, max_depth=8)
        else:
            resolved_base = existing_parents.get(branch_name) or self.root_branch

        with conn:
            # Upsert registry record
            conn.execute(
                """
                INSERT INTO graph_branches (project_id, branch_name, schema_name, base_branch, author, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT (project_id, branch_name) DO UPDATE SET
                    schema_name = excluded.schema_name,
                    base_branch = excluded.base_branch,
                    updated_at = datetime('now')
                """,
                (project_id, branch_name, schema_name, resolved_base, author),
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
                metadata TEXT DEFAULT '{{}}',
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, source, target, relation)
            )""")
            edge_upsert_cols = {
                r[1] for r in conn.execute(f"PRAGMA table_info({schema_name}_edge_upserts)").fetchall()
            }
            if "metadata" not in edge_upsert_cols:
                conn.execute(f"ALTER TABLE {schema_name}_edge_upserts ADD COLUMN metadata TEXT DEFAULT '{{}}'")

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
            SELECT project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata, created_at
            FROM {schema_name}_edge_upserts
            UNION ALL
            SELECT r.project_id, r.source, r.target, r.relation, r.confidence, r.confidence_score, r.source_file, r.source_location, r.weight, r.metadata, r.created_at
            FROM root_edges r
            WHERE r.project_id = '{clean_proj}'
              AND NOT EXISTS (
                  SELECT 1 FROM {schema_name}_edge_tombstones t
                  WHERE t.project_id = r.project_id AND t.source = r.source AND t.target = r.target AND t.relation = r.relation
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {schema_name}_edge_upserts u
                  WHERE u.project_id = r.project_id AND u.source = r.source AND u.target = r.target AND u.relation = r.relation
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
        base_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_root_schema()
        conn = self.get_conn()

        if self.is_root_branch(branch_name):
            # Direct application into root schema
            return self._sync_to_root(project_id, delta, partial, synced_files, deleted_files)

        schema_name = self.ensure_branch_schema(project_id, branch_name, author, base_branch=base_branch)
        lineage = self.get_branch_lineage(project_id, branch_name)
        parent_branch = lineage[-2] if len(lineage) >= 2 else self.root_branch

        with conn:
            if not partial:
                # Full sync on branch: compute delta relative to parent branch
                return self._sync_branch_full(conn, project_id, branch_name, schema_name, delta, parent_branch=parent_branch)
            else:
                # Partial sync on branch: scoped to synced_files & deleted_files
                return self._sync_branch_partial(
                    conn, project_id, branch_name, schema_name, delta, synced_files, deleted_files, parent_branch=parent_branch
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
            for n in _dedup_nodes(delta.nodes):
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
            for e in _dedup_edges(delta.edges):
                edge_meta_json = _serialize_edge_metadata(e)
                conn.execute(
                    """
                    INSERT INTO root_edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = excluded.confidence,
                        confidence_score = excluded.confidence_score,
                        source_file = excluded.source_file,
                        source_location = excluded.source_location,
                        weight = excluded.weight,
                        metadata = excluded.metadata
                    """,
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight, edge_meta_json),
                )
                edges_upserted += 1

            # Upsert hyperedges
            for h in _dedup_hyperedges(delta.hyperedges):
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
            "schema_name": "pb_root" if self.project_scoped_schemas else "root",
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
        parent_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Fetch baseline state (fast root path or resolved parent branch state)
        if parent_branch and not self.is_root_branch(parent_branch):
            parent_state = self.get_resolved_subgraph(project_id, parent_branch, limit=1_000_000)
            root_nodes = {n["id"]: dict(n) for n in parent_state.get("nodes", [])}
            for r in root_nodes.values():
                if isinstance(r.get("metadata"), str):
                    try:
                        r["metadata"] = json.loads(r["metadata"])
                    except Exception:
                        pass
                elif r.get("metadata") is None:
                    r["metadata"] = {}
            root_edges = {
                (e["source"], e["target"], e["relation"]): dict(e)
                for e in parent_state.get("edges", [])
            }
        else:
            root_nodes_rows = conn.execute("SELECT * FROM root_nodes WHERE project_id = ?", (project_id,)).fetchall()
            root_nodes = {r["id"]: dict(r) for r in root_nodes_rows}
            for r in root_nodes.values():
                if isinstance(r["metadata"], str):
                    try:
                        r["metadata"] = json.loads(r["metadata"])
                    except Exception:
                        pass

            root_edges_rows = conn.execute("SELECT * FROM root_edges WHERE project_id = ?", (project_id,)).fetchall()
            root_edges = {}
            for row in root_edges_rows:
                rd = dict(row)
                rd["metadata"] = _parse_edge_metadata(rd.get("metadata"))
                root_edges[(rd["source"], rd["target"], rd["relation"])] = rd

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
        for n in _dedup_nodes(delta.nodes):
            delta_node_ids.add(n.id)
            meta_dict = n.metadata if isinstance(n.metadata, dict) else {}
            meta_json = json.dumps(meta_dict)

            should_upsert = n.id not in root_nodes
            if not should_upsert:
                r = root_nodes[n.id]
                should_upsert = (
                    r["label"] != n.label
                    or r["file_type"] != n.file_type
                    or r["source_file"] != n.source_file
                    or (r["source_location"] or None) != (n.source_location or None)
                    or r["community"] != n.community
                    or r["metadata"] != meta_dict
                )
            if should_upsert:
                conn.execute(
                    f"""
                    INSERT INTO {schema_name}_node_upserts
                    (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
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

        # Root nodes missing from branch snapshot become tombstones
        for rid in root_nodes:
            if rid not in delta_node_ids:
                conn.execute(
                    f"INSERT OR IGNORE INTO {schema_name}_node_tombstones (project_id, id) VALUES (?, ?)",
                    (project_id, rid),
                )
                nodes_tombstoned += 1

        # Edge comparison
        delta_edge_keys = set()
        for e in _dedup_edges(delta.edges):
            ekey = (e.source, e.target, e.relation)
            delta_edge_keys.add(ekey)
            edge_meta_dict = _extract_edge_metadata_dict(e)
            edge_meta_json = json.dumps(edge_meta_dict)

            should_upsert = ekey not in root_edges
            if not should_upsert:
                r = root_edges[ekey]
                should_upsert = (
                    r["confidence"] != e.confidence
                    or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                    or (r["source_file"] or None) != (e.source_file or None)
                    or (r["source_location"] or None) != (e.source_location or None)
                    or abs(r["weight"] - e.weight) > 1e-4
                    or _parse_edge_metadata(r.get("metadata")) != edge_meta_dict
                )
            if should_upsert:
                conn.execute(
                    f"""
                    INSERT INTO {schema_name}_edge_upserts
                    (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = excluded.confidence,
                        confidence_score = excluded.confidence_score,
                        source_file = excluded.source_file,
                        source_location = excluded.source_location,
                        weight = excluded.weight,
                        metadata = excluded.metadata
                    """,
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight, edge_meta_json),
                )
                edges_upserted += 1

        # Missing root edges become tombstones
        for ekey in root_edges:
            if ekey not in delta_edge_keys:
                conn.execute(
                    f"INSERT OR IGNORE INTO {schema_name}_edge_tombstones (project_id, source, target, relation) VALUES (?, ?, ?, ?)",
                    (project_id, ekey[0], ekey[1], ekey[2]),
                )
                edges_tombstoned += 1

        for htid in delta.hyperedge_tombstones:
            conn.execute(f"DELETE FROM {schema_name}_hyperedge_upserts WHERE project_id = ? AND id = ?", (project_id, htid))
            conn.execute(f"INSERT OR IGNORE INTO {schema_name}_hyperedge_tombstones (project_id, id) VALUES (?, ?)", (project_id, htid))

        for h in _dedup_hyperedges(delta.hyperedges):
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
        parent_branch: Optional[str] = None,
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

        use_parent_state = bool(parent_branch and not self.is_root_branch(parent_branch))
        parent_nodes_by_file: Dict[str, Dict[str, Dict[str, Any]]] = {}
        parent_edges_by_file: Dict[str, Dict[Tuple[str, str, str], Dict[str, Any]]] = {}
        if use_parent_state and (deleted_file_set or synced_file_set):
            parent_state = self.get_resolved_subgraph(project_id, parent_branch, limit=1_000_000)
            all_target_files = deleted_file_set | synced_file_set
            for n in parent_state.get("nodes", []):
                sf = n.get("source_file")
                if sf in all_target_files:
                    nd = dict(n)
                    if isinstance(nd.get("metadata"), str):
                        try:
                            nd["metadata"] = json.loads(nd["metadata"])
                        except Exception:
                            pass
                    elif nd.get("metadata") is None:
                        nd["metadata"] = {}
                    parent_nodes_by_file.setdefault(sf, {})[nd["id"]] = nd
            for e in parent_state.get("edges", []):
                sf = e.get("source_file")
                if sf in all_target_files:
                    parent_edges_by_file.setdefault(sf, {})[(e["source"], e["target"], e["relation"])] = dict(e)

        # 1. Process deleted files
        for fpath in deleted_file_set:
            # Drop previous branch upserts for this file
            conn.execute(f"DELETE FROM {schema_name}_node_upserts WHERE project_id = ? AND source_file = ?", (project_id, fpath))
            conn.execute(f"DELETE FROM {schema_name}_edge_upserts WHERE project_id = ? AND source_file = ?", (project_id, fpath))

            # Tombstone all baseline nodes belonging to this file
            if use_parent_state:
                base_del_nodes = list(parent_nodes_by_file.get(fpath, {}).values())
            else:
                base_del_nodes = conn.execute("SELECT id FROM root_nodes WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
            for r in base_del_nodes:
                conn.execute(
                    f"INSERT OR IGNORE INTO {schema_name}_node_tombstones (project_id, id) VALUES (?, ?)",
                    (project_id, r["id"]),
                )
                nodes_tombstoned += 1

            # Tombstone all baseline edges belonging to this file
            if use_parent_state:
                base_del_edges = list(parent_edges_by_file.get(fpath, {}).values())
            else:
                base_del_edges = conn.execute("SELECT source, target, relation FROM root_edges WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
            for r in base_del_edges:
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

            if use_parent_state:
                root_nodes = parent_nodes_by_file.get(fpath, {})
                root_edges = parent_edges_by_file.get(fpath, {})
            else:
                root_nodes_rows = conn.execute("SELECT * FROM root_nodes WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
                root_nodes = {r["id"]: dict(r) for r in root_nodes_rows}
                for r in root_nodes.values():
                    if isinstance(r["metadata"], str):
                        try:
                            r["metadata"] = json.loads(r["metadata"])
                        except Exception:
                            pass

                root_edges_rows = conn.execute("SELECT * FROM root_edges WHERE project_id = ? AND source_file = ?", (project_id, fpath)).fetchall()
                root_edges = {}
                for row in root_edges_rows:
                    rd = dict(row)
                    rd["metadata"] = _parse_edge_metadata(rd.get("metadata"))
                    root_edges[(rd["source"], rd["target"], rd["relation"])] = rd

            # File scoped delta entities
            delta_file_nodes = _dedup_nodes([n for n in delta.nodes if n.source_file == fpath])
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

                should_upsert = n.id not in root_nodes
                if not should_upsert:
                    r = root_nodes[n.id]
                    should_upsert = (
                        r["label"] != n.label
                        or r["file_type"] != n.file_type
                        or (r["source_location"] or None) != (n.source_location or None)
                        or r["community"] != n.community
                        or r["metadata"] != meta_dict
                    )
                if should_upsert:
                    conn.execute(
                        f"""
                        INSERT INTO {schema_name}_node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
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

            # Edge file scoping
            delta_file_edges = _dedup_edges([e for e in delta.edges if e.source_file == fpath])
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
                edge_meta_dict = _extract_edge_metadata_dict(e)
                edge_meta_json = json.dumps(edge_meta_dict)
                conn.execute(
                    f"DELETE FROM {schema_name}_edge_tombstones WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                    (project_id, e.source, e.target, e.relation),
                )
                should_upsert = ekey not in root_edges
                if not should_upsert:
                    r = root_edges[ekey]
                    should_upsert = (
                        r["confidence"] != e.confidence
                        or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                        or (r["source_location"] or None) != (e.source_location or None)
                        or abs(r["weight"] - e.weight) > 1e-4
                        or _parse_edge_metadata(r.get("metadata")) != edge_meta_dict
                    )
                if should_upsert:
                    conn.execute(
                        f"""
                        INSERT INTO {schema_name}_edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                            confidence = excluded.confidence,
                            confidence_score = excluded.confidence_score,
                            source_file = excluded.source_file,
                            source_location = excluded.source_location,
                            weight = excluded.weight,
                            metadata = excluded.metadata
                        """,
                        (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight, edge_meta_json),
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

        for h in _dedup_hyperedges(delta.hyperedges):
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
        schema_name = self._branch_schema_name(project_id, branch_name)
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
                d["metadata"] = _parse_edge_metadata(d.get("metadata"))
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
                d = dict(r)
                d["metadata"] = _parse_edge_metadata(d.get("metadata"))
                res[(s, t, rel)] = d
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

        lineage = self.get_branch_lineage(project_id, branch_name)
        if len(lineage) > 2:
            return self._diff_resolved_subgraphs(project_id, self.root_branch, branch_name, file_filter, community)

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

    def promote_branch_to_root(
        self,
        project_id: str,
        branch_name: str,
        target_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.is_root_branch(branch_name):
            return {"status": "noop", "message": "Already root branch"}

        effective_target = target_branch.strip() if (target_branch and target_branch.strip()) else self.root_branch
        if effective_target == branch_name.strip():
            return {"status": "noop", "message": "Source and target branch are identical"}

        overlay = self._get_branch_overlay(project_id, branch_name)
        schema_name = overlay["schema_name"]
        conn = self.get_conn()

        if self.is_root_branch(effective_target):
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
                    edge_meta_json = _serialize_edge_metadata(e.get("metadata"))
                    conn.execute(
                        """
                        INSERT INTO root_edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                            confidence = excluded.confidence,
                            confidence_score = excluded.confidence_score,
                            source_file = excluded.source_file,
                            source_location = excluded.source_location,
                            weight = excluded.weight,
                            metadata = excluded.metadata
                        """,
                        (project_id, e["source"], e["target"], e["relation"], e["confidence"], e["confidence_score"], e.get("source_file"), e.get("source_location"), e["weight"], edge_meta_json),
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

                # 6. Delete branch registration & reparent children to root
                conn.execute("DELETE FROM graph_branches WHERE project_id = ? AND branch_name = ?", (project_id, branch_name))
                conn.execute(
                    "UPDATE graph_branches SET base_branch = ?, updated_at = datetime('now') WHERE project_id = ? AND base_branch = ?",
                    (self.root_branch, project_id, branch_name),
                )
        else:
            target_schema = self.ensure_branch_schema(project_id, effective_target)
            target_lineage = self.get_branch_lineage(project_id, effective_target)
            target_parent = target_lineage[-2] if len(target_lineage) >= 2 else self.root_branch
            target_parent_state = self.get_resolved_subgraph(project_id, target_parent, limit=1_000_000)
            target_parent_node_ids = {n["id"] for n in target_parent_state.get("nodes", [])}
            target_parent_edge_keys = {(e["source"], e["target"], e["relation"]) for e in target_parent_state.get("edges", [])}
            target_parent_hyperedge_ids = {h["id"] for h in target_parent_state.get("hyperedges", [])}

            with conn:
                # 1. Node tombstones into target_branch overlay
                for tid in overlay["node_tombstones"]:
                    conn.execute(f"DELETE FROM {target_schema}_node_upserts WHERE project_id = ? AND id = ?", (project_id, tid))
                    if tid in target_parent_node_ids:
                        conn.execute(
                            f"INSERT OR IGNORE INTO {target_schema}_node_tombstones (project_id, id) VALUES (?, ?)",
                            (project_id, tid),
                        )

                # 2. Node upserts into target_branch overlay
                for nid, n in overlay["node_upserts"].items():
                    meta_json = json.dumps(n["metadata"]) if isinstance(n.get("metadata"), dict) else str(n.get("metadata", "{}"))
                    conn.execute(f"DELETE FROM {target_schema}_node_tombstones WHERE project_id = ? AND id = ?", (project_id, nid))
                    conn.execute(
                        f"""
                        INSERT INTO {target_schema}_node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
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

                # 3. Edge tombstones into target_branch overlay
                for s, t, rel in overlay["edge_tombstones"]:
                    conn.execute(
                        f"DELETE FROM {target_schema}_edge_upserts WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                        (project_id, s, t, rel),
                    )
                    if (s, t, rel) in target_parent_edge_keys:
                        conn.execute(
                            f"INSERT OR IGNORE INTO {target_schema}_edge_tombstones (project_id, source, target, relation) VALUES (?, ?, ?, ?)",
                            (project_id, s, t, rel),
                        )

                # 4. Edge upserts into target_branch overlay
                for ekey, e in overlay["edge_upserts"].items():
                    edge_meta_json = _serialize_edge_metadata(e.get("metadata"))
                    conn.execute(
                        f"DELETE FROM {target_schema}_edge_tombstones WHERE project_id = ? AND source = ? AND target = ? AND relation = ?",
                        (project_id, e["source"], e["target"], e["relation"]),
                    )
                    conn.execute(
                        f"""
                        INSERT INTO {target_schema}_edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                            confidence = excluded.confidence,
                            confidence_score = excluded.confidence_score,
                            source_file = excluded.source_file,
                            source_location = excluded.source_location,
                            weight = excluded.weight,
                            metadata = excluded.metadata
                        """,
                        (project_id, e["source"], e["target"], e["relation"], e["confidence"], e["confidence_score"], e.get("source_file"), e.get("source_location"), e["weight"], edge_meta_json),
                    )

                # 4b. Hyperedge tombstones into target_branch overlay
                for htid in overlay.get("hyperedge_tombstones", set()):
                    conn.execute(f"DELETE FROM {target_schema}_hyperedge_upserts WHERE project_id = ? AND id = ?", (project_id, htid))
                    if htid in target_parent_hyperedge_ids:
                        conn.execute(
                            f"INSERT OR IGNORE INTO {target_schema}_hyperedge_tombstones (project_id, id) VALUES (?, ?)",
                            (project_id, htid),
                        )

                # 4c. Hyperedge upserts into target_branch overlay
                for hid, h in overlay.get("hyperedge_upserts", {}).items():
                    nodes_val = json.dumps(h["nodes"]) if not isinstance(h.get("nodes"), str) else h["nodes"]
                    conn.execute(f"DELETE FROM {target_schema}_hyperedge_tombstones WHERE project_id = ? AND id = ?", (project_id, hid))
                    conn.execute(
                        f"""
                        INSERT INTO {target_schema}_hyperedge_upserts
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
                        (h["id"], project_id, h["label"], nodes_val, h["relation"], h["confidence"], h["confidence_score"], h.get("source_file")),
                    )

                # 5. Drop source overlay tables & views
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

                # 6. Delete source branch registration & reparent children to target_branch
                conn.execute("DELETE FROM graph_branches WHERE project_id = ? AND branch_name = ?", (project_id, branch_name))
                conn.execute(
                    "UPDATE graph_branches SET base_branch = ?, updated_at = datetime('now') WHERE project_id = ? AND base_branch = ? AND branch_name != ?",
                    (effective_target, project_id, branch_name, effective_target),
                )

        return {
            "status": "promoted",
            "project_id": project_id,
            "branch": branch_name,
            "target_branch": effective_target,
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
            lineage = self.get_branch_lineage(project_id, branch_name)
            if len(lineage) > 2:
                return self._resolve_multi_level_subgraph(
                    project_id, branch_name, lineage, files=files, limit=limit
                )
            schema_name = self._branch_schema_name(project_id, branch_name)
            # Query resolved node/hyperedge views, and merge edges in O(N+M) to avoid SQLite tuple NOT IN scans
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
                upsert_edges_rows = conn.execute(
                    f"SELECT * FROM {schema_name}_edge_upserts WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                tomb_edges_rows = conn.execute(
                    f"SELECT source, target, relation FROM {schema_name}_edge_tombstones WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                root_edges_rows = conn.execute(
                    "SELECT * FROM root_edges WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                hyperedges_rows = conn.execute(f"SELECT * FROM {schema_name}_v_resolved_hyperedges WHERE project_id = ?", (project_id,)).fetchall()
            except sqlite3.OperationalError:
                # View doesn't exist yet, fall back to root
                return self.get_resolved_subgraph(project_id, self.root_branch, files=files, limit=limit)

            tomb_edge_set = {(r[0], r[1], r[2]) for r in tomb_edges_rows}
            upsert_edge_keys = {(r["source"], r["target"], r["relation"]) for r in upsert_edges_rows}
            edges_rows = list(upsert_edges_rows)
            for r in root_edges_rows:
                ekey = (r["source"], r["target"], r["relation"])
                if ekey not in tomb_edge_set and ekey not in upsert_edge_keys:
                    edges_rows.append(r)

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
            if r["source"] in node_id_set and r["target"] in node_id_set:
                d = dict(r)
                d["metadata"] = _parse_edge_metadata(d.get("metadata"))
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
        schema_name = self._branch_schema_name(project_id, branch_name)
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
            {
                "branch_name": self.root_branch,
                "schema_name": "pb_root" if self.project_scoped_schemas else "root",
                "is_root": True,
            }
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
        subgraph = self._resolve_subgraph_for_visjs(
            project_id, branch_name=branch_name, query=query, kinds=kinds, limit=limit
        )
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
        project_scoped_schemas: bool = False,
    ):
        super().__init__(root_branch=root_branch, project_scoped_schemas=project_scoped_schemas)
        self.connection_url = connection_url
        self._external_conn = conn
        self._conn = conn

    @property
    def conn(self) -> Optional[Any]:
        return self._conn

    @conn.setter
    def conn(self, value: Optional[Any]) -> None:
        self._conn = value

    def get_conn(self):
        if self._conn and not getattr(self._conn, "closed", False):
            return self._conn
        if self.connection_url:
            self._conn = psycopg2.connect(self.connection_url)
            self._conn.autocommit = True
            return self._conn
        try:
            from ..core.config import env
            self._conn = psycopg2.connect(env.db_url)
            self._conn.autocommit = True
            return self._conn
        except Exception as e:
            raise RuntimeError(f"Failed to connect to PostgreSQL: {e}")

    def close(self) -> None:
        if self._external_conn is None and self._conn is not None:
            try:
                if not getattr(self._conn, "closed", False):
                    self._conn.close()
            finally:
                self._conn = None

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
                metadata JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, source, target, relation)
            );
            """),
            psql.SQL("ALTER TABLE pb_root.edges ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;"),
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
        schema_name = self._branch_schema_name(project_id, branch_name)
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
                metadata JSONB DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (project_id, source, target, relation)
            );
            """).format(schema=schema_id),
            psql.SQL("ALTER TABLE {schema}.edge_upserts ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{{}}'::jsonb;").format(schema=schema_id),
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
            SELECT project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata, created_at
            FROM {schema}.edge_upserts
            UNION ALL
            SELECT r.project_id, r.source, r.target, r.relation, r.confidence, r.confidence_score, r.source_file, r.source_location, r.weight, r.metadata, r.created_at
            FROM pb_root.edges r
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

    def get_branch_lineage(self, project_id: str, branch_name: str, max_depth: int = 8) -> List[str]:
        if self.is_root_branch(branch_name):
            return [self.root_branch]
        conn = self.get_conn()
        parent_map: Dict[str, str] = {}
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT branch_name, base_branch FROM pb_root.graph_branches WHERE project_id = %s",
                    (project_id,),
                )
                for r in cur.fetchall():
                    if r["branch_name"] and r["base_branch"]:
                        parent_map[r["branch_name"]] = r["base_branch"]
        except psycopg2.Error:
            pass
        return self._walk_lineage(branch_name, parent_map, max_depth=max_depth)

    def ensure_branch_schema(
        self,
        project_id: str,
        branch_name: str,
        author: str = "system",
        base_branch: Optional[str] = None,
    ) -> str:
        self.ensure_root_schema()
        schema_name = self._branch_schema_name(project_id, branch_name)
        conn = self.get_conn()

        existing_parents: Dict[str, str] = {}
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT branch_name, base_branch FROM pb_root.graph_branches WHERE project_id = %s",
                (project_id,),
            )
            for r in cur.fetchall():
                if r["branch_name"]:
                    existing_parents[r["branch_name"]] = r["base_branch"]

        if base_branch and base_branch.strip() and base_branch.strip() != branch_name:
            resolved_base = base_branch.strip()
            self._validate_branch_hierarchy(branch_name, resolved_base, existing_parents, max_depth=8)
        else:
            resolved_base = existing_parents.get(branch_name) or self.root_branch

        with conn.cursor() as cur:
            # Register branch
            cur.execute(
                """
                INSERT INTO pb_root.graph_branches (project_id, branch_name, schema_name, base_branch, author, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (project_id, branch_name) DO UPDATE SET
                    schema_name = EXCLUDED.schema_name,
                    base_branch = EXCLUDED.base_branch,
                    updated_at = NOW()
                """,
                (project_id, branch_name, schema_name, resolved_base, author),
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
        base_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_root_schema()
        conn = self.get_conn()

        if self.is_root_branch(branch_name):
            return self._sync_to_root_pg(conn, project_id, delta, partial, synced_files, deleted_files)

        schema_name = self.ensure_branch_schema(project_id, branch_name, author, base_branch=base_branch)
        lineage = self.get_branch_lineage(project_id, branch_name)
        parent_branch = lineage[-2] if len(lineage) >= 2 else self.root_branch
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not partial:
                return self._sync_branch_full_pg(cur, project_id, branch_name, schema_name, delta, parent_branch=parent_branch)
            else:
                return self._sync_branch_partial_pg(cur, project_id, branch_name, schema_name, delta, synced_files, deleted_files, parent_branch=parent_branch)

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
            for n in _dedup_nodes(delta.nodes):
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
            for e in _dedup_edges(delta.edges):
                edge_meta_json = _serialize_edge_metadata(e)
                cur.execute(
                    """
                    INSERT INTO pb_root.edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = EXCLUDED.confidence,
                        confidence_score = EXCLUDED.confidence_score,
                        source_file = EXCLUDED.source_file,
                        source_location = EXCLUDED.source_location,
                        weight = EXCLUDED.weight,
                        metadata = EXCLUDED.metadata
                    """,
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight, edge_meta_json),
                )
                edges_upserted += 1

            for h in _dedup_hyperedges(delta.hyperedges):
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
        parent_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        schema_id = psql.Identifier(schema_name)

        # Clear overlay
        for tbl in ["node_upserts", "node_tombstones", "edge_upserts", "edge_tombstones", "hyperedge_upserts", "hyperedge_tombstones", "synced_files"]:
            cur.execute(psql.SQL("DELETE FROM {schema}.{table} WHERE project_id = %s;").format(schema=schema_id, table=psql.Identifier(tbl)), (project_id,))

        if parent_branch and not self.is_root_branch(parent_branch):
            parent_state = self.get_resolved_subgraph(project_id, parent_branch, limit=1_000_000)
            root_nodes = {n["id"]: dict(n) for n in parent_state.get("nodes", [])}
            root_edges = {(e["source"], e["target"], e["relation"]): dict(e) for e in parent_state.get("edges", [])}
        else:
            cur.execute("SELECT * FROM pb_root.nodes WHERE project_id = %s", (project_id,))
            root_nodes = {r["id"]: dict(r) for r in cur.fetchall()}

            cur.execute("SELECT * FROM pb_root.edges WHERE project_id = %s", (project_id,))
            root_edges = {}
            for row in cur.fetchall():
                rd = dict(row)
                rd["metadata"] = _parse_edge_metadata(rd.get("metadata"))
                root_edges[(rd["source"], rd["target"], rd["relation"])] = rd

        nodes_upserted = 0
        nodes_tombstoned = 0
        edges_upserted = 0
        edges_tombstoned = 0

        delta_node_ids = set()
        for n in _dedup_nodes(delta.nodes):
            delta_node_ids.add(n.id)
            meta_json = json.dumps(n.metadata or {})
            should_upsert = n.id not in root_nodes
            if not should_upsert:
                r = root_nodes[n.id]
                should_upsert = (
                    r["label"] != n.label
                    or r["file_type"] != n.file_type
                    or r["source_file"] != n.source_file
                    or (r["source_location"] or None) != (n.source_location or None)
                    or r["community"] != n.community
                    or r["metadata"] != (n.metadata or {})
                )
            if should_upsert:
                cur.execute(
                    psql.SQL("""
                    INSERT INTO {schema}.node_upserts
                    (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (project_id, id) DO UPDATE SET
                        label = EXCLUDED.label,
                        file_type = EXCLUDED.file_type,
                        source_file = EXCLUDED.source_file,
                        source_location = EXCLUDED.source_location,
                        community = EXCLUDED.community,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW();
                    """).format(schema=schema_id),
                    (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                )
                nodes_upserted += 1

        for rid in root_nodes:
            if rid not in delta_node_ids:
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                    (project_id, rid),
                )
                nodes_tombstoned += 1

        delta_edge_keys = set()
        for e in _dedup_edges(delta.edges):
            ekey = (e.source, e.target, e.relation)
            delta_edge_keys.add(ekey)
            edge_meta_dict = _extract_edge_metadata_dict(e)
            edge_meta_json = json.dumps(edge_meta_dict)
            should_upsert = ekey not in root_edges
            if not should_upsert:
                r = root_edges[ekey]
                should_upsert = (
                    r["confidence"] != e.confidence
                    or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                    or (r["source_file"] or None) != (e.source_file or None)
                    or (r["source_location"] or None) != (e.source_location or None)
                    or abs(r["weight"] - e.weight) > 1e-4
                    or _parse_edge_metadata(r.get("metadata")) != edge_meta_dict
                )
            if should_upsert:
                cur.execute(
                    psql.SQL("""
                    INSERT INTO {schema}.edge_upserts
                    (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                        confidence = EXCLUDED.confidence,
                        confidence_score = EXCLUDED.confidence_score,
                        source_file = EXCLUDED.source_file,
                        source_location = EXCLUDED.source_location,
                        weight = EXCLUDED.weight,
                        metadata = EXCLUDED.metadata;
                    """).format(schema=schema_id),
                    (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight, edge_meta_json),
                )
                edges_upserted += 1

        for ekey in root_edges:
            if ekey not in delta_edge_keys:
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                    (project_id, ekey[0], ekey[1], ekey[2]),
                )
                edges_tombstoned += 1

        for htid in delta.hyperedge_tombstones:
            cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_upserts WHERE project_id = %s AND id = %s;").format(schema=schema_id), (project_id, htid))
            cur.execute(psql.SQL("INSERT INTO {schema}.hyperedge_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id), (project_id, htid))

        for h in _dedup_hyperedges(delta.hyperedges):
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
        parent_branch: Optional[str] = None,
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

        use_parent_state = bool(parent_branch and not self.is_root_branch(parent_branch))
        parent_nodes_by_file: Dict[str, Dict[str, Dict[str, Any]]] = {}
        parent_edges_by_file: Dict[str, Dict[Tuple[str, str, str], Dict[str, Any]]] = {}
        if use_parent_state and (deleted_file_set or synced_file_set):
            parent_state = self.get_resolved_subgraph(project_id, parent_branch, limit=1_000_000)
            all_target_files = deleted_file_set | synced_file_set
            for n in parent_state.get("nodes", []):
                sf = n.get("source_file")
                if sf in all_target_files:
                    parent_nodes_by_file.setdefault(sf, {})[n["id"]] = dict(n)
            for e in parent_state.get("edges", []):
                sf = e.get("source_file")
                if sf in all_target_files:
                    parent_edges_by_file.setdefault(sf, {})[(e["source"], e["target"], e["relation"])] = dict(e)

        for fpath in deleted_file_set:
            cur.execute(psql.SQL("DELETE FROM {schema}.node_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))
            cur.execute(psql.SQL("DELETE FROM {schema}.edge_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))

            if use_parent_state:
                base_del_nodes = list(parent_nodes_by_file.get(fpath, {}).values())
            else:
                cur.execute("SELECT id FROM pb_root.nodes WHERE project_id = %s AND source_file = %s", (project_id, fpath))
                base_del_nodes = cur.fetchall()
            for r in base_del_nodes:
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                    (project_id, r["id"]),
                )
                nodes_tombstoned += 1

            if use_parent_state:
                base_del_edges = list(parent_edges_by_file.get(fpath, {}).values())
            else:
                cur.execute("SELECT source, target, relation FROM pb_root.edges WHERE project_id = %s AND source_file = %s", (project_id, fpath))
                base_del_edges = cur.fetchall()
            for r in base_del_edges:
                cur.execute(
                    psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;").format(schema=schema_id),
                    (project_id, r["source"], r["target"], r["relation"]),
                )
                edges_tombstoned += 1

            cur.execute(psql.SQL("DELETE FROM {schema}.synced_files WHERE project_id = %s AND file_path = %s;").format(schema=schema_id), (project_id, fpath))

        for fpath in synced_file_set:
            cur.execute(psql.SQL("DELETE FROM {schema}.node_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))
            cur.execute(psql.SQL("DELETE FROM {schema}.edge_upserts WHERE project_id = %s AND source_file = %s;").format(schema=schema_id), (project_id, fpath))

            if use_parent_state:
                root_nodes = parent_nodes_by_file.get(fpath, {})
                root_edges = parent_edges_by_file.get(fpath, {})
            else:
                cur.execute("SELECT * FROM pb_root.nodes WHERE project_id = %s AND source_file = %s", (project_id, fpath))
                root_nodes = {r["id"]: dict(r) for r in cur.fetchall()}

                cur.execute("SELECT * FROM pb_root.edges WHERE project_id = %s AND source_file = %s", (project_id, fpath))
                root_edges = {}
                for row in cur.fetchall():
                    rd = dict(row)
                    rd["metadata"] = _parse_edge_metadata(rd.get("metadata"))
                    root_edges[(rd["source"], rd["target"], rd["relation"])] = rd

            delta_file_nodes = _dedup_nodes([n for n in delta.nodes if n.source_file == fpath])
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

                should_upsert = n.id not in root_nodes
                if not should_upsert:
                    r = root_nodes[n.id]
                    should_upsert = (
                        r["label"] != n.label
                        or r["file_type"] != n.file_type
                        or (r["source_location"] or None) != (n.source_location or None)
                        or r["community"] != n.community
                        or r["metadata"] != (n.metadata or {})
                    )
                if should_upsert:
                    cur.execute(
                        psql.SQL("""
                        INSERT INTO {schema}.node_upserts
                        (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (project_id, id) DO UPDATE SET
                            label = EXCLUDED.label,
                            file_type = EXCLUDED.file_type,
                            source_file = EXCLUDED.source_file,
                            source_location = EXCLUDED.source_location,
                            community = EXCLUDED.community,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW();
                        """).format(schema=schema_id),
                        (n.id, project_id, n.label, n.file_type, n.source_file, n.source_location, n.community, meta_json),
                    )
                    nodes_upserted += 1

            delta_file_edges = _dedup_edges([e for e in delta.edges if e.source_file == fpath])
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
                edge_meta_dict = _extract_edge_metadata_dict(e)
                edge_meta_json = json.dumps(edge_meta_dict)
                cur.execute(
                    psql.SQL("DELETE FROM {schema}.edge_tombstones WHERE project_id = %s AND source = %s AND target = %s AND relation = %s;").format(schema=schema_id),
                    (project_id, e.source, e.target, e.relation),
                )
                should_upsert = ekey not in root_edges
                if not should_upsert:
                    r = root_edges[ekey]
                    should_upsert = (
                        r["confidence"] != e.confidence
                        or abs(r["confidence_score"] - e.confidence_score) > 1e-4
                        or (r["source_location"] or None) != (e.source_location or None)
                        or abs(r["weight"] - e.weight) > 1e-4
                        or _parse_edge_metadata(r.get("metadata")) != edge_meta_dict
                    )
                if should_upsert:
                    cur.execute(
                        psql.SQL("""
                        INSERT INTO {schema}.edge_upserts
                        (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                            confidence = EXCLUDED.confidence,
                            confidence_score = EXCLUDED.confidence_score,
                            source_file = EXCLUDED.source_file,
                            source_location = EXCLUDED.source_location,
                            weight = EXCLUDED.weight,
                            metadata = EXCLUDED.metadata;
                        """).format(schema=schema_id),
                        (project_id, e.source, e.target, e.relation, e.confidence, e.confidence_score, e.source_file, e.source_location, e.weight, edge_meta_json),
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

        for h in _dedup_hyperedges(delta.hyperedges):
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
        schema_name = self._branch_schema_name(project_id, branch_name)
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
                    d = dict(r)
                    d["metadata"] = _parse_edge_metadata(d.get("metadata"))
                    edge_upserts[(d["source"], d["target"], d["relation"])] = d
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
                    d = dict(r)
                    d["metadata"] = _parse_edge_metadata(d.get("metadata"))
                    res[(s, t, rel)] = d
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

        lineage = self.get_branch_lineage(project_id, branch_name)
        if len(lineage) > 2:
            return self._diff_resolved_subgraphs(project_id, self.root_branch, branch_name, file_filter, community)

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

    def promote_branch_to_root(
        self,
        project_id: str,
        branch_name: str,
        target_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.is_root_branch(branch_name):
            return {"status": "noop", "message": "Already root branch"}

        effective_target = target_branch.strip() if (target_branch and target_branch.strip()) else self.root_branch
        if effective_target == branch_name.strip():
            return {"status": "noop", "message": "Source and target branch are identical"}

        overlay = self._get_branch_overlay(project_id, branch_name)
        schema_name = overlay["schema_name"]
        schema_id = psql.Identifier(schema_name)
        conn = self.get_conn()
        is_autocommit = getattr(conn, "autocommit", False)

        if self.is_root_branch(effective_target):
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
                        edge_meta_json = _serialize_edge_metadata(e.get("metadata"))
                        cur.execute(
                            """
                            INSERT INTO pb_root.edges (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                                confidence = EXCLUDED.confidence,
                                confidence_score = EXCLUDED.confidence_score,
                                source_file = EXCLUDED.source_file,
                                source_location = EXCLUDED.source_location,
                                weight = EXCLUDED.weight,
                                metadata = EXCLUDED.metadata
                            """,
                            (project_id, e["source"], e["target"], e["relation"], e["confidence"], e["confidence_score"], e.get("source_file"), e.get("source_location"), e["weight"], edge_meta_json),
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

                    # 6. Delete branch registration & reparent children to root
                    cur.execute("DELETE FROM pb_root.graph_branches WHERE project_id = %s AND branch_name = %s", (project_id, branch_name))
                    cur.execute(
                        "UPDATE pb_root.graph_branches SET base_branch = %s, updated_at = NOW() WHERE project_id = %s AND base_branch = %s",
                        (self.root_branch, project_id, branch_name),
                    )

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
        else:
            target_schema = self.ensure_branch_schema(project_id, effective_target)
            target_schema_id = psql.Identifier(target_schema)
            target_lineage = self.get_branch_lineage(project_id, effective_target)
            target_parent = target_lineage[-2] if len(target_lineage) >= 2 else self.root_branch
            target_parent_state = self.get_resolved_subgraph(project_id, target_parent, limit=1_000_000)
            target_parent_node_ids = {n["id"] for n in target_parent_state.get("nodes", [])}
            target_parent_edge_keys = {(e["source"], e["target"], e["relation"]) for e in target_parent_state.get("edges", [])}
            target_parent_hyperedge_ids = {h["id"] for h in target_parent_state.get("hyperedges", [])}

            try:
                with conn.cursor() as cur:
                    if is_autocommit:
                        cur.execute("BEGIN;")

                    for tid in overlay["node_tombstones"]:
                        cur.execute(psql.SQL("DELETE FROM {schema}.node_upserts WHERE project_id = %s AND id = %s;").format(schema=target_schema_id), (project_id, tid))
                        if tid in target_parent_node_ids:
                            cur.execute(
                                psql.SQL("INSERT INTO {schema}.node_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=target_schema_id),
                                (project_id, tid),
                            )

                    for nid, n in overlay["node_upserts"].items():
                        cur.execute(psql.SQL("DELETE FROM {schema}.node_tombstones WHERE project_id = %s AND id = %s;").format(schema=target_schema_id), (project_id, nid))
                        cur.execute(
                            psql.SQL("""
                            INSERT INTO {schema}.node_upserts (id, project_id, label, file_type, source_file, source_location, community, metadata, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (project_id, id) DO UPDATE SET
                                label = EXCLUDED.label,
                                file_type = EXCLUDED.file_type,
                                source_file = EXCLUDED.source_file,
                                source_location = EXCLUDED.source_location,
                                community = EXCLUDED.community,
                                metadata = EXCLUDED.metadata,
                                updated_at = NOW();
                            """).format(schema=target_schema_id),
                            (n["id"], project_id, n["label"], n["file_type"], n["source_file"], n.get("source_location"), n.get("community"), json.dumps(n.get("metadata", {}))),
                        )

                    for s, t, rel in overlay["edge_tombstones"]:
                        cur.execute(
                            psql.SQL("DELETE FROM {schema}.edge_upserts WHERE project_id = %s AND source = %s AND target = %s AND relation = %s;").format(schema=target_schema_id),
                            (project_id, s, t, rel),
                        )
                        if (s, t, rel) in target_parent_edge_keys:
                            cur.execute(
                                psql.SQL("INSERT INTO {schema}.edge_tombstones (project_id, source, target, relation) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING;").format(schema=target_schema_id),
                                (project_id, s, t, rel),
                            )

                    for ekey, e in overlay["edge_upserts"].items():
                        edge_meta_json = _serialize_edge_metadata(e.get("metadata"))
                        cur.execute(
                            psql.SQL("DELETE FROM {schema}.edge_tombstones WHERE project_id = %s AND source = %s AND target = %s AND relation = %s;").format(schema=target_schema_id),
                            (project_id, e["source"], e["target"], e["relation"]),
                        )
                        cur.execute(
                            psql.SQL("""
                            INSERT INTO {schema}.edge_upserts (project_id, source, target, relation, confidence, confidence_score, source_file, source_location, weight, metadata)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (project_id, source, target, relation) DO UPDATE SET
                                confidence = EXCLUDED.confidence,
                                confidence_score = EXCLUDED.confidence_score,
                                source_file = EXCLUDED.source_file,
                                source_location = EXCLUDED.source_location,
                                weight = EXCLUDED.weight,
                                metadata = EXCLUDED.metadata;
                            """).format(schema=target_schema_id),
                            (project_id, e["source"], e["target"], e["relation"], e["confidence"], e["confidence_score"], e.get("source_file"), e.get("source_location"), e["weight"], edge_meta_json),
                        )

                    for htid in overlay.get("hyperedge_tombstones", set()):
                        cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_upserts WHERE project_id = %s AND id = %s;").format(schema=target_schema_id), (project_id, htid))
                        if htid in target_parent_hyperedge_ids:
                            cur.execute(
                                psql.SQL("INSERT INTO {schema}.hyperedge_tombstones (project_id, id) VALUES (%s, %s) ON CONFLICT DO NOTHING;").format(schema=target_schema_id),
                                (project_id, htid),
                            )

                    for hid, h in overlay.get("hyperedge_upserts", {}).items():
                        nodes_list = h["nodes"] if isinstance(h["nodes"], list) else json.loads(h["nodes"])
                        cur.execute(psql.SQL("DELETE FROM {schema}.hyperedge_tombstones WHERE project_id = %s AND id = %s;").format(schema=target_schema_id), (project_id, hid))
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
                            """).format(schema=target_schema_id),
                            (h["id"], project_id, h["label"], nodes_list, h["relation"], h["confidence"], h["confidence_score"], h.get("source_file")),
                        )

                    cur.execute(psql.SQL("DROP SCHEMA IF EXISTS {schema} CASCADE;").format(schema=schema_id))
                    cur.execute("DELETE FROM pb_root.graph_branches WHERE project_id = %s AND branch_name = %s", (project_id, branch_name))
                    cur.execute(
                        "UPDATE pb_root.graph_branches SET base_branch = %s, updated_at = NOW() WHERE project_id = %s AND base_branch = %s AND branch_name != %s",
                        (effective_target, project_id, branch_name, effective_target),
                    )

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
            "target_branch": effective_target,
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
        if not self.is_root_branch(branch_name):
            lineage = self.get_branch_lineage(project_id, branch_name)
            if len(lineage) > 2:
                return self._resolve_multi_level_subgraph(
                    project_id, branch_name, lineage, files=files, limit=limit
                )
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
                schema_name = self._branch_schema_name(project_id, branch_name)
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
        edges: List[Dict[str, Any]] = []
        for r in edges_rows:
            if r["source"] in node_id_set and r["target"] in node_id_set:
                d = dict(r)
                d["metadata"] = _parse_edge_metadata(d.get("metadata"))
                edges.append(d)
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
        schema_name = self._branch_schema_name(project_id, branch_name)
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
        subgraph = self._resolve_subgraph_for_visjs(
            project_id, branch_name=branch_name, query=query, kinds=kinds, limit=limit
        )
        return self._format_legacy_visjs(subgraph, query=query, kinds=kinds, limit=limit)


# =====================================================================
# 3-Tier Multi-Project GraphStore Router (1 DB per Project + 1 Schema per Branch)
# =====================================================================


class MultiProjectGraphStore(GraphStore):
    """Routes each project_id to its dedicated physical Project DB via tenant_manager.

    - PostgreSQL: dedicated DB `pb_proj_*` with `pb_root` and `pb_br_*` schemas.
    - SQLite: dedicated file `<master_dir>/pb_projects/pb_proj_*/graph.sqlite`.
    Keeps `master_projects` and `master_branches` in Master DB synchronized.
    """

    def __init__(
        self,
        master_db_path: Optional[str] = None,
        connection_url: Optional[str] = None,
        root_branch: str = "main",
    ):
        super().__init__(root_branch=root_branch, project_scoped_schemas=True)
        self.master_db_path = master_db_path
        self.connection_url = connection_url
        self._stores: Dict[str, GraphStore] = {}
        self._store_targets: Dict[str, Tuple[bool, str, str]] = {}
        self._lock = threading.RLock()

    def _ensure_master_binding(self) -> None:
        from ..core.db import db as core_db
        from ..core.tenant_manager import tenant_manager

        if getattr(core_db, "conn", None) is None:
            core_db.connect()
        tenant_manager.bind_master_db(core_db)
        tenant_manager.ensure_master_catalog()

    def _normalize_project_id(self, project_id: str) -> str:
        from ..core.tenant_manager import parse_tenant_from_user_id

        return parse_tenant_from_user_id(project_id, default_branch=self.root_branch)[0]

    def _get_project_store(self, project_id: str) -> Tuple[GraphStore, Dict[str, Any]]:
        from ..core.tenant_manager import tenant_manager

        with self._lock:
            self._ensure_master_binding()
            proj = self._normalize_project_id(project_id)
            target = tenant_manager.get_project_graph_target(proj)
            target_key = (
                bool(target["is_postgres"]),
                str(target.get("dsn") or target.get("sqlite_path") or ":memory:"),
                str(target["db_name"]),
            )
            cached = self._stores.get(proj)
            if cached is not None and self._store_targets.get(proj) == target_key:
                return cached, target
            if cached is not None:
                try:
                    cached.close()
                except Exception:
                    pass
            if target["is_postgres"]:
                delegate: GraphStore = PostgresSchemaGraphStore(
                    connection_url=target["dsn"],
                    root_branch=self.root_branch,
                    project_scoped_schemas=True,
                )
            else:
                delegate = SqliteOverlayGraphStore(
                    db_path=target["sqlite_path"] or ":memory:",
                    root_branch=self.root_branch,
                    project_scoped_schemas=True,
                )
            self._stores[proj] = delegate
            self._store_targets[proj] = target_key
            return delegate, target

    def close(self) -> None:
        with self._lock:
            for st in list(self._stores.values()):
                try:
                    st.close()
                except Exception:
                    pass
            self._stores.clear()
            self._store_targets.clear()

    def ensure_root_schema(self) -> None:
        with self._lock:
            self._ensure_master_binding()
            for st in list(self._stores.values()):
                st.ensure_root_schema()

    def get_branch_lineage(
        self, project_id: str, branch_name: str, max_depth: int = 8
    ) -> List[str]:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate.get_branch_lineage(proj, branch_name, max_depth=max_depth)

    def ensure_branch_schema(
        self,
        project_id: str,
        branch_name: str,
        author: str = "system",
        base_branch: Optional[str] = None,
    ) -> str:
        from ..core.tenant_manager import tenant_manager

        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        if self.is_root_branch(branch_name):
            delegate.ensure_root_schema()
            info = tenant_manager.ensure_branch_schema(
                proj,
                self.root_branch,
                base_branch=self.root_branch,
                author=author or "system",
                has_graph=True,
                record_lineage=False,
            )
            return str(info["schema_name"])

        schema_name = delegate.ensure_branch_schema(
            proj, branch_name, author=author, base_branch=base_branch
        )
        has_explicit_base = bool(
            base_branch and base_branch.strip() and base_branch.strip() != branch_name.strip()
        )
        tenant_manager.ensure_branch_schema(
            proj,
            branch_name,
            base_branch=base_branch or self.root_branch,
            author=author or "system",
            has_graph=True,
            record_lineage=has_explicit_base,
            preserve_existing_base=not has_explicit_base,
        )
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
        base_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        from ..core.tenant_manager import tenant_manager

        proj = self._normalize_project_id(project_id)
        delegate, target = self._get_project_store(proj)
        res = delegate.sync_branch_delta(
            proj,
            branch_name,
            delta,
            partial=partial,
            synced_files=synced_files,
            deleted_files=deleted_files,
            author=author,
            base_branch=base_branch,
        )
        has_explicit_base = bool(
            base_branch and base_branch.strip() and base_branch.strip() != branch_name.strip()
        )
        branch_info = tenant_manager.ensure_branch_schema(
            proj,
            branch_name,
            base_branch=base_branch or self.root_branch,
            author=author or "system",
            has_graph=True,
            record_lineage=has_explicit_base,
            preserve_existing_base=not has_explicit_base,
        )
        res["db_name"] = target["db_name"]
        if self.is_root_branch(branch_name):
            res["schema_name"] = branch_info["schema_name"]
        else:
            res.setdefault("schema_name", branch_info["schema_name"])
        return res

    sync_delta = sync_branch_delta

    def diff_branch_vs_root(
        self,
        project_id: str,
        branch_name: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate.diff_branch_vs_root(
            proj, branch_name, file_filter=file_filter, community=community
        )

    def diff_branches(
        self,
        project_id: str,
        base_branch: str,
        target_branch: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate.diff_branches(
            proj, base_branch, target_branch, file_filter=file_filter, community=community
        )

    def promote_branch_to_root(
        self,
        project_id: str,
        branch_name: str,
        target_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        from ..core.tenant_manager import tenant_manager

        proj = self._normalize_project_id(project_id)
        delegate, target = self._get_project_store(proj)
        res = delegate.promote_branch_to_root(
            proj, branch_name, target_branch=target_branch
        )
        if res.get("status") == "promoted" and not self.is_root_branch(branch_name):
            effective_target = str(res.get("target_branch") or self.root_branch)
            tenant_manager.ensure_branch_schema(
                proj,
                effective_target,
                base_branch=self.root_branch,
                has_graph=True,
                record_lineage=False,
                preserve_existing_base=True,
            )
            now_ms = int(time.time() * 1000)
            tenant_manager._master_execute(
                "UPDATE master_branches SET base_branch = ?, updated_at = ? "
                "WHERE project_id = ? AND base_branch = ? AND branch_name != ?",
                (effective_target, now_ms, proj, branch_name, effective_target),
            )
            try:
                tenant_manager.drop_branch_schema(proj, branch_name)
            except Exception as exc:
                logger.debug("Master branch drop warning on promote %s:%s (%s)", proj, branch_name, exc)
        res["db_name"] = target["db_name"]
        return res

    def get_resolved_subgraph(
        self,
        project_id: str,
        branch_name: str,
        files: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate.get_resolved_subgraph(proj, branch_name, files=files, limit=limit)

    def get_stats(self, project_id: str, branch_name: str = "main") -> Dict[str, Any]:
        proj = self._normalize_project_id(project_id)
        delegate, target = self._get_project_store(proj)
        stats = delegate.get_stats(proj, branch_name)
        stats["db_name"] = target["db_name"]
        return stats

    def list_projects(self) -> List[str]:
        from ..core.tenant_manager import tenant_manager

        with self._lock:
            self._ensure_master_binding()
            rows = tenant_manager._master_fetchall(
                "SELECT project_id FROM master_projects WHERE status = 'active' ORDER BY project_id ASC"
            )
            projs = {str(r["project_id"]) for r in rows if r.get("project_id")}
            projs |= set(self._stores.keys())
            return sorted(projs)

    def delete_project(self, project_id: str) -> bool:
        from ..core.tenant_manager import tenant_manager

        proj = self._normalize_project_id(project_id)
        with self._lock:
            self._ensure_master_binding()
            cached = self._stores.pop(proj, None)
            self._store_targets.pop(proj, None)
            if cached is not None:
                try:
                    cached.delete_project(proj)
                finally:
                    cached.close()
        tenant_manager.drop_project_database(proj)
        return True

    def delete_branch(self, project_id: str, branch_name: str) -> bool:
        from ..core.tenant_manager import tenant_manager

        if self.is_root_branch(branch_name):
            return False
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        ok = delegate.delete_branch(proj, branch_name)
        try:
            tenant_manager.drop_branch_schema(proj, branch_name)
        except Exception as exc:
            logger.debug("Master branch drop warning on delete %s:%s (%s)", proj, branch_name, exc)
        return ok

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        from ..core.tenant_manager import tenant_manager

        proj = self._normalize_project_id(project_id)
        delegate, target = self._get_project_store(proj)
        branches = delegate.list_branches(proj)
        mb_rows = tenant_manager._master_fetchall(
            "SELECT * FROM master_branches WHERE project_id = ? ORDER BY is_root DESC, created_at ASC",
            (proj,),
        )
        mb_map = {str(r["branch_name"]): r for r in mb_rows if r.get("branch_name")}
        seen: Set[str] = set()
        for b in branches:
            bname = str(b.get("branch_name") or "")
            seen.add(bname)
            b["db_name"] = target["db_name"]
            if bname in mb_map:
                mb = mb_map[bname]
                b.setdefault("base_branch", mb.get("base_branch"))
                b["has_graph_schema"] = bool(mb.get("has_graph_schema", 1))
                b["has_doc_schema"] = bool(mb.get("has_doc_schema", 1))
                if b.get("is_root"):
                    b["schema_name"] = mb.get("schema_name") or "pb_root"
        for bname, mb in mb_map.items():
            if bname not in seen:
                branches.append(
                    {
                        "branch_name": bname,
                        "schema_name": mb.get("schema_name"),
                        "base_branch": mb.get("base_branch"),
                        "author": mb.get("author") or "system",
                        "is_root": bool(mb.get("is_root")),
                        "has_graph_schema": bool(mb.get("has_graph_schema", 1)),
                        "has_doc_schema": bool(mb.get("has_doc_schema", 1)),
                        "db_name": target["db_name"],
                    }
                )
        return branches

    def to_legacy_visjs(
        self,
        project_id: str,
        branch_name: str = "main",
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate.to_legacy_visjs(
            proj, branch_name=branch_name, query=query, kinds=kinds, limit=limit
        )

    def _get_branch_overlay(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate._get_branch_overlay(proj, branch_name)

    def _get_root_nodes_by_ids(
        self, project_id: str, node_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate._get_root_nodes_by_ids(proj, node_ids)

    def _get_root_edges_by_keys(
        self, project_id: str, edge_keys: List[Tuple[str, str, str]]
    ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        proj = self._normalize_project_id(project_id)
        delegate, _ = self._get_project_store(proj)
        return delegate._get_root_edges_by_keys(proj, edge_keys)


# =====================================================================
# GraphStore Factory
# =====================================================================

def _map_graph_backend_to_plugin_id(backend_name: str) -> str:
    norm = (backend_name or "").strip().lower()
    aliases = {
        "sqlite": "graph.sqlite_overlay",
        "sqlite_overlay": "graph.sqlite_overlay",
        "postgres": "graph.postgres_schema",
        "postgresql": "graph.postgres_schema",
        "postgres_schema": "graph.postgres_schema",
        "neo4j": "graph.neo4j",
    }
    return aliases.get(norm, backend_name.strip())


def _is_explicit_standalone_graph_override(
    caller_db_path: Optional[str],
    caller_connection_url: Optional[str],
    backend_val: str,
) -> bool:
    """Return True if caller passed an explicit standalone path/URL/backend distinct from Master DB."""
    from ..core.tenant_manager import resolve_effective_master_url

    master_url = resolve_effective_master_url() or ""
    master_is_pg = master_url.startswith(("postgresql://", "postgres://"))

    if caller_connection_url is not None:
        if not master_is_pg or caller_connection_url != master_url:
            return True

    if backend_val in ("postgres", "postgresql") and not master_is_pg:
        return True
    if backend_val in ("sqlite", "sqlite_overlay") and master_is_pg:
        return True

    if caller_db_path is not None:
        raw_caller = str(caller_db_path).strip()
        if raw_caller == ":memory:" or raw_caller.startswith("file::memory:"):
            return True
        known_master_paths: Set[str] = set()
        if master_url.startswith("sqlite:///"):
            raw_m = master_url[len("sqlite:///"):]
            if raw_m and raw_m != ":memory:" and not raw_m.startswith("file::memory:"):
                known_master_paths.add(str(Path(raw_m).expanduser().resolve()))
        for env_key in ("PB_DB_PATH", "OM_DB_PATH"):
            ev = os.getenv(env_key)
            if ev and ev != ":memory:" and not ev.startswith("file::memory:"):
                known_master_paths.add(str(Path(ev).expanduser().resolve()))
        req_resolved = str(Path(raw_caller).expanduser().resolve())
        if known_master_paths and req_resolved not in known_master_paths:
            return True

    return False


def get_graph_store(
    db_path: Optional[str] = None,
    backend: Optional[str] = None,
    connection_url: Optional[str] = None,
    root_branch: Optional[str] = None,
    project_id: Optional[str] = None,
    registry: Optional[Any] = None,
) -> GraphStore:
    """Factory creating configured GraphStore instance based on environment, PluginRegistry, or parameters."""
    from ..core.tenant_manager import is_multi_db_enabled

    caller_root_branch = root_branch
    if root_branch is None:
        resolved_root = os.getenv("PB_ROOT_BRANCH") or os.getenv("OM_ROOT_BRANCH")
        if not resolved_root:
            try:
                from ..core.config import env
                resolved_root = getattr(env, "root_branch", None)
            except Exception:
                resolved_root = None
        root_branch = resolved_root or "main"

    caller_db_path = db_path
    caller_connection_url = connection_url
    if not db_path:
        db_path = os.getenv("OM_DB_PATH") or os.getenv("PB_DB_PATH") or None

    explicit_backend = (backend or "").strip()
    env_graph_backend = (os.getenv("PB_GRAPH_BACKEND") or "").strip()
    backend_val = (explicit_backend or env_graph_backend).lower()
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

    multi_db_active = is_multi_db_enabled()
    standalone_override = _is_explicit_standalone_graph_override(
        caller_db_path, caller_connection_url, backend_val
    )

    import sys

    reg_mod = sys.modules.get("projectbrain.plugins.registry")
    has_active_default_reg = bool(
        reg_mod is not None and getattr(reg_mod, "_DEFAULT_REGISTRY", None) is not None
    )

    should_consult_registry = (
        registry is not None
        or project_id is not None
        or has_active_default_reg
        or bool(env_graph_backend)
        or (
            bool(explicit_backend)
            and explicit_backend.lower() not in ("sqlite", "postgres", "postgresql")
        )
        or bool(os.getenv("PB_DISABLED_PLUGINS") or os.getenv("PB_ENABLED_PLUGINS"))
    )

    if should_consult_registry:
        try:
            active_reg = registry
            if active_reg is None:
                from ..plugins.registry import get_default_registry
                active_reg = get_default_registry()

            preferred_id: Optional[str] = None
            if explicit_backend:
                preferred_id = _map_graph_backend_to_plugin_id(explicit_backend)
            elif env_graph_backend:
                preferred_id = _map_graph_backend_to_plugin_id(env_graph_backend)
            elif backend_val in ("postgres", "postgresql"):
                has_project_override = False
                if project_id and hasattr(active_reg, "_project_overrides"):
                    has_project_override = bool(active_reg._project_overrides.get(project_id))
                if not has_project_override:
                    top_candidates = active_reg.resolve_all("graph_backend", project_id=project_id)
                    if not top_candidates or top_candidates[0].metadata.plugin_id == "graph.sqlite_overlay":
                        preferred_id = "graph.postgres_schema"

            plugin = active_reg.resolve(
                "graph_backend",
                preferred_id=preferred_id,
                project_id=project_id,
            )

            if preferred_id and preferred_id not in ("graph.sqlite_overlay", "graph.postgres_schema"):
                if plugin.metadata.plugin_id != preferred_id:
                    raise LookupError(
                        f"Requested graph_backend plugin '{preferred_id}' is not enabled or registered"
                    )

            is_non_default_plugin = plugin.metadata.plugin_id not in (
                "graph.sqlite_overlay",
                "graph.postgres_schema",
            )
            if is_non_default_plugin or not multi_db_active or standalone_override:
                store_cfg: Dict[str, Any] = {}
                if hasattr(active_reg, "get_effective_config"):
                    store_cfg.update(
                        active_reg.get_effective_config(
                            plugin.metadata.plugin_id,
                            project_id=project_id,
                        )
                    )
                if caller_db_path is not None:
                    store_cfg["db_path"] = caller_db_path
                elif "db_path" not in store_cfg and db_path is not None:
                    store_cfg["db_path"] = db_path
                if connection_url is not None:
                    store_cfg["connection_url"] = connection_url
                if caller_root_branch is not None:
                    store_cfg["root_branch"] = caller_root_branch
                else:
                    store_cfg.setdefault("root_branch", root_branch)

                return plugin.create_store(store_cfg)
        except LookupError:
            if explicit_backend and explicit_backend.lower() not in ("sqlite", "postgres", "postgresql"):
                raise
        except ImportError:
            pass

    if multi_db_active and not standalone_override:
        return MultiProjectGraphStore(
            master_db_path=caller_db_path or db_path,
            connection_url=caller_connection_url,
            root_branch=root_branch,
        )

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
