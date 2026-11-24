import sys
import os
import asyncio
import inspect
import json
import sqlite3
import traceback
import datetime
import logging
from typing import Optional, List, Dict, Any, Union, Tuple
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logger = logging.getLogger("mcp")

from ..main import Memory
from ..core.config import env
from ..temporal_graph.store import insert_fact
from ..temporal_graph.query import query_facts_at_time
from ..ops.dynamics import applyRetrievalTraceReinforcementToMemory
from ..core.db import db

# Create FastMCP server instance
from mcp.server.transport_security import TransportSecuritySettings

mcp_server = FastMCP(
    "projectbrain-mcp",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)
mem = Memory()

import contextvars
from pathlib import Path

# ContextVar to store project/user ID and role for the current request context (e.g. from query params/auth middleware)
mcp_request_project_id = contextvars.ContextVar("mcp_request_project_id", default=None)
mcp_request_role = contextvars.ContextVar("mcp_request_role", default=None)

def resolve_mcp_user_id(user_id: str = None) -> str:
    # 1. Check if user_id is explicitly passed in the tool call arguments
    uid = user_id
    
    # 2. Check ContextVar (set by FastAPI middleware for the current HTTP/SSE request)
    if not uid:
        uid = mcp_request_project_id.get()
        
    # 3. Check environment variables
    if not uid:
        uid = os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        
    # 4. Check active project state (set by dashboard)
    if not uid:
        uid = env.active_project
        
    # 5. Detect project name dynamically from directory configuration or CWD
    if not uid:
        from ..core.config import detect_project_name
        uid = detect_project_name()

    # 6. If uid is not one of the generic default IDs, append active git branch if not present
    if uid and uid not in ["default", "anonymous", "all", "postgres", "admin", "collaborator"]:
        if ":" not in uid:
            try:
                import shutil
                import subprocess
                cwd = Path.cwd()
                has_git = False
                for parent in [cwd] + list(cwd.parents):
                    if (parent / ".git").exists():
                        has_git = True
                        break
                git_bin = shutil.which("git")
                if git_bin and has_git:
                    # Run git command from CWD to get active branch
                    res_git = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=5)
                    if res_git.returncode == 0:
                        branch = res_git.stdout.strip()
                        if branch and branch != "HEAD":
                            uid = f"{uid}:{branch}"
            except Exception:
                pass
                
    return uid or "default"
def check_read_only() -> bool:
    return os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes")


def _filter_supported_kwargs(fn: Any, candidate_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Return subset of `candidate_kwargs` accepted by `fn` (safe across gradual Wave 3A rollout)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(candidate_kwargs)
    return {k: v for k, v in candidate_kwargs.items() if k in params}


def _resolve_project_source_branch(
    project_id: Optional[str],
    source_id: Optional[str] = None,
    branch: str = "main",
    scope: Optional[str] = None,
) -> Tuple[str, Optional[str], str]:
    """Resolve `(base_project_id, effective_source_id, target_branch)` from MCP inputs."""
    from ..core.tenant_manager import parse_tenant_spec

    raw_src = str(source_id).strip() if isinstance(source_id, str) and source_id.strip() else None
    resolved = resolve_mcp_user_id(project_id)
    eff_default_branch = (branch or "main").strip() or "main"
    proj, parsed_src, parsed_br = parse_tenant_spec(
        resolved,
        default_branch=eff_default_branch,
        default_source=raw_src,
    )
    if raw_src is not None and "@" not in str(resolved or ""):
        eff_src: Optional[str] = raw_src
    else:
        eff_src = parsed_src

    if eff_src == "__default__":
        eff_src = None

    clean_scope = str(scope or "").strip()
    if clean_scope == "project_shared" and eff_src is None:
        eff_src = "__shared__"

    if ":" in str(resolved or "") and (branch is None or branch == "main"):
        eff_branch = parsed_br or "main"
    else:
        eff_branch = eff_default_branch
    return proj, eff_src, eff_branch


def _format_graph_target(
    project_id: str,
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> str:
    """Format `project_id` or `project_id@source_id` for GraphStore / KB routing."""
    clean_src = str(source_id).strip() if isinstance(source_id, str) and source_id.strip() else None
    if clean_src and clean_src != "__default__":
        return f"{project_id}@{clean_src}"
    if str(scope or "").strip() == "project_shared":
        return f"{project_id}@__shared__"
    return project_id


def _format_mem_user_id(
    project_id: str,
    branch: str = "main",
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> str:
    """Format `user_id` (`proj[@src][:branch]`) for HSG memory routing."""
    base = _format_graph_target(project_id, source_id=source_id, scope=scope)
    clean_br = (branch or "").strip()
    if clean_br and (clean_br != "main" or "@" in base):
        return f"{base}:{clean_br}"
    return base


def _extract_cross_source_provenance(item: Dict[str, Any]) -> Tuple[Optional[str], str, Optional[Any]]:
    """Extract `(source_id, scope, via_relation)` provenance from a search result item."""
    from ..core.tenant_manager import parse_tenant_spec

    meta = item.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}

    raw_src = item.get("source_id") or meta.get("source_id")
    if not raw_src and item.get("user_id"):
        _, parsed_src, _ = parse_tenant_spec(str(item["user_id"]))
        raw_src = parsed_src
    eff_src = str(raw_src).strip() if raw_src else None
    if eff_src == "__default__":
        eff_src = None

    raw_scope = item.get("scope") or meta.get("scope")
    if raw_scope:
        eff_scope = str(raw_scope).strip()
    elif eff_src == "__shared__":
        eff_scope = "project_shared"
    else:
        eff_scope = "source"

    via_rel = item.get("via_relation") if item.get("via_relation") is not None else meta.get("via_relation")
    return eff_src, eff_scope, via_rel


def _record_source_upgrade_and_auto_link(
    *,
    project_id: str,
    source_id: Optional[str],
    branch: str,
    source_version: Optional[str],
    upgrade_mode: str = "delta",
    auto_link_sources: bool = True,
    workspace_path: Optional[str] = None,
    nodes_delta: int = 0,
    edges_delta: int = 0,
    docs_updated: int = 0,
) -> Dict[str, Any]:
    """Record optional sub-source upgrade telemetry and run cross-source auto-linking."""
    from ..core.tenant_manager import parse_tenant_from_user_id, tenant_manager

    base_proj = parse_tenant_from_user_id(project_id)[0]
    eff_src = str(source_id).strip() if isinstance(source_id, str) and source_id.strip() else None
    out: Dict[str, Any] = {}

    if eff_src and eff_src not in ("__default__", "__shared__"):
        try:
            existing = tenant_manager.get_project_source(base_proj, eff_src)
            if existing is None:
                tenant_manager.register_project_source(
                    base_proj,
                    eff_src,
                    workspace_subpath=workspace_path,
                    current_version=source_version or "1.0.0",
                )
            if source_version or (upgrade_mode and upgrade_mode != "delta"):
                upg = tenant_manager.record_source_upgrade(
                    project_id=base_proj,
                    source_id=eff_src,
                    branch_name=branch or "main",
                    to_version=source_version or (existing.get("current_version") if existing else "1.0.0"),
                    upgrade_mode=upgrade_mode or "delta",
                    nodes_delta=nodes_delta,
                    edges_delta=edges_delta,
                    docs_updated=docs_updated,
                    actor=os.getenv("USER", "mcp"),
                )
                out["upgrade"] = upg
        except Exception as exc:
            logger.debug(f"Source upgrade tracking skipped for {base_proj}@{eff_src}: {exc}")

    if auto_link_sources:
        try:
            reg_sources = tenant_manager.list_project_sources(base_proj)
            if eff_src or reg_sources:
                from ..graphify_engine.cross_source_linker import CrossSourceLinker

                ws_map = None
                if eff_src and workspace_path and os.path.exists(workspace_path):
                    ws_map = {eff_src: workspace_path}
                linker = CrossSourceLinker()
                link_res = linker.auto_link_project(
                    project_id=base_proj,
                    source_id=eff_src if eff_src and eff_src != "__shared__" else None,
                    branch=branch or "main",
                    workspace_map=ws_map,
                )
                out["cross_source_linking"] = link_res
        except Exception as exc:
            logger.debug(f"Auto-link skipped for {base_proj}: {exc}")

    return out


@mcp_server.tool(name="projectbrain_query", description="Query ProjectBrain for contextual memories (HSG) and/or temporal facts")
async def projectbrain_query(
    query: str,
    type: str = "contextual",
    fact_pattern: dict = None,
    at: str = None,
    k: int = 10,
    user_id: str = None,
    sector: str = None,
    author: str = None,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
) -> str:
    """
    Query ProjectBrain for contextual memories (HSG) and/or temporal facts.
    
    Args:
        query: Free-form search text.
        type: Query type: 'contextual' for HSG semantic search (default), 'factual' for temporal fact queries, 'unified' for both.
        fact_pattern: Fact pattern for temporal queries. Used when type is 'factual' or 'unified'.
        at: ISO date string for point-in-time queries (default: now).
        k: Max results for HSG queries.
        user_id: Isolate results to specific user.
        sector: Restrict to sector (lexical, semantic, etc).
        author: Optional name of the user performing the search.
        source_id: Optional sub-source ID for scoped/cross-source retrieval.
        include_shared_docs: Include project-shared documents (default True).
        relation_depth: Cross-source relation expansion depth (0 disables expansion, default 1).
        relation_types: Optional list of cross-source relation types to traverse.
    """
    try:
        import getpass
        resolved_author = author or os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous-mcp"
        logger.info(f"MCP User '{resolved_author}' queried: '{query}' on project '{user_id or 'default'}'")

        # Normalize and validate qtype
        qtype = type
        if not qtype or not isinstance(qtype, str):
            qtype = "contextual"
        qtype = qtype.lower().strip()
        if qtype not in ["contextual", "factual", "unified"]:
            qtype = "contextual"

        limit = k
        uid = resolve_mcp_user_id(user_id)
        
        at_date = datetime.datetime.fromisoformat(at) if at else datetime.datetime.now()
        at_ts = int(at_date.timestamp() * 1000)
        
        results = {"type": qtype, "query": query}
        
        # contextual (hsg) query
        if qtype in ["contextual", "unified"]:
            filters: Dict[str, Any] = {}
            if sector:
                filters["sector"] = sector
            has_cross_source_args = (
                source_id is not None
                or "@" in str(uid or "")
                or include_shared_docs is not True
                or relation_depth != 1
                or relation_types is not None
            )
            if has_cross_source_args:
                if source_id is not None:
                    filters["source_id"] = source_id
                filters["include_shared_docs"] = include_shared_docs
                filters["relation_depth"] = relation_depth
                if relation_types is not None:
                    filters["relation_types"] = relation_types

            if is_pb_remote_mode():
                import httpx
                server_url = get_pb_server_url()
                api_key = get_pb_api_key()
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                payload: Dict[str, Any] = {
                    "query": query,
                    "user_id": uid,
                    "limit": limit,
                    "filters": filters,
                    "include_shared_docs": include_shared_docs,
                    "relation_depth": relation_depth,
                }
                if source_id is not None:
                    payload["source_id"] = source_id
                if relation_types is not None:
                    payload["relation_types"] = relation_types
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        f"{server_url}/memory/search",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    contextual = resp.json().get("results") or []
            else:
                contextual = await mem.search(query, user_id=uid, limit=limit, **filters)

            contextual_out = []
            for m in contextual:
                m_src, m_scope, m_via_rel = _extract_cross_source_provenance(m)
                entry: Dict[str, Any] = {
                    "source": "hsg",
                    "id": m.get("id"),
                    "score": round(m.get("score", 0), 4),
                    "primary_sector": m.get("primary_sector"),
                    "salience": round(m.get("salience", 0), 4),
                    "source_id": m_src,
                    "scope": m_scope,
                    "via_relation": m_via_rel,
                    "content": m.get("content"),
                }
                if m.get("via_contract") is not None:
                    entry["via_contract"] = m.get("via_contract")
                contextual_out.append(entry)
            results["contextual"] = contextual_out
        
        # temporal fact query
        if qtype in ["factual", "unified"]:
            pattern = fact_pattern or {}
            facts = await query_facts_at_time(
                subject=pattern.get("subject"),
                predicate=pattern.get("predicate"),
                subject_object=pattern.get("object"),
                at=at_ts,
                min_confidence=0.0,
                user_id=uid
            )
            results["factual"] = [{
                "source": "temporal",
                "id": f["id"],
                "subject": f["subject"],
                "predicate": f["predicate"],
                "object": f["object"],
                "valid_from": f["valid_from"],
                "valid_to": f.get("valid_to"),
                "confidence": round(f["confidence"], 4),
                "content": f"{f['subject']} {f['predicate']} {f['object']}"
            } for f in facts]
        
        # build summary based on what was successfully queried
        has_contextual = "contextual" in results
        has_factual = "factual" in results
        
        if has_contextual and has_factual:
            ctx_count = len(results.get("contextual", []))
            fact_count = len(results.get("factual", []))
            summary = f"Found {ctx_count} contextual memories and {fact_count} temporal facts"
        elif has_contextual:
            count = len(results.get("contextual", []))
            summary = f"Found {count} contextual memories for '{query}'" if count > 0 else "No contextual memories matched."
        elif has_factual:
            count = len(results.get("factual", []))
            summary = f"Found {count} temporal facts" if count > 0 else "No temporal facts matched."
        else:
            summary = "No query results."

        return f"{summary}\n\n{json.dumps(results, default=str, indent=2)}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_store", description="Persist new content into ProjectBrain (HSG contextual memory and/or temporal facts)")
async def projectbrain_store(
    content: str,
    type: str = "contextual",
    facts: list = None,
    user_id: str = None,
    tags: list = None,
    metadata: dict = None,
    author: str = None
) -> str:
    """
    Persist new content into ProjectBrain (HSG contextual memory and/or temporal facts).
    
    Args:
        content: Raw memory text to store.
        type: Storage type: 'contextual' for HSG only (default), 'factual' for temporal facts only, 'both' for both systems.
        facts: Array of facts to store. Required when type is 'factual' or 'both'.
        user_id: User ID associated with the changes.
        tags: Custom tags for organizing memories.
        metadata: Custom metadata dictionary.
        author: Optional name of the user storing the memory.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        import getpass
        # Normalize and validate stype
        stype = type
        if not stype or not isinstance(stype, str):
            stype = "contextual"
        stype = stype.lower().strip()
        if stype not in ["contextual", "factual", "both"]:
            stype = "contextual"

        uid = resolve_mcp_user_id(user_id)
        
        # Read tags from args, apply configured tags if present
        actual_tags = tags or []
        env_tags_str = os.getenv("PB_DEFAULT_TAGS") or os.getenv("OM_DEFAULT_TAGS")
        if env_tags_str:
            env_tags = [t.strip() for t in env_tags_str.split(",") if t.strip()]
            for et in env_tags:
                if et not in actual_tags:
                    actual_tags.append(et)
        
        # Track edit origin metadata
        meta = metadata or {}
        meta["editor"] = "mcp-client"
        meta["editor_user"] = uid
        resolved_author = author or os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous-mcp"
        meta["author"] = resolved_author
        if "tags" not in meta:
            meta["tags"] = actual_tags

        facts_data = facts or []
        
        # validate facts requirement
        if stype in ["factual", "both"] and not facts_data:
            raise ValueError(f"Facts array is required when type is '{stype}'. Please provide at least one fact.")
        
        results = {"type": stype}
        
        # store contextual memory
        if stype in ["contextual", "both"]:
            res = await mem.add(content, user_id=uid, meta=meta, tags=actual_tags)
            if res:
                results["hsg"] = {
                    "id": res.get('root_memory_id') or res.get('id'),
                    "primary_sector": res.get('primary_sector')
                }
        
        # store temporal facts
        if stype in ["factual", "both"] and facts_data:
            temporal_results = []
            for fact in facts_data:
                valid_from_str = fact.get("valid_from")
                valid_from_dt = datetime.datetime.fromisoformat(valid_from_str) if valid_from_str else datetime.datetime.now()
                valid_from_ts = int(valid_from_dt.timestamp() * 1000)
                confidence = fact.get("confidence", 1.0)
                
                fact_id = await insert_fact(
                    subject=fact["subject"],
                    predicate=fact["predicate"],
                    subject_object=fact["object"],
                    valid_from=valid_from_ts,
                    confidence=confidence,
                    metadata=meta,
                    user_id=uid
                )
                
                temporal_results.append({
                    "id": fact_id,
                    "subject": fact["subject"],
                    "predicate": fact["predicate"],
                    "object": fact["object"],
                    "valid_from": valid_from_dt.isoformat(),
                    "confidence": confidence
                })
            results["temporal"] = temporal_results
        
        # build response message based on what was successfully stored
        has_hsg = "hsg" in results
        has_temporal = "temporal" in results
        
        if has_hsg and has_temporal:
            txt = f"Stored in both systems: HSG memory {results['hsg']['id']} + {len(results['temporal'])} temporal fact(s)"
        elif has_hsg:
            txt = f"Stored memory {results['hsg']['id']}"
        elif has_temporal:
            txt = f"Stored {len(results['temporal'])} temporal fact(s)"
        else:
            txt = "No data was stored (invalid or empty inputs)."
            
        if uid and (has_hsg or has_temporal):
            txt += f" [user={uid}]"
        
        return f"{txt}\n\n{json.dumps(results, default=str, indent=2)}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_get", description="Fetch a single memory by ID")
async def projectbrain_get(id: str) -> str:
    """Fetch a single memory by ID."""
    try:
        m = await mem.get(id)
        if not m:
            return f"Memory {id} not found"
        return json.dumps(dict(m), default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_delete", description="Delete a memory by ID")
async def projectbrain_delete(id: str, user_id: str = None) -> str:
    """Fetch a single memory by ID."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        uid = resolve_mcp_user_id(user_id)
        m = await mem.get(id)
        if not m:
            return f"Memory {id} not found"
        if uid and m.get("user_id") != uid:
            return f"Memory {id} not found for user {uid}"
        await mem.delete(id)
        return f"Memory {id} deleted"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_list", description="List recent memories")
async def projectbrain_list(limit: int = 20, user_id: str = None) -> str:
    """List recent memories."""
    try:
        uid = resolve_mcp_user_id(user_id)
        res = mem.history(user_id=uid, limit=limit)
        return json.dumps([dict(r) for r in res], default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_reinforce", description="Reinforce a memory's salience/importance by its ID")
async def projectbrain_reinforce(id: str) -> str:
    """Reinforce a memory's salience/importance by its ID."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        m = await mem.get(id)
        if not m:
            return f"Memory {id} not found"
        new_sal = await applyRetrievalTraceReinforcementToMemory(id, m.get("salience", 0) or 0)
        db.execute("UPDATE memories SET salience=?, last_seen_at=? WHERE id=?", (new_sal, int(datetime.datetime.now().timestamp()*1000), id))
        db.commit()
        return f"Memory {id} reinforced. New salience: {new_sal:.4f}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_delete_all", description="Delete all memories for a user")
async def projectbrain_delete_all(user_id: str = None) -> str:
    """Delete all memories for a user."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        uid = resolve_mcp_user_id(user_id)
        await mem.delete_all(user_id=uid)
        return f"All memories deleted for user {uid or 'default'}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_stats", description="Retrieve cognitive memory engine statistics (total count, sectors, tags)")
async def projectbrain_stats(user_id: str = None) -> str:
    """Retrieve cognitive memory engine statistics (total count, sectors, tags)."""
    try:
        uid = resolve_mcp_user_id(user_id)
        if uid:
            total_res = db.fetchone("SELECT count(*) as c FROM memories WHERE user_id=?", (uid,))
            sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories WHERE user_id=? GROUP BY primary_sector", (uid,))
            facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts WHERE user_id=?", (uid,))
        else:
            total_res = db.fetchone("SELECT count(*) as c FROM memories")
            sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories GROUP BY primary_sector")
            facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts")
        
        total_mems = total_res["c"] if total_res else 0
        total_facts = facts_res["c"] if facts_res else 0
        sectors = {r["primary_sector"]: r["c"] for r in sector_res}
        
        stats = {
            "total_memories": total_mems,
            "total_temporal_facts": total_facts,
            "sectors": sectors,
            "user_id": uid or "all"
        }
        return json.dumps(stats, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_ingest", description="Ingest data from an external connector (GitHub, Notion, Web Crawler, OneDrive, Google Drive/Sheets/Slides)")
async def projectbrain_ingest(source: str, creds: dict = None, filters: dict = None, user_id: str = None) -> str:
    """Ingest data from an external connector."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        actual_creds = creds or {}
        actual_filters = filters or {}
        uid = resolve_mcp_user_id(user_id)
        
        src = mem.source(source)
        if uid:
            src.user_id = uid
        
        await src.connect(**actual_creds)
        ids = await src.ingest_all(**actual_filters)
        return json.dumps({"ok": True, "count": len(ids), "memory_ids": ids}, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

async def sync_codebase_memories(project_id: str, dir_path: str, base_branch: Optional[str] = None) -> dict:
    import os
    exclude_dirs = {
        ".git", "node_modules", "dist", "build", "__pycache__", 
        ".pytest_cache", ".codegraph", "cloned_repos", "bin", "obj", 
        ".vs", "uploads", "venv", ".venv", ".next", ".nuxt", ".out", 
        "target", "vendor", "staticwebassets"
    }
    exclude_exts = {
        ".db", ".sqlite", ".sqlite3",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
        ".exe", ".bin", ".dll", ".pdb", ".so", ".dylib",
        ".woff", ".woff2", ".ttf", ".eot",
        ".cache", ".up2date", ".log",
        ".suo", ".user", ".map", ".lock",
        ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flac", ".ogg",
        ".dll.config", ".exe.config"
    }
    
    count = 0
    errors = 0

    if base_branch and ":" in project_id:
        proj, br = project_id.split(":", 1)
        if br and br != base_branch:
            from ..core.db import register_memory_branch, record_memory_tombstone
            from ..memory.hsg import delete_branch_target_memories, invalidate_query_cache
            from ..graphify_engine.extractor import GraphifyExtractor

            register_memory_branch(proj, br, base_branch)
            invalidate_query_cache()

            resolved_dir = str(Path(dir_path).resolve())
            changed_files, deleted_rel_paths = GraphifyExtractor()._discover_git_diff(
                Path(resolved_dir), base_branch
            )
            for rel in deleted_rel_paths:
                await delete_branch_target_memories(proj, br, "file", rel)
                record_memory_tombstone(proj, br, "file", rel)

            for fpath in changed_files:
                fpath_str = str(fpath)
                rel_path = os.path.relpath(fpath_str, resolved_dir).replace("\\", "/")
                dir_parts = rel_path.split("/")[:-1]
                if any(p in exclude_dirs or (p.startswith(".") and p not in (".", "..")) for p in dir_parts):
                    continue
                file = os.path.basename(fpath_str)
                ext = os.path.splitext(file)[1].lower()
                if ext in exclude_exts:
                    continue

                await delete_branch_target_memories(proj, br, "file", rel_path)
                record_memory_tombstone(proj, br, "file", rel_path)
                try:
                    with open(fpath_str, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if not content.strip():
                        continue
                    meta = {
                        "source": "mcp_sync_all",
                        "filename": file,
                        "file_path": rel_path,
                        "project_id": project_id
                    }
                    await mem.add(
                        content=f"File: {rel_path}\n\n{content}",
                        user_id=project_id,
                        meta=meta,
                        tags=["codebase", "file"]
                    )
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to ingest {rel_path} in MCP delta sync: {e}")
                    errors += 1

            invalidate_query_cache()
            return {"total_ingested": count, "total_errors": errors}
    
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in exclude_exts:
                continue
            file_path = os.path.join(root, file)
            parts = file_path.split(os.sep)
            if any(p in exclude_dirs for p in parts):
                continue
            
            rel_path = os.path.relpath(file_path, dir_path)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if not content.strip():
                    continue
                meta = {
                    "source": "mcp_sync_all",
                    "filename": file,
                    "file_path": rel_path,
                    "project_id": project_id
                }
                await mem.add(
                    content=f"File: {rel_path}\n\n{content}",
                    user_id=project_id,
                    meta=meta,
                    tags=["codebase", "file"]
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to ingest {rel_path} in MCP sync: {e}")
                errors += 1
                
    return {"total_ingested": count, "total_errors": errors}

@mcp_server.tool(name="projectbrain_sync_codegraph", description="Synchronize local codegraph database structure (nodes/edges) and codebase files (as memories) to ProjectBrain.")
async def projectbrain_sync_codegraph(
    project_id: str, 
    project_path: str = None, 
    branch: str = None, 
    sync_memories: bool = True,
    author: str = None,
    base_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    source_version: Optional[str] = None,
    upgrade_mode: str = "delta",
    auto_link_sources: bool = True,
) -> str:
    """
    Synchronize the local codegraph database (.codegraph/codegraph.db in current working directory or specified project path)
    to the ProjectBrain server under the specified project ID and branch, and optionally ingest files into memories.
    
    Args:
        project_id: Unique identifier for the project (e.g. 'projectbrain-py').
        project_path: Optional local path to the project directory containing .codegraph/
        branch: Optional branch name to associate with the synchronization. If not specified, tries to auto-detect the active git branch.
        sync_memories: If True (default), scans and ingests all text source files as memories.
        author: Optional name of the user performing the sync.
        base_branch: Optional parent/base branch for delta-only memory sync and overlay inheritance.
        source_id: Optional sub-source ID within a multi-source project.
        source_version: Optional semantic version or build tag for source upgrade history.
        upgrade_mode: Upgrade mode ('delta' or 'full_replace').
        auto_link_sources: If True (default), automatically reconciles cross-source links after sync.
    """
    import shutil
    import subprocess
    
    if check_read_only():
        return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

    from ..core.tenant_manager import parse_tenant_spec
    parsed_proj, parsed_src, parsed_br = parse_tenant_spec(
        project_id, default_branch=branch or "main", default_source=source_id
    )
    eff_source = (
        str(source_id).strip()
        if isinstance(source_id, str) and source_id.strip()
        else (parsed_src if parsed_src != "__default__" else None)
    )
    if eff_source == "__default__":
        eff_source = None

    projects_root = os.getenv("PB_PROJECTS_ROOT_DIR") or os.getenv("OM_PROJECTS_ROOT_DIR")
    
    if projects_root:
        projects_root_resolved = Path(projects_root).resolve()
        projects_root = str(projects_root_resolved)
        project_dir_name = parsed_proj
        if project_path:
            if os.path.isabs(project_path):
                resolved_project_path = os.path.abspath(project_path)
            else:
                resolved_project_path = os.path.abspath(os.path.join(projects_root, project_path))
        else:
            resolved_project_path = os.path.abspath(os.path.join(projects_root, project_dir_name))

        if not Path(resolved_project_path).resolve().is_relative_to(Path(projects_root).resolve()):
            return f"Error: Security violation. Path '{resolved_project_path}' is outside of the configured root directory '{projects_root}'."

        resolved_project_path = str(Path(resolved_project_path).resolve())
            
        if not os.path.isdir(resolved_project_path):
            return f"Error: The resolved project directory '{resolved_project_path}' does not exist or is not a directory on the server."
            
        target_dir = resolved_project_path
    else:
        resolved_project_path = project_path
        path_source = "specified" if project_path else "database"
        
        if not resolved_project_path:
            try:
                from ..core.db import db
                db.connect()
                cursor = db.conn.cursor()
                try:
                    base_id = project_id.split(":", 1)[0] if ":" in project_id else project_id
                    cursor.execute(
                        db.translate_query("SELECT project_path FROM projects WHERE id = ? OR id LIKE ? ORDER BY updated_at DESC LIMIT 1;"),
                        (project_id, f"{base_id}%"),
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        resolved_project_path = row[0]
                finally:
                    cursor.close()
            except Exception:
                pass

        if resolved_project_path:
            if not os.path.isdir(resolved_project_path):
                return f"Error: The {path_source} project path '{resolved_project_path}' does not exist or is not a directory on this machine."
            target_dir = resolved_project_path
        else:
            target_dir = os.getcwd()
        
    resolved_project_path = os.path.abspath(target_dir)

    is_server_project = parsed_proj in ["openmemory", "projectbrain", "test-project"]
    is_server_dir = False
    if not is_server_project:
        is_server_dir = os.path.exists(os.path.join(resolved_project_path, "src", "projectbrain", "main.py"))
        
    if is_server_dir and not is_server_project:
        return (
            f"Error: The sync tool is running on the remote server and tried to scan the server's own directory ({resolved_project_path}) instead of your local project.\n"
            f"To synchronize your local project '{project_id}' to the remote server, please run the command locally on your machine:\n"
            f"  python -m projectbrain.main codegraph-sync {project_id} {mem.url} /path/to/your/local/project"
        )

    if resolved_project_path:
        if os.path.isdir(resolved_project_path):
            db_path = os.path.join(resolved_project_path, ".codegraph", "codegraph.db")
        else:
            db_path = resolved_project_path
    else:
        db_path = os.path.join(os.getcwd(), ".codegraph", "codegraph.db")
        
    # Check if we should force the pure-Python parser
    force_python = os.getenv("PB_USE_PURE_PYTHON_PARSER", "false").lower() in ("true", "1", "yes")
    
    if not os.path.exists(db_path) or force_python:
        python_parser_run = False
        codegraph_bin = None
        if force_python:
            try:
                from extensions_mcp.codebase_migration_helper.python_codegraph import main as run_python_parser
                await asyncio.to_thread(run_python_parser, resolved_project_path)
                python_parser_run = True
            except Exception as py_err:
                return f"Error: Forced to use pure-Python parser, but execution failed: {str(py_err)}"
        else:
            # Check if codegraph command is available
            codegraph_bin = shutil.which("codegraph")
            if not codegraph_bin:
                # Check if automatic installation is allowed
                allow_auto = os.getenv("PB_ALLOW_AUTO_INSTALL", "false").lower() in ("true", "1", "yes")
                if allow_auto:
                    # Try to install it via npm globally
                    try:
                        res = subprocess.run(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True, timeout=120)
                        if res.returncode != 0:
                            # Fallback to pure-Python parser if npm install fails
                            try:
                                from extensions_mcp.codebase_migration_helper.python_codegraph import main as run_python_parser
                                await asyncio.to_thread(run_python_parser, resolved_project_path)
                                python_parser_run = True
                            except Exception as py_err:
                                return (
                                    f"Error: Codegraph database not found at {db_path}.\n"
                                    f"Automatic installation of 'codegraph' CLI failed, and Python fallback failed: {str(py_err)}.\n"
                                    f"Please install manually: npm install -g @colbymchenry/codegraph"
                                )
                        else:
                            # Re-check PATH
                            codegraph_bin = shutil.which("codegraph")
                            if not codegraph_bin:
                                # Fallback
                                try:
                                    from extensions_mcp.codebase_migration_helper.python_codegraph import main as run_python_parser
                                    await asyncio.to_thread(run_python_parser, resolved_project_path)
                                    python_parser_run = True
                                except Exception as py_err:
                                    return (
                                        f"Error: 'codegraph' executable is still not found in PATH after installation.\n"
                                        f"Fallback to Python codebase parser failed: {str(py_err)}."
                                    )
                    except Exception as inst_err:
                        # Fallback
                        try:
                            from extensions_mcp.codebase_migration_helper.python_codegraph import main as run_python_parser
                            await asyncio.to_thread(run_python_parser, resolved_project_path)
                            python_parser_run = True
                        except Exception as py_err:
                            return (
                                f"Error: Codegraph database not found at {db_path}.\n"
                                f"Attempted to install 'codegraph' CLI but failed: {str(inst_err)}, and Python fallback failed: {str(py_err)}."
                            )
                else:
                    # Fallback: Run pure-Python codebase parser
                    try:
                        from extensions_mcp.codebase_migration_helper.python_codegraph import main as run_python_parser
                        await asyncio.to_thread(run_python_parser, resolved_project_path)
                        python_parser_run = True
                    except Exception as py_err:
                        return (
                            f"Error: Codegraph database not found at {db_path} and 'codegraph' CLI is not installed.\n"
                            f"Attempted to fallback to the pure-Python codebase parser but failed: {str(py_err)}.\n"
                            f"Please install the dependency manually using:\n"
                            f"  npm install -g @colbymchenry/codegraph"
                        )
            else:
                # Already installed, fall through to run init below
                pass
        
        # Now run 'codegraph init' in the project directory if we have codegraph_bin and did not run the python parser
        if codegraph_bin and not python_parser_run:
            try:
                res_init = subprocess.run([codegraph_bin, "init"], cwd=resolved_project_path, capture_output=True, text=True, timeout=120)
                if res_init.returncode != 0:
                    return (
                        f"Error: Failed to initialize codegraph database via '{codegraph_bin} init' (exit code {res_init.returncode}) in directory {resolved_project_path}.\n"
                        f"Stdout: {res_init.stdout}\n"
                        f"Stderr: {res_init.stderr}"
                    )
            except Exception as init_err:
                return f"Error executing 'codegraph init' in directory {resolved_project_path}: {str(init_err)}"
            
        # Re-check db path existence
        if not os.path.exists(db_path):
            return f"Error: Finished running 'codegraph init' but codegraph database file was not created at {db_path}."
        
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at FROM nodes;")
        nodes = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute("SELECT id, source, target, kind, metadata, line, col FROM edges;")
        edges = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        # Auto-detect git branch if branch is not explicitly provided
        resolved_branch = branch
        if not resolved_branch and ":" in str(project_id or ""):
            resolved_branch = parsed_br
        if not resolved_branch:
            try:
                git_bin = shutil.which("git")
                if git_bin and os.path.exists(os.path.join(resolved_project_path, ".git")):
                    res_git = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=resolved_project_path, capture_output=True, text=True, timeout=5)
                    if res_git.returncode == 0:
                        resolved_branch = res_git.stdout.strip()
            except Exception:
                pass

        graph_proj = _format_graph_target(parsed_proj, eff_source)
        if resolved_branch:
            sync_project_id = f"{graph_proj}:{resolved_branch}"
        else:
            sync_project_id = graph_proj

        from ..server.routes.codegraph import sync_codegraph_data, SyncRequest
        import getpass

        resolved_author = author or os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous-mcp"
        
        req = SyncRequest(
            project_id=sync_project_id,
            project_name=sync_project_id,
            nodes=nodes,
            edges=edges,
            author=resolved_author,
            project_path=resolved_project_path
        )
        
        if mem.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {mem.api_key}"} if mem.api_key else {}
            req_payload = req.dict()
            if eff_source:
                req_payload["source_id"] = eff_source
            if source_version is not None:
                req_payload["source_version"] = source_version
            req_payload["upgrade_mode"] = upgrade_mode
            req_payload["auto_link_sources"] = auto_link_sources
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{mem.url}/codegraph/sync",
                    json=req_payload,
                    headers=headers,
                    timeout=120.0
                )
                resp.raise_for_status()
                res = resp.json()
        else:
            res = await sync_codegraph_data(req)
        
        mem_res = None
        if sync_memories:
            mem_res = await sync_codebase_memories(sync_project_id, resolved_project_path, base_branch=base_branch)
            res["memories"] = mem_res
            res["message"] = (
                f"Successfully synchronized {len(nodes)} nodes, {len(edges)} edges, "
                f"and ingested {mem_res['total_ingested']} codebase files as memories "
                f"(with {mem_res['total_errors']} errors)!"
            )
        elif base_branch and ":" in sync_project_id:
            proj, br = sync_project_id.split(":", 1)
            if br and br != base_branch:
                from ..core.db import register_memory_branch
                from ..memory.hsg import invalidate_query_cache
                register_memory_branch(proj, br, base_branch)
                invalidate_query_cache()

        if eff_source:
            res["source_id"] = eff_source
        if mem.mode != "remote":
            extra = _record_source_upgrade_and_auto_link(
                project_id=parsed_proj,
                source_id=eff_source,
                branch=resolved_branch or "main",
                source_version=source_version,
                upgrade_mode=upgrade_mode,
                auto_link_sources=auto_link_sources,
                workspace_path=resolved_project_path,
                nodes_delta=len(nodes),
                edges_delta=len(edges),
                docs_updated=(mem_res or {}).get("total_ingested", 0) if isinstance(mem_res, dict) else 0,
            )
            res.update(extra)

        return json.dumps(res, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error synchronizing codegraph: {str(e)}"

@mcp_server.tool(name="projectbrain_diff_project_versions", description="Compare codebase structural symbols (codegraph) and context memories between two versions/branches of a project.")
async def projectbrain_diff_project_versions(base_project_id: str, target_project_id: str) -> str:
    """
    Compare codebase structural symbols (codegraph) and context memories between two versions/branches of a project.
    
    Args:
        base_project_id: The base project ID (e.g. 'E-commerce-jp-vn:main')
        target_project_id: The target project ID to compare against (e.g. 'E-commerce-jp-vn:feature-x')
    """
    try:
        from ..server.routes.codegraph import diff_projects
        
        # 1. Diff codegraph
        cg_diff = await diff_projects(base_project_id, target_project_id)
        added_nodes = cg_diff.get("added", [])
        deleted_nodes = cg_diff.get("deleted", [])
        modified_nodes = cg_diff.get("modified", [])
        
        # 2. Diff memories
        base_mems = db.fetchall("SELECT id, content, tags, primary_sector FROM memories WHERE user_id = ?", (base_project_id,))
        target_mems = db.fetchall("SELECT id, content, tags, primary_sector FROM memories WHERE user_id = ?", (target_project_id,))
        
        base_mem_map = {m["content"].strip(): m for m in base_mems}
        target_mem_map = {m["content"].strip(): m for m in target_mems}
        
        mems_added = [m for content, m in target_mem_map.items() if content not in base_mem_map]
        mems_deleted = [m for content, m in base_mem_map.items() if content not in target_mem_map]
        
        # 3. Format markdown report
        lines = []
        lines.append(f"# Project Version Comparison: `{base_project_id}` vs `{target_project_id}`")
        lines.append(f"Comparison generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # 3.1 Code Graph Section
        lines.append("## 📊 Codebase Structure Changes (Codegraph)")
        
        # Added Nodes
        if added_nodes:
            lines.append(f"### 🟢 Added Symbols ({len(added_nodes)})")
            # Group by kind
            by_kind = {}
            for n in added_nodes:
                by_kind.setdefault(n["kind"], []).append(n)
            for kind, items in by_kind.items():
                lines.append(f"#### {kind.capitalize()}")
                for item in items:
                    sig = f" `{item['signature']}`" if item.get('signature') else ""
                    lines.append(f"- **{item['name']}** in `{item['file_path']}`{sig}")
        else:
            lines.append("### 🟢 Added Symbols: None")
            
        # Deleted Nodes
        if deleted_nodes:
            lines.append(f"\n### 🔴 Deleted Symbols ({len(deleted_nodes)})")
            by_kind = {}
            for n in deleted_nodes:
                by_kind.setdefault(n["kind"], []).append(n)
            for kind, items in by_kind.items():
                lines.append(f"#### {kind.capitalize()}")
                for item in items:
                    lines.append(f"- **{item['name']}** (previously in `{item['file_path']}`)")
        else:
            lines.append("\n### 🔴 Deleted Symbols: None")
            
        # Modified Nodes
        if modified_nodes:
            lines.append(f"\n### 🟡 Modified Symbols ({len(modified_nodes)})")
            for m in modified_nodes:
                node = m["node"]
                changes = ", ".join(m["changes"])
                lines.append(f"- **{node['name']}** (`{node['kind']}`) in `{node['file_path']}`")
                lines.append(f"  - *Changes*: {changes}")
                if "signature" in m["changes"] and node.get("signature"):
                    lines.append(f"  - *New signature*: `{node['signature']}`")
        else:
            lines.append("\n### 🟡 Modified Symbols: None")
            
        # 3.2 Memories Section
        lines.append("\n## 🧠 Cognitive Memory Changes (ProjectBrain)")
        
        if mems_added:
            lines.append(f"### 🟢 Added Memories ({len(mems_added)})")
            for m in mems_added:
                tags_str = f" [Tags: {m['tags']}]" if m.get('tags') else ""
                lines.append(f"- [{m['primary_sector']}] \"{m['content']}\"{tags_str}")
        else:
            lines.append("### 🟢 Added Memories: None")
            
        if mems_deleted:
            lines.append(f"\n### 🔴 Deleted Memories ({len(mems_deleted)})")
            for m in mems_deleted:
                tags_str = f" [Tags: {m['tags']}]" if m.get('tags') else ""
                lines.append(f"- [{m['primary_sector']}] \"{m['content']}\"{tags_str}")
        else:
            lines.append("\n### 🔴 Deleted Memories: None")
            
        return "\n".join(lines)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error performing project comparison: {str(e)}"

@mcp_server.tool(name="projectbrain_register_mcp", description="Register or update the projectbrain MCP server in Claude Desktop config.")
async def projectbrain_register_mcp_tool(
    transport_type: str,
    user_id: str = "default",
    tags: str = "source:mcp",
    sse_url: str = "http://localhost:8080/mcp/sse"
) -> str:
    """
    Register or update the projectbrain MCP server in Claude Desktop config.
    
    Args:
        transport_type: 'stdio' or 'sse'
        user_id: The default user ID/project ID.
        tags: Tags associated with the connection.
        sse_url: The SSE endpoint URL (if sse transport type is used).
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        from ..server.routes.mcp_registry import register_mcp, RegisterMCPRequest
        req = RegisterMCPRequest(
            transport_type=transport_type,
            user_id=user_id,
            tags=tags,
            sse_url=sse_url
        )
        res = register_mcp(req)
        return json.dumps(res, indent=2)
    except Exception as e:
        return f"Error registering MCP server: {str(e)}"

@mcp_server.tool(name="projectbrain_register_external_mcp", description="Register or update an external/backup MCP server in Claude Desktop config.")
async def projectbrain_register_external_mcp_tool(
    name: str,
    command: str,
    args: list = None,
    env: dict = None
) -> str:
    """
    Register or update an external/backup MCP server in Claude Desktop config.
    
    Args:
        name: Name of the external MCP server.
        command: Command to run (e.g. 'npx', 'python3').
        args: Command arguments list.
        env: Dictionary of environment variables.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."
        from ..server.routes.mcp_registry import save_external_mcp_server, ExternalMCPRequest
        req = ExternalMCPRequest(
            name=name,
            command=command,
            args=args or [],
            env=env or {}
        )
        res = save_external_mcp_server(req)
        return json.dumps(res, indent=2)
    except Exception as e:
        return f"Error registering external MCP server: {str(e)}"

@mcp_server.tool(name="projectbrain_query_codegraph", description="Query ProjectBrain's codebase structure graph (codegraph) to find classes, functions, files, variables, clients, servers, and trace their connections (edges) in the project.")
async def projectbrain_query_codegraph(
    project_id: str = None,
    query: str = None,
    kinds: str = None,
    node_id: str = None,
    follow_connections: bool = False,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
) -> str:
    """
    Query ProjectBrain's codebase structure graph (codegraph) to find classes, functions, files, variables, clients, servers, and trace their connections (edges) in the project.
    
    Args:
        project_id: Unique identifier for the project (e.g. 'lasal-test:master'). If omitted, automatically resolved from the workspace/environment.
        query: Search term to match against symbol names, qualified names, or file paths.
        kinds: Comma-separated list of node kinds to filter by (e.g., 'class,function,method,file,variable,server,client').
        node_id: Specific node ID to fetch details for.
        follow_connections: If True, returns incoming and outgoing edges and connected nodes for the matching nodes or the specified node_id.
        source_id: Optional sub-source ID; routes lookup to `project_id@source_id`.
        include_shared_docs: Include shared document bindings for this project/source (default True).
        relation_depth: Cross-source relation expansion depth (default 1).
    """
    try:
        from ..core.tenant_manager import parse_tenant_spec, tenant_manager

        raw_uid = resolve_mcp_user_id(project_id)
        base_proj, parsed_src, parsed_br = parse_tenant_spec(
            raw_uid, default_branch="main", default_source=source_id
        )
        eff_source = (
            str(source_id).strip()
            if isinstance(source_id, str) and source_id.strip()
            else (parsed_src if parsed_src != "__default__" else None)
        )
        if eff_source == "__default__":
            eff_source = None

        graph_target = _format_graph_target(base_proj, eff_source)
        uid = f"{graph_target}:{parsed_br}" if ":" in str(raw_uid or "") else graph_target

        db.connect()
        cursor = db.conn.cursor()
        
        nodes = []
        if node_id:
            # Query specific node
            if db.is_postgres:
                cursor.execute(
                    "SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature FROM project_nodes WHERE project_id = %s AND id = %s",
                    (uid, node_id)
                )
            else:
                cursor.execute(
                    "SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature FROM project_nodes WHERE project_id = ? AND id = ?",
                    (uid, node_id)
                )
            rows = cursor.fetchall()
            for r in rows:
                if db.is_postgres:
                    nodes.append({
                        "id": r[0], "kind": r[1], "name": r[2], "qualified_name": r[3],
                        "file_path": r[4], "language": r[5], "start_line": r[6], "end_line": r[7],
                        "docstring": r[8], "signature": r[9]
                    })
                else:
                    nodes.append(dict(r))
        else:
            # Query by text / kinds
            where_clauses = ["project_id = " + ("%s" if db.is_postgres else "?")]
            params = [uid]
            
            if query:
                where_clauses.append("(name LIKE " + ("%s" if db.is_postgres else "?") + 
                                     " OR qualified_name LIKE " + ("%s" if db.is_postgres else "?") + 
                                     " OR file_path LIKE " + ("%s" if db.is_postgres else "?") + ")")
                q_param = f"%{query}%"
                params.extend([q_param, q_param, q_param])
                
            if kinds:
                kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
                if kind_list:
                    placeholders = ",".join(["%s" if db.is_postgres else "?"] * len(kind_list))
                    where_clauses.append(f"kind IN ({placeholders})")
                    params.extend(kind_list)
                    
            sql = f"""
                SELECT id, kind, name, qualified_name, file_path, language, 
                       start_line, end_line, docstring, signature 
                FROM project_nodes 
                WHERE {' AND '.join(where_clauses)} 
                LIMIT 100
            """
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            for r in rows:
                if db.is_postgres:
                    nodes.append({
                        "id": r[0], "kind": r[1], "name": r[2], "qualified_name": r[3],
                        "file_path": r[4], "language": r[5], "start_line": r[6], "end_line": r[7],
                        "docstring": r[8], "signature": r[9]
                    })
                else:
                    nodes.append(dict(r))

        edges = []
        used_graphify_fallback = False
        if not nodes:
            try:
                store = _get_mcp_graph_store()
                try:
                    vis = store.to_legacy_visjs(project_id=graph_target, branch_name=parsed_br, query=query, kinds=kinds)
                finally:
                    store.close()
                if vis.get("nodes"):
                    nodes = vis["nodes"]
                    edges = vis.get("edges", [])
                    used_graphify_fallback = True
            except Exception:
                pass
                    
        if follow_connections and nodes and not used_graphify_fallback:
            node_ids = [n["id"] for n in nodes]
            if len(node_ids) > 0:
                # Query edges
                placeholders = ",".join(["%s" if db.is_postgres else "?"] * len(node_ids))
                edges_query = f"""
                    SELECT id, source, target, kind, metadata, line, col
                    FROM project_edges
                    WHERE project_id = {("%s" if db.is_postgres else "?")} 
                      AND (source IN ({placeholders}) OR target IN ({placeholders}))
                    LIMIT 150
                """
                edge_params = [uid] + node_ids + node_ids
                cursor.execute(edges_query, edge_params)
                rows_e = cursor.fetchall()
                for r in rows_e:
                    if db.is_postgres:
                        edges.append({
                            "id": r[0], "source": r[1], "target": r[2], "kind": r[3],
                            "metadata": json.loads(r[4]) if r[4] else None, "line": r[5], "col": r[6]
                        })
                    else:
                        d = dict(r)
                        if d.get("metadata"):
                            try:
                                d["metadata"] = json.loads(d["metadata"])
                            except:
                                pass
                        edges.append(d)
                        
        cursor.close()

        cross_source_relations: List[Dict[str, Any]] = []
        if follow_connections or (relation_depth and relation_depth > 0):
            try:
                rels = tenant_manager.list_cross_source_relations(
                    base_proj,
                    source_id=eff_source,
                    branch=parsed_br,
                )
                cross_source_relations = [r for r in rels if r.get("status") != "rejected"]
            except Exception:
                cross_source_relations = []

        shared_doc_bindings: List[Dict[str, Any]] = []
        if include_shared_docs:
            try:
                shared_doc_bindings = tenant_manager.list_shared_doc_bindings(
                    base_proj,
                    target_source_id=eff_source,
                    include_wildcard=True,
                )
            except Exception:
                shared_doc_bindings = []
        
        output: Dict[str, Any] = {
            "project_id": uid,
            "source_id": eff_source,
            "nodes_count": len(nodes),
            "edges_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }
        if cross_source_relations or follow_connections or (relation_depth and relation_depth > 0):
            output["cross_source_relations"] = cross_source_relations
        if include_shared_docs and shared_doc_bindings:
            output["shared_doc_bindings"] = shared_doc_bindings
        return json.dumps(output, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error querying codegraph: {str(e)}"

# ---------------------------------------------------------------------------
# Graphify Engine FastMCP Integration (Hybrid Local / Remote Tools)
# ---------------------------------------------------------------------------

def is_pb_remote_mode() -> bool:
    return (
        os.getenv("PB_MODE", "local").lower() == "remote"
        or getattr(mem, "mode", "local") == "remote"
    )

def get_pb_server_url() -> str:
    url = (
        os.getenv("PB_URL")
        or os.getenv("PB_API_URL")
        or os.getenv("OM_URL")
        or os.getenv("OM_API_URL")
        or getattr(mem, "url", "http://localhost:8080")
    )
    return str(url).rstrip("/")

def get_pb_api_key() -> Optional[str]:
    return (
        os.getenv("PB_API_KEY")
        or os.getenv("OM_API_KEY")
        or getattr(mem, "api_key", None)
    )

def _get_mcp_graph_store():
    from ..graphify_engine.store import get_graph_store
    db_path = (
        os.getenv("PB_GRAPH_DB_PATH")
        or os.getenv("OM_GRAPH_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or os.getenv("OM_DB_PATH")
    )
    if not db_path:
        try:
            from ..core.config import env
            db_url = getattr(env, "database_url", "")
            if db_url.startswith("sqlite:///"):
                db_path = db_url.replace("sqlite:///", "")
        except Exception:
            pass
    return get_graph_store(db_path=db_path)

def get_hybrid_resolver():
    from ..graphify_engine.hybrid_resolver import HybridResolver
    if is_pb_remote_mode():
        return HybridResolver(remote_url=get_pb_server_url(), api_token=get_pb_api_key())
    return HybridResolver(store=_get_mcp_graph_store())

@mcp_server.tool(
    name="projectbrain_hardware_status",
    description="Inspect read-only hardware acceleration (CUDA, Apple MPS, CPU, ONNX), OCR engines, Graphify compute backend, and local AI server readiness."
)
async def projectbrain_hardware_status(
    ocr_device: Optional[str] = None,
    graphify_device: Optional[str] = None,
    semantic_engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Return combined OCR, Graphify GPU/CPU, and Local AI hardware readiness status."""
    try:
        remote_url = os.getenv("PB_REMOTE_URL")
        if is_pb_remote_mode() or remote_url:
            import httpx

            server_url = (remote_url or get_pb_server_url()).rstrip("/")
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            params: Dict[str, str] = {}
            if ocr_device is not None:
                params["ocr_device"] = ocr_device
            if graphify_device is not None:
                params["graphify_device"] = graphify_device
            if semantic_engine is not None:
                params["semantic_engine"] = semantic_engine
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{server_url}/graphify/hardware",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                return resp.json()

        from ..server.routes.graphify import collect_hardware_status

        return await collect_hardware_status(
            ocr_device=ocr_device,
            graphify_device=graphify_device,
            semantic_engine=semantic_engine,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"status": "error", "error": str(e)}


@mcp_server.tool(
    name="projectbrain_sync_graphify",
    description="Extract AST code graph and document entities via Graphify and synchronize to ProjectBrain local or remote graph store."
)
async def projectbrain_sync_graphify(
    workspace_path: str = ".",
    project_id: Optional[str] = None,
    branch: str = "main",
    scoped_paths: Optional[List[str]] = None,
    git_dirty_only: bool = False,
    local_only: bool = False,
    cluster: bool = True,
    include_docs: bool = True,
    base_branch: Optional[str] = None,
    device: Optional[str] = None,
    workers: Optional[int] = None,
    semantic_engine: Optional[str] = None,
    source_id: Optional[str] = None,
    source_version: Optional[str] = None,
    upgrade_mode: str = "delta",
    auto_link_sources: bool = True,
) -> str:
    """
    Extract AST code graph and document entities via Graphify and synchronize to ProjectBrain.
    """
    try:
        if not local_only and check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..graphify_engine.extractor import GraphifyExtractor

        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch
        )
        graph_target = _format_graph_target(target_proj, target_source)

        eff_device = device or "auto"
        eff_semantic_engine = semantic_engine or "auto"
        extractor = GraphifyExtractor(
            device=eff_device,
            workers=workers,
            semantic_engine=eff_semantic_engine,
        )
        delta = extractor.extract_workspace(
            workspace_path=workspace_path,
            project_id=target_proj,
            branch=target_branch,
            scoped_paths=scoped_paths,
            git_dirty_only=git_dirty_only,
            include_docs=include_docs,
            base_branch=base_branch,
            device=eff_device,
            workers=workers,
            semantic_engine=eff_semantic_engine,
        )

        if cluster and delta.nodes:
            try:
                delta = extractor.cluster_graph(
                    delta,
                    device=eff_device,
                    workers=workers,
                    semantic_engine=eff_semantic_engine,
                )
            except Exception as ce:
                logger.debug(f"Clustering skipped: {ce}")

        # Always save or update local partial graph cache
        local_file = extractor.save_local_partial_graph(
            delta,
            output_dir=os.path.join(workspace_path, ".projectbrain", "local_graph")
        )

        is_partial = bool(scoped_paths or git_dirty_only or (base_branch and base_branch != target_branch))

        if local_only:
            local_out: Dict[str, Any] = {
                "status": "cached_local",
                "project_id": target_proj,
                "branch": target_branch,
                "base_branch": base_branch,
                "local_path": local_file,
                "nodes_count": len(delta.nodes),
                "edges_count": len(delta.edges),
                "hyperedges_count": len(delta.hyperedges),
                "partial": is_partial,
            }
            if target_source is not None:
                local_out["source_id"] = target_source
                local_out["source_version"] = source_version
                local_out["upgrade_mode"] = upgrade_mode
            return json.dumps(local_out, indent=2)

        synced_files_list = [sf["file_path"] if isinstance(sf, dict) else str(sf) for sf in delta.synced_files]

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload: Dict[str, Any] = {
                "project_id": graph_target,
                "branch": target_branch,
                "base_branch": base_branch,
                "nodes": [n.to_dict() if hasattr(n, "to_dict") else n.model_dump() for n in delta.nodes],
                "edges": [e.to_dict() if hasattr(e, "to_dict") else e.model_dump() for e in delta.edges],
                "hyperedges": [h.to_dict() if hasattr(h, "to_dict") else h.model_dump() for h in delta.hyperedges],
                "node_tombstones": delta.node_tombstones,
                "edge_tombstones": delta.edge_tombstones,
                "hyperedge_tombstones": delta.hyperedge_tombstones,
                "synced_files": delta.synced_files,
                "deleted_files": delta.deleted_files,
                "partial": is_partial,
                "author": os.getenv("USER", "system"),
                "cluster": False,
                "upgrade_mode": upgrade_mode,
                "auto_link_sources": auto_link_sources,
            }
            if target_source is not None:
                payload["source_id"] = target_source
            if source_version is not None:
                payload["source_version"] = source_version
            if device is not None:
                payload["device"] = device
            if workers is not None:
                payload["workers"] = workers
            if semantic_engine is not None:
                payload["semantic_engine"] = semantic_engine
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{server_url}/graphify/sync", json=payload, headers=headers)
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            store = _get_mcp_graph_store()
            try:
                extra_sync = _filter_supported_kwargs(
                    store.sync_branch_delta,
                    {"source_id": target_source, "upgrade_mode": upgrade_mode},
                )
                res = store.sync_branch_delta(
                    project_id=graph_target,
                    branch_name=target_branch,
                    delta=delta,
                    partial=is_partial,
                    synced_files=synced_files_list,
                    deleted_files=delta.deleted_files,
                    author=os.getenv("USER", "system"),
                    base_branch=base_branch,
                    **extra_sync,
                )
                if base_branch and base_branch != target_branch:
                    try:
                        from ..core.db import register_memory_branch
                        from ..memory.hsg import invalidate_query_cache
                        register_memory_branch(graph_target, target_branch, base_branch)
                        invalidate_query_cache()
                    except Exception as me:
                        logger.debug(f"Memory branch registration skipped for {graph_target}:{target_branch}: {me}")
            finally:
                store.close()
            if isinstance(res, dict):
                if target_source is not None:
                    res["source_id"] = target_source
                extra = _record_source_upgrade_and_auto_link(
                    project_id=target_proj,
                    source_id=target_source,
                    branch=target_branch,
                    source_version=source_version,
                    upgrade_mode=upgrade_mode,
                    auto_link_sources=auto_link_sources,
                    workspace_path=workspace_path,
                    nodes_delta=len(delta.nodes),
                    edges_delta=len(delta.edges),
                )
                res.update(extra)
            return json.dumps(res, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_sync_graphify: {str(e)}"

@mcp_server.tool(
    name="projectbrain_query_graphify",
    description="Query ProjectBrain Graphify hybrid graph with actions: query, trace, impact, callers, callees, node, god_nodes, community."
)
async def projectbrain_query_graphify(
    query: str,
    project_id: Optional[str] = None,
    branch: str = "main",
    action: str = "query",
    target_symbol: Optional[str] = None,
    mode: str = "bfs",
    max_hops: int = 3,
    budget: int = 2500,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
) -> str:
    """
    Query ProjectBrain Graphify hybrid graph with various graph analytics and traversal actions.
    """
    try:
        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch
        )
        graph_target = _format_graph_target(target_proj, target_source)

        resolver = get_hybrid_resolver()
        try:
            act = (action or "query").lower().strip()

            if act == "query":
                extra_q = _filter_supported_kwargs(
                    resolver.query,
                    {
                        "source_id": target_source,
                        "include_shared_docs": include_shared_docs,
                        "relation_depth": relation_depth,
                    },
                )
                q_proj = target_proj if "source_id" in extra_q else graph_target
                res = resolver.query(
                    project_id=q_proj,
                    query_text=query,
                    branch=target_branch,
                    mode=mode,
                    budget=budget,
                    **extra_q,
                )
            elif act == "trace":
                res = resolver.trace_path(
                    project_id=graph_target,
                    source_id=query,
                    target_id=target_symbol or "",
                    branch=target_branch,
                )
            elif act == "impact":
                res = resolver.get_impact_radius(
                    project_id=graph_target,
                    node_id=query,
                    max_hops=max_hops,
                    branch=target_branch,
                )
            elif act == "callers":
                res = resolver.get_callers(
                    project_id=graph_target,
                    node_id=query,
                    branch=target_branch,
                )
            elif act == "callees":
                res = resolver.get_callees(
                    project_id=graph_target,
                    node_id=query,
                    branch=target_branch,
                )
            elif act == "node":
                res = resolver.get_node_details(
                    project_id=graph_target,
                    node_id=query,
                    branch=target_branch,
                )
            elif act == "god_nodes":
                top_n = max_hops if max_hops > 0 else 10
                res = resolver.get_god_nodes(
                    project_id=graph_target,
                    branch=target_branch,
                    top_k=top_n,
                )
            elif act == "community":
                comm_id = int(query) if query.isdigit() else 0
                res = resolver.get_community(
                    project_id=graph_target,
                    community_id=comm_id,
                    branch=target_branch,
                )
            else:
                return f"Error: Unknown action '{action}'. Supported actions: query, trace, impact, callers, callees, node, god_nodes, community."

            if isinstance(res, dict) and relation_depth and relation_depth > 0 and "cross_source_relations" not in res:
                try:
                    from ..core.tenant_manager import tenant_manager
                    rels = tenant_manager.list_cross_source_relations(
                        target_proj, source_id=target_source, branch=target_branch
                    )
                    res["cross_source_relations"] = [r for r in rels if r.get("status") != "rejected"]
                except Exception:
                    pass

            return json.dumps(res, indent=2)
        finally:
            if getattr(resolver, "store", None) is not None:
                resolver.store.close()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_query_graphify: {str(e)}"

@mcp_server.tool(
    name="projectbrain_local_graphify_diff",
    description="Compute structural difference between local uncommitted graph and remote server branch/root overlay."
)
async def projectbrain_local_graphify_diff(
    workspace_path: str = ".",
    project_id: Optional[str] = None,
    branch: str = "main",
    scoped_paths: Optional[List[str]] = None,
    git_dirty_only: bool = False,
    refresh_local: bool = True
) -> str:
    """
    Compute structural difference between local uncommitted graph and remote server branch/root overlay.
    """
    try:
        from ..graphify_engine.extractor import GraphifyExtractor
        resolved_proj = resolve_mcp_user_id(project_id)
        if ":" in resolved_proj:
            p_parts = resolved_proj.split(":", 1)
            target_proj = p_parts[0]
            target_branch = branch if branch != "main" else (p_parts[1] or "main")
        else:
            target_proj = resolved_proj
            target_branch = branch or "main"

        extractor = GraphifyExtractor()
        if refresh_local:
            delta = extractor.extract_workspace(
                workspace_path=workspace_path,
                project_id=target_proj,
                branch=target_branch,
                scoped_paths=scoped_paths,
                git_dirty_only=git_dirty_only,
            )
            extractor.save_local_partial_graph(
                delta,
                output_dir=os.path.join(workspace_path, ".projectbrain", "local_graph")
            )

        local_path = os.path.join(workspace_path, ".projectbrain", "local_graph", "partial_graph.json")
        resolver = get_hybrid_resolver()
        try:
            res = resolver.diff_local_vs_remote(
                project_id=target_proj,
                branch=target_branch,
                local_path=local_path if os.path.exists(local_path) else None,
            )
            return json.dumps(res, indent=2)
        finally:
            if getattr(resolver, "store", None) is not None:
                resolver.store.close()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_local_graphify_diff: {str(e)}"

@mcp_server.tool(
    name="projectbrain_hybrid_query",
    description="Execute 3-tier Hybrid Knowledge Base query combining Graphify structural graph traversal and HSG cognitive memories."
)
async def projectbrain_hybrid_query(
    query: str,
    project_id: Optional[str] = None,
    branch: str = "main",
    local_path: Optional[str] = None,
    include_memories: bool = True,
    mode: str = "bfs",
    budget: int = 2500,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
) -> str:
    """
    Execute 3-tier Hybrid Knowledge Base query combining Graphify structural graph traversal and HSG cognitive memories.
    """
    try:
        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch
        )

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload: Dict[str, Any] = {
                "project_id": target_proj,
                "query": query,
                "branch": target_branch,
                "local_path": local_path,
                "include_memories": include_memories,
                "mode": mode,
                "budget": budget,
                "include_shared_docs": include_shared_docs,
                "relation_depth": relation_depth,
            }
            if target_source is not None:
                payload["source_id"] = target_source
            if relation_types is not None:
                payload["relation_types"] = relation_types
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{server_url}/graphify/hybrid-query",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)

        graph_target = _format_graph_target(target_proj, target_source)
        resolver = get_hybrid_resolver()
        try:
            extra_q = _filter_supported_kwargs(
                resolver.query,
                {
                    "source_id": target_source,
                    "include_shared_docs": include_shared_docs,
                    "relation_depth": relation_depth,
                    "relation_types": relation_types,
                },
            )
            q_proj = target_proj if "source_id" in extra_q else graph_target
            graph_res = resolver.query(
                project_id=q_proj,
                query_text=query,
                branch=target_branch,
                local_path=local_path,
                mode=mode,
                budget=budget,
                **extra_q,
            )
        finally:
            if getattr(resolver, "store", None) is not None:
                resolver.store.close()

        memories: List[Dict[str, Any]] = []
        if include_memories:
            mem_user_id = _format_mem_user_id(target_proj, target_branch, source_id=target_source)
            try:
                mem_filters: Dict[str, Any] = {}
                if (
                    target_source is not None
                    or include_shared_docs is not True
                    or relation_depth != 1
                    or relation_types is not None
                ):
                    if target_source is not None:
                        mem_filters["source_id"] = target_source
                    mem_filters["include_shared_docs"] = include_shared_docs
                    mem_filters["relation_depth"] = relation_depth
                    if relation_types is not None:
                        mem_filters["relation_types"] = relation_types
                memories = await mem.search(query=query, user_id=mem_user_id, limit=5, **mem_filters)
            except Exception as me:
                logger.debug(f"Memory search error in hybrid query: {me}")

        cross_source_relations: List[Dict[str, Any]] = list(graph_res.get("cross_source_relations") or [])
        if not cross_source_relations and relation_depth and relation_depth > 0:
            try:
                from ..core.tenant_manager import tenant_manager
                rels = tenant_manager.list_cross_source_relations(
                    target_proj,
                    source_id=target_source,
                    branch=target_branch,
                )
                if relation_types:
                    allowed_rt = {rt.strip() for rt in relation_types if rt and rt.strip()}
                    rels = [r for r in rels if r.get("relation_type") in allowed_rt]
                cross_source_relations = [r for r in rels if r.get("status") != "rejected"]
            except Exception as rel_exc:
                logger.debug(f"Cross-source relations lookup skipped in hybrid query: {rel_exc}")

        matched_nodes = graph_res.get("nodes", [])
        linked_doc_chunks = [
            n for n in matched_nodes
            if n.get("file_type") == "document" or "chunk_id" in (n.get("metadata") or {})
        ]
        shared_doc_chunks = list(graph_res.get("shared_doc_chunks") or [])
        if not shared_doc_chunks:
            shared_doc_chunks = [
                n for n in linked_doc_chunks
                if n.get("source_id") == "__shared__"
                or n.get("scope") == "project_shared"
                or (n.get("metadata") or {}).get("scope") == "project_shared"
            ]
        output: Dict[str, Any] = {
            "query": query,
            "project_id": target_proj,
            "branch": target_branch,
            "source_id": target_source,
            "include_shared_docs": include_shared_docs,
            "relation_depth": relation_depth,
            "relation_types": relation_types,
            "graph_summary": graph_res.get("context_markdown", ""),
            "matched_nodes": matched_nodes,
            "linked_doc_chunks": linked_doc_chunks,
            "shared_doc_chunks": shared_doc_chunks,
            "cross_source_relations": cross_source_relations,
            "traversal_stats": graph_res.get("tier_breakdown", {}),
            "cognitive_memories_count": len(memories),
            "cognitive_memories": memories,
        }
        return json.dumps(output, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_hybrid_query: {str(e)}"

@mcp_server.tool(
    name="projectbrain_upload_document",
    description="Process and ingest multimodal document (.pdf, .docx, .xlsx, .pptx, .csv, .md, images) into Graphify and ProjectBrain memory with versioned pre-OCR delta reuse."
)
async def projectbrain_upload_document(
    file_path: str,
    project_id: Optional[str] = None,
    branch: str = "main",
    author: Optional[str] = None,
    tags: Optional[List[str]] = None,
    push_to_server: Optional[bool] = None,
    doc_key: Optional[str] = None,
    doc_version: Optional[str] = None,
    base_version: Optional[str] = None,
    base_branch: Optional[str] = None,
    ocr_engine: Optional[str] = None,
    ocr_device: Optional[str] = None,
    local_ai_model: Optional[str] = None,
    local_ai_url: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: str = "source",
    doc_role: str = "general",
) -> str:
    """
    Process and ingest multimodal document into Graphify and ProjectBrain memory.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..graphify_engine.doc_pipeline import DocumentPipeline
        from ..graphify_engine.doc_versioning import ingest_document_versioned
        from ..graphify_engine.models import GraphDelta

        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch, scope=scope
        )
        eff_scope = "project_shared" if (scope == "project_shared" or target_source == "__shared__") else (scope or "source")
        eff_source = "__shared__" if eff_scope == "project_shared" else target_source
        eff_doc_role = (doc_role or "general").strip() or "general"
        graph_target = _format_graph_target(target_proj, eff_source, eff_scope)

        pipeline = DocumentPipeline()
        process_kwargs: Dict[str, Any] = dict(
            file_path=file_path,
            project_id=target_proj,
            branch=target_branch,
            author=author,
            tags=tags,
            doc_key=doc_key,
            doc_version=doc_version,
            base_version=base_version,
            base_branch=base_branch,
        )
        if ocr_engine is not None:
            process_kwargs["ocr_engine"] = ocr_engine
        if ocr_device is not None:
            process_kwargs["ocr_device"] = ocr_device
        if local_ai_model is not None:
            process_kwargs["local_ai_model"] = local_ai_model
        if local_ai_url is not None:
            process_kwargs["local_ai_url"] = local_ai_url
        payload = await pipeline.process_file(**process_kwargs)

        should_push = push_to_server if push_to_server is not None else is_pb_remote_mode()
        if should_push:
            payload["doc_key"] = doc_key or payload["document"].get("doc_key")
            payload["doc_version"] = doc_version or payload["document"].get("doc_version")
            payload["base_version"] = base_version
            payload["base_branch"] = base_branch or payload["document"].get("base_branch")
            payload["source_id"] = eff_source
            payload["scope"] = eff_scope
            payload["doc_role"] = eff_doc_role
            if eff_source is not None:
                payload["document"]["source_id"] = eff_source
            payload["document"]["scope"] = eff_scope
            payload["document"]["doc_role"] = eff_doc_role
            if ocr_engine is not None:
                payload["document"]["ocr_engine"] = ocr_engine
            if ocr_device is not None:
                payload["document"]["ocr_device"] = ocr_device
            if local_ai_model is not None:
                payload["document"]["local_ai_model"] = local_ai_model
            if local_ai_url is not None:
                payload["document"]["local_ai_url"] = local_ai_url
            res = await pipeline.push_processed_to_server(
                server_url=get_pb_server_url(),
                payload=payload,
                token=get_pb_api_key(),
            )
            return json.dumps(res, indent=2)
        else:
            doc = payload["document"]
            extra_ingest = _filter_supported_kwargs(
                ingest_document_versioned,
                {
                    "source_id": eff_source,
                    "scope": eff_scope,
                    "doc_role": eff_doc_role,
                },
            )
            ver_res = await ingest_document_versioned(
                filename=doc.get("filename") or str(file_path),
                markdown_content=doc.get("markdown_content", ""),
                precomputed_units=doc.get("units") or None,
                project_id=target_proj,
                branch=target_branch,
                author=author,
                tags=tags or ["document"],
                doc_key=doc_key or doc.get("doc_key"),
                doc_version=doc_version or doc.get("doc_version"),
                base_version=base_version,
                base_branch=base_branch or doc.get("base_branch"),
                **extra_ingest,
            )

            delta_dict = payload["graph_delta"]
            delta = GraphDelta(
                project_id=target_proj,
                branch=target_branch,
                nodes=delta_dict.get("nodes", []),
                edges=delta_dict.get("edges", []),
                hyperedges=delta_dict.get("hyperedges", []),
                node_tombstones=delta_dict.get("node_tombstones", []),
                edge_tombstones=delta_dict.get("edge_tombstones", []),
                hyperedge_tombstones=delta_dict.get("hyperedge_tombstones", []),
                synced_files=delta_dict.get("synced_files", []),
            )
            store = _get_mcp_graph_store()
            try:
                sync_res = store.sync_branch_delta(
                    project_id=graph_target,
                    branch_name=target_branch,
                    delta=delta,
                    partial=True,
                    synced_files=[str(file_path)],
                    author=author or "system",
                    base_branch=ver_res.get("base_branch") or base_branch,
                )
            finally:
                store.close()
            return json.dumps({
                "status": "success",
                "mode": "local",
                "project_id": target_proj,
                "branch": target_branch,
                "source_id": eff_source,
                "scope": eff_scope,
                "doc_role": eff_doc_role,
                "memories_created": ver_res["memories_created"],
                "nodes_upserted": sync_res.get("nodes_upserted", len(delta.nodes)),
                "edges_upserted": sync_res.get("edges_upserted", len(delta.edges)),
                "doc_key": ver_res["doc_key"],
                "doc_version": ver_res["doc_version"],
                "parent_version": ver_res["parent_version"],
                "base_branch": ver_res["base_branch"],
                "total_units": ver_res["total_units"],
                "reused_units": ver_res["reused_units"],
                "new_units": ver_res["new_units"],
                "deleted_units": ver_res["deleted_units"],
            }, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_upload_document: {str(e)}"

@mcp_server.tool(
    name="projectbrain_push_processed_document",
    description="Directly push pre-extracted document Markdown and Graphify entities to ProjectBrain server or local memory with versioning."
)
async def projectbrain_push_processed_document(
    filename: str,
    markdown_content: str,
    project_id: Optional[str] = None,
    branch: str = "main",
    author: Optional[str] = None,
    tags: Optional[List[str]] = None,
    server_url: Optional[str] = None,
    doc_key: Optional[str] = None,
    doc_version: Optional[str] = None,
    base_version: Optional[str] = None,
    base_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: str = "source",
    doc_role: str = "general",
) -> str:
    """
    Directly push pre-extracted document Markdown and Graphify entities to ProjectBrain server or local memory.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..graphify_engine.extractor import GraphifyExtractor
        from ..graphify_engine.doc_versioning import ingest_document_versioned
        from ..utils.chunking import chunk_text

        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch, scope=scope
        )
        eff_scope = "project_shared" if (scope == "project_shared" or target_source == "__shared__") else (scope or "source")
        eff_source = "__shared__" if eff_scope == "project_shared" else target_source
        eff_doc_role = (doc_role or "general").strip() or "general"
        graph_target = _format_graph_target(target_proj, eff_source, eff_scope)

        import hashlib
        sha256 = hashlib.sha256(markdown_content.encode("utf-8")).hexdigest()
        extractor = GraphifyExtractor()
        doc_metadata: Dict[str, Any] = {
            "author": author or "system",
            "sha256": sha256,
            "file_type": "document",
            "scope": eff_scope,
            "doc_role": eff_doc_role,
        }
        if eff_source is not None:
            doc_metadata["source_id"] = eff_source
        delta = extractor.extract_document_graph(
            file_path=filename,
            markdown_content=markdown_content,
            project_id=target_proj,
            branch=target_branch,
            metadata=doc_metadata,
        )

        raw_chunks = chunk_text(markdown_content, tgt=768, ovr=0.1)
        chunks = [{"content": c["text"], "chunk_id": f"p_{i+1}", "tags": tags or ["document"]} for i, c in enumerate(raw_chunks)]

        payload: Dict[str, Any] = {
            "project_id": target_proj,
            "branch": target_branch,
            "doc_key": doc_key,
            "doc_version": doc_version,
            "base_version": base_version,
            "base_branch": base_branch,
            "source_id": eff_source,
            "scope": eff_scope,
            "doc_role": eff_doc_role,
            "document": {
                "filename": filename,
                "sha256": sha256,
                "author": author or "system",
                "file_type": "document",
                "markdown_content": markdown_content,
                "chunks": chunks,
                "doc_key": doc_key,
                "doc_version": doc_version,
                "base_version": base_version,
                "base_branch": base_branch,
                "source_id": eff_source,
                "scope": eff_scope,
                "doc_role": eff_doc_role,
            },
            "graph_delta": delta.to_dict() if hasattr(delta, "to_dict") else delta.model_dump(),
        }

        target_url = server_url or (get_pb_server_url() if is_pb_remote_mode() else None)
        if target_url:
            import httpx
            headers = {"Authorization": f"Bearer {get_pb_api_key()}"} if get_pb_api_key() else {}
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{target_url.rstrip('/')}/sources/push-processed", json=payload, headers=headers)
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            extra_ingest = _filter_supported_kwargs(
                ingest_document_versioned,
                {
                    "source_id": eff_source,
                    "scope": eff_scope,
                    "doc_role": eff_doc_role,
                },
            )
            ver_res = await ingest_document_versioned(
                filename=filename,
                markdown_content=markdown_content,
                project_id=target_proj,
                branch=target_branch,
                author=author,
                tags=tags or ["document"],
                doc_key=doc_key,
                doc_version=doc_version,
                base_version=base_version,
                base_branch=base_branch,
                **extra_ingest,
            )

            store = _get_mcp_graph_store()
            try:
                sync_res = store.sync_branch_delta(
                    project_id=graph_target,
                    branch_name=target_branch,
                    delta=delta,
                    partial=True,
                    synced_files=[filename],
                    author=author or "system",
                    base_branch=ver_res.get("base_branch") or base_branch,
                )
            finally:
                store.close()
            return json.dumps({
                "status": "success",
                "mode": "local",
                "project_id": target_proj,
                "branch": target_branch,
                "source_id": eff_source,
                "scope": eff_scope,
                "doc_role": eff_doc_role,
                "memories_created": ver_res["memories_created"],
                "nodes_upserted": sync_res.get("nodes_upserted", len(delta.nodes)),
                "edges_upserted": sync_res.get("edges_upserted", len(delta.edges)),
                "doc_key": ver_res["doc_key"],
                "doc_version": ver_res["doc_version"],
                "parent_version": ver_res["parent_version"],
                "base_branch": ver_res["base_branch"],
                "total_units": ver_res["total_units"],
                "reused_units": ver_res["reused_units"],
                "new_units": ver_res["new_units"],
                "deleted_units": ver_res["deleted_units"],
            }, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_push_processed_document: {str(e)}"

@mcp_server.tool(
    name="projectbrain_list_document_versions",
    description="List version history for a versioned document (doc_key) in a ProjectBrain project and optional branch."
)
async def projectbrain_list_document_versions(
    doc_key: str,
    project_id: Optional[str] = None,
    branch: Optional[str] = None,
) -> str:
    """List version history for a specific doc_key."""
    try:
        if project_id and project_id.strip():
            raw_proj = project_id.strip()
            if ":" in raw_proj:
                target_proj, proj_br = raw_proj.split(":", 1)
                target_branch = branch if branch is not None else proj_br
            else:
                target_proj = raw_proj
                target_branch = branch
        else:
            resolved_proj = resolve_mcp_user_id(project_id)
            if ":" in resolved_proj:
                target_proj, proj_br = resolved_proj.split(":", 1)
                target_branch = branch if branch is not None else proj_br
            else:
                target_proj = resolved_proj
                target_branch = branch

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            headers = {"Authorization": f"Bearer {get_pb_api_key()}"} if get_pb_api_key() else {}
            params: Dict[str, Any] = {"project_id": target_proj}
            if target_branch:
                params["branch"] = target_branch
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{server_url}/sources/documents/{doc_key}/versions",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            from ..graphify_engine.doc_versioning import list_document_versions
            versions = list_document_versions(
                project_id=target_proj,
                doc_key=doc_key,
                branch=target_branch,
            )
            return json.dumps({
                "project_id": target_proj,
                "doc_key": doc_key,
                "branch": target_branch,
                "versions": versions,
            }, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_list_document_versions: {str(e)}"

@mcp_server.tool(
    name="projectbrain_diff_document_versions",
    description="Compute unit-level diff (unchanged, modified, added, removed pages/sections/slides/sheets) between two versions of a document."
)
async def projectbrain_diff_document_versions(
    doc_key: str,
    from_version: str,
    to_version: str,
    project_id: Optional[str] = None,
    branch: Optional[str] = None,
    from_branch: Optional[str] = None,
    to_branch: Optional[str] = None,
) -> str:
    """Compute unit-level diff between two versions of a document."""
    try:
        if project_id and project_id.strip():
            raw_proj = project_id.strip()
            if ":" in raw_proj:
                target_proj, proj_br = raw_proj.split(":", 1)
                target_branch = branch or proj_br or "main"
            else:
                target_proj = raw_proj
                target_branch = branch or "main"
        else:
            resolved_proj = resolve_mcp_user_id(project_id)
            if ":" in resolved_proj:
                target_proj, proj_br = resolved_proj.split(":", 1)
                target_branch = branch or proj_br or "main"
            else:
                target_proj = resolved_proj
                target_branch = branch or "main"

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            headers = {"Authorization": f"Bearer {get_pb_api_key()}"} if get_pb_api_key() else {}
            params: Dict[str, Any] = {
                "project_id": target_proj,
                "from_version": from_version,
                "to_version": to_version,
                "branch": target_branch,
            }
            if from_branch:
                params["from_branch"] = from_branch
            if to_branch:
                params["to_branch"] = to_branch
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{server_url}/sources/documents/{doc_key}/diff",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            from ..graphify_engine.doc_versioning import diff_document_versions
            diff_res = diff_document_versions(
                project_id=target_proj,
                doc_key=doc_key,
                from_version=from_version,
                to_version=to_version,
                branch=target_branch,
                from_branch=from_branch,
                to_branch=to_branch,
            )
            return json.dumps(diff_res, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_diff_document_versions: {str(e)}"

@mcp_server.tool(
    name="projectbrain_promote_branch_graph",
    description="Atomically promote branch overlay schema mutations into canonical root schema."
)
async def projectbrain_promote_branch_graph(
    project_id: str,
    branch: str,
    target_branch: Optional[str] = None
) -> str:
    """
    Atomically promote branch overlay schema mutations into canonical root or target parent branch schema.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..graphify_engine.store import get_graph_store
        resolved_proj = resolve_mcp_user_id(project_id)
        if ":" in resolved_proj:
            p_parts = resolved_proj.split(":", 1)
            target_proj = p_parts[0]
            source_branch = branch if branch else (p_parts[1] or "main")
        else:
            target_proj = resolved_proj
            source_branch = branch

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            headers = {"Authorization": f"Bearer {get_pb_api_key()}"} if get_pb_api_key() else {}
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{server_url}/graphify/promote",
                    json={
                        "project_id": target_proj,
                        "branch": source_branch,
                        "target_branch": target_branch,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            store = _get_mcp_graph_store()
            try:
                res = store.promote_branch_to_root(
                    project_id=target_proj,
                    branch_name=source_branch,
                    target_branch=target_branch,
                )
                return json.dumps(res, indent=2)
            finally:
                store.close()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_promote_branch_graph: {str(e)}"

@mcp_server.tool(
    name="projectbrain_build_complete_kb",
    description="Synthesize zero-hallucination Markdown wiki, multimodal Office documents (.docx, .xlsx, .pptx), chunk documents into HSG cognitive memory, and build multi-signal code-doc-test cross-links."
)
async def projectbrain_build_complete_kb(
    workspace_path: str = ".",
    project_id: Optional[str] = None,
    branch: str = "main",
    persist_memory: bool = True,
    output_dir: Optional[str] = None,
    generate_office_exports: bool = True,
    device: Optional[str] = None,
    workers: Optional[int] = None,
    semantic_engine: Optional[str] = None,
    ocr_engine: Optional[str] = None,
    ocr_device: Optional[str] = None,
    source_id: Optional[str] = None,
    source_version: Optional[str] = None,
    upgrade_mode: str = "delta",
    auto_link_sources: bool = True,
) -> str:
    """
    Build and cross-link complete ProjectBrain Knowledge Base for the workspace.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch
        )
        graph_target = _format_graph_target(target_proj, target_source)

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload: Dict[str, Any] = {
                "project_id": graph_target,
                "branch": target_branch,
                "workspace_path": workspace_path,
                "persist_memory": persist_memory,
                "output_dir": output_dir,
                "generate_office_exports": generate_office_exports,
                "upgrade_mode": upgrade_mode,
                "auto_link_sources": auto_link_sources,
            }
            if target_source is not None:
                payload["source_id"] = target_source
            if source_version is not None:
                payload["source_version"] = source_version
            if device is not None:
                payload["device"] = device
            if workers is not None:
                payload["workers"] = workers
            if semantic_engine is not None:
                payload["semantic_engine"] = semantic_engine
            if ocr_engine is not None:
                payload["ocr_engine"] = ocr_engine
            if ocr_device is not None:
                payload["ocr_device"] = ocr_device
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(f"{server_url}/kb/build", json=payload, headers=headers)
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            from ..graphify_engine.kb_linker import UnifiedKBBuilder
            db_path = (
                os.getenv("PB_GRAPH_DB_PATH")
                or os.getenv("OM_GRAPH_DB_PATH")
                or os.getenv("PB_DB_PATH")
                or os.getenv("OM_DB_PATH")
            )
            store = _get_mcp_graph_store()
            try:
                eff_device = device or "auto"
                eff_ocr_device = ocr_device or "auto"
                eff_ocr_engine = ocr_engine or "auto"
                eff_semantic_engine = semantic_engine or "auto"
                builder = UnifiedKBBuilder(
                    workspace_path=workspace_path,
                    device=eff_device,
                    ocr_device=eff_ocr_device,
                    ocr_engine=eff_ocr_engine,
                )
                extra_kb = _filter_supported_kwargs(
                    builder.build_unified_kb,
                    {
                        "source_id": target_source,
                        "source_version": source_version,
                        "upgrade_mode": upgrade_mode,
                        "auto_link_sources": auto_link_sources,
                    },
                )
                res = await builder.build_unified_kb(
                    project_id=graph_target,
                    branch=target_branch,
                    persist_memory=persist_memory,
                    output_dir=output_dir,
                    generate_office_exports=generate_office_exports,
                    store=store,
                    db_path=db_path,
                    device=eff_device,
                    ocr_device=eff_ocr_device,
                    ocr_engine=eff_ocr_engine,
                    workers=workers,
                    semantic_engine=eff_semantic_engine,
                    **extra_kb,
                )
            finally:
                store.close()
            if isinstance(res, dict):
                if target_source is not None:
                    res["source_id"] = target_source
                extra = _record_source_upgrade_and_auto_link(
                    project_id=target_proj,
                    source_id=target_source,
                    branch=target_branch,
                    source_version=source_version,
                    upgrade_mode=upgrade_mode,
                    auto_link_sources=auto_link_sources,
                    workspace_path=workspace_path,
                    docs_updated=int(res.get("total_chunks") or 0),
                )
                res.update(extra)
            return json.dumps(res, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_build_complete_kb: {str(e)}"

@mcp_server.tool(
    name="projectbrain_trace_code_and_docs",
    description="Trace unified code AST symbols, linked documentation chunks, tests, callers/callees, and drift detection status for any symbol, route, or file."
)
async def projectbrain_trace_code_and_docs(
    target: str,
    project_id: Optional[str] = None,
    branch: str = "main",
    workspace_path: str = ".",
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
) -> str:
    """
    Trace code, doc chunks, and test links for a target query.
    """
    try:
        target_proj, target_source, target_branch = _resolve_project_source_branch(
            project_id, source_id=source_id, branch=branch
        )
        graph_target = _format_graph_target(target_proj, target_source)

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            params: Dict[str, Any] = {
                "project_id": target_proj,
                "target": target,
                "branch": target_branch,
                "workspace_path": workspace_path,
                "include_shared_docs": include_shared_docs,
                "relation_depth": relation_depth,
            }
            if target_source is not None:
                params["source_id"] = target_source
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(f"{server_url}/kb/trace", params=params, headers=headers)
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            from ..graphify_engine.kb_linker import UnifiedKBBuilder
            from ..core.tenant_manager import tenant_manager

            store = _get_mcp_graph_store()
            try:
                builder = UnifiedKBBuilder(workspace_path=workspace_path)
                builder.store = store
                extra_tr = _filter_supported_kwargs(
                    builder.trace_code_and_docs,
                    {
                        "source_id": target_source,
                        "include_shared_docs": include_shared_docs,
                        "relation_depth": relation_depth,
                    },
                )
                tr_proj = target_proj if "source_id" in extra_tr else graph_target
                res = builder.trace_code_and_docs(
                    project_id=tr_proj,
                    target=target,
                    branch=target_branch,
                    **extra_tr,
                )
                out = res.model_dump() if hasattr(res, "model_dump") else dict(res)
                out["project_id"] = target_proj
                out["branch"] = target_branch
                out["source_id"] = target_source

                if relation_depth and relation_depth > 0:
                    try:
                        rels = tenant_manager.list_cross_source_relations(
                            target_proj, source_id=target_source, branch=target_branch
                        )
                        active_rels = [r for r in rels if r.get("status") != "rejected"]
                        t_low = target.strip().lower()
                        matched_contracts = [
                            r for r in active_rels
                            if not t_low or any(
                                t_low in str(r.get(f) or "").lower()
                                for f in ("contract_key", "src_node_id", "dst_node_id", "src_file_path", "dst_doc_key", "contract_metadata_json")
                            )
                        ]
                        out["cross_source_relations"] = active_rels
                        out["cross_source_contracts"] = matched_contracts
                    except Exception:
                        out["cross_source_relations"] = []
                        out["cross_source_contracts"] = []

                if include_shared_docs:
                    try:
                        bindings = tenant_manager.list_shared_doc_bindings(
                            target_proj,
                            target_source_id=target_source,
                            include_wildcard=True,
                        )
                    except Exception:
                        bindings = []
                    shared_docs: List[Dict[str, Any]] = []
                    try:
                        shared_sub = store.get_resolved_subgraph(
                            f"{target_proj}@__shared__", target_branch, limit=5000
                        )
                        t_low = target.strip().lower()
                        for sn in shared_sub.get("nodes", []):
                            if sn.get("file_type") == "document" or "doc" in str(sn.get("id", "")).lower():
                                blob = f"{sn.get('id', '')} {sn.get('label', '')} {sn.get('source_file', '')} {json.dumps(sn.get('metadata') or {})}".lower()
                                if not t_low or t_low in blob:
                                    shared_docs.append(sn)
                    except Exception:
                        shared_docs = []
                    out["shared_doc_bindings"] = bindings
                    out["shared_docs"] = shared_docs

                return json.dumps(out, default=str, indent=2)
            finally:
                store.close()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_trace_code_and_docs: {str(e)}"

@mcp_server.tool(
    name="projectbrain_kb_coverage_report",
    description="Get repository-wide knowledge base coverage analytics, documented/inferred/undocumented ratios, API & MCP coverage %, and drift alerts."
)
async def projectbrain_kb_coverage_report(
    project_id: Optional[str] = None,
    branch: str = "main",
    workspace_path: str = "."
) -> str:
    """
    Compute and return repository-wide documentation and test coverage analytics.
    """
    try:
        resolved_proj = resolve_mcp_user_id(project_id)
        if ":" in resolved_proj:
            p_parts = resolved_proj.split(":", 1)
            target_proj = p_parts[0]
            target_branch = branch if branch != "main" else (p_parts[1] or "main")
        else:
            target_proj = resolved_proj
            target_branch = branch or "main"

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            params = {
                "project_id": target_proj,
                "branch": target_branch,
                "workspace_path": workspace_path,
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(f"{server_url}/kb/coverage", params=params, headers=headers)
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            from ..graphify_engine.kb_linker import UnifiedKBBuilder
            store = _get_mcp_graph_store()
            try:
                builder = UnifiedKBBuilder(workspace_path=workspace_path)
                builder.store = store
                res = builder.get_kb_coverage_report(
                    project_id=target_proj,
                    branch=target_branch,
                )
                return json.dumps(res.model_dump(), indent=2)
            finally:
                store.close()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_kb_coverage_report: {str(e)}"

@mcp_server.tool(
    name="projectbrain_generate_project_docs",
    description="Generate on-demand Markdown Knowledge Wiki and Multimodal Office exports (.docx, .xlsx, .pptx) from current GraphStore."
)
async def projectbrain_generate_project_docs(
    workspace_path: str = ".",
    project_id: Optional[str] = None,
    branch: str = "main",
    output_dir: str = "docs/kb",
    formats: Optional[List[str]] = None
) -> str:
    """
    Generate documentation and office exports on-demand.
    """
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        resolved_proj = resolve_mcp_user_id(project_id)
        if ":" in resolved_proj:
            p_parts = resolved_proj.split(":", 1)
            target_proj = p_parts[0]
            target_branch = branch if branch != "main" else (p_parts[1] or "main")
        else:
            target_proj = resolved_proj
            target_branch = branch or "main"

        target_formats = [f.lower().strip() for f in formats] if formats else ["wiki", "docx", "xlsx", "pptx"]

        if is_pb_remote_mode():
            import httpx
            server_url = get_pb_server_url()
            api_key = get_pb_api_key()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload = {
                "project_id": target_proj,
                "branch": target_branch,
                "workspace_path": workspace_path,
                "output_dir": output_dir,
                "formats": target_formats,
            }
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(f"{server_url}/kb/export", json=payload, headers=headers)
                resp.raise_for_status()
                return json.dumps(resp.json(), indent=2)
        else:
            from ..graphify_engine.doc_synthesizer import ProjectDocSynthesizer
            store = _get_mcp_graph_store()
            try:
                subgraph = store.get_resolved_subgraph(target_proj, target_branch, limit=100000)
            finally:
                store.close()
            synthesizer = ProjectDocSynthesizer(workspace_path=workspace_path)
            out_p = Path(output_dir)
            if not out_p.is_absolute():
                out_p = Path(workspace_path).resolve() / out_p
            out_p.mkdir(parents=True, exist_ok=True)
            wiki_dir = out_p / "wiki" if "wiki" not in out_p.parts else out_p
            exports_dir = out_p / "exports" if "exports" not in out_p.parts else out_p

            artifacts = []
            if "wiki" in target_formats:
                pages = synthesizer.synthesize_wiki(subgraph, output_dir=wiki_dir)
                artifacts.append({"format": "wiki", "count": len(pages), "dir": str(wiki_dir)})
            if "docx" in target_formats:
                exports_dir.mkdir(parents=True, exist_ok=True)
                r = synthesizer.export_docx_specification(subgraph, output_path=exports_dir / "ProjectBrain_Architecture_Spec.docx")
                artifacts.append(r.model_dump())
            if "xlsx" in target_formats:
                exports_dir.mkdir(parents=True, exist_ok=True)
                linker_index = {
                    "links": [
                        e
                        for e in subgraph.get("edges", [])
                        if e.get("relation")
                        in (
                            "documents_symbol",
                            "documented_in",
                            "conceptually_related_to",
                            "verifies_symbol",
                            "verifies_requirement",
                            "specifies_endpoint",
                        )
                    ],
                    "chunks": [n for n in subgraph.get("nodes", []) if n.get("file_type") == "document"],
                    "coverage": {},
                }
                r = synthesizer.export_xlsx_traceability_matrix(subgraph, output_path=exports_dir / "ProjectBrain_Traceability_Matrix.xlsx", linker_index=linker_index)
                artifacts.append(r.model_dump())
            if "pptx" in target_formats:
                exports_dir.mkdir(parents=True, exist_ok=True)
                r = synthesizer.export_pptx_architecture_deck(subgraph, output_path=exports_dir / "ProjectBrain_Architecture_Deck.pptx")
                artifacts.append(r.model_dump())

            res = {
                "status": "success",
                "project_id": target_proj,
                "branch": target_branch,
                "output_dir": str(out_p),
                "formats": target_formats,
                "artifacts": artifacts,
            }
            return json.dumps(res, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_generate_project_docs: {str(e)}"



# ---------------------------------------------------------------------------
# Plugin Microkernel & v3.0 Testing KB FastMCP Tools (Local & Remote Mode)
# ---------------------------------------------------------------------------

def _resolve_v3_project_branch(project_id: Optional[str], branch: str = "main") -> Tuple[str, str]:
    resolved_proj = resolve_mcp_user_id(project_id)
    if ":" in resolved_proj:
        p_parts = resolved_proj.split(":", 1)
        target_proj = p_parts[0]
        target_branch = branch if branch != "main" else (p_parts[1] or "main")
    else:
        target_proj = resolved_proj
        target_branch = branch or "main"
    return target_proj, target_branch


async def _pb_remote_v3_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Any:
    import httpx
    server_url = get_pb_server_url()
    api_key = get_pb_api_key()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method.upper(),
            f"{server_url}{path}",
            params=clean_params or None,
            json=json_body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


def _select_v3_etl_parser(
    registry: Any,
    source_name: str,
    format_hint: Optional[str] = None,
    content_preview: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[Any]:
    from ..plugins.base import PluginCategory

    parsers = registry.resolve_all(PluginCategory.ETL_PARSER, project_id=project_id)
    if format_hint:
        hint_lower = format_hint.strip().lower()
        for p in parsers:
            supported = tuple(getattr(p, "supported_formats", ()) or ())
            if hint_lower in supported and p.can_parse(
                source_name, format_hint=format_hint, content_preview=content_preview
            ):
                return p

    generic_ids = {"etl.graphify_ast", "etl.multimodal_doc"}
    for p in parsers:
        if p.metadata.plugin_id not in generic_ids and p.can_parse(
            source_name, format_hint=format_hint, content_preview=content_preview
        ):
            return p

    for p in parsers:
        if p.can_parse(source_name, format_hint=format_hint, content_preview=content_preview):
            return p

    return None


_FORBIDDEN_MCP_SYSTEM_PREFIXES = (
    "/etc",
    "/private/etc",
    "/proc",
    "/sys",
    "/var/run",
    "/private/var/run",
    "/root",
    "/dev",
    "/boot",
)
_FORBIDDEN_MCP_SENSITIVE_DIR_NAMES = (".ssh", ".aws", ".gnupg")


def _validate_mcp_safe_path(file_path: str) -> Path:
    """Reject raw '..' traversal and sensitive system/credential paths while allowing workspace and pytest tmp_path files."""
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("file_path must be a non-empty string")
    if "\x00" in file_path:
        raise ValueError("Null byte is not allowed in file_path")

    clean_str = file_path.strip()
    norm_slashes = clean_str.replace("\\", "/")
    raw_path = Path(clean_str)
    if ".." in raw_path.parts or ".." in norm_slashes.split("/"):
        raise ValueError(f"Path traversal ('..') is not allowed in file_path: {file_path}")

    expanded = raw_path.expanduser()
    norm_abs = os.path.abspath(str(expanded))
    resolved = expanded.resolve()

    for cand in (norm_abs, str(resolved)):
        for prefix in _FORBIDDEN_MCP_SYSTEM_PREFIXES:
            if cand == prefix or cand.startswith(prefix + "/"):
                raise ValueError(f"Access to sensitive system path '{file_path}' is forbidden")

    home_raw = Path.home()
    home_resolved = home_raw.resolve()
    forbidden_home_dirs = []
    for sub in _FORBIDDEN_MCP_SENSITIVE_DIR_NAMES:
        forbidden_home_dirs.append(Path(os.path.abspath(str(home_raw / sub))))
        forbidden_home_dirs.append((home_resolved / sub).resolve())

    for cand_path in (Path(norm_abs), resolved):
        for forbidden_dir in forbidden_home_dirs:
            if cand_path == forbidden_dir or forbidden_dir in cand_path.parents:
                raise ValueError(f"Access to sensitive credential directory '{file_path}' is forbidden")
        if any(part in _FORBIDDEN_MCP_SENSITIVE_DIR_NAMES for part in cand_path.parts):
            raise ValueError(f"Access to sensitive credential path '{file_path}' is forbidden")

    pb_ws_root = os.getenv("PB_WORKSPACE_ROOT")
    if pb_ws_root:
        ws_root_resolved = Path(pb_ws_root).expanduser().resolve()
        try:
            resolved.relative_to(ws_root_resolved)
        except ValueError as exc:
            raise ValueError(f"file_path '{file_path}' escapes configured PB_WORKSPACE_ROOT") from exc

    return resolved


@mcp_server.tool(
    name="projectbrain_list_plugins",
    description="List registered ProjectBrain v3.0 microkernel plugins, categories, priorities, enabled status, and health."
)
async def projectbrain_list_plugins(
    category: Optional[str] = None,
    project_id: Optional[str] = None,
) -> str:
    """List all registered ProjectBrain plugins and health status."""
    try:
        from ..server.routes.plugins_v3 import _redact_plugin_config

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request(
                "GET",
                "/plugins",
                params={"category": category, "project_id": project_id},
            )
            if isinstance(res, dict) and isinstance(res.get("plugins"), list):
                redacted = []
                for p in res["plugins"]:
                    if isinstance(p, dict):
                        p_copy = dict(p)
                        p_copy["config"] = _redact_plugin_config(p_copy.get("config") or {})
                        redacted.append(p_copy)
                    else:
                        redacted.append(p)
                res = {**res, "plugins": redacted}
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        registry = get_default_registry()
        plugins = registry.list_plugins(category=category, project_id=project_id)
        redacted_plugins: List[Dict[str, Any]] = []
        for p in plugins:
            p_copy = dict(p)
            p_copy["config"] = _redact_plugin_config(p_copy.get("config") or {})
            redacted_plugins.append(p_copy)
        health = registry.health_report(project_id=project_id)
        return json.dumps(
            {
                "status": "ok",
                "project_id": project_id,
                "category": category,
                "count": len(redacted_plugins),
                "plugins": redacted_plugins,
                "health": health,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_list_plugins: {str(e)}"


@mcp_server.tool(
    name="projectbrain_toggle_plugin",
    description="Enable, disable, or update configuration for a ProjectBrain v3.0 plugin globally or per project."
)
async def projectbrain_toggle_plugin(
    plugin_id: str,
    enabled: bool = True,
    project_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Enable, disable, or configure a plugin in ProjectBrain."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        req_role = mcp_request_role.get() or os.getenv("PB_MCP_ROLE")
        if req_role is not None and str(req_role).strip().lower() != "admin":
            return "Error: Permission denied. Admin role is required to toggle or configure plugins."

        from ..server.routes.plugins_v3 import _redact_plugin_config

        if is_pb_remote_mode():
            action = "enable" if enabled else "disable"
            body: Dict[str, Any] = {}
            if project_id is not None:
                body["project_id"] = project_id
            res = await _pb_remote_v3_request("POST", f"/plugins/{plugin_id}/{action}", json_body=body)
            if config is not None:
                cfg_body: Dict[str, Any] = {"config": config}
                if project_id is not None:
                    cfg_body["project_id"] = project_id
                cfg_res = await _pb_remote_v3_request("PUT", f"/plugins/{plugin_id}/config", json_body=cfg_body)
                if isinstance(cfg_res, dict) and "config" in cfg_res:
                    cfg_res = {**cfg_res, "config": _redact_plugin_config(cfg_res["config"])}
                if isinstance(res, dict):
                    res["config_update"] = cfg_res
            if isinstance(res, dict) and "config" in res:
                res = {**res, "config": _redact_plugin_config(res["config"])}
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        registry = get_default_registry()
        if registry.get(plugin_id) is None:
            return f"Error: Plugin '{plugin_id}' is not registered."

        if enabled:
            registry.enable(plugin_id, project_id=project_id)
        else:
            registry.disable(plugin_id, project_id=project_id)

        effective_config = registry.get_effective_config(plugin_id, project_id=project_id)
        if config is not None:
            effective_config = registry.configure(plugin_id, config, project_id=project_id)

        return json.dumps(
            {
                "status": "ok",
                "plugin_id": plugin_id,
                "project_id": project_id,
                "enabled": registry.is_enabled(plugin_id, project_id=project_id),
                "config": _redact_plugin_config(effective_config),
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_toggle_plugin: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_ingest_etl",
    description="Ingest testcases, hardware datasheets, OpenAPI 3.1 specs, C++/Qt/LD artifacts, or documents via v3.0 ETL plugins and sync resulting GraphDelta."
)
async def projectbrain_v3_ingest_etl(
    file_path: Optional[str] = None,
    content: Optional[str] = None,
    source_name: str = "input.md",
    format_hint: Optional[str] = None,
    project_id: Optional[str] = None,
    branch: str = "main",
    version_tag: Optional[str] = None,
) -> str:
    """Parse and ingest a source via ProjectBrain v3.0 ETL parser plugins."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        if not file_path and content is None:
            return "Error: Either 'file_path' or 'content' must be provided."

        validated_path: Optional[Path] = None
        if file_path is not None:
            try:
                validated_path = _validate_mcp_safe_path(file_path)
            except ValueError as ve:
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"Invalid or forbidden file_path: {ve}",
                    },
                    indent=2,
                )

        target_proj, target_branch = _resolve_v3_project_branch(project_id, branch)

        if is_pb_remote_mode():
            payload = {
                "file_path": file_path,
                "content": content,
                "source_name": source_name,
                "format_hint": format_hint,
                "project_id": target_proj,
                "branch": target_branch,
                "version_tag": version_tag,
            }
            res = await _pb_remote_v3_request("POST", "/v3/etl/ingest", json_body=payload, timeout=120.0)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..graphify_engine.models import GraphDelta

        registry = get_default_registry()
        preview_text: Optional[str] = None
        if file_path:
            src_path = validated_path if validated_path is not None else _validate_mcp_safe_path(file_path)
            if not src_path.is_file():
                return f"Error: File '{file_path}' does not exist or is not a file."
            effective_name = src_path.name
            source_obj: Any = str(src_path)
            try:
                preview_text = src_path.read_bytes()[:4096].decode("utf-8", errors="ignore")
            except Exception:
                preview_text = None
        else:
            effective_name = source_name or "input.md"
            source_obj = content or ""
            preview_text = (content or "")[:4096]

        parser = _select_v3_etl_parser(
            registry=registry,
            source_name=effective_name,
            format_hint=format_hint,
            content_preview=preview_text,
            project_id=target_proj,
        )
        if parser is None:
            return f"Error: No enabled ETL parser plugin could handle '{effective_name}' (format_hint={format_hint})."

        parsed = await parser.parse(
            source=source_obj,
            source_name=effective_name,
            project_id=target_proj,
            branch=target_branch,
            version_tag=version_tag,
        )

        # Sync graph delta if nodes or edges were produced
        graph_sync: Optional[Dict[str, Any]] = None
        delta_dict = parsed.get("graph_delta")
        if isinstance(delta_dict, dict) and (delta_dict.get("nodes") or delta_dict.get("edges")):
            delta = GraphDelta(
                project_id=target_proj,
                branch=target_branch,
                nodes=delta_dict.get("nodes", []),
                edges=delta_dict.get("edges", []),
                hyperedges=delta_dict.get("hyperedges", []),
                node_tombstones=delta_dict.get("node_tombstones", []),
                edge_tombstones=delta_dict.get("edge_tombstones", []),
                hyperedge_tombstones=delta_dict.get("hyperedge_tombstones", []),
                synced_files=delta_dict.get("synced_files", []),
            )
            synced_file_set = {
                sf
                for sf in (
                    [effective_name]
                    + [n.source_file for n in delta.nodes]
                    + [e.source_file for e in delta.edges]
                )
                if sf
            }
            store = _get_mcp_graph_store()
            try:
                graph_sync = store.sync_branch_delta(
                    project_id=target_proj,
                    branch_name=target_branch,
                    delta=delta,
                    partial=True,
                    synced_files=sorted(synced_file_set) or [effective_name],
                    author=os.getenv("USER", "mcp"),
                )
            finally:
                store.close()

        return json.dumps(
            {
                "status": "ok",
                "plugin_id": parsed.get("plugin_id", parser.metadata.plugin_id),
                "project_id": target_proj,
                "branch": target_branch,
                "version_tag": version_tag,
                "source_name": effective_name,
                "canonical_intents_count": len(parsed.get("canonical_intents") or []),
                "error_rules_count": len(parsed.get("error_rules") or []),
                "flow_nodes_count": len(parsed.get("flow_nodes") or []),
                "flow_edges_count": len(parsed.get("flow_edges") or []),
                "graph_sync": graph_sync,
                "result": parsed,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_ingest_etl: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_script_cache_lookup",
    description="Look up a verified UDCP script cache entry by intent_id, platform, build_version, and optional screen_hash."
)
async def projectbrain_v3_script_cache_lookup(
    intent_id: str,
    platform: str,
    build_version: str,
    project_id: Optional[str] = None,
    screen_hash: Optional[str] = None,
) -> str:
    """Look up a verified UDCP script in the Layer 1 script cache."""
    try:
        target_proj, _ = _resolve_v3_project_branch(project_id)

        if is_pb_remote_mode():
            payload = {
                "project_id": target_proj,
                "intent_id": intent_id,
                "platform": platform,
                "build_version": build_version,
                "screen_hash": screen_hash,
            }
            res = await _pb_remote_v3_request("POST", "/v3/script-cache/lookup", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory

        registry = get_default_registry()
        cache_plugin = registry.resolve(PluginCategory.SCRIPT_CACHE, project_id=target_proj)
        entry = cache_plugin.lookup_script(
            project_id=target_proj,
            intent_id=intent_id,
            platform=platform,
            build_version=build_version,
            screen_hash=screen_hash,
        )
        return json.dumps(
            {
                "status": "ok",
                "project_id": target_proj,
                "cache_hit": entry is not None,
                "entry": entry,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_script_cache_lookup: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_script_cache_store",
    description="Store or update a verified UDCP script cache entry for a test intent, platform, and build version."
)
async def projectbrain_v3_script_cache_store(
    intent_id: str,
    platform: str,
    build_version: str,
    udcp_commands: List[Dict[str, Any]],
    project_id: Optional[str] = None,
    screen_hash: str = "",
    ui_fingerprint: Optional[Dict[str, Any]] = None,
) -> str:
    """Store a verified UDCP script in the Layer 1 script cache."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        target_proj, _ = _resolve_v3_project_branch(project_id)
        payload = {
            "project_id": target_proj,
            "intent_id": intent_id,
            "platform": platform,
            "build_version": build_version,
            "udcp_commands": udcp_commands,
            "screen_hash": screen_hash,
            "ui_fingerprint": ui_fingerprint or {},
        }

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request("POST", "/v3/script-cache/store", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory

        registry = get_default_registry()
        cache_plugin = registry.resolve(PluginCategory.SCRIPT_CACHE, project_id=target_proj)
        stored = cache_plugin.store_script(payload)
        return json.dumps(
            {
                "status": "ok",
                "project_id": target_proj,
                "entry": stored,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_script_cache_store: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_index_artifact",
    description="Index a synchronized telemetry or evidence artifact (video_h264, har, pcap, crashpad_dmp, gdb_hardfault, uart_log, power_trace_ma, screenshot_png)."
)
async def projectbrain_v3_index_artifact(
    run_id: str,
    step_id: str,
    artifact_type: str,
    uri: str,
    ntp_timestamp_ms: int,
    project_id: Optional[str] = None,
    device_id: str = "",
    build_version: str = "",
    retention_days: int = 30,
    summary_metrics: Optional[Dict[str, Any]] = None,
) -> str:
    """Index a synchronized telemetry artifact into the Layer 1 artifact store."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        target_proj, _ = _resolve_v3_project_branch(project_id)
        payload = {
            "project_id": target_proj,
            "run_id": run_id,
            "step_id": step_id,
            "artifact_type": artifact_type,
            "uri": uri,
            "ntp_timestamp_ms": ntp_timestamp_ms,
            "device_id": device_id,
            "build_version": build_version,
            "retention_days": retention_days,
            "summary_metrics": summary_metrics or {},
        }

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request("POST", "/v3/artifacts/index", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory

        registry = get_default_registry()
        artifact_plugin = registry.resolve(PluginCategory.ARTIFACT_STORE, project_id=target_proj)
        indexed = artifact_plugin.index_artifact(payload)
        return json.dumps(
            {
                "status": "ok",
                "project_id": target_proj,
                "artifact": indexed,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_index_artifact: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_query_evidence_window",
    description="Query synchronized multimodal telemetry evidence around an NTP timestamp window with video_offset_ms alignment."
)
async def projectbrain_v3_query_evidence_window(
    run_id: str,
    center_ts_ms: int,
    window_ms: int = 5000,
    project_id: Optional[str] = None,
) -> str:
    """Query synchronized telemetry artifacts within a +/- window_ms NTP window."""
    try:
        target_proj, _ = _resolve_v3_project_branch(project_id)

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request(
                "GET",
                "/v3/artifacts/window",
                params={
                    "run_id": run_id,
                    "center_ts_ms": center_ts_ms,
                    "window_ms": window_ms,
                    "project_id": target_proj,
                },
            )
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory
        from ..server.routes.plugins_v3 import _accepts_kwarg

        registry = get_default_registry()
        artifact_plugin = registry.resolve(PluginCategory.ARTIFACT_STORE, project_id=target_proj)
        win_kwargs: Dict[str, Any] = {
            "run_id": run_id,
            "center_ts_ms": center_ts_ms,
            "window_ms": window_ms,
        }
        if target_proj and _accepts_kwarg(artifact_plugin.query_evidence_window, "project_id"):
            win_kwargs["project_id"] = target_proj
        window = artifact_plugin.query_evidence_window(**win_kwargs)
        return json.dumps(window, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_query_evidence_window: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_plan_test_dag",
    description="Plan an impact-driven topological test execution DAG with automatic precondition injection and UDCP script cache bindings."
)
async def projectbrain_v3_plan_test_dag(
    project_id: Optional[str] = None,
    branch: str = "main",
    version_tag: Optional[str] = None,
    srs_diff: str = "",
    code_diff_files: Optional[List[str]] = None,
    artifacts: Optional[List[str]] = None,
    target_devices: Optional[List[str]] = None,
) -> str:
    """Plan an impact-driven topological test execution DAG via brain.planner."""
    try:
        target_proj, target_branch = _resolve_v3_project_branch(project_id, branch)
        payload = {
            "project_id": target_proj,
            "branch": target_branch,
            "version_tag": version_tag,
            "srs_diff": srs_diff,
            "code_diff_files": code_diff_files or [],
            "artifacts": artifacts or [],
            "target_devices": target_devices or [],
        }

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request("POST", "/v3/planner/test-dag", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory

        registry = get_default_registry()
        planner = registry.resolve(
            PluginCategory.BRAIN_CONSUMER,
            preferred_id="brain.planner",
            project_id=target_proj,
        )
        res = await planner.execute(payload, registry=registry)
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_plan_test_dag: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_oracle_context",
    description="Evaluate telemetry against SRS acceptance criteria, expected state flow transitions, UART/AT error rules, HardFaults, power budgets, and HTTP responses."
)
async def projectbrain_v3_oracle_context(
    project_id: Optional[str] = None,
    branch: str = "main",
    version_tag: Optional[str] = None,
    test_intent_id: Optional[str] = None,
    screen_id: Optional[str] = None,
    telemetry_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """Evaluate telemetry against knowledge base rules via brain.oracle."""
    try:
        target_proj, target_branch = _resolve_v3_project_branch(project_id, branch)
        payload = {
            "project_id": target_proj,
            "branch": target_branch,
            "version_tag": version_tag,
            "test_intent_id": test_intent_id,
            "screen_id": screen_id,
            "telemetry_summary": telemetry_summary or {},
        }

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request("POST", "/v3/oracle/context", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory

        registry = get_default_registry()
        oracle = registry.resolve(
            PluginCategory.BRAIN_CONSUMER,
            preferred_id="brain.oracle",
            project_id=target_proj,
        )
        res = await oracle.execute(payload, registry=registry)
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_oracle_context: {str(e)}"


@mcp_server.tool(
    name="projectbrain_v3_rca_triage",
    description="Normalize stacktraces/logs, deduplicate RCA incidents, classify fault domain, extract ticket refs, and apply self-heal feedback to UDCP script cache."
)
async def projectbrain_v3_rca_triage(
    stacktrace_or_log: str,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    test_intent_id: Optional[str] = None,
    cache_id: Optional[str] = None,
) -> str:
    """Run deterministic RCA triage and deduplication via brain.rca."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        target_proj, _ = _resolve_v3_project_branch(project_id)
        payload = {
            "project_id": target_proj,
            "stacktrace_or_log": stacktrace_or_log,
            "run_id": run_id or "",
            "test_intent_id": test_intent_id,
            "cache_id": cache_id,
        }

        if is_pb_remote_mode():
            res = await _pb_remote_v3_request("POST", "/v3/rca/triage", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        from ..plugins.registry import get_default_registry
        from ..plugins.base import PluginCategory

        registry = get_default_registry()
        rca = registry.resolve(
            PluginCategory.BRAIN_CONSUMER,
            preferred_id="brain.rca",
            project_id=target_proj,
        )
        res = await rca.execute(payload, registry=registry)
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_v3_rca_triage: {str(e)}"


@mcp_server.tool(
    name="projectbrain_database_topology",
    description="Inspect read-only Master DB -> Project DB -> Branch Schema topology and node/doc/memory counts.",
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def projectbrain_database_topology(
    project_id: Optional[str] = None,
) -> str:
    """Return read-only Master -> Project DB -> Branch Schema database topology."""
    try:
        if is_pb_remote_mode():
            res = await _pb_remote_v3_request(
                "GET",
                "/master/topology",
                params={"project_id": project_id} if project_id else None,
            )
            if isinstance(res, dict):
                for proj_item in res.get("projects") or []:
                    if isinstance(proj_item, dict):
                        proj_item.setdefault("sources", [])
                        proj_item.setdefault("shared_doc_count", 0)
                        proj_item.setdefault("relation_count", 0)
            return json.dumps(res, default=str, indent=2)

        from ..server.routes.master_topology import collect_master_topology

        topo = collect_master_topology(project_id=project_id)
        if isinstance(topo, dict):
            for proj_item in topo.get("projects") or []:
                if isinstance(proj_item, dict):
                    proj_item.setdefault("sources", [])
                    proj_item.setdefault("shared_doc_count", 0)
                    proj_item.setdefault("relation_count", 0)
        return json.dumps(topo, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_database_topology: {str(e)}"


@mcp_server.tool(
    name="projectbrain_register_source",
    description="Register or update a sub-source (service, web frontend, mobile app, worker, infra) under a ProjectBrain multi-source project.",
    annotations=ToolAnnotations(readOnlyHint=False),
)
async def projectbrain_register_source(
    project_id: str,
    source_id: str,
    display_name: Optional[str] = None,
    source_type: str = "service",
    tech_stack: Optional[List[str]] = None,
    repo_url: Optional[str] = None,
    workspace_subpath: Optional[str] = None,
    current_version: str = "1.0.0",
    inherit_shared_docs: bool = True,
) -> str:
    """Register or update a sub-source within `project_id`."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..core.tenant_manager import parse_tenant_from_user_id, tenant_manager

        base_proj = parse_tenant_from_user_id(project_id)[0]
        if is_pb_remote_mode():
            payload: Dict[str, Any] = {
                "project_id": base_proj,
                "source_id": source_id,
                "display_name": display_name,
                "source_type": source_type,
                "tech_stack": tech_stack,
                "repo_url": repo_url,
                "workspace_subpath": workspace_subpath,
                "current_version": current_version,
                "inherit_shared_docs": inherit_shared_docs,
            }
            res = await _pb_remote_v3_request("POST", "/master/sources", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        source = tenant_manager.register_project_source(
            project_id=base_proj,
            source_id=source_id,
            display_name=display_name,
            source_type=source_type,
            tech_stack=tech_stack,
            repo_url=repo_url,
            workspace_subpath=workspace_subpath,
            current_version=current_version,
            inherit_shared_docs=inherit_shared_docs,
        )
        return json.dumps({"status": "ok", "source": source}, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_register_source: {str(e)}"


@mcp_server.tool(
    name="projectbrain_list_sources",
    description="List all registered sub-sources for a ProjectBrain multi-source project.",
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def projectbrain_list_sources(
    project_id: str,
) -> str:
    """List registered sub-sources for `project_id`."""
    try:
        from ..core.tenant_manager import parse_tenant_from_user_id, tenant_manager

        base_proj = parse_tenant_from_user_id(project_id)[0]
        if is_pb_remote_mode():
            res = await _pb_remote_v3_request(
                "GET",
                "/master/sources",
                params={"project_id": base_proj},
            )
            return json.dumps(res, default=str, indent=2)

        sources = tenant_manager.list_project_sources(base_proj)
        return json.dumps(
            {
                "status": "ok",
                "project_id": base_proj,
                "sources": sources,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_list_sources: {str(e)}"


@mcp_server.tool(
    name="projectbrain_link_sources",
    description="Create or update a typed cross-source relation edge (calls_api, shares_schema, consumes_events, deploys, depends_on, documented_by) between two sources.",
    annotations=ToolAnnotations(readOnlyHint=False),
)
async def projectbrain_link_sources(
    project_id: str,
    src_source_id: str,
    dst_source_id: str,
    relation_type: str,
    src_branch: str = "main",
    dst_branch: str = "main",
    contract_key: str = "",
    contract_metadata: Optional[Dict[str, Any]] = None,
    pin: bool = True,
) -> str:
    """Create or update a manual/pinned cross-source relation edge."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..core.tenant_manager import parse_tenant_from_user_id, tenant_manager

        base_proj = parse_tenant_from_user_id(project_id)[0]
        if is_pb_remote_mode():
            payload: Dict[str, Any] = {
                "project_id": base_proj,
                "src_source_id": src_source_id,
                "dst_source_id": dst_source_id,
                "relation_type": relation_type,
                "src_branch": src_branch,
                "dst_branch": dst_branch,
                "contract_key": contract_key,
                "contract_metadata": contract_metadata,
                "pin": pin,
            }
            res = await _pb_remote_v3_request("POST", "/master/relations", json_body=payload)
            return json.dumps(res, default=str, indent=2)

        relation = tenant_manager.upsert_cross_source_relation(
            project_id=base_proj,
            src_source_id=src_source_id,
            dst_source_id=dst_source_id,
            relation_type=relation_type,
            src_branch=src_branch,
            dst_branch=dst_branch,
            origin="manual",
            confidence="MANUAL",
            contract_key=contract_key,
            contract_metadata=contract_metadata,
            status="pinned" if pin else "active",
            created_by="mcp",
        )
        return json.dumps({"status": "ok", "relation": relation}, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_link_sources: {str(e)}"


@mcp_server.tool(
    name="projectbrain_auto_link_sources",
    description="Automatically extract contract surfaces (APIs, schemas, events, infra, shared docs) across registered sources and reconcile cross-source relations and drift.",
    annotations=ToolAnnotations(readOnlyHint=False),
)
async def projectbrain_auto_link_sources(
    project_id: str,
    source_id: Optional[str] = None,
    branch: str = "main",
    workspace_map: Optional[Dict[str, str]] = None,
) -> str:
    """Extract contract surfaces across project sources and reconcile cross-source relations."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..core.tenant_manager import parse_tenant_from_user_id

        base_proj = parse_tenant_from_user_id(project_id)[0]
        if is_pb_remote_mode():
            payload: Dict[str, Any] = {
                "project_id": base_proj,
                "source_id": source_id,
                "branch": branch,
                "workspace_map": workspace_map,
            }
            res = await _pb_remote_v3_request(
                "POST", "/master/relations/auto-link", json_body=payload, timeout=120.0
            )
            return json.dumps(res, default=str, indent=2)

        validated_map: Optional[Dict[str, str]] = None
        if workspace_map is not None:
            validated_map = {}
            for sid, raw_path in workspace_map.items():
                clean_sid = str(sid or "").strip()
                if not clean_sid:
                    return "Error executing projectbrain_auto_link_sources: workspace_map keys must be non-empty source_ids."
                validated_map[clean_sid] = str(_validate_mcp_safe_path(raw_path))

        from ..graphify_engine.cross_source_linker import CrossSourceLinker

        linker = CrossSourceLinker()
        res = linker.auto_link_project(
            project_id=base_proj,
            source_id=source_id,
            branch=branch,
            workspace_map=validated_map,
        )
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_auto_link_sources: {str(e)}"


@mcp_server.tool(
    name="projectbrain_cross_source_impact",
    description="Analyze read-only cross-source blast radius and impacted downstream/upstream services, schemas, events, and shared specs for a symbol, endpoint, or contract.",
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def projectbrain_cross_source_impact(
    project_id: str,
    target: str,
    source_id: Optional[str] = None,
    branch: str = "main",
    relation_depth: int = 2,
) -> str:
    """Compute cross-source impact radius for `target` across `cross_source_relations`."""
    try:
        from collections import deque
        from ..core.tenant_manager import parse_tenant_spec, tenant_manager

        base_proj, parsed_src, parsed_br = parse_tenant_spec(
            project_id, default_branch=branch or "main", default_source=source_id
        )
        eff_source = (
            str(source_id).strip()
            if isinstance(source_id, str) and source_id.strip()
            else (parsed_src if parsed_src != "__default__" else None)
        )
        if eff_source == "__default__":
            eff_source = None
        eff_branch = branch if branch != "main" else parsed_br
        max_depth = max(0, int(relation_depth))

        if is_pb_remote_mode():
            rel_resp = await _pb_remote_v3_request(
                "GET",
                "/master/relations",
                params={"project_id": base_proj, "branch": eff_branch},
            )
            all_rels = list((rel_resp or {}).get("relations") or [])
            bind_resp = await _pb_remote_v3_request(
                "GET",
                "/sources/shared-bindings",
                params={"project_id": base_proj},
            )
            all_bindings = list((bind_resp or {}).get("bindings") or [])
        else:
            all_rels = tenant_manager.list_cross_source_relations(
                base_proj, branch=eff_branch
            )
            all_bindings = tenant_manager.list_shared_doc_bindings(base_proj)

        active_rels = [r for r in all_rels if r.get("status") != "rejected"]
        t_low = (target or "").strip().lower()

        def _rel_matches_target(rel: Dict[str, Any]) -> bool:
            if not t_low:
                return True
            haystack = " ".join(
                str(rel.get(k) or "")
                for k in (
                    "contract_key",
                    "src_node_id",
                    "dst_node_id",
                    "src_file_path",
                    "dst_doc_key",
                    "relation_type",
                    "contract_metadata_json",
                )
            ).lower()
            return t_low in haystack

        direct_relations: List[Dict[str, Any]] = []
        seed_sources: set = set()
        if eff_source:
            seed_sources.add(eff_source)

        for rel in active_rels:
            src_s = str(rel.get("src_source_id") or "")
            dst_s = str(rel.get("dst_source_id") or "")
            if _rel_matches_target(rel):
                if eff_source is None or eff_source in (src_s, dst_s):
                    direct_relations.append(rel)
                    if src_s:
                        seed_sources.add(src_s)
                    if dst_s:
                        seed_sources.add(dst_s)

        visited_sources: set = set(seed_sources)
        propagated_relations: List[Dict[str, Any]] = []
        seen_rel_ids: set = set()
        queue = deque([(s, 0) for s in sorted(seed_sources)])

        while queue:
            curr_src, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for rel in active_rels:
                src_s = str(rel.get("src_source_id") or "")
                dst_s = str(rel.get("dst_source_id") or "")
                if curr_src not in (src_s, dst_s):
                    continue
                if depth == 0 and direct_relations and rel not in direct_relations and eff_source is None:
                    continue
                rid = rel.get("id") or f"{src_s}->{dst_s}:{rel.get('relation_type')}:{rel.get('contract_key')}"
                if rid not in seen_rel_ids:
                    seen_rel_ids.add(rid)
                    propagated_relations.append({**rel, "hop": depth + 1})
                other_s = dst_s if curr_src == src_s else src_s
                if other_s and other_s not in visited_sources:
                    visited_sources.add(other_s)
                    queue.append((other_s, depth + 1))

        drifted_relations = [
            r for r in (propagated_relations or direct_relations)
            if r.get("status") == "drifted"
        ]
        matching_bindings = [
            b for b in all_bindings
            if (
                b.get("target_source_id") in (visited_sources | {"*"})
                or (t_low and t_low in str(b.get("doc_key") or "").lower())
            )
        ]

        intra_source_impact: Optional[Dict[str, Any]] = None
        if not is_pb_remote_mode():
            resolver = get_hybrid_resolver()
            try:
                graph_target = _format_graph_target(base_proj, eff_source)
                cand_impact = resolver.get_impact_radius(
                    project_id=graph_target,
                    node_id=target,
                    max_hops=max(1, max_depth),
                    branch=eff_branch,
                )
                if isinstance(cand_impact, dict) and cand_impact.get("found"):
                    intra_source_impact = cand_impact
            except Exception:
                intra_source_impact = None
            finally:
                if getattr(resolver, "store", None) is not None:
                    resolver.store.close()

        return json.dumps(
            {
                "status": "ok",
                "project_id": base_proj,
                "target": target,
                "source_id": eff_source,
                "branch": eff_branch,
                "relation_depth": max_depth,
                "affected_sources": sorted(visited_sources),
                "direct_relations": direct_relations,
                "propagated_relations": propagated_relations,
                "drifted_relations": drifted_relations,
                "shared_doc_bindings": matching_bindings,
                "intra_source_impact": intra_source_impact,
            },
            default=str,
            indent=2,
        )
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_cross_source_impact: {str(e)}"


@mcp_server.tool(
    name="projectbrain_bind_shared_doc",
    description="Bind a project-shared document (doc_key) to '*' (all sources) or a specific sub-source, optionally pinning a document version.",
    annotations=ToolAnnotations(readOnlyHint=False),
)
async def projectbrain_bind_shared_doc(
    project_id: str,
    doc_key: str,
    target_source_id: str = "*",
    pinned_version: Optional[str] = None,
    binding_role: str = "reference",
) -> str:
    """Bind a shared document to `'*'` or a specific sub-source."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        from ..core.tenant_manager import parse_tenant_from_user_id, tenant_manager

        base_proj = parse_tenant_from_user_id(project_id)[0]
        if is_pb_remote_mode():
            payload: Dict[str, Any] = {
                "project_id": base_proj,
                "doc_key": doc_key,
                "target_source_id": target_source_id,
                "pinned_version": pinned_version,
                "binding_role": binding_role,
            }
            res = await _pb_remote_v3_request(
                "POST", "/sources/shared-bindings", json_body=payload
            )
            return json.dumps(res, default=str, indent=2)

        binding = tenant_manager.bind_shared_doc(
            project_id=base_proj,
            doc_key=doc_key,
            target_source_id=target_source_id,
            pinned_version=pinned_version,
            binding_role=binding_role,
        )
        return json.dumps({"status": "ok", "binding": binding}, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_bind_shared_doc: {str(e)}"


@mcp_server.tool(
    name="projectbrain_discover_workspace_sources",
    description="Scan a workspace directory (read-only) to auto-detect sub-sources (backend, frontend, worker, infra) and project-wide shared docs/contracts before bootstrapping.",
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def projectbrain_discover_workspace_sources(
    workspace_root: str = ".",
) -> str:
    """Auto-discover sub-sources and shared docs inside `workspace_root`."""
    try:
        safe_root = _validate_mcp_safe_path(workspace_root)
        if is_pb_remote_mode():
            res = await _pb_remote_v3_request(
                "POST",
                "/master/hub/discover",
                json_body={"workspace_root": workspace_root},
            )
            return json.dumps(res, default=str, indent=2)

        from ..graphify_engine.hub_bootstrap import discover_workspace_sources

        res = discover_workspace_sources(str(safe_root))
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_discover_workspace_sources: {str(e)}"


@mcp_server.tool(
    name="projectbrain_bootstrap_project_hub",
    description="Zero-touch one-shot ProjectBrain Multi-Source Hub setup: ensure project DB, auto-discover & register sub-sources, sync Graphify ASTs + HSG memories, ingest shared docs, and auto-link cross-source contracts.",
    annotations=ToolAnnotations(readOnlyHint=False),
)
async def projectbrain_bootstrap_project_hub(
    project_id: str,
    workspace_root: str = ".",
    sources: Optional[List[Dict[str, Any]]] = None,
    shared_docs: Optional[List[Dict[str, Any]]] = None,
    relations: Optional[List[Dict[str, Any]]] = None,
    auto_discover: bool = True,
    sync_graphs: bool = True,
    auto_link: bool = True,
    upgrade_mode: str = "delta",
    root_branch: str = "main",
    author: str = "agent",
    description: Optional[str] = None,
) -> str:
    """One-shot zero-touch multi-source project hub provisioning, syncing, and cross-linking."""
    try:
        if check_read_only():
            return "Error: Operation not permitted. The ProjectBrain server is currently running in Read-Only mode."

        safe_root = _validate_mcp_safe_path(workspace_root)
        if sources:
            for s in sources:
                if isinstance(s, dict):
                    raw_p = s.get("path") or s.get("workspace_subpath")
                    if isinstance(raw_p, str) and raw_p.strip() and Path(raw_p.strip()).is_absolute():
                        _validate_mcp_safe_path(raw_p)
        if shared_docs:
            for d in shared_docs:
                if isinstance(d, dict):
                    raw_p = d.get("path")
                    if isinstance(raw_p, str) and raw_p.strip() and Path(raw_p.strip()).is_absolute():
                        _validate_mcp_safe_path(raw_p)

        if is_pb_remote_mode():
            payload: Dict[str, Any] = {
                "project_id": project_id,
                "workspace_root": workspace_root,
                "sources": sources,
                "shared_docs": shared_docs,
                "relations": relations,
                "auto_discover": auto_discover,
                "sync_graphs": sync_graphs,
                "auto_link": auto_link,
                "upgrade_mode": upgrade_mode,
                "root_branch": root_branch,
                "author": author,
                "description": description,
            }
            res = await _pb_remote_v3_request(
                "POST",
                "/master/hub/bootstrap",
                json_body=payload,
                timeout=180.0,
            )
            return json.dumps(res, default=str, indent=2)

        from ..graphify_engine.hub_bootstrap import bootstrap_project_hub

        res = await bootstrap_project_hub(
            project_id=project_id,
            workspace_root=str(safe_root),
            sources=sources,
            shared_docs=shared_docs,
            relations=relations,
            auto_discover=auto_discover,
            sync_graphs=sync_graphs,
            auto_link=auto_link,
            upgrade_mode=upgrade_mode,
            root_branch=root_branch,
            author=author,
            description=description,
        )
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_bootstrap_project_hub: {str(e)}"


@mcp_server.tool(
    name="projectbrain_get_agent_setup_playbook",
    description="Return a zero-touch self-setup playbook, copy-paste MCP sequence, and agent rules snippet (Claude Code, Cursor, Antigravity, Codex) for ProjectBrain Multi-Source Hub.",
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def projectbrain_get_agent_setup_playbook(
    project_id: Optional[str] = None,
    workspace_root: str = ".",
) -> str:
    """Return structured multi-source onboarding steps, MCP sequence, and rules snippet."""
    try:
        safe_root = _validate_mcp_safe_path(workspace_root)
        from ..graphify_engine.hub_bootstrap import build_agent_setup_playbook

        res = build_agent_setup_playbook(
            project_id=project_id,
            workspace_root=str(safe_root),
        )
        return json.dumps(res, default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error executing projectbrain_get_agent_setup_playbook: {str(e)}"


@mcp_server.resource(
    "projectbrain://guides/agent-multi-source-setup",
    name="agent-multi-source-setup",
    description="Zero-touch onboarding playbook and rules snippet for external AI agents (Claude Code, Cursor, Antigravity, Codex) using ProjectBrain Multi-Source Hub.",
    mime_type="application/json",
)
def agent_multi_source_setup_resource() -> str:
    """Serve the ProjectBrain Multi-Source Hub zero-touch setup playbook as an MCP resource."""
    from ..graphify_engine.hub_bootstrap import build_agent_setup_playbook

    return json.dumps(build_agent_setup_playbook(), default=str, indent=2)


# Dynamically register custom extensions
try:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from extensions_mcp.loader import load_extensions
    load_extensions(mcp_server)
except Exception as e:
    logger.error(f"Failed to load custom MCP extensions: {e}")
    logger.error(traceback.format_exc())

async def run_mcp_server():
    await mcp_server.run_stdio_async()


