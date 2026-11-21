r"""
3-Tier Hybrid Knowledge Base Resolver & Local-vs-Server Diff Engine.

Resolves multi-tier knowledge graph state:
- Tier 1: Local uncommitted overlay (.projectbrain/local_graph/partial_graph.json) [Highest precedence]
- Tier 2: Server branch delta schema [Medium precedence]
- Tier 3: Server root base schema [Lowest precedence]

Enforces tombstone suppression, automatic stale-symbol pruning for locally edited files,
$O(\Delta_{local})$ local-vs-remote diffs, and hybrid graph traversals (BFS/DFS query,
shortest path tracing, blast-radius impact analysis, god node identification).
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

# ---------------------------------------------------------------------------
# Concurrency-Safe Defensive Imports
# ---------------------------------------------------------------------------
try:
    from projectbrain.graphify_engine.models import (
        BranchDiffResult,
        BranchDiffSummary,
        GraphDelta,
        GraphEdge,
        GraphHyperedge,
        GraphNode,
    )
except ImportError:
    from pydantic import BaseModel, Field

    class GraphNode(BaseModel):
        id: str
        project_id: str
        label: str
        file_type: str = "code"  # "code" | "document" | "config"
        source_file: str = ""
        source_location: Optional[str] = None
        community: Optional[int] = None
        metadata: Dict[str, Any] = Field(default_factory=dict)
        updated_at: Optional[str] = None
        tier: Optional[str] = None

    class GraphEdge(BaseModel):
        project_id: str
        source: str
        target: str
        relation: str = "calls"  # "calls" | "imports" | "defines" | "inherits" | "rationale_for" | "cites"
        confidence: str = "EXTRACTED"  # "EXTRACTED" | "INFERRED" | "AMBIGUOUS"
        confidence_score: float = 1.0
        source_file: Optional[str] = None
        source_location: Optional[str] = None
        weight: float = 1.0
        tier: Optional[str] = None

    class GraphHyperedge(BaseModel):
        id: str
        project_id: str
        label: str
        nodes: List[str] = Field(default_factory=list)
        relation: str = "hyperedge"
        confidence: str = "EXTRACTED"
        confidence_score: float = 1.0
        source_file: Optional[str] = None

    class GraphDelta(BaseModel):
        project_id: str
        branch: str = "main"
        nodes: List[GraphNode] = Field(default_factory=list)
        edges: List[GraphEdge] = Field(default_factory=list)
        hyperedges: List[GraphHyperedge] = Field(default_factory=list)
        node_tombstones: List[str] = Field(default_factory=list)
        edge_tombstones: List[Dict[str, str]] = Field(default_factory=list)
        hyperedge_tombstones: List[str] = Field(default_factory=list)
        synced_files: List[Any] = Field(default_factory=list)

    class BranchDiffSummary(BaseModel):
        new_nodes_count: int = 0
        modified_nodes_count: int = 0
        deleted_nodes_count: int = 0
        new_edges_count: int = 0
        deleted_edges_count: int = 0

    class BranchDiffResult(BaseModel):
        summary: BranchDiffSummary
        new_nodes: List[Dict[str, Any]] = Field(default_factory=list)
        modified_nodes: List[Dict[str, Any]] = Field(default_factory=list)
        deleted_node_ids: List[str] = Field(default_factory=list)
        new_edges: List[Dict[str, Any]] = Field(default_factory=list)
        deleted_edges: List[Dict[str, Any]] = Field(default_factory=list)

logger = logging.getLogger(__name__)

CONFIDENCE_RANK = {
    "EXTRACTED": 3,
    "INFERRED": 2,
    "AMBIGUOUS": 1,
}


def _normalize_path(p: Optional[str]) -> str:
    """Normalize file path for consistent matching across environments."""
    if not p:
        return ""
    p = p.strip()
    norm = os.path.normpath(p)
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def _extract_file_paths(synced_files: Any) -> Set[str]:
    """Extract clean string file paths from synced_files entries."""
    paths: Set[str] = set()
    if not synced_files:
        return paths
    for item in synced_files:
        if isinstance(item, str):
            p = _normalize_path(item)
            if p:
                paths.add(p)
        elif isinstance(item, dict):
            p = item.get("file_path") or item.get("path") or item.get("file")
            if p:
                norm = _normalize_path(str(p))
                if norm:
                    paths.add(norm)
    return paths


def _dump_model(model: Any) -> Dict[str, Any]:
    """Dump pydantic model in v1 or v2 format or dict fallback."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    elif hasattr(model, "dict"):
        return model.dict()
    elif isinstance(model, dict):
        return model
    return dict(model)


# ---------------------------------------------------------------------------
# HybridResolver Implementation
# ---------------------------------------------------------------------------
class HybridResolver:
    """
    3-Tier Hybrid Knowledge Base Resolver and Local-vs-Server Diff Engine.

    Seamlessly merges Tier 1 (local dirty files), Tier 2 (server branch overlay),
    and Tier 3 (server root base) into a unified NetworkX DiGraph with explicit
    `tier` provenance annotations, tombstone suppression, and O(Delta) diffing.
    """

    def __init__(
        self,
        store: Optional[Any] = None,
        remote_url: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        """
        Initialize the HybridResolver.

        :param store: Optional GraphStore instance (PostgresSchemaGraphStore or SqliteOverlayGraphStore).
        :param remote_url: Optional remote Project-Brain server base URL for hybrid client mode.
        :param api_token: Optional authentication token for remote server calls.
        """
        self.remote_url = remote_url.rstrip("/") if remote_url else None
        self.api_token = api_token

        if store is None and self.remote_url is None:
            try:
                from projectbrain.graphify_engine.store import get_graph_store
                self.store = get_graph_store()
            except Exception as e:
                logger.debug("GraphStore not available during HybridResolver init: %s", e)
                self.store = None
        else:
            self.store = store

    # -----------------------------------------------------------------------
    # Local Graph Ingestion
    # -----------------------------------------------------------------------
    def load_local_graph(
        self,
        local_path: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[GraphDelta]:
        """
        Load local graph delta from disk.

        Supports both standard GraphDelta JSON format and raw `graphify-out/graph.json` format.

        :param local_path: File path to local graph JSON (defaults to .projectbrain/local_graph/partial_graph.json).
        :param project_id: Fallback project_id if not present in the local graph payload.
        :return: Parsed GraphDelta or None if the file does not exist.
        """
        target_path = Path(local_path) if local_path else Path(".projectbrain/local_graph/partial_graph.json")
        if not target_path.is_file():
            return None

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as err:
            logger.warning("Failed to read local graph at %s: %s", target_path, err)
            return None

        eff_project_id = data.get("project_id") or project_id or "default"
        eff_branch = data.get("branch") or "main"

        # 1. Parse nodes
        raw_nodes = data.get("nodes", [])
        nodes: List[GraphNode] = []
        for n in raw_nodes:
            if isinstance(n, GraphNode):
                nodes.append(n)
                continue
            nid = str(n.get("id") or n.get("name") or "")
            if not nid:
                continue
            nodes.append(
                GraphNode(
                    id=nid,
                    project_id=str(n.get("project_id") or eff_project_id),
                    label=str(n.get("label") or nid),
                    file_type=str(n.get("file_type") or "code"),
                    source_file=str(n.get("source_file") or n.get("file") or ""),
                    source_location=n.get("source_location") or n.get("location"),
                    community=n.get("community"),
                    metadata=n.get("metadata") if isinstance(n.get("metadata"), dict) else {},
                    updated_at=n.get("updated_at"),
                    tier="local",
                )
            )

        # 2. Parse edges / links
        raw_edges = data.get("edges")
        if raw_edges is None:
            raw_edges = data.get("links", [])

        edges: List[GraphEdge] = []
        for e in raw_edges:
            if isinstance(e, GraphEdge):
                edges.append(e)
                continue
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            if not src or not tgt:
                continue
            confidence = str(e.get("confidence") or "EXTRACTED").upper()
            try:
                score = float(e.get("confidence_score", 1.0))
            except (ValueError, TypeError):
                score = 1.0
            try:
                weight = float(e.get("weight", 1.0))
            except (ValueError, TypeError):
                weight = 1.0

            edges.append(
                GraphEdge(
                    project_id=str(e.get("project_id") or eff_project_id),
                    source=src,
                    target=tgt,
                    relation=str(e.get("relation") or "calls"),
                    confidence=confidence,
                    confidence_score=score,
                    source_file=e.get("source_file") or e.get("file"),
                    source_location=e.get("source_location") or e.get("location"),
                    weight=weight,
                    tier="local",
                )
            )

        # 3. Parse hyperedges
        raw_hyperedges = data.get("hyperedges")
        if raw_hyperedges is None and isinstance(data.get("graph"), dict):
            raw_hyperedges = data["graph"].get("hyperedges", [])
        if raw_hyperedges is None:
            raw_hyperedges = []

        hyperedges: List[GraphHyperedge] = []
        for h in raw_hyperedges:
            if isinstance(h, GraphHyperedge):
                hyperedges.append(h)
                continue
            hid = str(h.get("id") or "")
            if not hid:
                continue
            confidence = str(h.get("confidence") or "EXTRACTED").upper()
            try:
                score = float(h.get("confidence_score", 1.0))
            except (ValueError, TypeError):
                score = 1.0

            hyperedges.append(
                GraphHyperedge(
                    id=hid,
                    project_id=str(h.get("project_id") or eff_project_id),
                    label=str(h.get("label") or hid),
                    nodes=list(h.get("nodes", [])),
                    relation=str(h.get("relation") or "hyperedge"),
                    confidence=confidence,
                    confidence_score=score,
                    source_file=h.get("source_file"),
                )
            )

        # 4. Parse tombstones
        raw_node_tombstones = data.get("node_tombstones", [])
        node_tombstones = [str(x) for x in raw_node_tombstones]

        raw_edge_tombstones = data.get("edge_tombstones", [])
        edge_tombstones: List[Dict[str, str]] = []
        for item in raw_edge_tombstones:
            if isinstance(item, dict):
                edge_tombstones.append({
                    "source": str(item.get("source", "")),
                    "target": str(item.get("target", "")),
                    "relation": str(item.get("relation", "calls")),
                })
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                edge_tombstones.append({
                    "source": str(item[0]),
                    "target": str(item[1]),
                    "relation": str(item[2]) if len(item) > 2 else "calls",
                })

        raw_hyperedge_tombstones = data.get("hyperedge_tombstones", [])
        hyperedge_tombstones = [str(x) for x in raw_hyperedge_tombstones]

        # 5. Synced files (derive from nodes if not explicitly provided, ensure Dict format)
        raw_synced = data.get("synced_files", [])
        synced_files: List[Dict[str, Any]] = []
        if raw_synced:
            for item in raw_synced:
                if isinstance(item, str):
                    synced_files.append({"file_path": item, "sha256": ""})
                elif isinstance(item, dict):
                    synced_files.append(item)
        elif nodes:
            derived_files = sorted({_normalize_path(n.source_file) for n in nodes if n.source_file})
            synced_files = [{"file_path": f, "sha256": ""} for f in derived_files]

        return GraphDelta(
            project_id=eff_project_id,
            branch=eff_branch,
            nodes=nodes,
            edges=edges,
            hyperedges=hyperedges,
            node_tombstones=node_tombstones,
            edge_tombstones=edge_tombstones,
            hyperedge_tombstones=hyperedge_tombstones,
            synced_files=synced_files,
        )

    # -----------------------------------------------------------------------
    # Server Subgraph Fetching (Remote HTTP vs In-Process GraphStore)
    # -----------------------------------------------------------------------
    def _fetch_server_subgraph(
        self,
        project_id: str,
        branch: str = "main",
        files: Optional[List[str]] = None,
        limit: int = 10000,
    ) -> Dict[str, Any]:
        """
        Fetch server resolved subgraph either via HTTP or in-process GraphStore.
        """
        if self.remote_url:
            import httpx
            url = f"{self.remote_url}/graphify/subgraph"
            params: Dict[str, Any] = {
                "project_id": project_id,
                "branch": branch,
                "limit": limit,
            }
            if files:
                params["files"] = ",".join(files)

            headers: Dict[str, str] = {}
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"

            with httpx.Client(timeout=30.0) as client:
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

        elif self.store is not None:
            if hasattr(self.store, "get_resolved_subgraph"):
                try:
                    return self.store.get_resolved_subgraph(
                        project_id=project_id,
                        branch_name=branch,
                        files=files,
                        limit=limit,
                    )
                except TypeError:
                    try:
                        return self.store.get_resolved_subgraph(
                            project_id=project_id,
                            branch=branch,
                            files=files,
                            limit=limit,
                        )
                    except TypeError:
                        return self.store.get_resolved_subgraph(
                            project_id,
                            branch,
                            files,
                            limit,
                        )
            return {"nodes": [], "edges": [], "hyperedges": []}
        else:
            return {"nodes": [], "edges": [], "hyperedges": []}

    # -----------------------------------------------------------------------
    # 3-Tier Resolution Engine
    # -----------------------------------------------------------------------
    def resolve_hybrid_graph(
        self,
        project_id: str,
        branch: str = "main",
        local_path: Optional[str] = None,
        files: Optional[List[str]] = None,
    ) -> nx.DiGraph:
        """
        Merge 3 tiers into a unified networkx.DiGraph with `tier` provenance attribute:
        Tier 1: Local uncommitted overlay (highest precedence)
        Tier 2: Server branch overlay (medium precedence)
        Tier 3: Server root base schema (lowest precedence)

        Applies:
        - Explicit tombstone suppression from Tier 1
        - Stale-symbol pruning for locally edited files
        - Local node and edge attribute overrides with tier="local"
        """
        g = nx.DiGraph()
        g.graph["project_id"] = project_id
        g.graph["branch"] = branch

        # 1. Load local Tier 1 graph
        local_graph = self.load_local_graph(local_path=local_path, project_id=project_id)

        # 2. Fetch server subgraph (Tiers 2 & 3 resolved)
        server_data = self._fetch_server_subgraph(
            project_id=project_id,
            branch=branch,
            files=files,
            limit=10000,
        )
        server_nodes: List[Dict[str, Any]] = server_data.get("nodes", [])
        server_edges: List[Dict[str, Any]] = server_data.get("edges", [])

        # Prepare suppression index
        local_node_tombstones: Set[str] = set()
        local_edge_tombstones: Set[Tuple[str, str, str]] = set()
        synced_files: Set[str] = set()
        local_node_ids: Set[str] = set()
        local_edge_keys: Set[Tuple[str, str, str]] = set()

        if local_graph is not None:
            local_node_tombstones = set(local_graph.node_tombstones)
            for et in local_graph.edge_tombstones:
                if isinstance(et, dict):
                    local_edge_tombstones.add((
                        str(et.get("source", "")),
                        str(et.get("target", "")),
                        str(et.get("relation", "calls")),
                    ))
            synced_files = {_normalize_path(f) for f in _extract_file_paths(local_graph.synced_files)}
            local_node_ids = {n.id for n in local_graph.nodes}
            local_edge_keys = {(e.source, e.target, e.relation) for e in local_graph.edges}

        # 3. Add server nodes, suppressing tombstones & stale symbols in locally edited files
        suppressed_nodes: Set[str] = set()

        for sn in server_nodes:
            sid = str(sn.get("id", ""))
            if not sid:
                continue

            s_file = _normalize_path(sn.get("source_file"))

            # Explicit tombstone
            if sid in local_node_tombstones:
                suppressed_nodes.add(sid)
                continue

            # Automatic stale-symbol pruning: file was edited locally, but symbol is no longer in local graph
            if s_file and s_file in synced_files:
                if sid not in local_node_ids:
                    suppressed_nodes.add(sid)
                    continue

            # Node accepted from server
            tier = sn.get("tier")
            if not tier:
                tier = "branch" if branch != "main" else "root"
            attrs = dict(sn)
            attrs["tier"] = tier
            g.add_node(sid, **attrs)

        # 4. Add server edges, suppressing tombstones & stale edges
        for se in server_edges:
            src = str(se.get("source", ""))
            tgt = str(se.get("target", ""))
            rel = str(se.get("relation", "calls"))
            if not src or not tgt:
                continue

            # Suppress if endpoints were suppressed
            if src in suppressed_nodes or tgt in suppressed_nodes:
                continue

            # Explicit edge tombstone
            if (src, tgt, rel) in local_edge_tombstones:
                continue

            # Automatic stale-edge pruning: file was edited locally, edge no longer exists
            se_file = _normalize_path(se.get("source_file"))
            if se_file and se_file in synced_files:
                if (src, tgt, rel) not in local_edge_keys:
                    continue

            tier = se.get("tier")
            if not tier:
                tier = "branch" if branch != "main" else "root"
            attrs = dict(se)
            attrs["tier"] = tier

            # Ensure endpoints exist
            if not g.has_node(src):
                g.add_node(src, id=src, label=src, tier=tier)
            if not g.has_node(tgt):
                g.add_node(tgt, id=tgt, label=tgt, tier=tier)

            g.add_edge(src, tgt, **attrs)

        # 5. Overlay Tier 1 (local graph) with highest precedence
        if local_graph is not None:
            for ln in local_graph.nodes:
                if ln.id in local_node_tombstones:
                    continue
                attrs = _dump_model(ln)
                attrs["tier"] = "local"
                g.add_node(ln.id, **attrs)

            for le in local_graph.edges:
                if le.source in local_node_tombstones or le.target in local_node_tombstones:
                    continue
                if (le.source, le.target, le.relation) in local_edge_tombstones:
                    continue
                attrs = _dump_model(le)
                attrs["tier"] = "local"

                # Ensure endpoints exist in graph (e.g., local node calling remote node)
                if not g.has_node(le.source):
                    g.add_node(le.source, id=le.source, label=le.source, tier="local")
                if not g.has_node(le.target):
                    g.add_node(le.target, id=le.target, label=le.target, tier="root")

                g.add_edge(le.source, le.target, **attrs)

            g.graph["hyperedges"] = [_dump_model(h) for h in local_graph.hyperedges]
        else:
            g.graph["hyperedges"] = server_data.get("hyperedges", [])

        return g

    # -----------------------------------------------------------------------
    # Local-vs-Server Diff Engine (O(Delta) Scoped File Comparison)
    # -----------------------------------------------------------------------
    def diff_local_vs_remote(
        self,
        project_id: str,
        branch: str = "main",
        local_path: Optional[str] = None,
        scoped_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Compute structural difference between local uncommitted graph and server branch/root.

        When `scoped_only=True` (default), restricts comparison to files touched in
        `local_graph` (synced_files + source_files + tombstones) fetched from server in O(Delta_local).
        """
        local_graph = self.load_local_graph(local_path=local_path, project_id=project_id)
        if local_graph is None:
            return {
                "summary": "No local graph found; working copy matches remote.",
                "stats": {
                    "added_nodes_count": 0,
                    "modified_nodes_count": 0,
                    "deleted_nodes_count": 0,
                    "added_edges_count": 0,
                    "deleted_edges_count": 0,
                    "touched_files_count": 0,
                },
                "added_nodes": [],
                "modified_nodes": [],
                "deleted_nodes": [],
                "added_edges": [],
                "deleted_edges": [],
                "touched_files": [],
            }

        # Determine touched files
        touched_files_set: Set[str] = set()
        touched_files_set.update(_extract_file_paths(local_graph.synced_files))
        for n in local_graph.nodes:
            norm = _normalize_path(n.source_file)
            if norm:
                touched_files_set.add(norm)
        for e in local_graph.edges:
            norm = _normalize_path(e.source_file)
            if norm:
                touched_files_set.add(norm)

        touched_files = sorted(touched_files_set)

        # Fetch server subgraph
        query_files = touched_files if (scoped_only and touched_files) else None
        server_data = self._fetch_server_subgraph(
            project_id=project_id,
            branch=branch,
            files=query_files,
            limit=10000,
        )
        server_nodes_list = server_data.get("nodes", [])
        server_edges_list = server_data.get("edges", [])

        server_nodes_map: Dict[str, Dict[str, Any]] = {
            str(n.get("id", "")): n for n in server_nodes_list if n.get("id")
        }
        server_edges_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {
            (str(e.get("source")), str(e.get("target")), str(e.get("relation", "calls"))): e
            for e in server_edges_list
            if e.get("source") and e.get("target")
        }

        # Local maps
        local_nodes_map: Dict[str, Dict[str, Any]] = {
            n.id: _dump_model(n) for n in local_graph.nodes
        }
        local_node_tombstones = set(local_graph.node_tombstones)

        local_edges_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {
            (e.source, e.target, e.relation): _dump_model(e) for e in local_graph.edges
        }
        local_edge_tombstones: Set[Tuple[str, str, str]] = set()
        for et in local_graph.edge_tombstones:
            if isinstance(et, dict):
                local_edge_tombstones.add((
                    str(et.get("source", "")),
                    str(et.get("target", "")),
                    str(et.get("relation", "calls")),
                ))

        # 1. Node comparison
        added_nodes: List[Dict[str, Any]] = []
        modified_nodes: List[Dict[str, Any]] = []
        deleted_node_ids: Set[str] = set()

        for nid, l_dict in local_nodes_map.items():
            if nid in local_node_tombstones:
                continue
            if nid not in server_nodes_map:
                added_nodes.append(l_dict)
            else:
                s_dict = server_nodes_map[nid]
                changed_fields: List[str] = []
                for field in ["label", "file_type", "source_file", "source_location", "community", "metadata"]:
                    s_val = s_dict.get(field)
                    l_val = l_dict.get(field)
                    if s_val != l_val and (s_val is not None or l_val is not None):
                        changed_fields.append(field)
                if changed_fields:
                    modified_nodes.append({
                        "id": nid,
                        "changed_fields": changed_fields,
                        "before": s_dict,
                        "after": l_dict,
                    })

        # Explicit tombstones
        for tid in local_node_tombstones:
            deleted_node_ids.add(tid)

        # Stale symbols in touched files
        for sid, s_dict in server_nodes_map.items():
            s_file = _normalize_path(s_dict.get("source_file"))
            if s_file in touched_files_set and sid not in local_nodes_map:
                deleted_node_ids.add(sid)

        deleted_nodes = [
            server_nodes_map[did] if did in server_nodes_map else {"id": did}
            for did in sorted(deleted_node_ids)
        ]

        # 2. Edge comparison
        added_edges: List[Dict[str, Any]] = []
        deleted_edge_keys: Set[Tuple[str, str, str]] = set()

        for e_key, l_edge in local_edges_map.items():
            if e_key in local_edge_tombstones:
                continue
            if l_edge["source"] in deleted_node_ids or l_edge["target"] in deleted_node_ids:
                continue
            if e_key not in server_edges_map:
                added_edges.append(l_edge)

        for tet in local_edge_tombstones:
            deleted_edge_keys.add(tet)

        for se_key, s_edge in server_edges_map.items():
            src, tgt, _ = se_key
            if src in deleted_node_ids or tgt in deleted_node_ids:
                deleted_edge_keys.add(se_key)
            else:
                se_file = _normalize_path(s_edge.get("source_file"))
                if se_file in touched_files_set and se_key not in local_edges_map:
                    deleted_edge_keys.add(se_key)

        deleted_edges = [
            server_edges_map[dek] if dek in server_edges_map else {
                "source": dek[0],
                "target": dek[1],
                "relation": dek[2],
            }
            for dek in deleted_edge_keys
        ]

        summary_str = (
            f"Diff: +{len(added_nodes)} added, ~{len(modified_nodes)} modified, -{len(deleted_nodes)} deleted nodes; "
            f"+{len(added_edges)} added, -{len(deleted_edges)} deleted edges across {len(touched_files)} touched file(s)."
        )

        return {
            "summary": summary_str,
            "stats": {
                "added_nodes_count": len(added_nodes),
                "modified_nodes_count": len(modified_nodes),
                "deleted_nodes_count": len(deleted_nodes),
                "added_edges_count": len(added_edges),
                "deleted_edges_count": len(deleted_edges),
                "touched_files_count": len(touched_files),
            },
            "added_nodes": added_nodes,
            "modified_nodes": modified_nodes,
            "deleted_nodes": deleted_nodes,
            "added_edges": added_edges,
            "deleted_edges": deleted_edges,
            "touched_files": touched_files,
        }

    # -----------------------------------------------------------------------
    # Graph Query & Traversal Methods
    # -----------------------------------------------------------------------
    def query(
        self,
        project_id: str,
        query_text: str,
        branch: str = "main",
        local_path: Optional[str] = None,
        mode: str = "bfs",
        min_confidence: str = "AMBIGUOUS",
        budget: int = 2500,
    ) -> Dict[str, Any]:
        """
        Query hybrid knowledge base using BFS or DFS traversal from keyword-matched seed nodes.

        :param project_id: Project identifier.
        :param query_text: Natural language query or symbol token.
        :param branch: Branch name.
        :param local_path: Optional path to local graph JSON.
        :param mode: "bfs" (breadth-first context) or "dfs" (deep chain tracing).
        :param min_confidence: "EXTRACTED" > "INFERRED" > "AMBIGUOUS".
        :param budget: Approximate token budget for context markdown formatting.
        """
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if g.number_of_nodes() == 0:
            return {
                "query": query_text,
                "mode": mode,
                "nodes": [],
                "edges": [],
                "communities": [],
                "tier_breakdown": {"local": 0, "branch": 0, "root": 0},
                "context_markdown": "# Hybrid Knowledge Base Context\nNo entities found in knowledge base.",
            }

        min_rank = CONFIDENCE_RANK.get(min_confidence.upper(), 1)

        # 1. Seed selection via multi-criteria relevance scoring
        q_tokens = [t.lower() for t in query_text.replace("_", " ").replace(".", " ").split() if t]
        node_scores: List[Tuple[float, str]] = []

        for nid, data in g.nodes(data=True):
            score = 0.0
            label = str(data.get("label", nid)).lower()
            nid_lower = nid.lower()
            source_file = str(data.get("source_file", "")).lower()
            meta_str = str(data.get("metadata", "")).lower()

            if query_text.lower() in nid_lower or query_text.lower() in label:
                score += 15.0

            for tok in q_tokens:
                if tok == nid_lower or tok == label:
                    score += 10.0
                elif tok in nid_lower or tok in label:
                    score += 5.0
                elif tok in source_file:
                    score += 2.0
                elif tok in meta_str:
                    score += 1.0

            if score > 0:
                node_scores.append((score, nid))

        node_scores.sort(key=lambda x: x[0], reverse=True)

        # Pick top seeds; if no match, fallback to highest degree centrality
        if node_scores:
            seeds = [nid for _, nid in node_scores[:8]]
        else:
            deg = sorted(g.degree(), key=lambda x: x[1], reverse=True)
            seeds = [nid for nid, _ in deg[:3]]

        # 2. Graph Traversal (BFS / DFS) respecting min_confidence
        visited_nodes: Set[str] = set()
        traversed_edges: List[Dict[str, Any]] = []

        def edge_allowed(u: str, v: str, edata: Dict[str, Any]) -> bool:
            conf = str(edata.get("confidence", "AMBIGUOUS")).upper()
            return CONFIDENCE_RANK.get(conf, 1) >= min_rank

        if mode.lower() == "dfs":
            stack = list(reversed(seeds))
            while stack and len(visited_nodes) < 150:
                curr = stack.pop()
                if curr in visited_nodes:
                    continue
                visited_nodes.add(curr)

                # Outgoing & Incoming
                for neighbor in g.successors(curr):
                    edata = g.get_edge_data(curr, neighbor) or {}
                    if edge_allowed(curr, neighbor, edata):
                        traversed_edges.append({
                            "source": curr,
                            "target": neighbor,
                            **edata,
                        })
                        if neighbor not in visited_nodes:
                            stack.append(neighbor)
                for pred in g.predecessors(curr):
                    edata = g.get_edge_data(pred, curr) or {}
                    if edge_allowed(pred, curr, edata):
                        traversed_edges.append({
                            "source": pred,
                            "target": curr,
                            **edata,
                        })
                        if pred not in visited_nodes:
                            stack.append(pred)
        else:  # BFS
            queue = deque(seeds)
            for s in seeds:
                visited_nodes.add(s)

            while queue and len(visited_nodes) < 150:
                curr = queue.popleft()

                for neighbor in g.successors(curr):
                    edata = g.get_edge_data(curr, neighbor) or {}
                    if edge_allowed(curr, neighbor, edata):
                        traversed_edges.append({
                            "source": curr,
                            "target": neighbor,
                            **edata,
                        })
                        if neighbor not in visited_nodes:
                            visited_nodes.add(neighbor)
                            queue.append(neighbor)

                for pred in g.predecessors(curr):
                    edata = g.get_edge_data(pred, curr) or {}
                    if edge_allowed(pred, curr, edata):
                        traversed_edges.append({
                            "source": pred,
                            "target": curr,
                            **edata,
                        })
                        if pred not in visited_nodes:
                            visited_nodes.add(pred)
                            queue.append(pred)

        # 3. Format Subgraph & Token-Budget Truncation
        matched_node_dicts: List[Dict[str, Any]] = []
        tier_counts = {"local": 0, "branch": 0, "root": 0}
        communities: Set[int] = set()

        for nid in visited_nodes:
            ndata = dict(g.nodes[nid])
            tier = ndata.get("tier", "root")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            comm = ndata.get("community")
            if comm is not None:
                try:
                    communities.add(int(comm))
                except (ValueError, TypeError):
                    pass
            matched_node_dicts.append(ndata)

        # Markdown synthesis with token budget enforcement (~4 chars = 1 token)
        md_lines = [
            "# Hybrid Knowledge Base Context",
            f"**Project**: `{project_id}` | **Branch**: `{branch}` | **Query**: \"{query_text}\" | **Mode**: `{mode.upper()}`",
            f"**Entities**: {len(matched_node_dicts)} (Local: {tier_counts.get('local', 0)}, Branch: {tier_counts.get('branch', 0)}, Root: {tier_counts.get('root', 0)})",
            "",
            "## Key Entities",
        ]

        char_budget = budget * 4
        current_chars = sum(len(line) + 1 for line in md_lines)

        for nd in matched_node_dicts:
            nid = nd.get("id")
            lbl = nd.get("label", nid)
            tier = nd.get("tier", "root")
            ftype = nd.get("file_type", "code")
            loc = nd.get("source_location") or nd.get("source_file") or "unspecified"
            line = f"- **{lbl}** (`{tier}`) [{ftype}] — `{loc}`"

            # Check relations
            rels = [
                f"{te['relation']} -> `{te['target']}` ({te.get('confidence', 'EXTRACTED')})"
                for te in traversed_edges if te["source"] == nid
            ]
            if rels:
                line += "\n  - " + "\n  - ".join(rels[:4])

            if current_chars + len(line) + 1 > char_budget:
                md_lines.append("\n> *[Context truncated to remain within token budget]*")
                break

            md_lines.append(line)
            current_chars += len(line) + 1

        return {
            "query": query_text,
            "mode": mode,
            "nodes": matched_node_dicts,
            "edges": traversed_edges,
            "communities": sorted(communities),
            "tier_breakdown": tier_counts,
            "context_markdown": "\n".join(md_lines),
        }

    # -----------------------------------------------------------------------
    # Path Tracing (Shortest Path Across Tiers)
    # -----------------------------------------------------------------------
    def trace_path(
        self,
        project_id: str,
        source_id: str,
        target_id: str,
        branch: str = "main",
        local_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find shortest path between source_id and target_id across hybrid tiers.

        Falls back to undirected traversal across semantic/call links if no directed path exists.
        Returns ordered hop details with relation, confidence, and tier metadata.
        """
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if not g.has_node(source_id) or not g.has_node(target_id):
            return []

        path_nodes: List[str] = []
        is_directed = True

        try:
            path_nodes = nx.shortest_path(g, source=source_id, target=target_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            try:
                path_nodes = nx.shortest_path(g.to_undirected(), source=source_id, target=target_id)
                is_directed = False
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return []

        hops: List[Dict[str, Any]] = []
        for i in range(len(path_nodes) - 1):
            u = path_nodes[i]
            v = path_nodes[i + 1]

            if g.has_edge(u, v):
                edata = g.get_edge_data(u, v) or {}
                direction = "forward"
            elif g.has_edge(v, u):
                edata = g.get_edge_data(v, u) or {}
                direction = "reverse"
            else:
                edata = {}
                direction = "undirected"

            hops.append({
                "step": i + 1,
                "source": u,
                "target": v,
                "relation": edata.get("relation", "connected_to"),
                "confidence": edata.get("confidence", "EXTRACTED"),
                "confidence_score": edata.get("confidence_score", 1.0),
                "tier": edata.get("tier", "root"),
                "source_label": g.nodes[u].get("label", u),
                "target_label": g.nodes[v].get("label", v),
                "direction": direction,
                "directed_path": is_directed,
            })

        return hops

    # -----------------------------------------------------------------------
    # Blast Radius / Impact Radius
    # -----------------------------------------------------------------------
    def get_impact_radius(
        self,
        project_id: str,
        node_id: str,
        max_hops: int = 3,
        branch: str = "main",
        local_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculate blast radius ego-subgraph of a modified symbol up to max_hops.

        Returns upstream callers/dependents and downstream ego dependencies.
        """
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if not g.has_node(node_id):
            return {
                "node_id": node_id,
                "found": False,
                "max_hops": max_hops,
                "upstream_dependents": [],
                "downstream_dependencies": [],
                "affected_nodes_count": 0,
                "tier_breakdown": {"local": 0, "branch": 0, "root": 0},
            }

        # 1. Upstream BFS (predecessors / callers)
        upstream: List[Dict[str, Any]] = []
        up_visited: Set[str] = {node_id}
        queue: deque[Tuple[str, int]] = deque([(node_id, 0)])

        while queue:
            curr, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for pred in g.predecessors(curr):
                if pred not in up_visited:
                    up_visited.add(pred)
                    edata = g.get_edge_data(pred, curr) or {}
                    pdata = dict(g.nodes[pred])
                    upstream.append({
                        "node_id": pred,
                        "hop": depth + 1,
                        "label": pdata.get("label", pred),
                        "file_type": pdata.get("file_type", "code"),
                        "source_file": pdata.get("source_file", ""),
                        "tier": pdata.get("tier", "root"),
                        "relation": edata.get("relation", "calls"),
                    })
                    queue.append((pred, depth + 1))

        # 2. Downstream BFS (successors / callees)
        downstream: List[Dict[str, Any]] = []
        down_visited: Set[str] = {node_id}
        queue = deque([(node_id, 0)])

        while queue:
            curr, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for succ in g.successors(curr):
                if succ not in down_visited:
                    down_visited.add(succ)
                    edata = g.get_edge_data(curr, succ) or {}
                    sdata = dict(g.nodes[succ])
                    downstream.append({
                        "node_id": succ,
                        "hop": depth + 1,
                        "label": sdata.get("label", succ),
                        "file_type": sdata.get("file_type", "code"),
                        "source_file": sdata.get("source_file", ""),
                        "tier": sdata.get("tier", "root"),
                        "relation": edata.get("relation", "calls"),
                    })
                    queue.append((succ, depth + 1))

        all_affected = up_visited.union(down_visited)
        tier_counts = {"local": 0, "branch": 0, "root": 0}
        for aid in all_affected:
            tier = g.nodes[aid].get("tier", "root")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        return {
            "node_id": node_id,
            "found": True,
            "max_hops": max_hops,
            "upstream_dependents": upstream,
            "downstream_dependencies": downstream,
            "affected_nodes_count": len(all_affected),
            "tier_breakdown": tier_counts,
        }

    # -----------------------------------------------------------------------
    # Direct Callers & Callees Navigation
    # -----------------------------------------------------------------------
    def get_callers(
        self,
        project_id: str,
        node_id: str,
        branch: str = "main",
        local_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return direct incoming caller nodes and incoming edges."""
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if not g.has_node(node_id):
            return []

        callers: List[Dict[str, Any]] = []
        for pred in g.predecessors(node_id):
            edata = g.get_edge_data(pred, node_id) or {}
            pdata = dict(g.nodes[pred])
            callers.append({
                "node_id": pred,
                "label": pdata.get("label", pred),
                "file_type": pdata.get("file_type", "code"),
                "source_file": pdata.get("source_file", ""),
                "source_location": pdata.get("source_location"),
                "tier": pdata.get("tier", "root"),
                "relation": edata.get("relation", "calls"),
                "confidence": edata.get("confidence", "EXTRACTED"),
            })
        return callers

    def get_callees(
        self,
        project_id: str,
        node_id: str,
        branch: str = "main",
        local_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return direct outgoing callee nodes and outgoing edges."""
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if not g.has_node(node_id):
            return []

        callees: List[Dict[str, Any]] = []
        for succ in g.successors(node_id):
            edata = g.get_edge_data(node_id, succ) or {}
            sdata = dict(g.nodes[succ])
            callees.append({
                "node_id": succ,
                "label": sdata.get("label", succ),
                "file_type": sdata.get("file_type", "code"),
                "source_file": sdata.get("source_file", ""),
                "source_location": sdata.get("source_location"),
                "tier": sdata.get("tier", "root"),
                "relation": edata.get("relation", "calls"),
                "confidence": edata.get("confidence", "EXTRACTED"),
            })
        return callees

    def get_node_details(
        self,
        project_id: str,
        node_id: str,
        branch: str = "main",
        local_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return full attributes, degree metrics, callers, and callees for a node."""
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if not g.has_node(node_id):
            return None

        ndata = dict(g.nodes[node_id])
        callers = self.get_callers(project_id, node_id, branch=branch, local_path=local_path)
        callees = self.get_callees(project_id, node_id, branch=branch, local_path=local_path)

        return {
            "id": node_id,
            "label": ndata.get("label", node_id),
            "file_type": ndata.get("file_type", "code"),
            "source_file": ndata.get("source_file", ""),
            "source_location": ndata.get("source_location"),
            "community": ndata.get("community"),
            "tier": ndata.get("tier", "root"),
            "metadata": ndata.get("metadata", {}),
            "degree": g.degree(node_id),
            "in_degree": g.in_degree(node_id),
            "out_degree": g.out_degree(node_id),
            "callers_count": len(callers),
            "callees_count": len(callees),
            "callers": callers,
            "callees": callees,
        }

    # -----------------------------------------------------------------------
    # God Nodes & Community Retrieval
    # -----------------------------------------------------------------------
    def get_god_nodes(
        self,
        project_id: str,
        branch: str = "main",
        top_k: int = 10,
        local_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Identify architectural anchor symbols via degree centrality across the hybrid graph.
        """
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        if g.number_of_nodes() == 0:
            return []

        centrality = nx.degree_centrality(g)
        sorted_nodes = sorted(centrality.items(), key=lambda x: x[1], reverse=True)

        results: List[Dict[str, Any]] = []
        for nid, score in sorted_nodes[:top_k]:
            ndata = dict(g.nodes[nid])
            results.append({
                "id": nid,
                "label": ndata.get("label", nid),
                "centrality_score": round(score, 4),
                "degree": g.degree(nid),
                "in_degree": g.in_degree(nid),
                "out_degree": g.out_degree(nid),
                "tier": ndata.get("tier", "root"),
                "file_type": ndata.get("file_type", "code"),
                "source_file": ndata.get("source_file", ""),
                "community": ndata.get("community"),
            })
        return results

    def get_community(
        self,
        project_id: str,
        community_id: int,
        branch: str = "main",
        local_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return nodes and intra-community edges for a Leiden community cluster."""
        g = self.resolve_hybrid_graph(project_id, branch=branch, local_path=local_path)
        matching_nodes: List[Dict[str, Any]] = []

        for nid, data in g.nodes(data=True):
            if data.get("community") == community_id:
                matching_nodes.append(dict(data))

        node_ids = {n["id"] for n in matching_nodes}
        sub = g.subgraph(node_ids)

        edges: List[Dict[str, Any]] = []
        for u, v, data in sub.edges(data=True):
            edges.append({
                "source": u,
                "target": v,
                **data,
            })

        return {
            "community_id": community_id,
            "nodes_count": len(matching_nodes),
            "edges_count": len(edges),
            "nodes": matching_nodes,
            "edges": edges,
        }
