"""Graphify REST API Routes for Hybrid Graphify Engine.

Provides endpoints for branch delta synchronization, multi-tier querying,
structural diffing (O(Delta) branch-vs-root and branch-vs-branch), atomic promotion,
subgraph retrieval, and branch lifecycle management.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict

from ...core.db import db
from ...core.tenant_manager import parse_tenant_spec, tenant_manager
from ...graphify_engine.cross_source_linker import CrossSourceLinker
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
from ...memory.hsg import (
    hsg_query,
    invalidate_query_cache,
    invalidate_source_query_cache,
    resolve_cross_source_relations_summary,
)

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


def _resolve_project_db_name(project_id: str) -> str:
    """Resolve the physical Project DB identifier registered in Master catalog (or deterministic fallback)."""
    from ...core.tenant_manager import (
        _effective_root_branch,
        parse_tenant_from_user_id,
        sanitize_project_db_name,
        tenant_manager,
    )

    clean_proj = parse_tenant_from_user_id(project_id, default_branch=_effective_root_branch())[0]
    try:
        row = tenant_manager._master_fetchone(
            "SELECT db_name FROM master_projects WHERE project_id = ?",
            (clean_proj,),
        )
        if row and row.get("db_name"):
            return str(row["db_name"])
    except Exception:
        pass
    return sanitize_project_db_name(clean_proj)


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
    device: Optional[str] = None
    workers: Optional[int] = None
    semantic_engine: Optional[str] = None
    source_id: Optional[str] = None
    source_version: Optional[str] = None
    upgrade_mode: str = "delta"
    auto_link_sources: bool = True

    model_config = ConfigDict(populate_by_name=True, extra="allow")


SyncRequest = SyncBranchRequest


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
    source_id: Optional[str] = None
    include_shared_docs: bool = True
    expand_relations: bool = True
    relation_depth: int = 1
    relation_types: Optional[List[str]] = None


class HybridQueryRequest(BaseModel):
    query: str
    project_id: str = "default"
    branch: str = "main"
    source_id: Optional[str] = None
    include_memories: bool = True
    memory_limit: int = 10
    include_shared_docs: bool = True
    expand_relations: bool = True
    relation_depth: int = 1
    relation_types: Optional[List[str]] = None
    mode: str = "bfs"
    budget: int = 2500
    min_confidence: str = "AMBIGUOUS"
    local_delta: Optional[Dict[str, Any]] = None
    local_path: Optional[str] = None


class LocalDiffRequest(BaseModel):
    project_id: str
    branch: str = "main"
    local_delta: Optional[Dict[str, Any]] = None
    local_path: Optional[str] = None
    scoped_only: bool = True


def _prepare_local_path(
    local_path: Optional[str],
    local_delta: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    """Materialize temporary local_delta JSON or validate local_path."""
    if local_delta:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(local_delta, tf)
            return tf.name, tf.name
    if local_path:
        return _validate_local_path(local_path), None
    return None, None


def _normalize_graph_route_spec(
    project_id: str,
    branch: str = "main",
    source_id: Optional[str] = None,
) -> Tuple[str, str, Optional[str]]:
    base_proj, parsed_src, parsed_br = parse_tenant_spec(
        project_id,
        default_branch=branch,
        default_source=source_id,
    )
    eff_branch = parsed_br or branch or "main"
    eff_src = source_id if source_id is not None else parsed_src
    if eff_src is not None:
        eff_src = str(eff_src).strip() or None
    if eff_src == "__default__":
        eff_src = None
    return base_proj, eff_branch, eff_src


@router.post("/sync")
async def sync_graph(req: SyncBranchRequest, request: Request = None):
    _check_read_only()
    try:
        base_proj, eff_branch, eff_source_id = _normalize_graph_route_spec(
            req.project_id,
            branch=req.branch,
            source_id=req.source_id,
        )

        # Build GraphDelta instance
        delta = GraphDelta(
            project_id=base_proj,
            branch=eff_branch,
            nodes=req.nodes,
            edges=req.edges,
            hyperedges=req.hyperedges,
            node_tombstones=req.node_tombstones,
            edge_tombstones=req.edge_tombstones,
            hyperedge_tombstones=req.hyperedge_tombstones,
            synced_files=req.synced_files,
        )

        # Optional community clustering via GraphifyExtractor
        if (req.cluster or bool(getattr(req, "recluster", False))) and delta.nodes:
            try:
                extractor = GraphifyExtractor(
                    device=req.device or "auto",
                    workers=req.workers,
                    semantic_engine=req.semantic_engine or "auto",
                )
                delta = extractor.cluster_graph(
                    delta,
                    device=req.device or "auto",
                    workers=req.workers,
                    semantic_engine=req.semantic_engine or "auto",
                )
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
            sync_fn = store.sync_branch_delta
            sync_kwargs: Dict[str, Any] = {
                "project_id": base_proj,
                "branch_name": eff_branch,
                "delta": delta,
                "partial": req.partial,
                "synced_files": synced_files,
                "deleted_files": req.deleted_files,
                "author": req.author,
                "base_branch": req.base_branch,
            }
            try:
                sig = inspect.signature(sync_fn)
                accepts_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                )
                if eff_source_id is not None and ("source_id" in sig.parameters or accepts_kw):
                    sync_kwargs["source_id"] = eff_source_id
                if "upgrade_mode" in sig.parameters or accepts_kw:
                    sync_kwargs["upgrade_mode"] = req.upgrade_mode
            except Exception:
                pass

            res = sync_fn(**sync_kwargs)
        finally:
            store.close()

        if req.base_branch and req.base_branch != eff_branch:
            try:
                from ...core.db import register_memory_branch
                register_memory_branch(base_proj, eff_branch, req.base_branch)
                invalidate_query_cache()
            except Exception as me:
                logger.debug(f"Memory branch registration skipped for {base_proj}:{eff_branch}: {me}")

        # Ensure project exists in legacy projects catalog
        client_ip = request.client.host if request and request.client else "127.0.0.1"
        _upsert_project_metadata(
            project_id=base_proj,
            author=req.author,
            client_ip=client_ip,
            project_path=req.project_path,
        )

        # Auto-link cross-source contracts when multi-source tracking is active
        link_res: Optional[Dict[str, Any]] = None
        if req.auto_link_sources:
            try:
                reg_sources = tenant_manager.list_project_sources(base_proj)
                has_rels = bool(tenant_manager.list_cross_source_relations(base_proj))
                if eff_source_id is not None or len(reg_sources) > 1 or has_rels:
                    ws_map = (
                        {eff_source_id: req.project_path}
                        if (eff_source_id and req.project_path)
                        else None
                    )
                    linker = CrossSourceLinker()
                    link_res = linker.auto_link_project(
                        project_id=base_proj,
                        source_id=eff_source_id,
                        branch=eff_branch,
                        workspace_map=ws_map,
                    )
                    if isinstance(res, dict):
                        res["auto_link"] = {
                            "relations_upserted": link_res.get("relations_upserted", 0),
                            "drifted_count": link_res.get("drifted_count", 0),
                            "stale_count": link_res.get("stale_count", 0),
                        }
            except Exception as le:
                logger.debug(f"Cross-source auto-linking skipped for {base_proj}: {le}")

        # Record independent source upgrade event when requested
        if (
            (req.source_version or req.upgrade_mode == "full_replace")
            and eff_source_id is not None
            and eff_source_id != "__shared__"
        ):
            try:
                nodes_delta = (
                    int(res.get("upserted_nodes", len(delta.nodes)))
                    if isinstance(res, dict)
                    else len(delta.nodes)
                )
                edges_delta = (
                    int(res.get("upserted_edges", len(delta.edges)))
                    if isinstance(res, dict)
                    else len(delta.edges)
                )
                affected_rels = len(link_res.get("relations", [])) if link_res else 0
                drifted_rels = int(link_res.get("drifted_count", 0)) if link_res else 0
                upg = tenant_manager.record_source_upgrade(
                    base_proj,
                    eff_source_id,
                    eff_branch,
                    to_version=req.source_version or "1.0.0",
                    upgrade_mode=req.upgrade_mode,
                    nodes_delta=nodes_delta,
                    edges_delta=edges_delta,
                    affected_relations=affected_rels,
                    drifted_relations=drifted_rels,
                    actor=req.author or "system",
                )
                if isinstance(res, dict):
                    res["upgrade"] = upg
            except Exception as ue:
                logger.debug(f"Source upgrade recording skipped for {base_proj}@{eff_source_id}: {ue}")

        invalidate_source_query_cache(base_proj, eff_source_id)
        invalidate_query_cache()

        if isinstance(res, dict) and not res.get("db_name"):
            res["db_name"] = _resolve_project_db_name(base_proj)
        if isinstance(res, dict) and eff_source_id is not None and not res.get("source_id"):
            res["source_id"] = eff_source_id

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
    source_id: Optional[str] = None,
):
    try:
        file_list = [f.strip() for f in files.split(",") if f.strip()] if files else None
        base_proj, eff_branch, eff_src = _normalize_graph_route_spec(
            project_id, branch=branch, source_id=source_id
        )
        store = _get_store()
        try:
            fn = store.get_resolved_subgraph
            kwargs: Dict[str, Any] = {
                "project_id": base_proj,
                "branch_name": eff_branch,
                "files": file_list,
                "limit": limit,
            }
            try:
                sig = inspect.signature(fn)
                if eff_src is not None and (
                    "source_id" in sig.parameters
                    or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                ):
                    kwargs["source_id"] = eff_src
            except Exception:
                pass
            return fn(**kwargs)
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
    local_path, temp_path = _prepare_local_path(req.local_path, req.local_delta)
    try:
        base_proj, eff_branch, eff_src = _normalize_graph_route_spec(
            req.project_id, branch=req.branch, source_id=req.source_id
        )
        store = _get_store()
        try:
            resolver = HybridResolver(store=store)
            res = resolver.query(
                project_id=base_proj,
                query_text=req.query,
                branch=eff_branch,
                local_path=local_path,
                mode=req.mode,
                min_confidence=req.min_confidence,
                budget=req.budget,
                source_id=eff_src,
                include_shared_docs=req.include_shared_docs,
                expand_relations=req.expand_relations,
                relation_depth=req.relation_depth,
                relation_types=req.relation_types,
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


@router.post("/hybrid-query")
async def hybrid_query_graph_and_memory(req: HybridQueryRequest):
    """Combined cross-source Graphify + HSG memory query endpoint."""
    local_path, temp_path = _prepare_local_path(req.local_path, req.local_delta)
    try:
        base_proj, eff_branch, eff_src = _normalize_graph_route_spec(
            req.project_id, branch=req.branch, source_id=req.source_id
        )
        store = _get_store()
        try:
            resolver = HybridResolver(store=store)
            graph_res = resolver.query(
                project_id=base_proj,
                query_text=req.query,
                branch=eff_branch,
                local_path=local_path,
                mode=req.mode,
                min_confidence=req.min_confidence,
                budget=req.budget,
                source_id=eff_src,
                include_shared_docs=req.include_shared_docs,
                expand_relations=req.expand_relations,
                relation_depth=req.relation_depth,
                relation_types=req.relation_types,
            )
        finally:
            store.close()

        memories: List[Dict[str, Any]] = []
        if req.include_memories and int(req.memory_limit or 0) > 0:
            mem_filters = {
                "user_id": base_proj,
                "branch": eff_branch,
                "source_id": eff_src,
                "include_shared_docs": req.include_shared_docs,
                "relation_depth": req.relation_depth if req.expand_relations else 0,
                "relation_types": req.relation_types,
            }
            memories = await hsg_query(req.query, k=int(req.memory_limit), f=mem_filters)

        cross_source_relations = (
            graph_res.get("cross_source_relations")
            if graph_res.get("cross_source_relations") is not None
            else resolve_cross_source_relations_summary(
                base_proj,
                source_id=eff_src,
                branch=eff_branch,
                relation_depth=req.relation_depth,
                relation_types=req.relation_types,
                expand_relations=req.expand_relations,
                include_shared_docs=req.include_shared_docs,
            )
        )
        return {
            "status": "ok",
            "project_id": base_proj,
            "branch": eff_branch,
            "source_id": eff_src,
            "graph": graph_res,
            "matched_nodes": graph_res.get("nodes", []),
            "edges": graph_res.get("edges", []),
            "memories": memories,
            "cross_source_relations": cross_source_relations,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute hybrid-query for {req.project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Hybrid query failed: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@router.post("/local-diff")
async def local_diff(req: LocalDiffRequest):
    local_path, temp_path = _prepare_local_path(req.local_path, req.local_delta)

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
            db_name = next(
                (str(b["db_name"]) for b in branches if isinstance(b, dict) and b.get("db_name")),
                None,
            ) or _resolve_project_db_name(project_id)
            return {"project_id": project_id, "db_name": db_name, "branches": branches}
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


async def collect_hardware_status(
    ocr_device: Optional[str] = None,
    graphify_device: Optional[str] = None,
    semantic_engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect unified hardware, OCR, Graphify GPU/CPU, and Local AI readiness status."""
    from ...graphify_engine.gpu_accelerator import GraphGPUAccelerator
    from ...ocr_runtime import ComputeDeviceManager, UnifiedOCREngine

    ocr_engine = UnifiedOCREngine()
    ocr_status = ocr_engine.get_full_status()
    if inspect.isawaitable(ocr_status):
        ocr_status = await ocr_status

    if ocr_device is not None and isinstance(ocr_status, dict):
        cfg = ocr_status.get("config")
        selected_ocr_engine = str((cfg or {}).get("ocr_engine") or "auto")
        ocr_status = dict(ocr_status)
        ocr_status["resolved_device"] = ComputeDeviceManager.resolve_device(
            requested=ocr_device,
            engine=selected_ocr_engine,
            hardware=ocr_status.get("hardware"),
        )
        if isinstance(cfg, dict):
            ocr_status["config"] = {**cfg, "ocr_device": ocr_device}

    graphify_status = GraphGPUAccelerator.get_status()
    if inspect.isawaitable(graphify_status):
        graphify_status = await graphify_status

    if isinstance(graphify_status, dict) and (
        graphify_device is not None or semantic_engine is not None
    ):
        graphify_status = dict(graphify_status)
        cfg = dict(graphify_status.get("config") or {})
        if graphify_device is not None:
            graphify_status["resolved_backend"] = GraphGPUAccelerator.resolve_compute_backend(
                device=graphify_device
            )
            cfg["graphify_device"] = graphify_device
        if semantic_engine is not None:
            cfg["graphify_semantic_engine"] = semantic_engine
        graphify_status["config"] = cfg

    return {
        "status": "ok",
        "ocr": ocr_status,
        "graphify": graphify_status,
    }


@router.get("/hardware")
async def get_graphify_hardware(
    ocr_device: Optional[str] = Query(default=None),
    graphify_device: Optional[str] = Query(default=None),
    semantic_engine: Optional[str] = Query(default=None),
):
    """Report OCR and Graphify GPU/MPS/CUDA/CPU hardware and Local AI readiness."""
    try:
        return await collect_hardware_status(
            ocr_device=ocr_device,
            graphify_device=graphify_device,
            semantic_engine=semantic_engine,
        )
    except Exception as e:
        logger.error(f"Failed to inspect hardware status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to inspect hardware status: {str(e)}")
