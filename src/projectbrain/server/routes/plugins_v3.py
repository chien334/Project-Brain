"""FastAPI Microkernel Management (/plugins/*) & v3.0 Testing KB (/v3/*) REST Routes."""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import auth
from ...core.config import env
from ...core.db import db
from ...graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from ...graphify_engine.store import (
    GraphStore,
    _map_graph_backend_to_plugin_id,
    get_graph_store,
)
from ...plugins.registry import PluginRegistry, get_default_registry

logger = logging.getLogger("server.routes.plugins_v3")

router = APIRouter(tags=["plugins-v3"])

_FORMAT_EXT_MAP: Dict[str, str] = {
    "csv": "csv",
    "testcase": "csv",
    "testrail_json": "json",
    "testrail_xml": "xml",
    "xlsx": "xlsx",
    "datasheet": "md",
    "hardware_datasheet": "md",
    "uart_errors": "md",
    "errata": "md",
    "openapi": "yaml",
    "openapi3": "yaml",
    "openapi31": "yaml",
    "swagger": "yaml",
    "cpp": "cpp",
    "c": "c",
    "qt_ui": "ui",
    "linker_ld": "ld",
    "linker_map": "map",
    "markdown": "md",
    "ast": "py",
    "code": "py",
}


# ---------------------------------------------------------------------------
# Security & DB Helpers
# ---------------------------------------------------------------------------
def _check_read_only() -> None:
    """Raise 403 Forbidden if server is configured in Read-Only mode."""
    if (
        os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes")
        or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes")
    ):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted: Server is running in Read-Only mode.",
        )


def _validate_safe_path(
    path_str: Optional[str],
    field_name: str = "workspace_path",
    allow_none: bool = False,
) -> Optional[Path]:
    """Validate that path resolves strictly inside workspace root (cwd) to prevent path traversal."""
    if path_str is None:
        if allow_none:
            return None
        return Path.cwd().resolve()

    clean_str = str(path_str).strip()
    if clean_str == "":
        if allow_none:
            return None
        return Path.cwd().resolve()
    if clean_str == ".":
        return Path.cwd().resolve()

    try:
        raw_path = Path(clean_str)
        if ".." in raw_path.parts:
            raise ValueError("Parent directory traversal ('..') is not allowed")
        resolved = raw_path.resolve()
        cwd = Path.cwd().resolve()
        resolved.relative_to(cwd)
        return resolved
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or out-of-workspace {field_name}: path traversal detected",
        )


_SENSITIVE_KEY_TOKENS = (
    "secret",
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "authorization",
)


def _accepts_kwarg(fn: Any, kwarg_name: str) -> bool:
    """Return True if callable accepts keyword argument kwarg_name or **kwargs."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return kwarg_name in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


def _is_sensitive_config_key(key: str) -> bool:
    kl = str(key or "").strip().lower()
    if not kl:
        return False
    if any(tok in kl for tok in _SENSITIVE_KEY_TOKENS):
        return True
    return kl in ("auth", "basic_auth", "bearer_auth", "http_auth")


def _redact_dsn_value(val: str) -> str:
    redacted = re.sub(
        r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s]+)@",
        r"\1***@",
        val,
    )
    redacted = re.sub(
        r"(?i)\b(password|passwd|pwd|secret|token|api_key)\s*=\s*([^\s;]+)",
        r"\1=***",
        redacted,
    )
    return redacted


def _redact_plugin_config(obj: Any, *, parent_sensitive: bool = False) -> Any:
    """Return a non-mutating copy of plugin config with secrets and DSN credentials masked."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            is_sens = parent_sensitive or _is_sensitive_config_key(ks)
            if is_sens:
                if v is None:
                    out[ks] = None
                elif isinstance(v, (dict, list, tuple)):
                    out[ks] = _redact_plugin_config(v, parent_sensitive=True)
                else:
                    out[ks] = "***"
            else:
                out[ks] = _redact_plugin_config(v, parent_sensitive=False)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact_plugin_config(item, parent_sensitive=parent_sensitive) for item in obj]
    if parent_sensitive:
        return None if obj is None else "***"
    if isinstance(obj, str) and ("://" in obj or "password=" in obj.lower() or "secret=" in obj.lower()):
        return _redact_dsn_value(obj)
    return obj


def _nonempty_secret(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _validate_reload_directory(raw_dir: str, reg: PluginRegistry) -> Path:
    """Restrict /plugins/reload directory to configured plugins_dir (inside cwd) or registered _plugin_dirs."""
    clean_str = str(raw_dir).strip()
    if not clean_str:
        raise HTTPException(
            status_code=400,
            detail="Invalid or out-of-workspace directory: path traversal detected",
        )
    raw_path = Path(clean_str)
    if ".." in raw_path.parts or ".." in clean_str.replace("\\", "/").split("/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid or out-of-workspace directory: path traversal detected",
        )

    cwd = Path.cwd().resolve()
    candidate = raw_path.expanduser().resolve()

    # 1. Allow already-registered plugin directories on the active registry
    registered_dirs = [
        Path(d).expanduser().resolve()
        for d in (getattr(reg, "_plugin_dirs", None) or [])
    ]
    for reg_dir in registered_dirs:
        try:
            candidate.relative_to(reg_dir)
            return candidate
        except ValueError:
            continue

    # 2. Restrict unregistered directories strictly to configured plugins_dir inside cwd
    configured_raw = (
        os.getenv("PB_PLUGINS_DIR")
        or os.getenv("OM_PLUGINS_DIR")
        or getattr(env, "plugins_dir", None)
        or "plugins"
    )
    configured_path = Path(configured_raw)
    if ".." in configured_path.parts:
        raise HTTPException(
            status_code=400,
            detail="Invalid or out-of-workspace directory: path traversal detected",
        )
    configured_dir = configured_path.expanduser().resolve()
    try:
        configured_dir.relative_to(cwd)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid or out-of-workspace directory: path traversal detected",
        )

    try:
        candidate.relative_to(cwd)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid or out-of-workspace directory: path traversal detected",
        )

    try:
        candidate.relative_to(configured_dir)
        return candidate
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid plugin reload directory '{raw_dir}': must resolve inside "
                f"configured plugins directory '{configured_dir}' or registered plugin directories"
            ),
        )


def _get_db_path() -> Optional[str]:
    """Retrieve configured SQLite DB path for stateful v3 plugins and GraphStore."""
    db_path = (
        os.getenv("PB_GRAPH_DB_PATH")
        or os.getenv("OM_GRAPH_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or os.getenv("OM_DB_PATH")
    )
    if not db_path:
        db_url = getattr(env, "database_url", "") or ""
        if db_url.startswith("sqlite:///"):
            db_path = db_url.replace("sqlite:///", "", 1)
    return db_path or None


def _has_explicit_db_override(
    reg: PluginRegistry,
    plugin_id: str,
    project_id: Optional[str] = None,
) -> bool:
    db_keys = {"db_path", "db", "use_core_db", "database_url", "conn"}
    global_ov = getattr(reg, "_global_config_overrides", {}).get(plugin_id, {})
    if any(k in global_ov for k in db_keys):
        return True
    if project_id:
        proj_ov = (
            getattr(reg, "_project_overrides", {})
            .get(project_id, {})
            .get(plugin_id, {})
            .get("config", {})
        )
        if any(k in proj_ov for k in db_keys):
            return True
    return False


def _get_registry_and_bind_db(project_id: Optional[str] = None) -> PluginRegistry:
    """Return active PluginRegistry and synchronize stateful built-in plugins to server DB."""
    reg = get_default_registry()
    db_url = getattr(env, "database_url", "") or ""
    is_pg = db_url.startswith(("postgresql://", "postgres://"))
    db_path = None if is_pg else _get_db_path()

    if is_pg:
        target_sig = ("pg", db_url)
    elif db_path:
        target_sig = ("sqlite", str(db_path))
    else:
        target_sig = ("default", None)

    for pid in (
        "cache.sqlite_pg",
        "artifacts.local_sqlite",
        "brain.rca",
        "graph.sqlite_overlay",
        "brain.planner",
        "brain.oracle",
    ):
        plugin = reg.get(pid)
        if plugin is None:
            continue
        if _has_explicit_db_override(reg, pid, project_id=project_id):
            continue
        if getattr(plugin, "_pb_auto_db_sig", None) == target_sig:
            continue

        if is_pg and pid in ("cache.sqlite_pg", "artifacts.local_sqlite", "brain.rca"):
            if db.conn is None:
                db.connect()
            plugin.on_load({"db": db})
        elif db_path:
            if pid in ("cache.sqlite_pg", "artifacts.local_sqlite", "brain.rca"):
                plugin.on_load({"db_path": db_path})
            else:
                plugin.config["db_path"] = db_path
        setattr(plugin, "_pb_auto_db_sig", target_sig)

    return reg


def _get_store(
    project_id: Optional[str] = None,
    backend: Optional[str] = None,
    registry: Optional[PluginRegistry] = None,
) -> GraphStore:
    return get_graph_store(
        db_path=_get_db_path(),
        backend=backend,
        project_id=project_id,
        registry=registry,
    )


def _merge_delta_dict(
    target: GraphDelta,
    raw_delta: Optional[Dict[str, Any]],
    project_id: str,
) -> None:
    if not isinstance(raw_delta, dict):
        return
    seen_nodes = {n.id for n in target.nodes}
    for raw_n in raw_delta.get("nodes") or []:
        node_obj = (
            raw_n
            if isinstance(raw_n, GraphNode)
            else GraphNode(**{**raw_n, "project_id": raw_n.get("project_id") or project_id})
        )
        if node_obj.id not in seen_nodes:
            target.nodes.append(node_obj)
            seen_nodes.add(node_obj.id)

    seen_edges = {(e.source, e.target, e.relation) for e in target.edges}
    for raw_e in raw_delta.get("edges") or []:
        edge_obj = (
            raw_e
            if isinstance(raw_e, GraphEdge)
            else GraphEdge(**{**raw_e, "project_id": raw_e.get("project_id") or project_id})
        )
        ek = (edge_obj.source, edge_obj.target, edge_obj.relation)
        if ek not in seen_edges:
            target.edges.append(edge_obj)
            seen_edges.add(ek)


def _select_etl_parser(
    reg: PluginRegistry,
    project_id: str,
    source_name: str,
    format_hint: Optional[str],
    preview: str,
    preferred_plugin_id: Optional[str] = None,
) -> Any:
    if preferred_plugin_id:
        try:
            plugin = reg.resolve(
                "etl_parser",
                preferred_id=preferred_plugin_id,
                project_id=project_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        if plugin.metadata.plugin_id != preferred_plugin_id:
            raise HTTPException(
                status_code=404,
                detail=f"Requested ETL parser plugin '{preferred_plugin_id}' is not enabled or registered",
            )
        return plugin

    candidates = reg.resolve_all("etl_parser", project_id=project_id)
    hint_lower = (format_hint or "").strip().lower()

    if hint_lower:
        for parser in candidates:
            fmts = tuple(str(f).lower() for f in getattr(parser, "supported_formats", ()))
            caps = tuple(str(c).lower() for c in getattr(parser.metadata, "capabilities", ()))
            if hint_lower in fmts or hint_lower in caps or hint_lower == parser.metadata.plugin_id.lower():
                return parser

    generic_ids = {"etl.graphify_ast", "etl.multimodal_doc"}
    specialized = [p for p in candidates if p.metadata.plugin_id not in generic_ids]
    generic = [p for p in candidates if p.metadata.plugin_id in generic_ids]

    for parser in specialized + generic:
        try:
            if parser.can_parse(source_name, format_hint=format_hint, content_preview=preview):
                return parser
        except Exception:
            continue

    raise HTTPException(
        status_code=400,
        detail=f"No enabled ETL parser plugin could handle '{source_name}' (format_hint={format_hint!r})",
    )


# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------
class ReloadPluginsRequest(BaseModel):
    directory: Optional[str] = None


class PluginToggleRequest(BaseModel):
    project_id: Optional[str] = None


class PluginConfigRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    project_id: Optional[str] = None
    priority: Optional[int] = None


class V3ETLIngestRequest(BaseModel):
    project_id: str = "default"
    branch: str = "main"
    version_tag: Optional[str] = None
    file_path: Optional[str] = None
    content: Optional[str] = None
    source_name: Optional[str] = None
    format_hint: Optional[str] = None
    plugin_id: Optional[str] = None
    extract_flow: bool = True
    sync_to_store: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScriptCacheLookupRequest(BaseModel):
    project_id: str = "default"
    intent_id: str
    platform: str
    build_version: str
    screen_hash: Optional[str] = None
    include_invalidated: bool = False
    ui_fingerprint: Optional[Dict[str, Any]] = None


class ScriptCacheStoreRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    cache_id: Optional[str] = None
    project_id: str = "default"
    intent_id: str
    platform: str
    build_version: str
    screen_hash: str = ""
    ui_fingerprint: Dict[str, Any] = Field(default_factory=dict)
    udcp_commands: List[Dict[str, Any]] = Field(default_factory=list)
    ttl_seconds: int = 2592000


class ScriptCacheFeedbackRequest(BaseModel):
    project_id: str = "default"
    cache_id: str
    success: bool
    fallback_reason: Optional[str] = None
    self_heal_patch: Optional[Dict[str, Any]] = None


class ScriptCacheInvalidateRequest(BaseModel):
    project_id: str = "default"
    cache_id: str
    reason: str = "manual_invalidation"


class ArtifactIndexRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifact_id: Optional[str] = None
    run_id: str
    step_id: str
    project_id: str = "default"
    device_id: str = ""
    build_version: str = ""
    artifact_type: str
    ntp_timestamp_ms: int
    duration_ms: int = 0
    uri: str
    sha256: str = ""
    size_bytes: int = 0
    summary_metrics: Dict[str, Any] = Field(default_factory=dict)
    retention_days: int = 30
    expires_at: Optional[int] = None


class ArtifactPurgeRequest(BaseModel):
    project_id: Optional[str] = None
    now_ms: Optional[int] = None


class PlannerTestDAGRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: str = "default"
    branch: str = "main"
    base_branch: str = "main"
    version_tag: Optional[str] = None
    srs_diff: str = ""
    code_diff_files: List[str] = Field(default_factory=list)
    artifacts: List[Any] = Field(default_factory=list)
    target_devices: List[str] = Field(default_factory=list)
    platform: Optional[str] = None
    build_version: Optional[str] = None
    max_depth: int = 4
    intents: Optional[List[Dict[str, Any]]] = None
    graph_data: Optional[Dict[str, Any]] = None


class OracleContextRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: str = "default"
    branch: str = "main"
    version_tag: Optional[str] = None
    test_intent_id: Optional[str] = None
    screen_id: Optional[str] = None
    symbols: List[str] = Field(default_factory=list)
    telemetry_summary: Dict[str, Any] = Field(default_factory=dict)
    error_rules: Optional[List[Dict[str, Any]]] = None
    expected_transitions: Optional[List[Dict[str, Any]]] = None
    srs_criteria: Optional[List[Any]] = None
    acceptance_criteria: Optional[List[Any]] = None
    power_budget_ma: Optional[float] = None


class RCATriageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: str = "default"
    run_id: str = ""
    stacktrace_or_log: str = ""
    failure_artifacts: List[Any] = Field(default_factory=list)
    test_intent_id: Optional[str] = None
    screen_hash: Optional[str] = None
    cache_id: Optional[str] = None
    ticket_refs: Optional[List[str]] = None
    invalidate_on_flaky: bool = True
    self_heal_patch: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 1. Plugin Microkernel Management Endpoints (/plugins/*)
# ---------------------------------------------------------------------------
@router.get("/plugins")
async def list_plugins(
    category: Optional[str] = None,
    project_id: Optional[str] = None,
    include_disabled: bool = True,
):
    """List registered plugins, categories, priorities, health, and per-project enablement."""
    reg = _get_registry_and_bind_db(project_id=project_id)
    plugins = reg.list_plugins(
        category=category,
        project_id=project_id,
        include_disabled=include_disabled,
    )
    redacted_plugins: List[Dict[str, Any]] = []
    for p in plugins:
        p_copy = dict(p)
        p_copy["config"] = _redact_plugin_config(p_copy.get("config") or {})
        redacted_plugins.append(p_copy)
    health = reg.health_report(project_id=project_id)
    return {
        "status": health.get("status", "healthy"),
        "project_id": project_id,
        "total_plugins": len(redacted_plugins),
        "enabled_plugins": health.get("enabled_plugins", 0),
        "by_category": health.get("by_category", {}),
        "plugins": redacted_plugins,
        "health": health.get("plugins", {}),
    }


@router.post("/plugins/reload")
async def reload_plugins(req: Optional[ReloadPluginsRequest] = Body(default=None)):
    """Hot-reload plugin directory (restricted to configured plugins_dir inside cwd or registered _plugin_dirs)."""
    _check_read_only()
    reg = _get_registry_and_bind_db()
    reloaded: List[str] = []
    reloaded_dirs: List[str] = []

    if req is not None and req.directory is not None:
        safe_dir = _validate_reload_directory(req.directory, reg)
        reloaded.extend(reg.reload_directory(safe_dir))
        reloaded_dirs.append(str(safe_dir))
    else:
        for d in list(getattr(reg, "_plugin_dirs", [])):
            reloaded.extend(reg.reload_directory(d))
            reloaded_dirs.append(str(d))
        if not reloaded_dirs:
            env_dir = os.getenv("PB_PLUGINS_DIR") or getattr(env, "plugins_dir", None)
            if env_dir and Path(env_dir).expanduser().is_dir():
                resolved_env_dir = Path(env_dir).expanduser().resolve()
                reloaded.extend(reg.reload_directory(resolved_env_dir))
                reloaded_dirs.append(str(resolved_env_dir))

    return {
        "status": "ok",
        "reloaded_plugins": reloaded,
        "directories": reloaded_dirs,
    }


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    project_id: Optional[str] = Query(default=None),
    req: Optional[PluginToggleRequest] = Body(default=None),
):
    """Enable a registered plugin globally or for a specific project_id."""
    _check_read_only()
    eff_project_id = (req.project_id if req and req.project_id is not None else project_id)
    reg = _get_registry_and_bind_db(project_id=eff_project_id)
    if reg.get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    reg.enable(plugin_id, project_id=eff_project_id)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "project_id": eff_project_id,
        "enabled": reg.is_enabled(plugin_id, project_id=eff_project_id),
    }


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    project_id: Optional[str] = Query(default=None),
    req: Optional[PluginToggleRequest] = Body(default=None),
):
    """Disable a registered plugin globally or for a specific project_id."""
    _check_read_only()
    eff_project_id = (req.project_id if req and req.project_id is not None else project_id)
    reg = _get_registry_and_bind_db(project_id=eff_project_id)
    if reg.get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    reg.disable(plugin_id, project_id=eff_project_id)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "project_id": eff_project_id,
        "enabled": reg.is_enabled(plugin_id, project_id=eff_project_id),
    }


@router.put("/plugins/{plugin_id}/config")
async def configure_plugin(plugin_id: str, req: PluginConfigRequest):
    """Update plugin configuration and optional priority globally or per-project."""
    _check_read_only()
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    if reg.get(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    updated_cfg = reg.configure(
        plugin_id,
        req.config,
        project_id=req.project_id,
        priority=req.priority,
    )
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "project_id": req.project_id,
        "priority": reg.get_effective_priority(plugin_id, project_id=req.project_id),
        "config": _redact_plugin_config(updated_cfg),
    }


# ---------------------------------------------------------------------------
# 2. v3.0 Layer 1 ETL & Flow Graph Endpoints (/v3/etl/*, /v3/flow-graph)
# ---------------------------------------------------------------------------
@router.post("/v3/etl/ingest")
async def v3_etl_ingest(req: V3ETLIngestRequest):
    """Ingest workspace file or inline content via matching ETLParserPlugin + StateFlowGraphPlugin."""
    _check_read_only()
    if req.file_path is None and req.content is None:
        raise HTTPException(
            status_code=400,
            detail="Either 'file_path' or 'content' must be provided",
        )

    reg = _get_registry_and_bind_db(project_id=req.project_id)
    source_input: Any
    effective_name: str
    preview = ""
    text_for_flow: Optional[str] = None
    explicit_source_key: Optional[str] = None

    if req.file_path is not None:
        safe_path = _validate_safe_path(req.file_path, "file_path", allow_none=False)
        if safe_path is None or not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"Source path not found: {req.file_path}")
        source_input = str(safe_path)
        try:
            rel_name = str(safe_path.relative_to(Path.cwd().resolve())).replace("\\", "/")
        except ValueError:
            rel_name = safe_path.name
        effective_name = req.source_name or rel_name
        explicit_source_key = effective_name
        if safe_path.is_file() and safe_path.suffix.lower() not in (".xlsx", ".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg"):
            try:
                text_for_flow = safe_path.read_text(encoding="utf-8", errors="replace")
                preview = text_for_flow[:1024]
            except Exception:
                preview = ""
    else:
        source_input = req.content or ""
        ext_guess = _FORMAT_EXT_MAP.get((req.format_hint or "").strip().lower(), "txt")
        effective_name = req.source_name or f"inline.{ext_guess}"
        explicit_source_key = req.source_name
        text_for_flow = source_input
        preview = source_input[:1024]

    parser = _select_etl_parser(
        reg=reg,
        project_id=req.project_id,
        source_name=effective_name,
        format_hint=req.format_hint,
        preview=preview,
        preferred_plugin_id=req.plugin_id,
    )

    try:
        parsed = await parser.parse(
            source_input,
            source_name=effective_name,
            project_id=req.project_id,
            branch=req.branch,
            version_tag=req.version_tag,
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("v3 ETL ingest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"ETL ingestion failed: {exc}")

    delta = GraphDelta(project_id=req.project_id, branch=req.branch)
    _merge_delta_dict(delta, parsed.get("graph_delta"), req.project_id)

    flow_nodes = list(parsed.get("flow_nodes") or [])
    flow_edges = list(parsed.get("flow_edges") or [])

    if req.extract_flow and text_for_flow:
        try:
            flow_linker = reg.resolve(
                "knowledge_linker",
                preferred_id="linker.state_flow",
                project_id=req.project_id,
            )
            if flow_linker is not None and hasattr(flow_linker, "link"):
                flow_res = flow_linker.link(
                    project_id=req.project_id,
                    branch=req.branch,
                    graph_data=delta,
                    version_tag=req.version_tag,
                    context={
                        "flow_sources": [
                            {
                                "content": text_for_flow,
                                "source_file": effective_name,
                                "version_tag": req.version_tag,
                            }
                        ]
                    },
                )
                if isinstance(flow_res, dict):
                    flow_delta = flow_res.get("graph_delta")
                    if isinstance(flow_delta, dict):
                        for raw_item in (flow_delta.get("nodes") or []) + (flow_delta.get("edges") or []):
                            if isinstance(raw_item, dict) and (not raw_item.get("source_file") or raw_item.get("source_file") == "state_flow"):
                                raw_item["source_file"] = effective_name
                    _merge_delta_dict(delta, flow_delta, req.project_id)
                    flow_nodes.extend(flow_res.get("flow_nodes") or [])
                    flow_edges.extend(flow_res.get("flow_edges") or [])
        except LookupError:
            pass
        except Exception as exc:
            logger.debug("StateFlow extraction skipped during ETL ingest: %s", exc)

    sync_result: Optional[Dict[str, Any]] = None
    if req.sync_to_store and (delta.nodes or delta.edges):
        synced_file_set = {
            sf
            for sf in (
                [explicit_source_key]
                + [n.source_file for n in delta.nodes]
                + [e.source_file for e in delta.edges]
            )
            if sf
        }
        store = _get_store(project_id=req.project_id, registry=reg)
        try:
            store.ensure_root_schema()
            if not store.is_root_branch(req.branch):
                store.ensure_branch_schema(req.project_id, req.branch)
            sync_result = store.sync_branch_delta(
                project_id=req.project_id,
                branch_name=req.branch,
                delta=delta,
                partial=True,
                synced_files=sorted(synced_file_set) or None,
            )
        finally:
            store.close()

    return {
        "status": "ok",
        "plugin_id": parsed.get("plugin_id") or parser.metadata.plugin_id,
        "project_id": req.project_id,
        "branch": req.branch,
        "version_tag": req.version_tag,
        "source_name": effective_name,
        "canonical_intents": parsed.get("canonical_intents") or [],
        "error_rules": parsed.get("error_rules") or [],
        "flow_nodes": flow_nodes,
        "flow_edges": flow_edges,
        "graph_delta": delta.model_dump() if hasattr(delta, "model_dump") else delta.dict(),
        "sync_result": sync_result,
    }


@router.get("/v3/flow-graph")
async def get_v3_flow_graph(
    project_id: str = "default",
    branch: str = "main",
    version_tag: Optional[str] = None,
    backend: Optional[str] = None,
):
    """Query STATE_NODE, SCREEN_NODE, and TRANSITION_EDGE flow graph filtered by version_tag."""
    reg = _get_registry_and_bind_db(project_id=project_id)
    preferred_id = _map_graph_backend_to_plugin_id(backend) if backend else None
    try:
        graph_plugin = reg.resolve(
            "graph_backend",
            preferred_id=preferred_id,
            project_id=project_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        store = _get_store(project_id=project_id, backend=backend, registry=reg)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        flow = graph_plugin.query_flow_graph(
            project_id=project_id,
            branch=branch,
            version_tag=version_tag,
            store=store,
        )
        nodes = list(flow.get("nodes") or [])
        edges = list(flow.get("edges") or [])
        if version_tag:
            node_ids = {n.get("id") for n in nodes}
            edges = [
                e
                for e in edges
                if (
                    (e.get("metadata") or {}).get("version_tag") == version_tag
                    or (
                        (e.get("metadata") or {}).get("version_tag") is None
                        and e.get("source") in node_ids
                        and e.get("target") in node_ids
                    )
                )
            ]
        return {
            "status": "ok",
            "project_id": project_id,
            "branch": branch,
            "version_tag": version_tag,
            "nodes": nodes,
            "edges": edges,
        }
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 3. v3.0 Layer 1 Verified UDCP Script Cache Endpoints (/v3/script-cache/*)
# ---------------------------------------------------------------------------
@router.post("/v3/script-cache/lookup")
async def v3_script_cache_lookup(req: ScriptCacheLookupRequest):
    """Look up verified UDCP script cache entry (read-only query)."""
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        cache_plugin = reg.resolve("script_cache", project_id=req.project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    kwargs: Dict[str, Any] = {
        "project_id": req.project_id,
        "intent_id": req.intent_id,
        "platform": req.platform,
        "build_version": req.build_version,
        "screen_hash": req.screen_hash,
        "include_invalidated": req.include_invalidated,
    }
    if req.ui_fingerprint is not None:
        sig = inspect.signature(cache_plugin.lookup_script)
        if "ui_fingerprint" in sig.parameters:
            kwargs["ui_fingerprint"] = req.ui_fingerprint

    entry = cache_plugin.lookup_script(**kwargs)
    if inspect.isawaitable(entry):
        entry = await entry
    return {
        "status": "ok",
        "plugin_id": cache_plugin.metadata.plugin_id,
        "cache_hit": bool(entry),
        "entry": entry,
    }


@router.post("/v3/script-cache/store")
async def v3_script_cache_store(req: ScriptCacheStoreRequest):
    """Persist or update a compiled UDCP script cache entry."""
    _check_read_only()
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        cache_plugin = reg.resolve("script_cache", project_id=req.project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    stored = cache_plugin.store_script(payload)
    if inspect.isawaitable(stored):
        stored = await stored
    return {
        "status": "ok",
        "plugin_id": cache_plugin.metadata.plugin_id,
        "entry": stored,
    }


@router.post("/v3/script-cache/feedback")
async def v3_script_cache_feedback(req: ScriptCacheFeedbackRequest):
    """Record execution hit/fallback telemetry and optional self-heal patch."""
    _check_read_only()
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        cache_plugin = reg.resolve("script_cache", project_id=req.project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    fb_kwargs: Dict[str, Any] = {
        "cache_id": req.cache_id,
        "success": req.success,
        "fallback_reason": req.fallback_reason,
        "self_heal_patch": req.self_heal_patch,
    }
    if req.project_id is not None and _accepts_kwarg(cache_plugin.record_execution, "project_id"):
        fb_kwargs["project_id"] = req.project_id
    res = cache_plugin.record_execution(**fb_kwargs)
    if inspect.isawaitable(res):
        res = await res
    if isinstance(res, dict) and res.get("found") is False:
        raise HTTPException(status_code=404, detail=f"Script cache entry '{req.cache_id}' not found")
    return {
        "status": "ok",
        "plugin_id": cache_plugin.metadata.plugin_id,
        **(res if isinstance(res, dict) else {"result": res}),
    }


@router.post("/v3/script-cache/invalidate")
async def v3_script_cache_invalidate(req: ScriptCacheInvalidateRequest):
    """Invalidate a cached UDCP script entry."""
    _check_read_only()
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        cache_plugin = reg.resolve("script_cache", project_id=req.project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if req.project_id is not None and _accepts_kwarg(cache_plugin.invalidate_script, "project_id"):
        ok = cache_plugin.invalidate_script(req.cache_id, req.reason, project_id=req.project_id)
    else:
        ok = cache_plugin.invalidate_script(req.cache_id, req.reason)
    if inspect.isawaitable(ok):
        ok = await ok
    return {
        "status": "ok" if ok else "not_found",
        "plugin_id": cache_plugin.metadata.plugin_id,
        "cache_id": req.cache_id,
        "invalidated": bool(ok),
        "reason": req.reason,
    }


# ---------------------------------------------------------------------------
# 4. v3.0 Layer 1 Synchronized Telemetry Artifacts (/v3/artifacts/*)
# ---------------------------------------------------------------------------
@router.post("/v3/artifacts/index")
async def v3_artifacts_index(req: ArtifactIndexRequest):
    """Index an NTP-aligned telemetry artifact into active ArtifactStorePlugin."""
    _check_read_only()
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        art_plugin = reg.resolve("artifact_store", project_id=req.project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    stored = art_plugin.index_artifact(payload)
    if inspect.isawaitable(stored):
        stored = await stored
    return {
        "status": "ok",
        "plugin_id": art_plugin.metadata.plugin_id,
        "artifact": stored,
    }


@router.get("/v3/artifacts/window")
async def v3_artifacts_window(
    run_id: str = Query(..., description="Execution run identifier"),
    center_ts_ms: int = Query(..., description="Center NTP epoch timestamp in milliseconds"),
    window_ms: int = Query(5000, ge=0, description="Symmetric evidence window half-width in ms"),
    project_id: Optional[str] = Query(default=None),
):
    """Query NTP-synchronized multimodal evidence streams around center_ts_ms +/- window_ms."""
    reg = _get_registry_and_bind_db(project_id=project_id)
    try:
        art_plugin = reg.resolve("artifact_store", project_id=project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    win_kwargs: Dict[str, Any] = {
        "run_id": run_id,
        "center_ts_ms": center_ts_ms,
        "window_ms": window_ms,
    }
    if project_id is not None and _accepts_kwarg(art_plugin.query_evidence_window, "project_id"):
        win_kwargs["project_id"] = project_id
    win = art_plugin.query_evidence_window(**win_kwargs)
    if inspect.isawaitable(win):
        win = await win
    return {
        "status": "ok",
        "plugin_id": art_plugin.metadata.plugin_id,
        **(win if isinstance(win, dict) else {"window": win}),
    }


@router.post("/v3/artifacts/purge")
async def v3_artifacts_purge(
    request: Request,
    req: Optional[ArtifactPurgeRequest] = Body(default=None),
):
    """Purge expired telemetry artifacts past retention TTL."""
    _check_read_only()
    proj_id = req.project_id if req else None
    now_ms = req.now_ms if req else None
    if not proj_id and os.getenv("PB_REQUIRE_REST_AUTH", "false").lower() in ("true", "1", "yes"):
        account = auth.get_authenticated_account(request)
        if account and account.get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail="Permission denied: Admin role or explicit project_id required for artifact purge.",
            )
    reg = _get_registry_and_bind_db(project_id=proj_id)
    try:
        art_plugin = reg.resolve("artifact_store", project_id=proj_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    purge_kwargs: Dict[str, Any] = {"now_ms": now_ms}
    if proj_id is not None and _accepts_kwarg(art_plugin.purge_expired, "project_id"):
        purge_kwargs["project_id"] = proj_id
    res = art_plugin.purge_expired(**purge_kwargs)
    if inspect.isawaitable(res):
        res = await res
    return {
        "status": "ok",
        "plugin_id": art_plugin.metadata.plugin_id,
        **(res if isinstance(res, dict) else {"result": res}),
    }


# ---------------------------------------------------------------------------
# 5. v3.0 Layer 2 Brain Consumer Endpoints (/v3/planner/*, /v3/oracle/*, /v3/rca/*)
# ---------------------------------------------------------------------------
@router.post("/v3/planner/test-dag")
async def v3_planner_test_dag(req: PlannerTestDAGRequest):
    """Compute blast radius, inject preconditions, bind script cache, and emit test DAG (read-only)."""
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        planner = reg.resolve(
            "brain_consumer",
            preferred_id="brain.planner",
            project_id=req.project_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return await planner.execute(
        payload,
        registry=reg,
        context={"project_id": req.project_id, "branch": req.branch},
    )


@router.post("/v3/oracle/context")
async def v3_oracle_context(req: OracleContextRequest):
    """Evaluate telemetry summary against SRS criteria, transitions, and error rules (read-only)."""
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        oracle = reg.resolve(
            "brain_consumer",
            preferred_id="brain.oracle",
            project_id=req.project_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return await oracle.execute(
        payload,
        registry=reg,
        context={
            "project_id": req.project_id,
            "branch": req.branch,
            "version_tag": req.version_tag,
        },
    )


@router.post("/v3/rca/triage")
async def v3_rca_triage(req: RCATriageRequest):
    """Normalize failure stacktrace, deduplicate incident, classify domain, and trigger cache feedback."""
    _check_read_only()
    reg = _get_registry_and_bind_db(project_id=req.project_id)
    try:
        rca = reg.resolve(
            "brain_consumer",
            preferred_id="brain.rca",
            project_id=req.project_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return await rca.execute(
        payload,
        registry=reg,
        context={"project_id": req.project_id},
    )


# ---------------------------------------------------------------------------
# 6. v3.0 Layer 0 Webhook Dispatch Endpoint (/v3/webhooks/{source})
# ---------------------------------------------------------------------------
@router.post("/v3/webhooks/{source}")
async def v3_webhook_ingest(source: str, request: Request):
    """Verify signature and normalize GitLab/GitHub/TestRail/Jira/Notion webhook event."""
    _check_read_only()
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON webhook payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")

    query_project_id = (
        str(
            request.query_params.get("project_id")
            or request.headers.get("X-Project-Id")
            or request.headers.get("X-PB-Project-Id")
            or ""
        ).strip()
        or None
    )
    payload_project_id = str(payload.get("project_id") or "").strip() or None
    effective_project_id = query_project_id or payload_project_id

    reg = _get_registry_and_bind_db(project_id=effective_project_id)
    try:
        global_webhook_plugin = reg.resolve(
            "webhook",
            preferred_id=f"webhook.{source.lower()}",
            project_id=query_project_id,
        )
    except LookupError:
        global_webhook_plugin = None

    try:
        webhook_plugin = (
            global_webhook_plugin
            if (global_webhook_plugin is not None and query_project_id is None)
            else reg.resolve(
                "webhook",
                preferred_id=f"webhook.{source.lower()}",
                project_id=effective_project_id,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    pid = webhook_plugin.metadata.plugin_id
    global_cfg = reg.get_effective_config(pid, project_id=None)
    proj_cfg = (
        reg.get_effective_config(pid, project_id=effective_project_id)
        if effective_project_id
        else dict(global_cfg)
    )

    headers_dict = {str(k): str(v) for k, v in request.headers.items()}
    env_secret = _nonempty_secret(
        os.getenv(f"PB_{source.upper()}_WEBHOOK_SECRET")
        or os.getenv("PB_WEBHOOK_SECRET")
    )
    global_secret = (
        _nonempty_secret(global_cfg.get("webhook_secret"))
        or _nonempty_secret(global_cfg.get("secret"))
        or env_secret
    )
    proj_secret = (
        _nonempty_secret(proj_cfg.get("webhook_secret"))
        or _nonempty_secret(proj_cfg.get("secret"))
    )

    # Never allow an unverified payload project_id to downgrade or clear an active global secret
    if query_project_id is not None:
        active_secret = proj_secret or global_secret
    elif global_secret is not None:
        active_secret = global_secret
    else:
        active_secret = proj_secret

    force_sig = os.getenv("PB_REQUIRE_WEBHOOK_SIGNATURE", "false").lower() in ("true", "1", "yes")
    global_allow_unsigned = global_cfg.get("allow_unsigned")
    proj_allow_unsigned = proj_cfg.get("allow_unsigned")

    if force_sig or active_secret is not None:
        allow_unsigned_eff = False
    elif global_allow_unsigned is False:
        allow_unsigned_eff = False
    elif query_project_id is not None:
        allow_unsigned_eff = bool(proj_allow_unsigned is True)
    else:
        allow_unsigned_eff = bool(global_allow_unsigned is True and proj_allow_unsigned is not False)

    if not allow_unsigned_eff:
        if active_secret is None:
            raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")
        if _accepts_kwarg(webhook_plugin.verify_signature, "allow_unsigned"):
            sig_ok = webhook_plugin.verify_signature(
                headers_dict,
                raw_body,
                secret=active_secret,
                allow_unsigned=False,
            )
        else:
            sig_ok = webhook_plugin.verify_signature(
                headers_dict,
                raw_body,
                secret=active_secret,
            )
        if not sig_ok:
            raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")
    elif _accepts_kwarg(webhook_plugin.verify_signature, "allow_unsigned"):
        if not webhook_plugin.verify_signature(
            headers_dict,
            raw_body,
            secret=None,
            allow_unsigned=True,
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")

    if source.lower() != "generic" and "source" not in payload:
        payload = {**payload, "source": source.lower()}

    normalized = await webhook_plugin.parse_event(headers_dict, payload)
    event_copy = dict(normalized)
    return {
        **event_copy,
        "plugin_id": webhook_plugin.metadata.plugin_id,
        "event": normalized,
    }
