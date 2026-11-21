"""Built-in Storage Plugins (Graph & Vector Backends) for Project-Brain v3.0.

Provides:
- SqliteOverlayGraphPlugin (graph.sqlite_overlay, priority=100)
- PostgresSchemaGraphPlugin (graph.postgres_schema, priority=90)
- Neo4jGraphPlugin + Neo4jGraphStore (graph.neo4j, priority=50)
- HSGMemoryPlugin (vector.hsg_memory, priority=100)
- QdrantVectorPlugin (vector.qdrant, priority=60)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from ...graphify_engine.models import (
    BranchDiffResult,
    BranchDiffSummary,
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)
from ...graphify_engine.store import (
    GraphStore,
    PostgresSchemaGraphStore,
    SqliteOverlayGraphStore,
    sanitize_branch_schema_name,
)
from ..base import (
    GraphBackendPlugin,
    PluginCategory,
    PluginMetadata,
    VectorBackendPlugin,
)

logger = logging.getLogger("projectbrain.plugins.storage")


# ---------------------------------------------------------------------------
# Deterministic Text / Vector Helpers (Zero-Install Offline Hybrid Search)
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _deterministic_dense_vector(text: str, dim: int = 64) -> List[float]:
    """Deterministic L2-normalized dense embedding for offline/fallback operation."""
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=16).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(dot / (na * nb))


def build_sparse_bm25_vector(
    text: str,
    k1: float = 1.5,
    b: float = 0.75,
    avgdl: float = 32.0,
) -> Dict[str, List[Any]]:
    """Build deterministic Qdrant-compatible sparse BM25 vector {indices, values}."""
    tokens = _tokenize(text)
    if not tokens:
        return {"indices": [], "values": []}
    counts = Counter(tokens)
    dl = float(len(tokens))
    index_weights: Dict[int, float] = {}
    for tok, tf in counts.items():
        idx = int.from_bytes(
            hashlib.blake2b(tok.encode("utf-8"), digest_size=4).digest(),
            "big",
        ) & 0x7FFFFFFF
        weight = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (dl / max(1.0, avgdl))))
        index_weights[idx] = index_weights.get(idx, 0.0) + float(weight)

    sorted_items = sorted(index_weights.items(), key=lambda kv: kv[0])
    return {
        "indices": [k for k, _ in sorted_items],
        "values": [v for _, v in sorted_items],
    }


def _sparse_dot(a: Dict[str, List[Any]], b: Dict[str, List[Any]]) -> float:
    if not a.get("indices") or not b.get("indices"):
        return 0.0
    map_a = dict(zip(a["indices"], a["values"]))
    score = 0.0
    for idx, val in zip(b["indices"], b["values"]):
        if idx in map_a:
            score += float(map_a[idx]) * float(val)
    return score


def _matches_filters(payload: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
    if not filters:
        return True
    for k, expected in filters.items():
        if expected is None:
            continue
        actual = payload.get(k)
        if actual is None and isinstance(payload.get("metadata"), dict):
            actual = payload["metadata"].get(k)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
        else:
            if actual != expected:
                return False
    return True


# ---------------------------------------------------------------------------
# 1. SqliteOverlayGraphPlugin
# ---------------------------------------------------------------------------
class SqliteOverlayGraphPlugin(GraphBackendPlugin):
    """Built-in SQLite overlay GraphStore plugin (default priority=100)."""

    metadata = PluginMetadata(
        plugin_id="graph.sqlite_overlay",
        name="SQLite Overlay GraphStore",
        version="3.0.0",
        category=PluginCategory.GRAPH_BACKEND.value,
        description="Default local SQLite O(Delta) branch-overlay GraphStore.",
        priority=100,
        capabilities=["overlay_branches", "sql_views", "flow_graph", "offline"],
        default_enabled=True,
    )

    def create_store(self, config: Optional[Dict[str, Any]] = None) -> SqliteOverlayGraphStore:
        cfg = {**self.config, **(config or {})}
        return SqliteOverlayGraphStore(
            db_path=cfg.get("db_path"),
            conn=cfg.get("conn"),
            root_branch=cfg.get("root_branch") or "main",
        )


# ---------------------------------------------------------------------------
# 2. PostgresSchemaGraphPlugin
# ---------------------------------------------------------------------------
class PostgresSchemaGraphPlugin(GraphBackendPlugin):
    """Built-in PostgreSQL multi-schema GraphStore plugin (priority=90)."""

    metadata = PluginMetadata(
        plugin_id="graph.postgres_schema",
        name="PostgreSQL Multi-Schema GraphStore",
        version="3.0.0",
        category=PluginCategory.GRAPH_BACKEND.value,
        description="PostgreSQL isolated-schema branch-overlay GraphStore.",
        priority=90,
        capabilities=["multi_schema_branches", "sql_views", "flow_graph", "postgres"],
        default_enabled=True,
    )

    def create_store(self, config: Optional[Dict[str, Any]] = None) -> PostgresSchemaGraphStore:
        cfg = {**self.config, **(config or {})}
        return PostgresSchemaGraphStore(
            connection_url=cfg.get("connection_url") or cfg.get("db_url"),
            conn=cfg.get("conn"),
            root_branch=cfg.get("root_branch") or "main",
        )


# ---------------------------------------------------------------------------
# 3. Neo4jGraphStore & Neo4jGraphPlugin
# ---------------------------------------------------------------------------
class Neo4jGraphStore(GraphStore):
    """Neo4j GraphStore supporting HTTP Cypher Transaction API & offline mode='memory'."""

    def __init__(
        self,
        uri: str = "http://localhost:7474",
        database: str = "neo4j",
        username: Optional[str] = None,
        password: Optional[str] = None,
        root_branch: str = "main",
        mode: str = "http",
        http_client: Optional[httpx.Client] = None,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 15.0,
    ) -> None:
        super().__init__(root_branch=root_branch)
        self.uri = uri.rstrip("/")
        self.database = database or "neo4j"
        self.username = username
        self.password = password
        self.mode = mode.lower()
        self._timeout = timeout
        self._external_client = http_client is not None
        self._transport = transport
        self._client = http_client

        # In-memory backing store (used in mode="memory" and as overlay mirror)
        self._root_nodes: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._root_edges: Dict[str, Dict[Tuple[str, str, str], Dict[str, Any]]] = {}
        self._root_hyperedges: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._branches: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._overlays: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.executed_cypher: List[Dict[str, Any]] = []

    def _get_http_client(self) -> httpx.Client:
        if self._client is None:
            auth = (self.username, self.password) if (self.username and self.password) else None
            self._client = httpx.Client(
                transport=self._transport,
                auth=auth,
                timeout=self._timeout,
            )
        return self._client

    def close(self) -> None:
        if not self._external_client and self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def _run_cypher(
        self,
        statement: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a Cypher statement against Neo4j HTTP Transaction API."""
        payload = {
            "statements": [
                {
                    "statement": statement,
                    "parameters": parameters or {},
                }
            ]
        }
        self.executed_cypher.append({"statement": statement, "parameters": parameters or {}})
        if self.mode == "memory":
            return []

        url = f"{self.uri}/db/{self.database}/tx/commit"
        client = self._get_http_client()
        resp = client.post(
            url,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        body = resp.json()
        errors = body.get("errors") or []
        if errors:
            raise RuntimeError(f"Neo4j Cypher error: {errors}")

        rows: List[Dict[str, Any]] = []
        for result in body.get("results") or []:
            cols = result.get("columns") or []
            for entry in result.get("data") or []:
                row_vals = entry.get("row") or []
                rows.append({col: row_vals[idx] if idx < len(row_vals) else None for idx, col in enumerate(cols)})
        return rows

    def _node_to_dict(self, project_id: str, n: GraphNode) -> Dict[str, Any]:
        return {
            "id": n.id,
            "project_id": project_id,
            "label": n.label,
            "file_type": n.file_type,
            "source_file": n.source_file,
            "source_location": n.source_location,
            "community": n.community,
            "metadata": dict(n.metadata or {}),
        }

    def _edge_to_dict(self, project_id: str, e: GraphEdge) -> Dict[str, Any]:
        return {
            "project_id": project_id,
            "source": e.source,
            "target": e.target,
            "relation": e.relation,
            "confidence": e.confidence,
            "confidence_score": e.confidence_score,
            "source_file": e.source_file,
            "source_location": e.source_location,
            "weight": e.weight,
        }

    def _hyperedge_to_dict(self, project_id: str, h: GraphHyperedge) -> Dict[str, Any]:
        return {
            "id": h.id,
            "project_id": project_id,
            "label": h.label,
            "nodes": list(h.nodes),
            "relation": h.relation,
            "confidence": h.confidence,
            "confidence_score": h.confidence_score,
            "source_file": h.source_file,
        }

    def ensure_root_schema(self) -> None:
        if self.mode == "http":
            self._run_cypher(
                "CREATE CONSTRAINT pb_graph_node_id IF NOT EXISTS "
                "FOR (n:GraphNode) REQUIRE (n.project_id, n.branch, n.id) IS UNIQUE"
            )

    def ensure_branch_schema(
        self,
        project_id: str,
        branch_name: str,
        author: str = "system",
        base_branch: Optional[str] = None,
    ) -> str:
        schema_name = sanitize_branch_schema_name(project_id, branch_name)
        proj_branches = self._branches.setdefault(project_id, {})
        existing_parents = {b: meta["base_branch"] for b, meta in proj_branches.items()}

        if base_branch and base_branch.strip() and base_branch.strip() != branch_name:
            resolved_base = base_branch.strip()
            self._validate_branch_hierarchy(branch_name, resolved_base, existing_parents, max_depth=8)
        else:
            resolved_base = existing_parents.get(branch_name) or self.root_branch

        proj_branches[branch_name] = {
            "branch_name": branch_name,
            "schema_name": schema_name,
            "base_branch": resolved_base,
            "author": author,
            "is_root": False,
        }
        self._overlays.setdefault(
            (project_id, branch_name),
            {
                "schema_name": schema_name,
                "node_upserts": {},
                "node_tombstones": set(),
                "edge_upserts": {},
                "edge_tombstones": set(),
                "hyperedge_upserts": {},
                "hyperedge_tombstones": set(),
            },
        )
        if self.mode == "http":
            self._run_cypher(
                "MERGE (b:GraphBranch {project_id: $project_id, branch_name: $branch_name}) "
                "SET b.schema_name = $schema_name, b.base_branch = $base_branch, b.author = $author",
                {
                    "project_id": project_id,
                    "branch_name": branch_name,
                    "schema_name": schema_name,
                    "base_branch": resolved_base,
                    "author": author,
                },
            )
        return schema_name

    def get_branch_lineage(self, project_id: str, branch_name: str, max_depth: int = 8) -> List[str]:
        if self.is_root_branch(branch_name):
            return [self.root_branch]
        parent_map = {
            b: meta["base_branch"]
            for b, meta in self._branches.get(project_id, {}).items()
            if meta.get("base_branch")
        }
        return self._walk_lineage(branch_name, parent_map, max_depth=max_depth)

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
        nodes_upserted = 0
        nodes_deleted = 0
        edges_upserted = 0
        edges_deleted = 0

        if self.is_root_branch(branch_name):
            r_nodes = self._root_nodes.setdefault(project_id, {})
            r_edges = self._root_edges.setdefault(project_id, {})
            r_hyper = self._root_hyperedges.setdefault(project_id, {})

            if not partial:
                r_nodes.clear()
                r_edges.clear()
                r_hyper.clear()
            else:
                for fpath in deleted_files or []:
                    for nid in [k for k, v in r_nodes.items() if v.get("source_file") == fpath]:
                        r_nodes.pop(nid, None)
                        nodes_deleted += 1
                    for ek in [k for k, v in r_edges.items() if v.get("source_file") == fpath]:
                        r_edges.pop(ek, None)
                        edges_deleted += 1
                sync_set = set(
                    synced_files
                    or [sf["file_path"] if isinstance(sf, dict) else sf for sf in delta.synced_files]
                )
                for fpath in sync_set:
                    for nid in [k for k, v in r_nodes.items() if v.get("source_file") == fpath]:
                        r_nodes.pop(nid, None)
                    for ek in [k for k, v in r_edges.items() if v.get("source_file") == fpath]:
                        r_edges.pop(ek, None)
                for tid in delta.node_tombstones:
                    if r_nodes.pop(tid, None) is not None:
                        nodes_deleted += 1
                for et in delta.edge_tombstones:
                    ek = (et["source"], et["target"], et["relation"])
                    if r_edges.pop(ek, None) is not None:
                        edges_deleted += 1

            for n in delta.nodes:
                r_nodes[n.id] = self._node_to_dict(project_id, n)
                nodes_upserted += 1
            for e in delta.edges:
                ek = (e.source, e.target, e.relation)
                r_edges[ek] = self._edge_to_dict(project_id, e)
                edges_upserted += 1
            for h in delta.hyperedges:
                r_hyper[h.id] = self._hyperedge_to_dict(project_id, h)

            if self.mode == "http":
                self._run_cypher(
                    "UNWIND $nodes AS row "
                    "MERGE (n:GraphNode {project_id: $project_id, branch: $branch, id: row.id}) "
                    "SET n.label = row.label, n.file_type = row.file_type, "
                    "n.source_file = row.source_file, n.version_tag = row.version_tag, "
                    "n.metadata_json = row.metadata_json",
                    {
                        "project_id": project_id,
                        "branch": self.root_branch,
                        "nodes": [
                            {
                                "id": n.id,
                                "label": n.label,
                                "file_type": n.file_type,
                                "source_file": n.source_file,
                                "version_tag": (n.metadata or {}).get("version_tag"),
                                "metadata_json": json.dumps(n.metadata or {}),
                            }
                            for n in delta.nodes
                        ],
                    },
                )
                if delta.edges:
                    self._run_cypher(
                        "UNWIND $edges AS row "
                        "MATCH (s:GraphNode {project_id: $project_id, branch: $branch, id: row.source}), "
                        "(t:GraphNode {project_id: $project_id, branch: $branch, id: row.target}) "
                        "MERGE (s)-[r:REL {relation: row.relation}]->(t) "
                        "SET r.confidence = row.confidence, r.weight = row.weight",
                        {
                            "project_id": project_id,
                            "branch": self.root_branch,
                            "edges": [
                                {
                                    "source": e.source,
                                    "target": e.target,
                                    "relation": e.relation,
                                    "confidence": e.confidence,
                                    "weight": e.weight,
                                }
                                for e in delta.edges
                            ],
                        },
                    )

            return {
                "status": "synced",
                "project_id": project_id,
                "branch": self.root_branch,
                "schema_name": "neo4j_root",
                "nodes_upserted": nodes_upserted,
                "nodes_deleted": nodes_deleted,
                "edges_upserted": edges_upserted,
                "edges_deleted": edges_deleted,
                "partial": partial,
            }

        schema_name = self.ensure_branch_schema(project_id, branch_name, author, base_branch=base_branch)
        overlay = self._overlays[(project_id, branch_name)]
        root_nodes = self._root_nodes.get(project_id, {})
        root_edges = self._root_edges.get(project_id, {})

        if not partial:
            overlay["node_upserts"].clear()
            overlay["node_tombstones"].clear()
            overlay["edge_upserts"].clear()
            overlay["edge_tombstones"].clear()
            overlay["hyperedge_upserts"].clear()
            overlay["hyperedge_tombstones"].clear()

            seen_nids = set()
            for n in delta.nodes:
                seen_nids.add(n.id)
                nd = self._node_to_dict(project_id, n)
                if n.id not in root_nodes or root_nodes[n.id] != nd:
                    overlay["node_upserts"][n.id] = nd
                    nodes_upserted += 1
            for rid in root_nodes:
                if rid not in seen_nids:
                    overlay["node_tombstones"].add(rid)
                    nodes_deleted += 1

            seen_eks = set()
            for e in delta.edges:
                ek = (e.source, e.target, e.relation)
                seen_eks.add(ek)
                ed = self._edge_to_dict(project_id, e)
                if ek not in root_edges or root_edges[ek] != ed:
                    overlay["edge_upserts"][ek] = ed
                    edges_upserted += 1
            for rek in root_edges:
                if rek not in seen_eks:
                    overlay["edge_tombstones"].add(rek)
                    edges_deleted += 1
        else:
            for fpath in deleted_files or []:
                for nid in [k for k, v in list(overlay["node_upserts"].items()) if v.get("source_file") == fpath]:
                    overlay["node_upserts"].pop(nid, None)
                for rid, rnode in root_nodes.items():
                    if rnode.get("source_file") == fpath:
                        overlay["node_tombstones"].add(rid)
                        nodes_deleted += 1
                for ek in [k for k, v in list(overlay["edge_upserts"].items()) if v.get("source_file") == fpath]:
                    overlay["edge_upserts"].pop(ek, None)
                for rek, redge in root_edges.items():
                    if redge.get("source_file") == fpath:
                        overlay["edge_tombstones"].add(rek)
                        edges_deleted += 1

            for n in delta.nodes:
                overlay["node_tombstones"].discard(n.id)
                overlay["node_upserts"][n.id] = self._node_to_dict(project_id, n)
                nodes_upserted += 1
            for tid in delta.node_tombstones:
                overlay["node_upserts"].pop(tid, None)
                overlay["node_tombstones"].add(tid)
                nodes_deleted += 1
            for e in delta.edges:
                ek = (e.source, e.target, e.relation)
                overlay["edge_tombstones"].discard(ek)
                overlay["edge_upserts"][ek] = self._edge_to_dict(project_id, e)
                edges_upserted += 1
            for et in delta.edge_tombstones:
                ek = (et["source"], et["target"], et["relation"])
                overlay["edge_upserts"].pop(ek, None)
                overlay["edge_tombstones"].add(ek)
                edges_deleted += 1

        for h in delta.hyperedges:
            overlay["hyperedge_tombstones"].discard(h.id)
            overlay["hyperedge_upserts"][h.id] = self._hyperedge_to_dict(project_id, h)
        for htid in delta.hyperedge_tombstones:
            overlay["hyperedge_upserts"].pop(htid, None)
            overlay["hyperedge_tombstones"].add(htid)

        return {
            "status": "synced",
            "project_id": project_id,
            "branch": branch_name,
            "schema_name": schema_name,
            "nodes_upserted": nodes_upserted,
            "nodes_tombstoned": nodes_deleted,
            "edges_upserted": edges_upserted,
            "edges_tombstoned": edges_deleted,
            "partial": partial,
        }

    def _get_branch_overlay(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        return self._overlays.get(
            (project_id, branch_name),
            {
                "schema_name": sanitize_branch_schema_name(project_id, branch_name),
                "node_upserts": {},
                "node_tombstones": set(),
                "edge_upserts": {},
                "edge_tombstones": set(),
                "hyperedge_upserts": {},
                "hyperedge_tombstones": set(),
            },
        )

    def _get_root_nodes_by_ids(self, project_id: str, node_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        r_nodes = self._root_nodes.get(project_id, {})
        return {nid: dict(r_nodes[nid]) for nid in node_ids if nid in r_nodes}

    def _get_root_edges_by_keys(
        self, project_id: str, edge_keys: List[Tuple[str, str, str]]
    ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        r_edges = self._root_edges.get(project_id, {})
        return {ek: dict(r_edges[ek]) for ek in edge_keys if ek in r_edges}

    def get_resolved_subgraph(
        self,
        project_id: str,
        branch_name: str,
        files: Optional[List[str]] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        if not self.is_root_branch(branch_name):
            lineage = self.get_branch_lineage(project_id, branch_name)
            return self._resolve_multi_level_subgraph(
                project_id, branch_name, lineage, files=files, limit=limit
            )

        nodes = [dict(n) for n in self._root_nodes.get(project_id, {}).values()]
        if files:
            fset = set(files)
            nodes = [n for n in nodes if n.get("source_file") in fset]
        nodes = nodes[:limit]
        nids = {n["id"] for n in nodes}
        edges = [
            dict(e)
            for e in self._root_edges.get(project_id, {}).values()
            if e["source"] in nids and e["target"] in nids
        ]
        hyperedges = [dict(h) for h in self._root_hyperedges.get(project_id, {}).values()]
        return {
            "project_id": project_id,
            "branch": branch_name,
            "nodes": nodes,
            "edges": edges,
            "hyperedges": hyperedges,
        }

    def query_flow_graph(
        self,
        project_id: str,
        branch: str = "main",
        version_tag: Optional[str] = None,
        max_hops: int = 5,
    ) -> Dict[str, Any]:
        """Multi-hop flow graph query supporting version_tag filtering in HTTP & memory modes."""
        if self.mode == "http":
            cypher = (
                f"MATCH (s:GraphNode {{project_id: $project_id, branch: $branch}}) "
                f"WHERE s.file_type IN ['state', 'screen'] "
                f"AND ($version_tag IS NULL OR s.version_tag = $version_tag) "
                f"OPTIONAL MATCH p=(s)-[r:TRANSITIONS_TO*1..{int(max_hops)}]->(t:GraphNode) "
                f"RETURN s AS node, r AS transitions, t AS target"
            )
            rows = self._run_cypher(
                cypher,
                {
                    "project_id": project_id,
                    "branch": branch,
                    "version_tag": version_tag,
                },
            )
            nodes_by_id: Dict[str, Dict[str, Any]] = {}
            edges: List[Dict[str, Any]] = []
            for row in rows:
                n = row.get("node")
                if isinstance(n, dict) and n.get("id"):
                    nodes_by_id[n["id"]] = n
                t = row.get("target")
                if isinstance(t, dict) and t.get("id"):
                    nodes_by_id[t["id"]] = t
                trans = row.get("transitions")
                if isinstance(trans, list):
                    for item in trans:
                        if isinstance(item, dict):
                            edges.append(item)
                elif isinstance(trans, dict):
                    edges.append(trans)

            return {
                "project_id": project_id,
                "branch": branch,
                "version_tag": version_tag,
                "nodes": list(nodes_by_id.values()),
                "edges": edges,
                "cypher_executed": True,
            }

        sub = self.get_resolved_subgraph(project_id, branch, limit=100000)
        nodes = [
            n
            for n in sub.get("nodes", [])
            if n.get("file_type") in ("state", "screen")
            and (
                not version_tag
                or (n.get("metadata") or {}).get("version_tag") == version_tag
                or n.get("version_tag") == version_tag
            )
        ]
        node_ids = {n["id"] for n in nodes}
        edges = [
            e
            for e in sub.get("edges", [])
            if (e.get("relation") == "transitions_to" and e.get("source") in node_ids and e.get("target") in node_ids)
            or (e.get("source") in node_ids and e.get("target") in node_ids)
        ]
        return {
            "project_id": project_id,
            "branch": branch,
            "version_tag": version_tag,
            "nodes": nodes,
            "edges": edges,
            "cypher_executed": False,
        }

    def diff_branch_vs_root(
        self,
        project_id: str,
        branch_name: str,
        file_filter: Optional[str] = None,
        community: Optional[int] = None,
    ) -> BranchDiffResult:
        if self.is_root_branch(branch_name):
            return BranchDiffResult(summary=BranchDiffSummary())
        return self._diff_resolved_subgraphs(
            project_id, self.root_branch, branch_name, file_filter=file_filter, community=community
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
        overlay = self._get_branch_overlay(project_id, branch_name)

        if self.is_root_branch(effective_target):
            r_nodes = self._root_nodes.setdefault(project_id, {})
            r_edges = self._root_edges.setdefault(project_id, {})
            r_hyper = self._root_hyperedges.setdefault(project_id, {})
            for tid in overlay["node_tombstones"]:
                r_nodes.pop(tid, None)
            for nid, nd in overlay["node_upserts"].items():
                r_nodes[nid] = dict(nd)
            for ek in overlay["edge_tombstones"]:
                r_edges.pop(ek, None)
            for ek, ed in overlay["edge_upserts"].items():
                r_edges[ek] = dict(ed)
            for htid in overlay["hyperedge_tombstones"]:
                r_hyper.pop(htid, None)
            for hid, hd in overlay["hyperedge_upserts"].items():
                r_hyper[hid] = dict(hd)
        else:
            self.ensure_branch_schema(project_id, effective_target)
            tgt = self._overlays[(project_id, effective_target)]
            for tid in overlay["node_tombstones"]:
                tgt["node_upserts"].pop(tid, None)
                tgt["node_tombstones"].add(tid)
            for nid, nd in overlay["node_upserts"].items():
                tgt["node_tombstones"].discard(nid)
                tgt["node_upserts"][nid] = dict(nd)
            for ek in overlay["edge_tombstones"]:
                tgt["edge_upserts"].pop(ek, None)
                tgt["edge_tombstones"].add(ek)
            for ek, ed in overlay["edge_upserts"].items():
                tgt["edge_tombstones"].discard(ek)
                tgt["edge_upserts"][ek] = dict(ed)

        self._overlays.pop((project_id, branch_name), None)
        self._branches.get(project_id, {}).pop(branch_name, None)
        return {
            "status": "promoted",
            "project_id": project_id,
            "branch": branch_name,
            "target_branch": effective_target,
            "upserted_nodes": len(overlay["node_upserts"]),
            "deleted_nodes": len(overlay["node_tombstones"]),
            "upserted_edges": len(overlay["edge_upserts"]),
            "deleted_edges": len(overlay["edge_tombstones"]),
        }

    def get_stats(self, project_id: str, branch_name: str = "main") -> Dict[str, Any]:
        sub = self.get_resolved_subgraph(project_id, branch_name, limit=1_000_000)
        res = {
            "project_id": project_id,
            "branch": branch_name,
            "node_count": len(sub["nodes"]),
            "edge_count": len(sub["edges"]),
            "hyperedge_count": len(sub["hyperedges"]),
        }
        if not self.is_root_branch(branch_name):
            ov = self._get_branch_overlay(project_id, branch_name)
            res["delta_nodes_upserted"] = len(ov["node_upserts"])
            res["delta_nodes_tombstoned"] = len(ov["node_tombstones"])
            res["delta_edges_upserted"] = len(ov["edge_upserts"])
            res["delta_edges_tombstoned"] = len(ov["edge_tombstones"])
        return res

    def list_projects(self) -> List[str]:
        projs = set(self._root_nodes.keys()) | set(self._branches.keys())
        return sorted(projs)

    def delete_project(self, project_id: str) -> bool:
        self._root_nodes.pop(project_id, None)
        self._root_edges.pop(project_id, None)
        self._root_hyperedges.pop(project_id, None)
        for key in [k for k in list(self._overlays.keys()) if k[0] == project_id]:
            self._overlays.pop(key, None)
        self._branches.pop(project_id, None)
        return True

    def delete_branch(self, project_id: str, branch_name: str) -> bool:
        if self.is_root_branch(branch_name):
            return False
        self._overlays.pop((project_id, branch_name), None)
        self._branches.get(project_id, {}).pop(branch_name, None)
        return True

    def list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        branches = [{"branch_name": self.root_branch, "schema_name": "neo4j_root", "is_root": True}]
        for bname, meta in sorted(self._branches.get(project_id, {}).items()):
            if bname != self.root_branch:
                branches.append(dict(meta))
        return branches

    def to_legacy_visjs(
        self,
        project_id: str,
        branch_name: str = "main",
        query: Optional[str] = None,
        kinds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        sub = self.get_resolved_subgraph(project_id, branch_name, limit=limit * 2)
        return self._format_legacy_visjs(sub, query=query, kinds=kinds, limit=limit)


class Neo4jGraphPlugin(GraphBackendPlugin):
    """Built-in Neo4j GraphStore plugin (priority=50)."""

    metadata = PluginMetadata(
        plugin_id="graph.neo4j",
        name="Neo4j Cypher GraphStore",
        version="3.0.0",
        category=PluginCategory.GRAPH_BACKEND.value,
        description="Neo4j HTTP Cypher & offline memory-mode GraphStore with multi-hop flow queries.",
        priority=50,
        capabilities=["cypher", "multi_hop_flow_graph", "version_tag", "http_rest"],
        default_enabled=True,
    )

    def create_store(self, config: Optional[Dict[str, Any]] = None) -> Neo4jGraphStore:
        cfg = {**self.config, **(config or {})}
        return Neo4jGraphStore(
            uri=cfg.get("uri") or "http://localhost:7474",
            database=cfg.get("database") or "neo4j",
            username=cfg.get("username"),
            password=cfg.get("password"),
            root_branch=cfg.get("root_branch") or "main",
            mode=cfg.get("mode") or "memory",
            http_client=cfg.get("http_client"),
            transport=cfg.get("transport"),
            timeout=float(cfg.get("timeout", 15.0)),
        )

    def query_flow_graph(
        self,
        project_id: str,
        branch: str = "main",
        version_tag: Optional[str] = None,
        store: Optional[Any] = None,
    ) -> Dict[str, Any]:
        active = store or self.create_store(self.config)
        if hasattr(active, "query_flow_graph"):
            return active.query_flow_graph(
                project_id=project_id,
                branch=branch,
                version_tag=version_tag,
            )
        return super().query_flow_graph(
            project_id=project_id,
            branch=branch,
            version_tag=version_tag,
            store=active,
        )


# ---------------------------------------------------------------------------
# 4. HSGMemoryPlugin
# ---------------------------------------------------------------------------
class HSGMemoryPlugin(VectorBackendPlugin):
    """Built-in HSG Memory VectorBackendPlugin (default priority=100)."""

    metadata = PluginMetadata(
        plugin_id="vector.hsg_memory",
        name="HSG Memory Vector Backend",
        version="3.0.0",
        category=PluginCategory.VECTOR_BACKEND.value,
        description="Wraps Project-Brain HSG memory & vector store with project/branch/version_tag filtering.",
        priority=100,
        capabilities=["hsg_memory", "multi_sector", "hybrid_scoring", "offline"],
        default_enabled=True,
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config=config)
        self._memory_records: Dict[str, Dict[str, Any]] = {}

    def _is_memory_mode(self) -> bool:
        return str(self.config.get("mode", "hsg")).lower() == "memory"

    @staticmethod
    def _resolve_user_id(
        namespace: str,
        metadata: Dict[str, Any],
    ) -> str:
        if metadata.get("user_id"):
            return str(metadata["user_id"])
        proj = metadata.get("project_id")
        branch = metadata.get("branch")
        if proj and branch:
            return f"{proj}:{branch}"
        if proj:
            return str(proj)
        return namespace or "default"

    async def upsert_vectors(
        self, items: List[Dict[str, Any]], namespace: str = "default"
    ) -> Dict[str, Any]:
        dim = int(self.config.get("vector_dim", 64))
        upserted_ids: List[str] = []

        if self._is_memory_mode():
            for raw in items:
                item_id = str(raw.get("id") or uuid.uuid4())
                text = str(raw.get("text") or raw.get("content") or "")
                meta = dict(raw.get("metadata") or {})
                for key in ("project_id", "branch", "version_tag", "sector", "user_id"):
                    if key in raw and raw[key] is not None:
                        meta[key] = raw[key]
                meta.setdefault("namespace", namespace)
                vec = raw.get("vector") or _deterministic_dense_vector(text, dim=dim)
                self._memory_records[item_id] = {
                    "id": item_id,
                    "text": text,
                    "vector": list(vec),
                    "metadata": meta,
                    "namespace": namespace,
                    "tags": raw.get("tags") or [],
                }
                upserted_ids.append(item_id)
            return {"status": "ok", "upserted": len(upserted_ids), "ids": upserted_ids}

        # Live HSG delegation
        from ...core.vector_store import vector_store as core_vector_store
        from ...memory.embed import embed_for_sector
        from ...memory.hsg import add_hsg_memory

        active_store = self.config.get("vector_store") or core_vector_store
        for raw in items:
            text = str(raw.get("text") or raw.get("content") or "")
            meta = dict(raw.get("metadata") or {})
            for key in ("project_id", "branch", "version_tag", "sector"):
                if key in raw and raw[key] is not None:
                    meta[key] = raw[key]
            meta.setdefault("namespace", namespace)
            uid = self._resolve_user_id(namespace, meta)
            tags = raw.get("tags")
            tags_str = json.dumps(tags) if isinstance(tags, list) else tags

            if raw.get("vector") is not None:
                item_id = str(raw.get("id") or uuid.uuid4())
                sector = str(meta.get("sector") or "semantic")
                vec = list(raw["vector"])
                await active_store.storeVector(item_id, sector, vec, len(vec), uid)
                upserted_ids.append(item_id)
            else:
                res = await add_hsg_memory(
                    content=text,
                    tags=tags_str,
                    metadata=meta,
                    user_id=uid,
                )
                upserted_ids.append(str(res.get("id")))

        return {"status": "ok", "upserted": len(upserted_ids), "ids": upserted_ids}

    async def search(
        self,
        query: str,
        vector: Optional[List[float]] = None,
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        hybrid_alpha: float = 0.7,
    ) -> List[Dict[str, Any]]:
        dim = int(self.config.get("vector_dim", 64))
        flt = dict(filters or {})

        if self._is_memory_mode():
            q_vec = list(vector) if vector is not None else _deterministic_dense_vector(query, dim=dim)
            q_toks = set(_tokenize(query))
            scored: List[Dict[str, Any]] = []

            for rec in self._memory_records.values():
                combined_meta = {**rec.get("metadata", {}), "namespace": rec.get("namespace")}
                if not _matches_filters(combined_meta, flt):
                    continue
                dense_sc = _cosine_similarity(q_vec, rec["vector"])
                rec_toks = set(_tokenize(rec["text"]))
                kw_sc = (len(q_toks & rec_toks) / len(q_toks)) if q_toks else 0.0
                score = float(hybrid_alpha * dense_sc + (1.0 - hybrid_alpha) * kw_sc)
                scored.append(
                    {
                        "id": rec["id"],
                        "text": rec["text"],
                        "score": score,
                        "dense_score": dense_sc,
                        "keyword_score": kw_sc,
                        "metadata": dict(rec["metadata"]),
                    }
                )
            scored.sort(key=lambda x: (-x["score"], x["id"]))
            return scored[:top_k]

        from ...memory.hsg import hsg_query

        hsg_filters: Dict[str, Any] = {}
        if flt.get("user_id"):
            hsg_filters["user_id"] = flt["user_id"]
        elif flt.get("project_id") and flt.get("branch"):
            hsg_filters["user_id"] = f"{flt['project_id']}:{flt['branch']}"
        elif flt.get("project_id"):
            hsg_filters["user_id"] = str(flt["project_id"])
        if flt.get("sectors"):
            hsg_filters["sectors"] = flt["sectors"]

        raw_results = await hsg_query(query, k=max(top_k * 2, top_k), f=hsg_filters)
        extra_filters = {
            k: v for k, v in flt.items() if k not in ("user_id", "sectors", "project_id", "branch")
        }
        filtered: List[Dict[str, Any]] = []
        for item in raw_results:
            meta = item.get("metadata") or {}
            if extra_filters and not _matches_filters(meta, extra_filters):
                continue
            filtered.append(
                {
                    "id": item["id"],
                    "text": item.get("content", ""),
                    "score": float(item.get("score", 0.0)),
                    "metadata": meta,
                    "primary_sector": item.get("primary_sector"),
                }
            )
        return filtered[:top_k]

    async def delete_by_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        flt = dict(filters or {})
        if self._is_memory_mode():
            to_delete = [
                rid
                for rid, rec in self._memory_records.items()
                if _matches_filters({**rec.get("metadata", {}), "id": rid, "namespace": rec.get("namespace")}, flt)
            ]
            for rid in to_delete:
                self._memory_records.pop(rid, None)
            return {"status": "ok", "deleted": len(to_delete), "ids": to_delete}

        from ...core.db import q
        from ...core.vector_store import vector_store as core_vector_store

        active_store = self.config.get("vector_store") or core_vector_store
        deleted = 0
        if flt.get("id"):
            await active_store.deleteVectors(str(flt["id"]))
            q.del_mem(str(flt["id"]))
            deleted = 1
        elif flt.get("user_id"):
            q.del_mem_by_user(str(flt["user_id"]))
            deleted = 1
        return {"status": "ok", "deleted": deleted}


# ---------------------------------------------------------------------------
# 5. QdrantVectorPlugin (Hybrid BM25 + Dense)
# ---------------------------------------------------------------------------
class QdrantVectorPlugin(VectorBackendPlugin):
    """Built-in Qdrant Hybrid Dense + Sparse BM25 VectorBackendPlugin (priority=60)."""

    metadata = PluginMetadata(
        plugin_id="vector.qdrant",
        name="Qdrant Hybrid Vector Backend",
        version="3.0.0",
        category=PluginCategory.VECTOR_BACKEND.value,
        description="Qdrant REST Hybrid Dense + Sparse BM25 vector backend with offline memory mode.",
        priority=60,
        capabilities=["dense_vector", "sparse_bm25", "hybrid_search", "qdrant_rest"],
        default_enabled=True,
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config=config)
        self._memory_points: Dict[str, Dict[str, Any]] = {}

    def _mode(self) -> str:
        if self.config.get("transport") is not None or self.config.get("http_client") is not None:
            return str(self.config.get("mode", "http")).lower()
        return str(self.config.get("mode", "memory")).lower()

    def _base_url(self) -> str:
        return str(self.config.get("url") or "http://localhost:6333").rstrip("/")

    def _collection(self) -> str:
        return str(self.config.get("collection") or "projectbrain_v3")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        api_key = self.config.get("api_key")
        if api_key:
            headers["api-key"] = str(api_key)
        return headers

    @staticmethod
    def _build_qdrant_filter(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not filters:
            return None
        must = []
        for key, val in filters.items():
            if val is None:
                continue
            if isinstance(val, (list, tuple, set)):
                must.append({"key": key, "match": {"any": list(val)}})
            else:
                must.append({"key": key, "match": {"value": val}})
        return {"must": must} if must else None

    async def upsert_vectors(
        self, items: List[Dict[str, Any]], namespace: str = "default"
    ) -> Dict[str, Any]:
        dim = int(self.config.get("vector_dim", 64))
        points: List[Dict[str, Any]] = []

        for raw in items:
            pid = str(raw.get("id") or uuid.uuid4())
            text = str(raw.get("text") or raw.get("content") or "")
            dense = list(raw.get("vector") or _deterministic_dense_vector(text, dim=dim))
            sparse = raw.get("sparse_vector") or build_sparse_bm25_vector(text)
            payload = dict(raw.get("metadata") or {})
            for key in ("project_id", "branch", "version_tag", "source_file"):
                if key in raw and raw[key] is not None:
                    payload[key] = raw[key]
            payload["text"] = text
            payload.setdefault("namespace", namespace)

            points.append(
                {
                    "id": pid,
                    "vector": {
                        "dense": dense,
                        "bm25": sparse,
                    },
                    "payload": payload,
                }
            )

        if self._mode() == "memory":
            for pt in points:
                self._memory_points[pt["id"]] = pt
            return {
                "status": "ok",
                "mode": "memory",
                "upserted": len(points),
                "ids": [pt["id"] for pt in points],
            }

        url = f"{self._base_url()}/collections/{self._collection()}/points"
        ext_client: Optional[httpx.AsyncClient] = self.config.get("http_client")
        if ext_client is not None:
            resp = await ext_client.put(url, json={"points": points}, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        else:
            async with httpx.AsyncClient(
                transport=self.config.get("transport"),
                timeout=float(self.config.get("timeout", 15.0)),
            ) as client:
                resp = await client.put(url, json={"points": points}, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()

        return {
            "status": "ok",
            "mode": "http",
            "upserted": len(points),
            "ids": [pt["id"] for pt in points],
            "qdrant_response": data,
        }

    async def search(
        self,
        query: str,
        vector: Optional[List[float]] = None,
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        hybrid_alpha: float = 0.7,
    ) -> List[Dict[str, Any]]:
        dim = int(self.config.get("vector_dim", 64))
        q_dense = list(vector) if vector is not None else _deterministic_dense_vector(query, dim=dim)
        q_sparse = build_sparse_bm25_vector(query)

        if self._mode() == "memory":
            raw_scored: List[Tuple[float, float, Dict[str, Any]]] = []
            max_sparse = 0.0
            for pt in self._memory_points.values():
                payload = pt.get("payload") or {}
                if not _matches_filters(payload, filters):
                    continue
                d_sc = max(0.0, _cosine_similarity(q_dense, pt["vector"]["dense"]))
                s_sc = _sparse_dot(q_sparse, pt["vector"]["bm25"])
                if s_sc > max_sparse:
                    max_sparse = s_sc
                raw_scored.append((d_sc, s_sc, pt))

            results: List[Dict[str, Any]] = []
            for d_sc, s_sc, pt in raw_scored:
                s_norm = (s_sc / max_sparse) if max_sparse > 0.0 else 0.0
                hybrid_score = float(hybrid_alpha * d_sc + (1.0 - hybrid_alpha) * s_norm)
                payload = dict(pt.get("payload") or {})
                results.append(
                    {
                        "id": pt["id"],
                        "score": hybrid_score,
                        "dense_score": d_sc,
                        "sparse_score": s_norm,
                        "text": payload.get("text", ""),
                        "metadata": payload,
                    }
                )
            results.sort(key=lambda x: (-x["score"], x["id"]))
            return results[:top_k]

        url = f"{self._base_url()}/collections/{self._collection()}/points/query"
        q_filter = self._build_qdrant_filter(filters)
        body: Dict[str, Any] = {
            "prefetch": [
                {"query": q_dense, "using": "dense", "limit": max(top_k * 2, 20)},
                {"query": q_sparse, "using": "bm25", "limit": max(top_k * 2, 20)},
            ],
            "query": {"fusion": "rrf"},
            "limit": top_k,
            "with_payload": True,
        }
        if q_filter:
            body["filter"] = q_filter

        ext_client: Optional[httpx.AsyncClient] = self.config.get("http_client")
        if ext_client is not None:
            resp = await ext_client.post(url, json=body, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        else:
            async with httpx.AsyncClient(
                transport=self.config.get("transport"),
                timeout=float(self.config.get("timeout", 15.0)),
            ) as client:
                resp = await client.post(url, json=body, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()

        raw_res = data.get("result")
        pts = raw_res.get("points", []) if isinstance(raw_res, dict) else (raw_res or [])
        out: List[Dict[str, Any]] = []
        for pt in pts:
            payload = pt.get("payload") or {}
            out.append(
                {
                    "id": str(pt.get("id")),
                    "score": float(pt.get("score", 0.0)),
                    "text": payload.get("text", ""),
                    "metadata": payload,
                }
            )
        return out[:top_k]

    async def delete_by_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        if self._mode() == "memory":
            to_del = [
                pid
                for pid, pt in self._memory_points.items()
                if _matches_filters({**(pt.get("payload") or {}), "id": pid}, filters)
            ]
            for pid in to_del:
                self._memory_points.pop(pid, None)
            return {"status": "ok", "mode": "memory", "deleted": len(to_del), "ids": to_del}

        url = f"{self._base_url()}/collections/{self._collection()}/points/delete"
        q_filter = self._build_qdrant_filter(filters) or {"must": []}
        ext_client: Optional[httpx.AsyncClient] = self.config.get("http_client")
        if ext_client is not None:
            resp = await ext_client.post(url, json={"filter": q_filter}, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        else:
            async with httpx.AsyncClient(
                transport=self.config.get("transport"),
                timeout=float(self.config.get("timeout", 15.0)),
            ) as client:
                resp = await client.post(url, json={"filter": q_filter}, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()

        return {"status": "ok", "mode": "http", "qdrant_response": data}


__all__ = [
    "build_sparse_bm25_vector",
    "SqliteOverlayGraphPlugin",
    "PostgresSchemaGraphPlugin",
    "Neo4jGraphStore",
    "Neo4jGraphPlugin",
    "HSGMemoryPlugin",
    "QdrantVectorPlugin",
]
