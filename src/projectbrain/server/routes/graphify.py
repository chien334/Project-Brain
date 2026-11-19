"""Graphify REST API Routes for Hybrid Graphify Engine.

Provides endpoints for branch delta synchronization, multi-tier querying,
structural diffing (O(Delta) branch-vs-root and branch-vs-branch), atomic promotion,
subgraph retrieval, and branch lifecycle management.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict

from ...core.db import db
from ...graphify_engine.extractor import GraphifyExtractor
from ...graphify_engine.hybrid_resolver import HybridResolver
from ...graphify_engine.models import (
    BranchDiffResult,
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)
from ...graphify_engine.store import get_graph_store

logger = logging.getLogger("server.routes.graphify")

router = APIRouter(prefix="/graphify", tags=["graphify"])


def _get_store():
    """Retrieve GraphStore instance configured with shared database path if SQLite."""
    db_path = (
        os.getenv("PB_GRAPH_DB_PATH")
        or os.getenv("OM_GRAPH_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or os.getenv("OM_DB_PATH")
    )
    if not db_path:
        try:
            from ...core.config import env
            db_url = getattr(env, "database_url", "")
            if db_url.startswith("sqlite:///"):
                db_path = db_url.replace("sqlite:///", "")
        except Exception:
            pass
    return get_graph_store(db_path=db_path)


def _check_read_only() -> None:
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted: Server is running in Read-Only mode."
        )


def _validate_local_path(local_path: Optional[str]) -> Optional[str]:
    """Validate that local_path is within the workspace root to prevent path traversal."""
    if not local_path:
        return None
    try:
        resolved = Path(local_path).resolve()
        resolved.relative_to(Path.cwd().resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or out-of-workspace local_path")
    return str(resolved)


def _upsert_project_metadata(
    project_id: str,
    project_name: Optional[str] = None,
    author: Optional[str] = None,
    client_ip: Optional[str] = None,
    project_path: Optional[str] = None,
) -> None:
    """Ensure project metadata exists in the legacy projects table for dashboard listing."""
    try:
        db.connect()
        ts = int(time.time())
        name = project_name or project_id
        cursor = db.conn.cursor()
        if db.is_postgres:
            cursor.execute(
                """
                INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    updated_at = EXCLUDED.updated_at,
                    sync_ip = EXCLUDED.sync_ip,
                    sync_author = EXCLUDED.sync_author
                """,
                (project_id, name, f"Project {name}", ts, ts, client_ip or "127.0.0.1", author or "system", project_path)
            )
        else:
            cursor.execute(
                """
                INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at,
                    sync_ip = excluded.sync_ip,
                    sync_author = excluded.sync_author
                """,
                (project_id, name, f"Project {name}", ts, ts, client_ip or "127.0.0.1", author or "system", project_path)
            )
        db.conn.commit()
        cursor.close()
    except Exception as e:
        logger.warning(f"Failed to upsert project metadata for {project_id}: {e}")
        if db.conn:
            try:
                db.conn.rollback()
            except Exception:
                pass


class SyncBranchRequest(BaseModel):
    project_id: str
    branch: str = "main"
    base_branch: Optional[str] = None
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    hyperedges: List[GraphHyperedge] = Field(default_factory=list)
    node_tombstones: List[str] = Field(default_factory=list)
    edge_tombstones: List[Dict[str, Any]] = Field(default_factory=list)
    hyperedge_tombstones: List[str] = Field(default_factory=list)
    synced_files: List[Any] = Field(default_factory=list)
    partial: bool = False
    synced_files_filter: Optional[List[str]] = Field(default=None, alias="synced_files_list")
    deleted_files: Optional[List[str]] = None
    author: str = "system"
    cluster: bool = False
    project_path: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class PromoteRequest(BaseModel):
    project_id: str
    branch: str
    target_branch: Optional[str] = None


class QueryRequest(BaseModel):
    project_id: str
    query: str
    branch: str = "main"
    local_delta: Optional[Dict[str, Any]] = None
    local_path: Optional[str] = None
    mode: str = "bfs"
    min_confidence: str = "AMBIGUOUS"
    budget: int = 2500


class LocalDiffRequest(BaseModel):
    project_id: str
    branch: str = "main"
    local_delta: Optional[Dict[str, Any]] = None
    local_path: Optional[str] = None
    scoped_only: bool = True


@router.post("/sync")
async def sync_graph(req: SyncBranchRequest, request: Request = None):
    _check_read_only()
    try:
        # Build GraphDelta instance
        delta = GraphDelta(
            project_id=req.project_id,
            branch=req.branch,
            nodes=req.nodes,
            edges=req.edges,
            hyperedges=req.hyperedges,
            node_tombstones=req.node_tombstones,
            edge_tombstones=req.edge_tombstones,
            hyperedge_tombstones=req.hyperedge_tombstones,
            synced_files=req.synced_files,
        )

        # Optional community clustering via GraphifyExtractor
        if req.cluster and delta.nodes:
            try:
                extractor = GraphifyExtractor()
                delta = extractor.cluster_graph(delta)
            except Exception as ce:
                logger.warning(f"Community clustering failed or skipped: {ce}")

        # Determine synced and deleted file lists
        synced_files = req.synced_files_filter
        if not synced_files and req.synced_files:
            synced_files = [
                sf["file_path"] if isinstance(sf, dict) else str(sf)
                for sf in req.synced_files
            ]

        store = _get_store()
        try:
            res = store.sync_branch_delta(
                project_id=req.project_id,
                branch_name=req.branch,
                delta=delta,
                partial=req.partial,
                synced_files=synced_files,
                deleted_files=req.deleted_files,
                author=req.author,
                base_branch=req.base_branch,
            )
        finally:
            store.close()

        if req.base_branch and req.base_branch != req.branch:
            try:
                from ...core.db import register_memory_branch
                from ...memory.hsg import invalidate_query_cache
                register_memory_branch(req.project_id, req.branch, req.base_branch)
                invalidate_query_cache()
            except Exception as me:
                logger.debug(f"Memory branch registration skipped for {req.project_id}:{req.branch}: {me}")

        # Ensure project exists in legacy projects catalog
        client_ip = request.client.host if request and request.client else "127.0.0.1"
        _upsert_project_metadata(
            project_id=req.project_id,
            author=req.author,
            client_ip=client_ip,
            project_path=req.project_path,
        )

        return res
    except Exception as e:
        logger.error(f"Failed to sync graph for {req.project_id}:{req.branch}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Graph synchronization failed: {str(e)}")


@router.get("/subgraph")
async def get_subgraph(
    project_id: str,
    branch: str = "main",
    files: Optional[str] = None,
    limit: int = 1000,
):
    try:
        file_list = [f.strip() for f in files.split(",") if f.strip()] if files else None
        store = _get_store()
        try:
            res = store.get_resolved_subgraph(
                project_id=project_id,
                branch_name=branch,
                files=file_list,
                limit=limit,
            )
            return res
        finally:
            store.close()
    except Exception as e:
        logger.error(f"Failed to get subgraph for {project_id}:{branch}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve subgraph: {str(e)}")


@router.get("/diff")
async def diff_graph(
    project_id: str,
    branch: Optional[str] = None,
    base_branch: Optional[str] = None,
    target_branch: Optional[str] = None,
    file_filter: Optional[str] = None,
    community: Optional[int] = None,
):
    try:
        store = _get_store()
        try:
            effective_base = base_branch or store.root_branch
            if target_branch:
                res = store.diff_branches(
                    project_id=project_id,
                    base_branch=effective_base,
                    target_branch=target_branch,
                    file_filter=file_filter,
                    community=community,
                )
            else:
                diff_target = branch or target_branch or store.root_branch
                res = store.diff_branch_vs_root(
                    project_id=project_id,
                    branch_name=diff_target,
                    file_filter=file_filter,
                    community=community,
                )
        finally:
            store.close()

        if hasattr(res, "to_dict"):
            return res.to_dict()
        elif hasattr(res, "model_dump"):
            return res.model_dump()
        return res
    except Exception as e:
        logger.error(f"Failed to compute diff for {project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to diff graph: {str(e)}")


@router.post("/promote")
async def promote_branch(req: PromoteRequest):
    _check_read_only()
    try:
        store = _get_store()
        try:
            res = store.promote_branch_to_root(
                project_id=req.project_id,
                branch_name=req.branch,
                target_branch=req.target_branch,
            )
            return res
        finally:
            store.close()
    except Exception as e:
        logger.error(f"Failed to promote branch {req.branch} for {req.project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Branch promotion failed: {str(e)}")


@router.post("/query")
async def query_graph(req: QueryRequest):
    temp_path = None
    local_path = req.local_path
    if req.local_delta:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(req.local_delta, tf)
            temp_path = tf.name
        local_path = temp_path
    elif local_path:
        local_path = _validate_local_path(local_path)

    try:
        store = _get_store()
        try:
            resolver = HybridResolver(store=store)
            res = resolver.query(
                project_id=req.project_id,
                query_text=req.query,
                branch=req.branch,
                local_path=local_path,
                mode=req.mode,
                min_confidence=req.min_confidence,
                budget=req.budget,
            )
            return res
        finally:
            store.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute hybrid query for {req.project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Hybrid graph query failed: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@router.post("/local-diff")
async def local_diff(req: LocalDiffRequest):
    temp_path = None
    local_path = req.local_path
    if req.local_delta:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(req.local_delta, tf)
            temp_path = tf.name
        local_path = temp_path
    elif local_path:
        local_path = _validate_local_path(local_path)

    try:
        store = _get_store()
        try:
            resolver = HybridResolver(store=store)
            res = resolver.diff_local_vs_remote(
                project_id=req.project_id,
                branch=req.branch,
                local_path=local_path,
                scoped_only=req.scoped_only,
            )
            return res
        finally:
            store.close()
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to compute local diff for {req.project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Local graph diff failed: {str(e)}")


@router.get("/branches")
async def list_branches(project_id: str):
    try:
        store = _get_store()
        try:
            branches = store.list_branches(project_id)
            return {"project_id": project_id, "branches": branches}
        finally:
            store.close()
    except Exception as e:
        logger.error(f"Failed to list branches for {project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list branches: {str(e)}")


@router.delete("/branches")
async def delete_branch(project_id: str = Query(...), branch: str = Query(...)):
    _check_read_only()
    try:
        store = _get_store()
        try:
            success = store.delete_branch(project_id, branch)
            return {"status": "deleted" if success else "not_found", "project_id": project_id, "branch": branch}
        finally:
            store.close()
    except Exception as e:
        logger.error(f"Failed to delete branch {branch} for {project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete branch: {str(e)}")


@router.get("/stats")
async def get_stats(project_id: str, branch: str = "main"):
    try:
        store = _get_store()
        try:
            stats = store.get_stats(project_id, branch)
            return stats
        finally:
            store.close()
    except Exception as e:
        logger.error(f"Failed to get stats for {project_id}:{branch}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
