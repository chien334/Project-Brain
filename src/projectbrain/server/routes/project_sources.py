"""REST routes for multi-source project registry, source upgrades, cross-source relations, and shared doc bindings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.config import env
from ...core.tenant_manager import (
    _effective_root_branch,
    parse_tenant_from_user_id,
    tenant_manager,
)

router = APIRouter(tags=["project-sources"])


def _check_read_only() -> None:
    if (
        getattr(env, "read_only", False)
        or os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes")
        or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes")
    ):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted: Server is running in Read-Only mode.",
        )


def _normalize_project_id(raw_project_id: Optional[str]) -> str:
    cleaned = (raw_project_id or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="project_id is required")
    proj, _ = parse_tenant_from_user_id(cleaned, default_branch=_effective_root_branch())
    return proj


class RegisterSourceRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    display_name: Optional[str] = None
    source_type: str = "service"
    tech_stack: Optional[Any] = None
    repo_url: Optional[str] = None
    workspace_subpath: Optional[str] = None
    root_branch: str = "main"
    current_version: str = "1.0.0"
    inherit_shared_docs: bool = True
    metadata: Optional[Dict[str, Any]] = None


class UpdateSourceRequest(BaseModel):
    project_id: Optional[str] = None
    display_name: Optional[str] = None
    source_type: Optional[str] = None
    tech_stack: Optional[Any] = None
    repo_url: Optional[str] = None
    workspace_subpath: Optional[str] = None
    root_branch: Optional[str] = None
    current_version: Optional[str] = None
    inherit_shared_docs: Optional[bool] = None
    status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class UpgradeSourceRequest(BaseModel):
    project_id: Optional[str] = None
    branch_name: str = "main"
    to_version: str = Field(..., min_length=1)
    upgrade_mode: str = "delta"
    commit_sha: Optional[str] = None
    actor: str = "system"
    from_version: Optional[str] = None
    nodes_delta: int = 0
    edges_delta: int = 0
    docs_updated: int = 0
    affected_relations: int = 0
    drifted_relations: int = 0


class CreateRelationRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    src_source_id: str = Field(..., min_length=1)
    dst_source_id: str = Field(..., min_length=1)
    relation_type: str = Field(..., min_length=1)
    src_branch: str = "main"
    dst_branch: str = "main"
    src_node_id: str = ""
    dst_node_id: str = ""
    src_file_path: Optional[str] = None
    dst_doc_key: Optional[str] = None
    contract_key: str = ""
    contract_metadata: Optional[Dict[str, Any]] = None
    is_bidirectional: bool = False
    pin: bool = True
    created_by: str = "user"


class UpdateRelationRequest(BaseModel):
    project_id: Optional[str] = None
    status: Optional[str] = None
    contract_metadata: Optional[Dict[str, Any]] = None
    pin: Optional[bool] = None


class BindSharedDocRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    doc_key: str = Field(..., min_length=1)
    target_source_id: str = "*"
    pinned_version: Optional[str] = None
    binding_role: str = "reference"
    priority: float = 1.0


class AutoLinkRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    source_id: Optional[str] = None
    branch: str = "main"
    workspace_map: Optional[Dict[str, str]] = None


class HubDiscoverRequest(BaseModel):
    workspace_root: str = "."


class HubBootstrapRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    workspace_root: str = "."
    sources: Optional[List[Dict[str, Any]]] = None
    shared_docs: Optional[List[Dict[str, Any]]] = None
    relations: Optional[List[Dict[str, Any]]] = None
    auto_discover: bool = True
    sync_graphs: bool = True
    auto_link: bool = True
    upgrade_mode: str = "delta"
    root_branch: str = "main"
    author: str = "agent"
    description: Optional[str] = None


def _validate_hub_workspace_root(workspace_root: str) -> str:
    """Validate hub workspace_root against path traversal and sensitive host paths."""
    from ...graphify_engine.hub_bootstrap import _validate_safe_workspace_dir

    p_str = str(workspace_root or "").strip()
    if not p_str:
        raise HTTPException(status_code=400, detail="workspace_root is required")
    try:
        cwd = Path.cwd().resolve()
        raw_p = Path(p_str)
        if not raw_p.is_absolute():
            # Ensure relative path stays inside cwd before resolving
            if ".." in raw_p.parts or ".." in p_str.replace("\\", "/").split("/"):
                raise ValueError(f"Path traversal ('..') is not allowed in workspace_root: {workspace_root}")
            cand = (cwd / raw_p).resolve()
            if not cand.is_relative_to(cwd):
                raise ValueError(f"workspace_root '{workspace_root}' escapes cwd")
            resolved = _validate_safe_workspace_dir(str(cand))
        else:
            resolved = _validate_safe_workspace_dir(p_str)
        return str(resolved)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _validate_workspace_map(
    project_id: str,
    workspace_map: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """Validate that every path in workspace_map resolves inside cwd or a registered source workspace."""
    if workspace_map is None:
        return None

    cwd = Path.cwd().resolve()
    allowed_roots: List[Path] = [cwd]

    try:
        for src in tenant_manager.list_project_sources(project_id):
            for raw_sub in (
                src.get("workspace_subpath"),
                (src.get("metadata") or {}).get("workspace_path"),
            ):
                if not raw_sub:
                    continue
                sub_p = Path(str(raw_sub).strip())
                resolved_sub = sub_p.resolve() if sub_p.is_absolute() else (cwd / sub_p).resolve()
                allowed_roots.append(resolved_sub)
    except Exception:
        pass

    validated: Dict[str, str] = {}
    for raw_sid, raw_path in workspace_map.items():
        sid = str(raw_sid or "").strip()
        if not sid:
            raise HTTPException(status_code=400, detail="workspace_map keys must be non-empty source_ids")
        p_str = str(raw_path or "").strip()
        if not p_str or "\x00" in p_str:
            raise HTTPException(status_code=400, detail=f"Invalid path for source '{sid}' in workspace_map")
        try:
            p_obj = Path(p_str)
            resolved = p_obj.resolve() if p_obj.is_absolute() else (cwd / p_obj).resolve()
            if not any(resolved.is_relative_to(root) for root in allowed_roots):
                raise ValueError("outside allowed workspace roots")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid or out-of-workspace workspace_map path for '{sid}': path traversal detected",
            )
        validated[sid] = str(resolved)
    return validated


@router.get("/master/sources")
async def list_project_sources(project_id: str = Query(..., min_length=1)):
    base_proj = _normalize_project_id(project_id)
    try:
        sources = tenant_manager.list_project_sources(base_proj)
        return {
            "status": "ok",
            "project_id": base_proj,
            "sources": sources,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/master/sources")
async def register_project_source(req: RegisterSourceRequest):
    _check_read_only()
    base_proj = _normalize_project_id(req.project_id)
    try:
        source = tenant_manager.register_project_source(
            project_id=base_proj,
            source_id=req.source_id,
            display_name=req.display_name,
            source_type=req.source_type,
            tech_stack=req.tech_stack,
            repo_url=req.repo_url,
            workspace_subpath=req.workspace_subpath,
            root_branch=req.root_branch,
            current_version=req.current_version,
            inherit_shared_docs=req.inherit_shared_docs,
            metadata=req.metadata,
        )
        return {
            "status": "ok",
            "source": source,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.patch("/master/sources/{source_id}")
async def update_project_source(
    source_id: str,
    req: UpdateSourceRequest,
    project_id: Optional[str] = Query(default=None),
):
    _check_read_only()
    base_proj = _normalize_project_id(project_id or req.project_id)
    updates = req.model_dump(exclude_unset=True, exclude={"project_id"})
    try:
        updated = tenant_manager.update_project_source(base_proj, source_id, **updates)
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=f"Source '{source_id}' not found in project '{base_proj}'",
            )
        return {
            "status": "ok",
            "source": updated,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.delete("/master/sources/{source_id}")
async def delete_project_source(
    source_id: str,
    project_id: str = Query(..., min_length=1),
):
    _check_read_only()
    base_proj = _normalize_project_id(project_id)
    try:
        res = tenant_manager.drop_project_source(base_proj, source_id)
        return {
            **res,
            "status": "ok",
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.post("/master/sources/{source_id}/upgrade")
async def upgrade_project_source(
    source_id: str,
    req: UpgradeSourceRequest,
    project_id: Optional[str] = Query(default=None),
):
    _check_read_only()
    base_proj = _normalize_project_id(project_id or req.project_id)
    try:
        upgrade = tenant_manager.record_source_upgrade(
            project_id=base_proj,
            source_id=source_id,
            branch_name=req.branch_name,
            to_version=req.to_version,
            from_version=req.from_version,
            upgrade_mode=req.upgrade_mode,
            nodes_delta=req.nodes_delta,
            edges_delta=req.edges_delta,
            docs_updated=req.docs_updated,
            affected_relations=req.affected_relations,
            drifted_relations=req.drifted_relations,
            commit_sha=req.commit_sha,
            actor=req.actor,
        )
        source = tenant_manager.get_project_source(base_proj, source_id)
        return {
            "status": "ok",
            "project_id": base_proj,
            "source_id": source_id,
            "upgrade": upgrade,
            "source": source,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.get("/master/sources/{source_id}/upgrades")
async def list_project_source_upgrades(
    source_id: str,
    project_id: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=1000),
):
    base_proj = _normalize_project_id(project_id)
    try:
        upgrades = tenant_manager.list_source_upgrades(
            base_proj,
            source_id=source_id,
            limit=limit,
        )
        return {
            "status": "ok",
            "project_id": base_proj,
            "source_id": source_id,
            "upgrades": upgrades,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/master/relations")
async def list_relations(
    project_id: str = Query(..., min_length=1),
    source_id: Optional[str] = Query(default=None),
    branch: Optional[str] = Query(default=None),
    relation_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    base_proj = _normalize_project_id(project_id)
    try:
        relations = tenant_manager.list_cross_source_relations(
            base_proj,
            source_id=source_id,
            branch=branch,
            relation_type=relation_type,
            status=status,
        )
        return {
            "status": "ok",
            "project_id": base_proj,
            "relations": relations,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/master/relations")
async def create_relation(req: CreateRelationRequest):
    _check_read_only()
    base_proj = _normalize_project_id(req.project_id)
    try:
        relation = tenant_manager.upsert_cross_source_relation(
            project_id=base_proj,
            src_source_id=req.src_source_id,
            dst_source_id=req.dst_source_id,
            relation_type=req.relation_type,
            src_branch=req.src_branch,
            dst_branch=req.dst_branch,
            src_node_id=req.src_node_id,
            dst_node_id=req.dst_node_id,
            src_file_path=req.src_file_path,
            dst_doc_key=req.dst_doc_key,
            origin="manual",
            confidence="MANUAL",
            contract_key=req.contract_key,
            contract_metadata=req.contract_metadata,
            status="pinned" if req.pin else "active",
            is_bidirectional=req.is_bidirectional,
            created_by=req.created_by,
        )
        return {
            "status": "ok",
            "relation": relation,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.post("/master/relations/auto-link")
async def auto_link_relations(req: AutoLinkRequest):
    _check_read_only()
    base_proj = _normalize_project_id(req.project_id)
    validated_map = _validate_workspace_map(base_proj, req.workspace_map)
    try:
        from ...graphify_engine.cross_source_linker import CrossSourceLinker

        linker = CrossSourceLinker()
        result = linker.auto_link_project(
            project_id=base_proj,
            source_id=req.source_id,
            branch=req.branch,
            workspace_map=validated_map,
        )
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.patch("/master/relations/{relation_id}")
async def update_relation(
    relation_id: str,
    req: UpdateRelationRequest,
    project_id: Optional[str] = Query(default=None),
):
    _check_read_only()
    raw_proj = project_id or req.project_id
    eff_proj = _normalize_project_id(raw_proj) if raw_proj and raw_proj.strip() else None
    updates: Dict[str, Any] = {}
    if req.status is not None:
        updates["status"] = req.status
    elif req.pin is not None:
        updates["status"] = "pinned" if req.pin else "active"
    if req.contract_metadata is not None:
        updates["contract_metadata"] = req.contract_metadata
    try:
        updated = tenant_manager.update_cross_source_relation(
            relation_id,
            project_id=eff_proj,
            **updates,
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=f"Relation '{relation_id}' not found",
            )
        return {
            "status": "ok",
            "relation": updated,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.delete("/master/relations/{relation_id}")
async def delete_relation(
    relation_id: str,
    project_id: Optional[str] = Query(default=None),
):
    _check_read_only()
    eff_proj = (
        _normalize_project_id(project_id)
        if project_id is not None and project_id.strip()
        else None
    )
    try:
        deleted = tenant_manager.delete_cross_source_relation(
            relation_id,
            project_id=eff_proj,
        )
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Relation '{relation_id}' not found",
            )
        return {
            "status": "ok",
            "deleted": True,
            "relation_id": relation_id,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@router.get("/sources/shared-bindings")
async def list_shared_bindings(
    project_id: str = Query(..., min_length=1),
    target_source_id: Optional[str] = Query(default=None),
    include_wildcard: bool = Query(default=False),
):
    base_proj = _normalize_project_id(project_id)
    try:
        bindings = tenant_manager.list_shared_doc_bindings(
            base_proj,
            target_source_id=target_source_id,
            include_wildcard=include_wildcard,
        )
        return {
            "status": "ok",
            "project_id": base_proj,
            "bindings": bindings,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sources/shared-bindings")
async def bind_shared_document(req: BindSharedDocRequest):
    _check_read_only()
    base_proj = _normalize_project_id(req.project_id)
    try:
        binding = tenant_manager.bind_shared_doc(
            project_id=base_proj,
            doc_key=req.doc_key,
            target_source_id=req.target_source_id,
            pinned_version=req.pinned_version,
            binding_role=req.binding_role,
            priority=req.priority,
        )
        return {
            "status": "ok",
            "binding": binding,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/master/hub/discover")
async def discover_hub_sources(req: HubDiscoverRequest):
    safe_root = _validate_hub_workspace_root(req.workspace_root)
    try:
        from ...graphify_engine.hub_bootstrap import discover_workspace_sources

        return discover_workspace_sources(safe_root)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/master/hub/bootstrap")
async def bootstrap_hub(req: HubBootstrapRequest):
    _check_read_only()
    base_proj = _normalize_project_id(req.project_id)
    safe_root = _validate_hub_workspace_root(req.workspace_root)
    try:
        from ...graphify_engine.hub_bootstrap import bootstrap_project_hub

        return await bootstrap_project_hub(
            project_id=base_proj,
            workspace_root=safe_root,
            sources=req.sources,
            shared_docs=req.shared_docs,
            relations=req.relations,
            auto_discover=req.auto_discover,
            sync_graphs=req.sync_graphs,
            auto_link=req.auto_link,
            upgrade_mode=req.upgrade_mode,
            root_branch=req.root_branch,
            author=req.author,
            description=req.description,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

