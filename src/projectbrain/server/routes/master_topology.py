from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.config import env
from ...core.tenant_manager import (
    _effective_root_branch,
    parse_tenant_from_user_id,
    sanitize_branch_schema_for_project,
    tenant_manager,
)
from ...graphify_engine.store import get_graph_store

router = APIRouter(prefix="/master", tags=["master"])


def _check_read_only() -> None:
    if getattr(env, "read_only", False):
        raise HTTPException(
            status_code=403,
            detail="Server is running in read-only mode",
        )


def _normalize_backend_type(raw_backend: Any) -> str:
    val = str(raw_backend or "sqlite").strip().lower()
    if val.startswith("postgres"):
        return "postgres"
    return "sqlite"


def _normalize_project_id(raw_project_id: str) -> str:
    cleaned = (raw_project_id or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="project_id is required")
    if ":" in cleaned:
        proj, _ = parse_tenant_from_user_id(cleaned, default_branch=_effective_root_branch())
        return proj
    return cleaned


class CreateMasterProjectRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None
    root_branch: str = "main"


class CreateMasterBranchRequest(BaseModel):
    branch_name: str = Field(..., min_length=1)
    base_branch: str = "main"
    author: str = "system"


def collect_master_topology(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the Master -> Project DB -> Branch Schema topology summary."""
    raw = tenant_manager.get_master_topology()
    master_info = raw.get("master", {}) if isinstance(raw, dict) else {}
    raw_projects: List[Dict[str, Any]] = list(raw.get("projects", []))

    filter_proj: Optional[str] = None
    if project_id is not None and str(project_id).strip():
        raw_filter = str(project_id).strip()
        filter_proj = (
            parse_tenant_from_user_id(raw_filter, default_branch=_effective_root_branch())[0]
            if ":" in raw_filter
            else raw_filter
        )

    master_backend = _normalize_backend_type(master_info.get("backend", "sqlite"))
    multi_db_enabled = bool(master_info.get("multi_db_enabled", False))

    fallback_store = None
    if not multi_db_enabled:
        try:
            fallback_store = get_graph_store()
        except Exception:
            fallback_store = None

    formatted_projects: List[Dict[str, Any]] = []
    try:
        for p in raw_projects:
            pid = str(p.get("project_id") or "")
            if filter_proj is not None and pid != filter_proj:
                continue

            p_backend = _normalize_backend_type(p.get("backend_type", master_backend))
            p_root = str(p.get("root_branch") or _effective_root_branch())
            p_root_schema = str(p.get("root_schema") or "pb_root")

            formatted_branches: List[Dict[str, Any]] = []
            for b in p.get("branches", []) or []:
                b_name = str(b.get("branch_name") or "")
                counts = b.get("counts", {}) if isinstance(b.get("counts"), dict) else {}
                node_count = int(counts.get("graph_nodes", 0) or 0)
                if node_count == 0 and fallback_store is not None and b_name:
                    try:
                        stats = fallback_store.get_stats(pid, b_name)
                        if isinstance(stats, dict):
                            node_count = int(
                                stats.get("effective_nodes")
                                or stats.get("node_count")
                                or stats.get("total_nodes")
                                or 0
                            )
                    except Exception:
                        pass

                formatted_branches.append(
                    {
                        "branch_name": b_name,
                        "schema_name": str(
                            b.get("schema_name")
                            or sanitize_branch_schema_for_project(b_name, p_root)
                        ),
                        "base_branch": str(b.get("base_branch") or p_root),
                        "is_root": bool(b.get("is_root", False)),
                        "has_graph_schema": bool(b.get("has_graph_schema", False)),
                        "has_doc_schema": bool(b.get("has_doc_schema", False)),
                        "author": str(b.get("author") or "system"),
                        "node_count": node_count,
                        "doc_count": int(counts.get("documents", 0) or 0),
                        "memory_count": int(counts.get("memories", 0) or 0),
                        "updated_at": b.get("updated_at"),
                    }
                )

            formatted_projects.append(
                {
                    "project_id": pid,
                    "name": p.get("name") or pid,
                    "description": p.get("description"),
                    "db_name": str(p.get("db_name") or ""),
                    "backend_type": p_backend,
                    "db_descriptor": str(p.get("db_descriptor") or ""),
                    "root_branch": p_root,
                    "root_schema": p_root_schema,
                    "status": str(p.get("status") or "active"),
                    "created_at": p.get("created_at"),
                    "updated_at": p.get("updated_at"),
                    "branches": formatted_branches,
                    "sources": list(p.get("sources") or []),
                    "shared_doc_count": int(p.get("shared_doc_count", 0) or 0),
                    "relation_count": int(p.get("relation_count", 0) or 0),
                }
            )
    finally:
        if fallback_store is not None:
            try:
                fallback_store.close()
            except Exception:
                pass

    total_branches = sum(len(p["branches"]) for p in formatted_projects)
    return {
        "status": "ok",
        "multi_db_enabled": multi_db_enabled,
        "backend_type": master_backend,
        "master_database": {
            "descriptor": str(master_info.get("url") or ""),
            "project_count": (
                len(formatted_projects)
                if filter_proj is not None
                else int(master_info.get("projects_count", len(formatted_projects)))
            ),
            "branch_schema_count": (
                total_branches
                if filter_proj is not None
                else int(master_info.get("branches_count", total_branches))
            ),
        },
        "projects": formatted_projects,
    }


@router.get("/topology")
async def get_master_topology(project_id: Optional[str] = Query(default=None)):
    try:
        return collect_master_topology(project_id=project_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/projects")
async def create_master_project(req: CreateMasterProjectRequest):
    _check_read_only()
    clean_proj = _normalize_project_id(req.project_id)
    root_br = (req.root_branch or "").strip() or _effective_root_branch()
    try:
        proj_meta = tenant_manager.ensure_project_database(
            clean_proj,
            name=req.name,
            description=req.description,
            root_branch=root_br,
        )
        effective_root = str(proj_meta.get("root_branch") or root_br)
        root_branch_meta = tenant_manager.ensure_branch_schema(
            clean_proj,
            effective_root,
            base_branch=effective_root,
            author="system",
            has_graph=True,
            has_doc=True,
            record_lineage=False,
        )

        store = get_graph_store(project_id=clean_proj)
        try:
            if hasattr(store, "_get_project_store"):
                delegate, _ = store._get_project_store(clean_proj)
                delegate.ensure_root_schema()
            elif hasattr(store, "ensure_root_schema"):
                store.ensure_root_schema()
        finally:
            store.close()

        return {
            "status": "ok",
            "project": {
                "project_id": clean_proj,
                "name": proj_meta.get("name") or req.name or clean_proj,
                "description": proj_meta.get("description", req.description),
                "db_name": proj_meta["db_name"],
                "backend_type": _normalize_backend_type(proj_meta.get("backend_type")),
                "db_descriptor": proj_meta.get("db_descriptor", ""),
                "root_branch": effective_root,
                "root_schema": str(root_branch_meta.get("schema_name") or "pb_root"),
            },
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/projects/{project_id:path}/branches")
async def create_master_branch(project_id: str, req: CreateMasterBranchRequest):
    _check_read_only()
    clean_proj = _normalize_project_id(project_id)
    br_name = (req.branch_name or "").strip()
    if not br_name:
        raise HTTPException(status_code=400, detail="branch_name is required")
    author = (req.author or "").strip() or "system"

    try:
        proj_meta = tenant_manager.ensure_project_database(clean_proj)
        proj_root = str(proj_meta.get("root_branch") or _effective_root_branch())
        base_br = (req.base_branch or "").strip() or proj_root
        is_root = br_name == proj_root or sanitize_branch_schema_for_project(br_name, proj_root) == "pb_root"

        store = get_graph_store(project_id=clean_proj)
        try:
            if is_root:
                if hasattr(store, "_get_project_store"):
                    delegate, _ = store._get_project_store(clean_proj)
                    delegate.ensure_root_schema()
                elif hasattr(store, "ensure_root_schema"):
                    store.ensure_root_schema()
            elif hasattr(store, "ensure_branch_schema"):
                store.ensure_branch_schema(
                    clean_proj,
                    br_name,
                    author=author,
                    base_branch=base_br,
                )
        finally:
            store.close()

        branch_meta = tenant_manager.ensure_branch_schema(
            clean_proj,
            br_name,
            base_branch=br_name if is_root else base_br,
            author=author,
            has_graph=True,
            has_doc=True,
            record_lineage=not is_root,
        )

        return {
            "status": "ok",
            "project_id": clean_proj,
            "db_name": branch_meta["db_name"],
            "branch": {
                "branch_name": branch_meta["branch_name"],
                "schema_name": branch_meta["schema_name"],
                "base_branch": branch_meta["base_branch"],
                "is_root": bool(branch_meta.get("is_root", is_root)),
                "has_graph_schema": True,
                "has_doc_schema": True,
                "author": author,
            },
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/projects/{project_id:path}/branches/{branch_name:path}")
async def delete_master_branch(project_id: str, branch_name: str):
    _check_read_only()
    clean_proj = _normalize_project_id(project_id)
    br_name = (branch_name or "").strip()
    if not br_name:
        raise HTTPException(status_code=400, detail="branch_name is required")

    try:
        proj_row = tenant_manager._master_fetchone(
            "SELECT root_branch FROM master_projects WHERE project_id = ?",
            (clean_proj,),
        )
        proj_root = (
            str(proj_row["root_branch"])
            if proj_row and proj_row.get("root_branch")
            else _effective_root_branch()
        )
        if br_name == proj_root or sanitize_branch_schema_for_project(br_name, proj_root) == "pb_root":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete root branch '{proj_root}'",
            )

        graph_deleted = False
        store = get_graph_store(project_id=clean_proj)
        try:
            if hasattr(store, "delete_branch"):
                graph_deleted = bool(store.delete_branch(clean_proj, br_name))
        finally:
            store.close()

        doc_drop_res = tenant_manager.drop_branch_schema(clean_proj, br_name)
        return {
            "status": "ok",
            "deleted": True,
            "project_id": clean_proj,
            "branch_name": br_name,
            "graph_deleted": graph_deleted,
            "schema_dropped": doc_drop_res.get("schema_name"),
            "db_name": doc_drop_res.get("db_name"),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
