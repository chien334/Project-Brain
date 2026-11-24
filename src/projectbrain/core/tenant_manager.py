"""3-Tier Physical Isolation Manager (MasterDB -> ProjectDB -> BranchSchema).

Architecture:
1. Master Control-Plane DB (`MasterDB`): Global IAM/auth, migrations, stats,
   and project/branch catalog (`master_projects`, `master_branches`, `projects`).
2. Dedicated Physical Project Database (`1 Project = 1 Database`):
   - PostgreSQL: `CREATE DATABASE "pb_proj_<slug>_<hash8>" ENCODING 'UTF8'`
   - SQLite: `<master_db_dir>/pb_projects/pb_proj_<slug>_<hash8>/`
3. Dedicated Branch Schema (`1 Branch = 1 Schema`):
   - Root branch (`main` / `PB_ROOT_BRANCH`) -> `pb_root` (`pb_root.sqlite`)
   - Feature branch (`branch != root`) -> `pb_br_<slug>_<hash8>` (`pb_br_<slug>_<hash8>.sqlite`)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql as psql
from psycopg2.extras import RealDictCursor

from .config import env

logger = logging.getLogger("tenant_manager")
logger.setLevel(logging.INFO)

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MAX_MEMORY_HINTS = 50000
_NON_SUBSOURCE_IDS = frozenset({"", "__default__", "__shared__"})

_SOURCE_METADATA_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("source_id", "TEXT DEFAULT '__default__'"),
    ("scope", "TEXT DEFAULT 'source'"),
    ("doc_role", "TEXT DEFAULT 'general'"),
)

_current_tenant: ContextVar[Optional[Tuple[str, str]]] = ContextVar(
    "pb_current_tenant", default=None
)
_current_source: ContextVar[Optional[str]] = ContextVar(
    "pb_current_source", default=None
)


def _effective_root_branch(explicit_root: Optional[str] = None) -> str:
    """Resolve canonical root branch honoring PB_ROOT_BRANCH / env.root_branch."""
    if explicit_root and str(explicit_root).strip():
        return str(explicit_root).strip()
    env_root = os.getenv("PB_ROOT_BRANCH") or os.getenv("OM_ROOT_BRANCH")
    if env_root and env_root.strip():
        return env_root.strip()
    cfg_root = getattr(env, "root_branch", None)
    if cfg_root and str(cfg_root).strip():
        return str(cfg_root).strip()
    return "main"


def is_multi_db_enabled() -> bool:
    """Return whether 3-tier multi-DB/multi-schema tenant routing is active."""
    cur_env = os.getenv("PB_MULTI_DB_ENABLED")
    if cur_env is None:
        cur_env = os.getenv("OM_MULTI_DB_ENABLED")
    init_env = getattr(env, "_initial_multi_db_env", None)
    if cur_env is not None and cur_env != init_env:
        val = str(cur_env).strip().lower()
        if val in ("0", "false", "no", "off"):
            return False
        if val in ("1", "true", "yes", "on"):
            return True
    return bool(getattr(env, "multi_db_enabled", True))


def resolve_effective_master_url() -> str:
    """Resolve active Master DB URL, honoring test monkeypatches on PB_DB_PATH/env.database_url."""
    cfg_url = getattr(env, "database_url", "") or ""
    init_url = getattr(env, "_initial_db_url", cfg_url)
    if cfg_url and cfg_url != init_url:
        return cfg_url

    cur_pb = os.getenv("PB_DB_PATH")
    init_pb = getattr(env, "_initial_pb_db_path", None)
    if cur_pb and cur_pb != init_pb:
        return f"sqlite:///{cur_pb}"

    cur_om = os.getenv("OM_DB_PATH")
    init_om = getattr(env, "_initial_om_db_path", None)
    if cur_om and cur_om != init_om:
        return f"sqlite:///{cur_om}"

    return cfg_url


def sanitize_project_db_name(project_id: str) -> str:
    """Compute deterministic PostgreSQL-safe database identifier <= 60 bytes.

    Format: `pb_proj_{clean_proj[:36]}_{hash8}`.
    """
    raw = (project_id or "default").strip() or "default"
    clean_proj = re.sub(r"[^a-z0-9_]", "_", raw.lower()).strip("_")
    if not clean_proj:
        clean_proj = "proj"
    clean_proj = clean_proj[:36].rstrip("_") or "proj"
    hash8 = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    db_name = f"pb_proj_{clean_proj}_{hash8}"
    if len(db_name.encode("utf-8")) > 60:
        db_name = db_name[:60]
    if not _IDENT_RE.match(db_name):
        raise ValueError(f"Sanitized project DB name '{db_name}' violates identifier rules")
    return db_name


def sanitize_branch_schema_for_project(
    branch_name: str, root_branch: str = "main"
) -> str:
    """Compute deterministic branch schema name within a project DB (<= 60 bytes).

    Returns `'pb_root'` for the root branch, else `pb_br_{clean_branch[:36]}_{hash8}`.
    """
    eff_root = (root_branch or "main").strip() or "main"
    raw_branch = (branch_name or "").strip()
    if not raw_branch or raw_branch == eff_root:
        return "pb_root"

    clean_branch = re.sub(r"[^a-z0-9_]", "_", raw_branch.lower()).strip("_")
    if not clean_branch:
        clean_branch = "branch"
    clean_branch = clean_branch[:36].rstrip("_") or "branch"
    hash8 = hashlib.sha256(raw_branch.encode("utf-8")).hexdigest()[:8]
    schema_name = f"pb_br_{clean_branch}_{hash8}"
    if len(schema_name.encode("utf-8")) > 60:
        schema_name = schema_name[:60]
    if not _IDENT_RE.match(schema_name):
        raise ValueError(f"Sanitized branch schema '{schema_name}' violates identifier rules")
    return schema_name


def _normalize_subsource_id(source_id: Optional[str]) -> Optional[str]:
    """Return canonical sub-source ID, or None for default/shared/empty targets."""
    if source_id is None:
        return None
    cleaned = str(source_id).strip()
    if cleaned in _NON_SUBSOURCE_IDS:
        return None
    return cleaned


def _source_dir_slug(source_id: str) -> str:
    """Compute filesystem-safe deterministic directory slug `{src_slug}_{h6}` for a sub-source."""
    raw_src = str(source_id).strip()
    src_slug = re.sub(r"[^a-z0-9_]", "_", raw_src.lower()).strip("_")
    if not src_slug:
        src_slug = "src"
    src_slug = src_slug[:18].rstrip("_") or "src"
    h6 = hashlib.sha256(raw_src.encode("utf-8")).hexdigest()[:6]
    return f"{src_slug}_{h6}"


def sanitize_source_schema_name(
    source_id: Optional[str], branch_name: str, root_branch: str = "main"
) -> str:
    """Compute deterministic schema name <= 60 chars for a project source and branch.

    Returns `pb_root` / `pb_br_*` when `source_id` is None / "" / "__default__" / "__shared__",
    else `pb_src_{src_slug[:18]}_{h6}_root` or `pb_src_{src_slug[:18]}_{h6}_br_{br_slug[:18]}_{br_h6}`.
    """
    eff_source = _normalize_subsource_id(source_id)
    if eff_source is None:
        return sanitize_branch_schema_for_project(branch_name, root_branch)

    eff_root = (root_branch or "main").strip() or "main"
    raw_branch = (branch_name or "").strip()

    src_slug = re.sub(r"[^a-z0-9_]", "_", eff_source.lower()).strip("_")
    if not src_slug:
        src_slug = "src"
    src_slug = src_slug[:18].rstrip("_") or "src"
    h6 = hashlib.sha256(eff_source.encode("utf-8")).hexdigest()[:6]

    if not raw_branch or raw_branch == eff_root:
        schema_name = f"pb_src_{src_slug}_{h6}_root"
    else:
        br_slug = re.sub(r"[^a-z0-9_]", "_", raw_branch.lower()).strip("_")
        if not br_slug:
            br_slug = "branch"
        br_slug = br_slug[:18].rstrip("_") or "branch"
        br_h6 = hashlib.sha256(raw_branch.encode("utf-8")).hexdigest()[:6]
        schema_name = f"pb_src_{src_slug}_{h6}_br_{br_slug}_{br_h6}"
        if len(schema_name.encode("utf-8")) > 60:
            excess = len(schema_name.encode("utf-8")) - 60
            if len(br_slug) > excess:
                br_slug = br_slug[:-excess].rstrip("_") or "b"
            else:
                src_slug = src_slug[: max(1, len(src_slug) - excess)].rstrip("_") or "s"
            schema_name = f"pb_src_{src_slug}_{h6}_br_{br_slug}_{br_h6}"

    if len(schema_name.encode("utf-8")) > 60:
        schema_name = schema_name[:60]
    if not _IDENT_RE.match(schema_name):
        raise ValueError(f"Sanitized source schema '{schema_name}' violates identifier rules")
    return schema_name


def parse_tenant_spec(
    raw_id: Optional[str],
    default_branch: str = "main",
    default_source: Optional[str] = None,
) -> Tuple[str, Optional[str], str]:
    """Parse composite tenant spec into `(project_id, source_id, branch)`.

    Examples:
        - `"proj"` -> `("proj", default_source, default_branch)`
        - `"proj:br"` -> `("proj", default_source, "br")`
        - `"proj@src"` -> `("proj", "src", default_branch)`
        - `"proj@src:br"` -> `("proj", "src", "br")`
        - `"proj@__shared__"` -> `("proj", "__shared__", default_branch)`
    """
    eff_default_br = (default_branch or "main").strip() or "main"
    eff_default_src = (
        str(default_source).strip() or None
        if default_source is not None
        else None
    )
    if raw_id is None:
        return ("default", eff_default_src, eff_default_br)
    raw = str(raw_id).strip()
    if not raw or raw == "default":
        return ("default", eff_default_src, eff_default_br)

    if ":" in raw:
        left, branch_part = raw.split(":", 1)
        br = branch_part.strip() or eff_default_br
    else:
        left = raw
        br = eff_default_br

    if "@" in left:
        proj_part, src_part = left.split("@", 1)
        proj = proj_part.strip() or "default"
        src = src_part.strip() or eff_default_src
    else:
        proj = left.strip() or "default"
        src = eff_default_src

    return (proj, src, br)


def parse_tenant_from_user_id(
    user_id: Optional[str], default_branch: str = "main"
) -> Tuple[str, str]:
    """Parse composite `user_id` into `(project_id, branch)`, stripping any `@source`.

    Examples:
        - `"demo/app:feat-v3"` -> `("demo/app", "feat-v3")`
        - `"demo/app@web:feat-v3"` -> `("demo/app", "feat-v3")`
        - `"demo/app:%"` -> `("demo/app", "%")`
        - `"demo/app"` -> `("demo/app", "main")`
        - `None` / `""` / `"default"` -> `("default", "main")`
    """
    proj, _, br = parse_tenant_spec(user_id, default_branch=default_branch)
    return (proj, br)


def get_current_tenant() -> Optional[Tuple[str, str]]:
    """Return active `(project_id, branch)` from ContextVar, if set."""
    return _current_tenant.get()


def get_current_source() -> Optional[str]:
    """Return active `source_id` from ContextVar, if set."""
    return _current_source.get()


def _resolve_effective_subsource(
    project_id_spec: Optional[str], source_id: Optional[str] = None
) -> Optional[str]:
    """Resolve effective sub-source ID from explicit arg, ContextVar, or `@source` spec."""
    if source_id is not None:
        return _normalize_subsource_id(source_id)
    ctx_src = get_current_source()
    if ctx_src is not None:
        return _normalize_subsource_id(ctx_src)
    if project_id_spec:
        _, parsed_src, _ = parse_tenant_spec(project_id_spec)
        return _normalize_subsource_id(parsed_src)
    return None


@contextmanager
def tenant_context(
    project_id: Optional[str],
    branch: Optional[str] = "main",
    source_id: Optional[str] = None,
) -> Iterator[Optional[Tuple[str, str]]]:
    """Context manager scoping DB operations to `(project_id, branch)` and optional `source_id`."""
    if project_id is None:
        token = _current_tenant.set(None)
        src_token = _current_source.set(None)
    else:
        raw_proj = str(project_id).strip()
        eff_branch_default = (branch or "main").strip() or "main"
        parsed_proj, parsed_src, parsed_br = parse_tenant_spec(
            raw_proj, default_branch=eff_branch_default, default_source=None
        )
        proj = parsed_proj
        if ":" in raw_proj and (branch is None or branch == "main"):
            br = parsed_br
        else:
            br = eff_branch_default
        eff_src = (
            (str(source_id).strip() or None)
            if source_id is not None
            else parsed_src
        )
        token = _current_tenant.set((proj, br))
        src_token = _current_source.set(eff_src)
    try:
        yield _current_tenant.get()
    finally:
        _current_source.reset(src_token)
        _current_tenant.reset(token)


def _replace_pg_dbname(master_url: str, db_name: str) -> str:
    """Replace database path component in PostgreSQL DSN preserving auth, host, port, and query."""
    parts = urlsplit(master_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))


def _mask_url(url: str) -> str:
    """Redact credentials from PostgreSQL URL for safe metadata storage & topology display."""
    if not url or not url.startswith(("postgresql://", "postgres://")):
        return url
    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
        host_port = parts.hostname or ""
        if parts.port:
            host_port = f"{host_port}:{parts.port}"
        user_part = f"{parts.username}:***@" if parts.username else ""
        return urlunsplit((parts.scheme, f"{user_part}{host_port}", parts.path, parts.query, ""))
    except Exception:
        return "postgresql://***"


def _adapt_ddl_for_postgres(stmt: str) -> str:
    """Adapt SQLite-oriented ANSI DDL statement to PostgreSQL types."""
    out = re.sub(r"\bAUTOINCREMENT\b", "", stmt, flags=re.IGNORECASE)
    out = re.sub(r"\bBLOB\b", "BYTEA", out, flags=re.IGNORECASE)
    out = re.sub(r"\bREAL\b", "DOUBLE PRECISION", out, flags=re.IGNORECASE)
    return out


_TENANT_COMMON_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        content TEXT,
        primary_sector TEXT,
        sectors TEXT,
        tags TEXT,
        vector BLOB,
        norm_vector BLOB,
        compressed_vec BLOB,
        simhash TEXT,
        meta TEXT,
        user_id TEXT,
        segment INTEGER DEFAULT 0,
        created_at BIGINT,
        updated_at BIGINT,
        last_seen_at BIGINT,
        salience REAL,
        decay_lambda REAL,
        version INTEGER,
        mean_dim INTEGER,
        mean_vec BLOB,
        feedback_score REAL DEFAULT 0,
        source_id TEXT DEFAULT '__default__',
        scope TEXT DEFAULT 'source',
        doc_role TEXT DEFAULT 'general'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_memories_sector ON memories(primary_sector)",
    "CREATE INDEX IF NOT EXISTS idx_memories_segment ON memories(simhash)",
    "CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)",
    """
    CREATE TABLE IF NOT EXISTS vectors (
        id TEXT,
        v BLOB,
        dim INTEGER,
        sector TEXT,
        user_id TEXT,
        PRIMARY KEY (id, sector),
        FOREIGN KEY(id) REFERENCES memories(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_vectors_user ON vectors(user_id)",
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        summary TEXT,
        reflection_count INTEGER,
        created_at BIGINT,
        updated_at BIGINT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS waypoints (
        src_id TEXT,
        dst_id TEXT,
        dst_sector TEXT,
        user_id TEXT,
        weight REAL,
        created_at BIGINT,
        updated_at BIGINT,
        PRIMARY KEY (src_id, dst_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_waypoints_src ON waypoints(src_id)",
    "CREATE INDEX IF NOT EXISTS idx_waypoints_dst ON waypoints(dst_id)",
    """
    CREATE TABLE IF NOT EXISTS temporal_facts (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        subject TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object TEXT NOT NULL,
        valid_from BIGINT NOT NULL,
        valid_to BIGINT,
        confidence REAL,
        last_updated BIGINT,
        metadata TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_temporal_subject ON temporal_facts(subject)",
    """
    CREATE TABLE IF NOT EXISTS temporal_edges (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        valid_from BIGINT NOT NULL,
        valid_to BIGINT,
        weight REAL NOT NULL,
        metadata TEXT,
        FOREIGN KEY(source_id) REFERENCES temporal_facts(id),
        FOREIGN KEY(target_id) REFERENCES temporal_facts(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_overlay_tombstones (
        project_id TEXT NOT NULL,
        branch TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_key TEXT NOT NULL,
        created_at BIGINT,
        PRIMARY KEY (project_id, branch, target_type, target_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mem_tomb_proj_branch ON memory_overlay_tombstones(project_id, branch)",
    """
    CREATE TABLE IF NOT EXISTS document_versions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        branch TEXT NOT NULL,
        doc_key TEXT NOT NULL,
        version TEXT NOT NULL,
        parent_version TEXT,
        base_branch TEXT,
        filename TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        total_units INTEGER DEFAULT 0,
        reused_units INTEGER DEFAULT 0,
        new_units INTEGER DEFAULT 0,
        deleted_units INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at BIGINT NOT NULL,
        source_id TEXT DEFAULT '__default__',
        scope TEXT DEFAULT 'source',
        doc_role TEXT DEFAULT 'general',
        UNIQUE(project_id, branch, doc_key, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_versions_lookup ON document_versions(project_id, branch, doc_key, status)",
    """
    CREATE TABLE IF NOT EXISTS document_units (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        branch TEXT NOT NULL,
        doc_key TEXT NOT NULL,
        version TEXT NOT NULL,
        unit_index INTEGER NOT NULL,
        unit_type TEXT NOT NULL,
        unit_title TEXT,
        unit_sha256 TEXT NOT NULL,
        markdown_text TEXT NOT NULL,
        memory_ids_json TEXT DEFAULT '[]',
        source_id TEXT DEFAULT '__default__',
        scope TEXT DEFAULT 'source',
        doc_role TEXT DEFAULT 'general'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_units_lookup ON document_units(project_id, doc_key, unit_sha256)",
    """
    CREATE TABLE IF NOT EXISTS project_nodes (
        project_id TEXT NOT NULL,
        id TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        language TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        docstring TEXT,
        signature TEXT,
        updated_at BIGINT NOT NULL,
        PRIMARY KEY (project_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_nodes_search ON project_nodes(project_id, name)",
    """
    CREATE TABLE IF NOT EXISTS project_edges (
        project_id TEXT NOT NULL,
        id INTEGER NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        kind TEXT NOT NULL,
        metadata TEXT,
        line INTEGER,
        col INTEGER,
        PRIMARY KEY (project_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_edges_source ON project_edges(project_id, source)",
    """
    CREATE TABLE IF NOT EXISTS v3_script_cache (
        cache_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        intent_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        build_version TEXT NOT NULL,
        screen_hash TEXT DEFAULT '',
        ui_fingerprint_json TEXT DEFAULT '{}',
        udcp_commands_json TEXT NOT NULL,
        hit_count INTEGER DEFAULT 0,
        fallback_count INTEGER DEFAULT 0,
        fallback_rate DOUBLE PRECISION DEFAULT 0.0,
        invalidated INTEGER DEFAULT 0,
        invalidation_reason TEXT,
        self_heal_history_json TEXT DEFAULT '[]',
        ttl_seconds INTEGER DEFAULT 2592000,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v3_telemetry_artifacts (
        artifact_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        device_id TEXT DEFAULT '',
        build_version TEXT DEFAULT '',
        artifact_type TEXT NOT NULL,
        ntp_timestamp_ms BIGINT NOT NULL,
        duration_ms BIGINT DEFAULT 0,
        uri TEXT NOT NULL,
        sha256 TEXT DEFAULT '',
        size_bytes BIGINT DEFAULT 0,
        summary_metrics_json TEXT DEFAULT '{}',
        retention_days INTEGER DEFAULT 30,
        expires_at BIGINT NOT NULL,
        created_at BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS udcp_script_cache (
        cache_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        intent_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        build_version TEXT NOT NULL,
        screen_hash TEXT DEFAULT '',
        ui_fingerprint_json TEXT DEFAULT '{}',
        udcp_commands_json TEXT NOT NULL,
        hit_count INTEGER DEFAULT 0,
        fallback_count INTEGER DEFAULT 0,
        fallback_rate DOUBLE PRECISION DEFAULT 0.0,
        invalidated INTEGER DEFAULT 0,
        invalidation_reason TEXT,
        self_heal_history_json TEXT DEFAULT '[]',
        ttl_seconds INTEGER DEFAULT 2592000,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_udcp_cache_lookup ON udcp_script_cache(project_id, intent_id, platform, build_version)",
    "CREATE INDEX IF NOT EXISTS idx_udcp_cache_screen ON udcp_script_cache(project_id, intent_id, screen_hash)",
    """
    CREATE TABLE IF NOT EXISTS telemetry_artifacts (
        artifact_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        device_id TEXT DEFAULT '',
        build_version TEXT DEFAULT '',
        artifact_type TEXT NOT NULL,
        ntp_timestamp_ms BIGINT NOT NULL,
        duration_ms BIGINT DEFAULT 0,
        uri TEXT NOT NULL,
        sha256 TEXT DEFAULT '',
        size_bytes BIGINT DEFAULT 0,
        summary_metrics_json TEXT DEFAULT '{}',
        retention_days INTEGER DEFAULT 30,
        expires_at BIGINT NOT NULL,
        created_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_telemetry_run_step_ts ON telemetry_artifacts(run_id, step_id, ntp_timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_run_ts ON telemetry_artifacts(run_id, ntp_timestamp_ms)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_expires ON telemetry_artifacts(expires_at)",
    """
    CREATE TABLE IF NOT EXISTS plugin_project_configs (
        project_id TEXT NOT NULL,
        plugin_id TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        priority INTEGER,
        config_json TEXT DEFAULT '{}',
        updated_at BIGINT,
        PRIMARY KEY (project_id, plugin_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plugin_project_configs_proj ON plugin_project_configs(project_id)",
    """
    CREATE TABLE IF NOT EXISTS v3_rca_incidents (
        incident_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        signature_hash TEXT NOT NULL,
        fault_domain TEXT NOT NULL,
        normalized_signature TEXT NOT NULL,
        ticket_refs_json TEXT DEFAULT '[]',
        occurrence_count INTEGER DEFAULT 1,
        last_run_id TEXT,
        self_heal_hint TEXT,
        metadata_json TEXT DEFAULT '{}',
        first_seen_at BIGINT NOT NULL,
        last_seen_at BIGINT NOT NULL,
        UNIQUE(project_id, signature_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_v3_rca_project_hash ON v3_rca_incidents(project_id, signature_hash)",
)

_ROOT_ONLY_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS memory_branch_links (
        project_id TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_branch TEXT NOT NULL,
        updated_at BIGINT,
        source_id TEXT DEFAULT '__default__',
        scope TEXT DEFAULT 'source',
        doc_role TEXT DEFAULT 'general',
        PRIMARY KEY (project_id, branch)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cross_source_relations (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        src_source_id TEXT NOT NULL,
        src_branch TEXT DEFAULT 'main',
        src_version TEXT,
        src_node_id TEXT DEFAULT '',
        src_file_path TEXT,
        dst_source_id TEXT NOT NULL,
        dst_branch TEXT DEFAULT 'main',
        dst_version TEXT,
        dst_node_id TEXT DEFAULT '',
        dst_doc_key TEXT,
        relation_type TEXT NOT NULL,
        origin TEXT DEFAULT 'manual',
        confidence TEXT DEFAULT 'high',
        confidence_score DOUBLE PRECISION DEFAULT 1.0,
        contract_key TEXT DEFAULT '',
        contract_metadata_json TEXT DEFAULT '{}',
        status TEXT DEFAULT 'active',
        is_bidirectional INTEGER DEFAULT 0,
        created_by TEXT DEFAULT 'system',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        UNIQUE(project_id, src_source_id, src_branch, dst_source_id, dst_branch, relation_type, contract_key, src_node_id, dst_node_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cross_src_rel_proj_src ON cross_source_relations(project_id, src_source_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_cross_src_rel_proj_dst ON cross_source_relations(project_id, dst_source_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_cross_src_rel_contract ON cross_source_relations(project_id, contract_key)",
    """
    CREATE TABLE IF NOT EXISTS cross_source_contract_artifacts (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        branch TEXT DEFAULT 'main',
        contract_kind TEXT NOT NULL,
        role TEXT NOT NULL,
        normalized_key TEXT NOT NULL,
        node_id TEXT DEFAULT '',
        file_path TEXT,
        symbol_name TEXT,
        signature_hash TEXT,
        metadata_json TEXT DEFAULT '{}',
        sync_generation INTEGER DEFAULT 0,
        updated_at BIGINT NOT NULL,
        UNIQUE(project_id, source_id, branch, contract_kind, role, normalized_key, node_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cross_src_artifacts_lookup ON cross_source_contract_artifacts(project_id, contract_kind, normalized_key)",
    "CREATE INDEX IF NOT EXISTS idx_cross_src_artifacts_src ON cross_source_contract_artifacts(project_id, source_id, branch)",
)

_MASTER_CATALOG_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS master_projects (
        project_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        db_name TEXT UNIQUE NOT NULL,
        root_branch TEXT DEFAULT 'main',
        root_schema TEXT DEFAULT 'pb_root',
        backend_type TEXT NOT NULL,
        db_descriptor TEXT,
        status TEXT DEFAULT 'active',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_master_projects_db_name ON master_projects(db_name)",
    """
    CREATE TABLE IF NOT EXISTS master_branches (
        project_id TEXT NOT NULL,
        branch_name TEXT NOT NULL,
        db_name TEXT NOT NULL,
        schema_name TEXT NOT NULL,
        base_branch TEXT DEFAULT 'main',
        is_root INTEGER DEFAULT 0,
        has_graph_schema INTEGER DEFAULT 1,
        has_doc_schema INTEGER DEFAULT 1,
        author TEXT DEFAULT 'system',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        PRIMARY KEY (project_id, branch_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_master_branches_proj_schema ON master_branches(project_id, schema_name)",
    "CREATE INDEX IF NOT EXISTS idx_master_branches_db_name ON master_branches(db_name)",
    """
    CREATE TABLE IF NOT EXISTS master_project_sources (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        display_name TEXT,
        source_type TEXT DEFAULT 'service',
        tech_stack_json TEXT DEFAULT '[]',
        repo_url TEXT,
        workspace_subpath TEXT,
        root_branch TEXT DEFAULT 'main',
        root_schema TEXT NOT NULL,
        current_version TEXT DEFAULT '1.0.0',
        sync_generation INTEGER DEFAULT 0,
        last_commit_sha TEXT,
        last_sync_status TEXT DEFAULT 'pending',
        inherit_shared_docs INTEGER DEFAULT 1,
        status TEXT DEFAULT 'active',
        metadata_json TEXT DEFAULT '{}',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        UNIQUE(project_id, source_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_master_project_sources_proj ON master_project_sources(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_master_project_sources_proj_src ON master_project_sources(project_id, source_id)",
    """
    CREATE TABLE IF NOT EXISTS master_source_branches (
        project_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        branch_name TEXT NOT NULL,
        db_name TEXT NOT NULL,
        schema_name TEXT NOT NULL,
        base_branch TEXT DEFAULT 'main',
        is_root INTEGER DEFAULT 0,
        has_graph_schema INTEGER DEFAULT 1,
        has_doc_schema INTEGER DEFAULT 1,
        source_version TEXT,
        sync_generation INTEGER DEFAULT 0,
        commit_sha TEXT,
        author TEXT DEFAULT 'system',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        PRIMARY KEY (project_id, source_id, branch_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_master_source_branches_proj_src ON master_source_branches(project_id, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_master_source_branches_schema ON master_source_branches(project_id, schema_name)",
    """
    CREATE TABLE IF NOT EXISTS master_source_upgrades (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        branch_name TEXT NOT NULL,
        from_version TEXT,
        to_version TEXT NOT NULL,
        sync_generation INTEGER DEFAULT 1,
        upgrade_mode TEXT DEFAULT 'delta',
        nodes_delta INTEGER DEFAULT 0,
        edges_delta INTEGER DEFAULT 0,
        docs_updated INTEGER DEFAULT 0,
        affected_relations INTEGER DEFAULT 0,
        drifted_relations INTEGER DEFAULT 0,
        commit_sha TEXT,
        actor TEXT DEFAULT 'system',
        created_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_master_source_upgrades_proj_src ON master_source_upgrades(project_id, source_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS master_shared_doc_bindings (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        doc_key TEXT NOT NULL,
        target_source_id TEXT NOT NULL DEFAULT '*',
        pinned_version TEXT,
        binding_role TEXT DEFAULT 'reference',
        priority DOUBLE PRECISION DEFAULT 1.0,
        status TEXT DEFAULT 'active',
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        UNIQUE(project_id, doc_key, target_source_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_master_shared_doc_bindings_proj ON master_shared_doc_bindings(project_id, target_source_id)",
)


class ProjectDatabaseManager:
    """Manages Master DB catalog, per-project physical DBs, and per-branch schemas."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._master_db: Optional[Any] = None
        self._bound_master_url: Optional[str] = None
        self._bound_env_db_path: Optional[str] = None
        self._bound_master_conn_id: Optional[int] = None
        self._master_catalog_ensured_conn_id: Optional[int] = None

        self._sqlite_conns: Dict[Tuple[str, str], sqlite3.Connection] = {}
        self._pg_conns: Dict[Tuple[str, str], Any] = {}
        self._provisioned_projects: Dict[str, Dict[str, Any]] = {}
        self._provisioned_branches: Set[Tuple[str, str]] = set()
        self._source_root_branches: Dict[Tuple[str, str], str] = {}
        self._memory_id_hints: OrderedDict[str, Tuple[str, str]] = OrderedDict()
        self._memory_source_hints: OrderedDict[str, str] = OrderedDict()

    def bind_master_db(self, master_db: Any) -> None:
        """Bind Master DB facade instance and synchronize connection caches."""
        self._master_db = master_db
        self.sync_binding(master_db)

    def _resolve_master_db(self) -> Any:
        if self._master_db is None:
            from .db import db as core_db

            self._master_db = core_db
        return self._master_db

    def _close_cached_connections(self) -> None:
        for conn in list(self._sqlite_conns.values()):
            try:
                conn.close()
            except Exception:
                pass
        for conn in list(self._pg_conns.values()):
            try:
                conn.close()
            except Exception:
                pass
        self._sqlite_conns.clear()
        self._pg_conns.clear()
        self._provisioned_projects.clear()
        self._provisioned_branches.clear()
        self._source_root_branches.clear()
        self._memory_id_hints.clear()
        self._memory_source_hints.clear()
        self._master_catalog_ensured_conn_id = None

    def reset(self) -> None:
        """Close all cached tenant connections and clear provisioning/routing caches."""
        with self._lock:
            self._close_cached_connections()
            self._bound_master_url = None
            self._bound_env_db_path = None
            self._bound_master_conn_id = None

    def sync_binding(self, master_db: Optional[Any] = None) -> None:
        """Detect changes to active Master DB URL, env DB path, or db.conn and reset stale caches."""
        with self._lock:
            mdb = master_db or self._master_db
            cur_url = resolve_effective_master_url()
            cur_env_path = os.getenv("PB_DB_PATH") or os.getenv("OM_DB_PATH")
            cur_conn = getattr(mdb, "conn", None) if mdb is not None else None
            cur_conn_id = id(cur_conn) if cur_conn is not None else None

            changed = False
            if self._bound_master_url is not None and cur_url != self._bound_master_url:
                changed = True
            elif self._bound_env_db_path is not None and cur_env_path != self._bound_env_db_path:
                changed = True
            elif self._bound_master_conn_id is not None and cur_conn_id != self._bound_master_conn_id:
                changed = True

            if changed:
                self._close_cached_connections()

            self._bound_master_url = cur_url
            self._bound_env_db_path = cur_env_path
            self._bound_master_conn_id = cur_conn_id

    def _get_master_context(self) -> Tuple[Any, Any, bool, str]:
        """Return `(master_db, master_conn, is_postgres, master_url)` with fresh binding check."""
        mdb = self._resolve_master_db()
        self.sync_binding(mdb)
        master_url = resolve_effective_master_url()
        if getattr(mdb, "conn", None) is not None and isinstance(mdb.conn, sqlite3.Connection):
            if master_url.startswith("sqlite:///"):
                raw_target = master_url[len("sqlite:///"):]
                if raw_target and raw_target != ":memory:" and not raw_target.startswith("file::memory:"):
                    try:
                        target_resolved = str(Path(raw_target).expanduser().resolve())
                        db_rows = mdb.conn.execute("PRAGMA database_list").fetchall()
                        main_file = ""
                        for r in db_rows:
                            row_name = r["name"] if hasattr(r, "keys") else r[1]
                            row_file = r["file"] if hasattr(r, "keys") else r[2]
                            if row_name == "main":
                                main_file = str(row_file or "")
                                break
                        if main_file and str(Path(main_file).expanduser().resolve()) != target_resolved:
                            mdb.conn = None
                            self._close_cached_connections()
                    except Exception:
                        pass
        if getattr(mdb, "conn", None) is None:
            mdb.connect()
            self.sync_binding(mdb)
        master_url = resolve_effective_master_url()
        if isinstance(mdb.conn, sqlite3.Connection):
            is_pg = False
        else:
            is_pg = bool(getattr(mdb, "is_postgres", False)) or master_url.startswith(
                ("postgresql://", "postgres://")
            )
        return mdb, mdb.conn, is_pg, master_url

    def _master_execute(self, sql: str, params: tuple = ()) -> None:
        _, conn, is_pg, _ = self._get_master_context()
        if is_pg:
            pg_sql = sql.replace("?", "%s")
            with conn.cursor() as cur:
                if params:
                    cur.execute(pg_sql, tuple(params))
                else:
                    cur.execute(pg_sql)
        else:
            conn.execute(sql, params)
            try:
                conn.commit()
            except Exception:
                pass

    def _master_fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        _, conn, is_pg, _ = self._get_master_context()
        if is_pg:
            pg_sql = sql.replace("?", "%s")
            try:
                cur_ctx = conn.cursor(cursor_factory=RealDictCursor)
            except TypeError:
                cur_ctx = conn.cursor()
            with cur_ctx as cur:
                if params:
                    cur.execute(pg_sql, tuple(params))
                else:
                    cur.execute(pg_sql)
                rows = cur.fetchall()
                return self._normalize_pg_rows(rows, cur.description)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _master_fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self._master_fetchall(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _normalize_pg_rows(rows: Any, description: Any) -> List[Dict[str, Any]]:
        if not rows:
            return []
        first = rows[0]
        if isinstance(first, dict):
            return [dict(r) for r in rows]
        if hasattr(first, "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in description] if description else []
        out: List[Dict[str, Any]] = []
        for r in rows:
            if cols:
                out.append({cols[i]: r[i] for i in range(min(len(cols), len(r)))})
            else:
                out.append({"col_0": r[0]} if r else {})
        return out

    def _master_table_exists(self, table_name: str) -> bool:
        _, _, is_pg, _ = self._get_master_context()
        try:
            if is_pg:
                row = self._master_fetchone(
                    "SELECT 1 AS ok FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = ?",
                    (table_name,),
                )
            else:
                row = self._master_fetchone(
                    "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                )
            return bool(row)
        except Exception:
            return False

    def ensure_master_catalog(self) -> None:
        """Ensure `master_projects` and `master_branches` tables exist on Master DB."""
        with self._lock:
            _, conn, is_pg, _ = self._get_master_context()
            conn_id = id(conn)
            if self._master_catalog_ensured_conn_id == conn_id:
                return
            for stmt in _MASTER_CATALOG_STATEMENTS:
                self._master_execute(stmt.strip())
            self._master_catalog_ensured_conn_id = conn_id

    def _resolve_sqlite_projects_root(self, master_url: str) -> Optional[Path]:
        if not master_url.startswith("sqlite:///"):
            return None
        raw_path = master_url[len("sqlite:///"):]
        if not raw_path or raw_path == ":memory:" or raw_path.startswith("file::memory:"):
            return None
        master_dir = Path(raw_path).expanduser().resolve().parent
        return master_dir / "pb_projects"

    def _open_sqlite_tenant_conn(
        self, sqlite_target: str, include_root: bool
    ) -> sqlite3.Connection:
        if sqlite_target != ":memory:":
            Path(sqlite_target).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            sqlite_target, check_same_thread=False, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-4000")
        conn.execute("PRAGMA foreign_keys=OFF")
        self._init_tenant_tables_sqlite(conn, include_root=include_root)
        return conn

    def _ensure_source_columns_sqlite(
        self,
        conn: sqlite3.Connection,
        tables: Tuple[str, ...] = ("memories", "document_versions", "document_units"),
    ) -> None:
        for tbl in tables:
            try:
                rows = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                existing_cols = {
                    (r["name"] if hasattr(r, "keys") else r[1])
                    for r in rows
                }
                if not existing_cols:
                    continue
                for col_name, col_def in _SOURCE_METADATA_COLUMNS:
                    if col_name not in existing_cols:
                        conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col_name} {col_def}")
            except Exception as exc:
                logger.debug("SQLite column upgrader skipped for %s: %s", tbl, exc)

    def _ensure_source_columns_pg(
        self,
        cur: Any,
        tables: Tuple[str, ...] = ("memories", "document_versions", "document_units"),
    ) -> None:
        for tbl in tables:
            for col_name, col_def in _SOURCE_METADATA_COLUMNS:
                cur.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col_name} {col_def};"
                )

    def _ensure_source_columns_on_conn(
        self,
        conn: Any,
        is_pg: bool,
        tables: Tuple[str, ...] = ("document_versions", "document_units"),
    ) -> None:
        if is_pg:
            with conn.cursor() as cur:
                self._ensure_source_columns_pg(cur, tables=tables)
        else:
            self._ensure_source_columns_sqlite(conn, tables=tables)

    def _init_tenant_tables_sqlite(
        self, conn: sqlite3.Connection, include_root: bool
    ) -> None:
        for stmt in _TENANT_COMMON_STATEMENTS:
            conn.execute(stmt.strip())
        self._ensure_source_columns_sqlite(
            conn, tables=("memories", "document_versions", "document_units")
        )
        if include_root:
            for stmt in _ROOT_ONLY_STATEMENTS:
                conn.execute(stmt.strip())
            self._ensure_source_columns_sqlite(conn, tables=("memory_branch_links",))

    def _init_tenant_tables_pg(self, cur: Any, include_root: bool) -> None:
        for stmt in _TENANT_COMMON_STATEMENTS:
            cur.execute(_adapt_ddl_for_postgres(stmt.strip()))
        self._ensure_source_columns_pg(
            cur, tables=("memories", "document_versions", "document_units")
        )
        if include_root:
            for stmt in _ROOT_ONLY_STATEMENTS:
                cur.execute(_adapt_ddl_for_postgres(stmt.strip()))
            self._ensure_source_columns_pg(cur, tables=("memory_branch_links",))

    @staticmethod
    def _is_pg_conn_alive(conn: Any) -> bool:
        if conn is None:
            return False
        closed = getattr(conn, "closed", 0)
        return not bool(closed)

    def _open_pg_conn(self, dsn: str) -> Any:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn

    def _get_or_open_pg_tenant_conn(self, db_name: str, schema_name: str, master_url: str) -> Any:
        key = (db_name, schema_name)
        conn = self._pg_conns.get(key)
        if not self._is_pg_conn_alive(conn):
            proj_url = _replace_pg_dbname(master_url, db_name)
            conn = self._open_pg_conn(proj_url)
            self._pg_conns[key] = conn
        return conn

    def _create_pg_views_for_branch(self, cur: Any, schema_name: str) -> None:
        schema_ident = psql.Identifier(schema_name)
        v_docs = psql.SQL(
            """
            CREATE OR REPLACE VIEW {s}.v_resolved_document_versions AS
            SELECT id, project_id, branch, doc_key, version, parent_version, base_branch,
                   filename, file_sha256, total_units, reused_units, new_units, deleted_units,
                   status, created_at, 'branch' AS resolved_tier
            FROM {s}.document_versions
            WHERE status = 'active'
            UNION ALL
            SELECT r.id, r.project_id, r.branch, r.doc_key, r.version, r.parent_version, r.base_branch,
                   r.filename, r.file_sha256, r.total_units, r.reused_units, r.new_units, r.deleted_units,
                   r.status, r.created_at, 'root' AS resolved_tier
            FROM pb_root.document_versions r
            WHERE r.status = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM {s}.document_versions b
                  WHERE b.project_id = r.project_id
                    AND b.doc_key = r.doc_key
                    AND b.status = 'active'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {s}.memory_overlay_tombstones t
                  WHERE t.project_id = r.project_id
                    AND t.target_type = 'doc'
                    AND t.target_key = r.doc_key
              );
            """
        ).format(s=schema_ident)
        v_units = psql.SQL(
            """
            CREATE OR REPLACE VIEW {s}.v_resolved_document_units AS
            SELECT u.id, u.project_id, u.branch, u.doc_key, u.version, u.unit_index, u.unit_type,
                   u.unit_title, u.unit_sha256, u.markdown_text, u.memory_ids_json,
                   'branch' AS resolved_tier
            FROM {s}.document_units u
            INNER JOIN {s}.document_versions dv
              ON dv.project_id = u.project_id
             AND dv.branch = u.branch
             AND dv.doc_key = u.doc_key
             AND dv.version = u.version
            WHERE dv.status = 'active'
            UNION ALL
            SELECT u.id, u.project_id, u.branch, u.doc_key, u.version, u.unit_index, u.unit_type,
                   u.unit_title, u.unit_sha256, u.markdown_text, u.memory_ids_json,
                   'root' AS resolved_tier
            FROM pb_root.document_units u
            INNER JOIN pb_root.document_versions r
              ON r.project_id = u.project_id
             AND r.branch = u.branch
             AND r.doc_key = u.doc_key
             AND r.version = u.version
            WHERE r.status = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM {s}.document_versions b
                  WHERE b.project_id = r.project_id
                    AND b.doc_key = r.doc_key
                    AND b.status = 'active'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {s}.memory_overlay_tombstones t
                  WHERE t.project_id = r.project_id
                    AND t.target_type = 'doc'
                    AND t.target_key = r.doc_key
              );
            """
        ).format(s=schema_ident)
        cur.execute(v_docs)
        cur.execute(v_units)

    def ensure_project_database(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        root_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Provision physical project database (PostgreSQL DB or SQLite dir) + `pb_root`."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            eff_root = _effective_root_branch(root_branch)

            raw_proj = (project_id or "default").strip() or "default"
            extra_branch: Optional[str] = None
            parsed_src: Optional[str] = None
            proj, parsed_src, parsed_br = parse_tenant_spec(
                raw_proj, default_branch=eff_root
            )
            if ":" in raw_proj and parsed_br != eff_root and parsed_br != "%":
                extra_branch = parsed_br

            if (
                proj in self._provisioned_projects
                and name is None
                and description is None
                and extra_branch is None
            ):
                return dict(self._provisioned_projects[proj])

            self.ensure_master_catalog()
            if root_branch is None and proj not in self._provisioned_projects:
                existing_proj = self._master_fetchone(
                    "SELECT root_branch FROM master_projects WHERE project_id = ?", (proj,)
                )
                if existing_proj and existing_proj.get("root_branch"):
                    eff_root = str(existing_proj["root_branch"])
            db_name = sanitize_project_db_name(proj)
            proj_name = (name or proj).strip() or proj
            proj_desc = description if description is not None else f"Project {proj_name}"
            now_ms = int(time.time() * 1000)
            now_sec = int(now_ms // 1000)

            sqlite_dir_str: Optional[str] = None
            root_sqlite_str: Optional[str] = None
            graph_sqlite_str: Optional[str] = None

            if is_pg:
                backend_type = "postgresql"
                descriptor = _mask_url(_replace_pg_dbname(master_url, db_name))

                admin_conn = self._open_pg_conn(master_url)
                try:
                    with admin_conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
                        )
                        exists = bool(cur.fetchone())
                        if not exists:
                            cur.execute(
                                psql.SQL("CREATE DATABASE {} ENCODING 'UTF8';").format(
                                    psql.Identifier(db_name)
                                )
                            )
                finally:
                    try:
                        admin_conn.close()
                    except Exception:
                        pass

                root_conn = self._get_or_open_pg_tenant_conn(db_name, "pb_root", master_url)
                with root_conn.cursor() as cur:
                    cur.execute("CREATE SCHEMA IF NOT EXISTS pb_root;")
                    cur.execute("SET search_path TO pb_root, public;")
                    self._init_tenant_tables_pg(cur, include_root=True)
            else:
                backend_type = "sqlite"
                projects_root = self._resolve_sqlite_projects_root(master_url)
                if projects_root is None:
                    descriptor = f":memory:{db_name}"
                    root_sqlite_str = ":memory:"
                    graph_sqlite_str = ":memory:"
                    if (db_name, "pb_root") not in self._sqlite_conns:
                        self._sqlite_conns[(db_name, "pb_root")] = (
                            self._open_sqlite_tenant_conn(":memory:", include_root=True)
                        )
                else:
                    proj_dir = projects_root / db_name
                    proj_dir.mkdir(parents=True, exist_ok=True)
                    sqlite_dir_str = str(proj_dir)
                    root_sqlite_path = proj_dir / "pb_root.sqlite"
                    graph_sqlite_path = proj_dir / "graph.sqlite"
                    root_sqlite_str = str(root_sqlite_path)
                    graph_sqlite_str = str(graph_sqlite_path)
                    descriptor = sqlite_dir_str
                    if (db_name, "pb_root") not in self._sqlite_conns:
                        self._sqlite_conns[(db_name, "pb_root")] = (
                            self._open_sqlite_tenant_conn(
                                root_sqlite_str, include_root=True
                            )
                        )

            self._master_execute(
                """
                INSERT INTO master_projects (
                    project_id, name, description, db_name, root_branch, root_schema,
                    backend_type, db_descriptor, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id) DO UPDATE SET
                    name = excluded.name,
                    description = COALESCE(excluded.description, master_projects.description),
                    root_branch = excluded.root_branch,
                    backend_type = excluded.backend_type,
                    db_descriptor = excluded.db_descriptor,
                    status = 'active',
                    updated_at = excluded.updated_at
                """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                (
                    proj,
                    proj_name,
                    proj_desc,
                    db_name,
                    eff_root,
                    "pb_root",
                    backend_type,
                    descriptor,
                    "active",
                    now_ms,
                    now_ms,
                ),
            )

            if self._master_table_exists("projects"):
                self._master_execute(
                    """
                    INSERT INTO projects (id, name, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        name = excluded.name,
                        description = COALESCE(excluded.description, projects.description),
                        updated_at = excluded.updated_at
                    """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                    (proj, proj_name, proj_desc, now_sec, now_sec),
                )

            self._master_execute(
                """
                INSERT INTO master_branches (
                    project_id, branch_name, db_name, schema_name, base_branch,
                    is_root, has_graph_schema, has_doc_schema, author, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, branch_name) DO UPDATE SET
                    db_name = excluded.db_name,
                    schema_name = excluded.schema_name,
                    is_root = excluded.is_root,
                    updated_at = excluded.updated_at
                """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                (
                    proj,
                    eff_root,
                    db_name,
                    "pb_root",
                    eff_root,
                    1,
                    1,
                    1,
                    "system",
                    now_ms,
                    now_ms,
                ),
            )

            info: Dict[str, Any] = {
                "project_id": proj,
                "name": proj_name,
                "description": proj_desc,
                "db_name": db_name,
                "root_branch": eff_root,
                "root_schema": "pb_root",
                "backend_type": backend_type,
                "db_descriptor": descriptor,
                "sqlite_dir": sqlite_dir_str,
                "root_sqlite_path": root_sqlite_str,
                "graph_sqlite_path": graph_sqlite_str,
                "status": "active",
            }
            self._provisioned_projects[proj] = info
            self._provisioned_branches.add((proj, "pb_root"))

            if extra_branch:
                if _normalize_subsource_id(parsed_src):
                    self.ensure_source_branch_schema(
                        proj,
                        str(parsed_src),
                        extra_branch,
                        base_branch=eff_root,
                    )
                else:
                    self.ensure_branch_schema(
                        proj, extra_branch, base_branch=eff_root, record_lineage=False
                    )

            return dict(info)

    def ensure_branch_schema(
        self,
        project_id: str,
        branch_name: str,
        base_branch: str = "main",
        author: str = "system",
        has_graph: bool = True,
        has_doc: bool = True,
        *,
        record_lineage: bool = True,
        preserve_existing_base: bool = False,
    ) -> Dict[str, Any]:
        """Provision a dedicated schema (`pb_br_*`) or SQLite DB inside the project DB."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            proj_info = self.ensure_project_database(proj)
            _, _, is_pg, master_url = self._get_master_context()

            eff_root = proj_info["root_branch"]
            br = (branch_name or eff_root).strip() or eff_root
            eff_base = (base_branch or eff_root).strip() or eff_root
            db_name = proj_info["db_name"]
            schema_name = sanitize_branch_schema_for_project(br, eff_root)
            is_root = 1 if schema_name == "pb_root" else 0
            now_ms = int(time.time() * 1000)

            if preserve_existing_base:
                existing_row = self._master_fetchone(
                    "SELECT base_branch FROM master_branches WHERE project_id = ? AND branch_name = ?",
                    (proj, br),
                )
                if existing_row and existing_row.get("base_branch"):
                    eff_base = str(existing_row["base_branch"])

            sqlite_path_str: Optional[str] = proj_info.get("root_sqlite_path")

            if schema_name != "pb_root":
                if is_pg:
                    br_conn = self._get_or_open_pg_tenant_conn(
                        db_name, schema_name, master_url
                    )
                    if (proj, schema_name) not in self._provisioned_branches:
                        with br_conn.cursor() as cur:
                            cur.execute(
                                psql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(
                                    psql.Identifier(schema_name)
                                )
                            )
                            cur.execute(
                                psql.SQL("SET search_path TO {}, public;").format(
                                    psql.Identifier(schema_name)
                                )
                            )
                            self._init_tenant_tables_pg(cur, include_root=False)
                            self._create_pg_views_for_branch(cur, schema_name)
                else:
                    sqlite_dir = proj_info.get("sqlite_dir")
                    if sqlite_dir:
                        sqlite_path_str = str(Path(sqlite_dir) / f"{schema_name}.sqlite")
                    else:
                        sqlite_path_str = ":memory:"
                    if (db_name, schema_name) not in self._sqlite_conns:
                        self._sqlite_conns[(db_name, schema_name)] = (
                            self._open_sqlite_tenant_conn(
                                sqlite_path_str, include_root=False
                            )
                        )

            self._provisioned_branches.add((proj, schema_name))

            if record_lineage and not is_root and eff_base and eff_base != br:
                self._upsert_root_branch_link(proj, br, eff_base, db_name, is_pg, master_url, now_ms)

            self._master_execute(
                """
                INSERT INTO master_branches (
                    project_id, branch_name, db_name, schema_name, base_branch,
                    is_root, has_graph_schema, has_doc_schema, author, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, branch_name) DO UPDATE SET
                    db_name = excluded.db_name,
                    schema_name = excluded.schema_name,
                    base_branch = excluded.base_branch,
                    has_graph_schema = excluded.has_graph_schema,
                    has_doc_schema = excluded.has_doc_schema,
                    author = excluded.author,
                    updated_at = excluded.updated_at
                """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                (
                    proj,
                    br,
                    db_name,
                    schema_name,
                    eff_base,
                    is_root,
                    1 if has_graph else 0,
                    1 if has_doc else 0,
                    author or "system",
                    now_ms,
                    now_ms,
                ),
            )

            return {
                "project_id": proj,
                "branch_name": br,
                "db_name": db_name,
                "schema_name": schema_name,
                "base_branch": eff_base,
                "is_root": bool(is_root),
                "sqlite_path": sqlite_path_str if not is_pg else None,
            }

    def _upsert_root_branch_link(
        self,
        proj: str,
        branch_name: str,
        base_branch: str,
        db_name: str,
        is_pg: bool,
        master_url: str,
        now_ms: int,
    ) -> None:
        sql = (
            "INSERT INTO memory_branch_links (project_id, branch, base_branch, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (project_id, branch) DO UPDATE SET "
            "base_branch = excluded.base_branch, updated_at = excluded.updated_at"
        )
        if is_pg:
            root_conn = self._get_or_open_pg_tenant_conn(db_name, "pb_root", master_url)
            with root_conn.cursor() as cur:
                cur.execute("SET search_path TO pb_root, public;")
                cur.execute(
                    sql.replace("?", "%s").replace("excluded.", "EXCLUDED."),
                    (proj, branch_name, base_branch, now_ms),
                )
        else:
            root_conn = self._sqlite_conns.get((db_name, "pb_root"))
            if root_conn is not None:
                root_conn.execute(sql, (proj, branch_name, base_branch, now_ms))

    def _get_source_root_branch(
        self, proj: str, source_id: str, fallback_root: str
    ) -> str:
        cached = self._source_root_branches.get((proj, source_id))
        if cached:
            return cached
        if self._master_table_exists("master_project_sources"):
            row = self._master_fetchone(
                "SELECT root_branch FROM master_project_sources WHERE project_id = ? AND source_id = ?",
                (proj, source_id),
            )
            if row and row.get("root_branch"):
                rb = str(row["root_branch"]).strip() or fallback_root
                self._source_root_branches[(proj, source_id)] = rb
                return rb
        return fallback_root

    def get_tenant_connection(
        self,
        project_id: str,
        branch_name: str = "main",
        *,
        source_id: Optional[str] = None,
        record_lineage: bool = False,
    ) -> Tuple[Any, bool, str, str]:
        """Ensure project DB + branch/source schema exist and return `(conn, is_postgres, db_name, schema_name)`."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            eff_root = _effective_root_branch()
            proj = parse_tenant_from_user_id(project_id, default_branch=eff_root)[0]
            eff_source = _resolve_effective_subsource(project_id, source_id)
            br = (branch_name or eff_root).strip() or eff_root

            if proj in self._provisioned_projects:
                proj_root = self._provisioned_projects[proj]["root_branch"]
            else:
                proj_root = eff_root

            db_name = sanitize_project_db_name(proj)

            if eff_source is not None:
                src_root = self._get_source_root_branch(proj, eff_source, proj_root)
                schema_name = sanitize_source_schema_name(eff_source, br, src_root)
                if (proj, schema_name) not in self._provisioned_branches or proj not in self._provisioned_projects:
                    info = self.ensure_source_branch_schema(
                        proj,
                        eff_source,
                        br,
                        base_branch=src_root,
                    )
                    db_name = info["db_name"]
                    schema_name = info["schema_name"]

                if is_pg:
                    conn = self._get_or_open_pg_tenant_conn(db_name, schema_name, master_url)
                    with conn.cursor() as cur:
                        cur.execute(
                            psql.SQL("SET search_path TO {}, public;").format(
                                psql.Identifier(schema_name)
                            )
                        )
                    return conn, True, db_name, schema_name

                conn = self._sqlite_conns.get((db_name, schema_name))
                if conn is None:
                    info = self.ensure_source_branch_schema(
                        proj,
                        eff_source,
                        br,
                        base_branch=src_root,
                    )
                    conn = self._sqlite_conns[(info["db_name"], info["schema_name"])]
                    return conn, False, info["db_name"], info["schema_name"]
                return conn, False, db_name, schema_name

            schema_name = sanitize_branch_schema_for_project(br, proj_root)

            if (proj, schema_name) not in self._provisioned_branches or proj not in self._provisioned_projects:
                info = self.ensure_branch_schema(
                    proj,
                    br,
                    base_branch=proj_root,
                    record_lineage=record_lineage,
                    preserve_existing_base=True,
                )
                db_name = info["db_name"]
                schema_name = info["schema_name"]

            if is_pg:
                conn = self._get_or_open_pg_tenant_conn(db_name, schema_name, master_url)
                with conn.cursor() as cur:
                    cur.execute(
                        psql.SQL("SET search_path TO {}, public;").format(
                            psql.Identifier(schema_name)
                        )
                    )
                return conn, True, db_name, schema_name

            conn = self._sqlite_conns.get((db_name, schema_name))
            if conn is None:
                info = self.ensure_branch_schema(
                    proj,
                    br,
                    base_branch=proj_root,
                    record_lineage=record_lineage,
                    preserve_existing_base=True,
                )
                conn = self._sqlite_conns[(info["db_name"], info["schema_name"])]
                return conn, False, info["db_name"], info["schema_name"]
            return conn, False, db_name, schema_name

    def has_branch(
        self,
        project_id: str,
        branch_name: str = "main",
        *,
        source_id: Optional[str] = None,
    ) -> bool:
        """Check whether `(project_id, branch_name)` (or sub-source branch) has already been provisioned."""
        with self._lock:
            self._get_master_context()
            eff_root = _effective_root_branch()
            proj = parse_tenant_from_user_id(project_id, default_branch=eff_root)[0]
            eff_source = _resolve_effective_subsource(project_id, source_id)
            br = (branch_name or eff_root).strip() or eff_root
            proj_root = (
                self._provisioned_projects[proj]["root_branch"]
                if proj in self._provisioned_projects
                else eff_root
            )
            if eff_source is not None:
                src_root = self._get_source_root_branch(proj, eff_source, proj_root)
                schema_name = sanitize_source_schema_name(eff_source, br, src_root)
                if (proj, schema_name) in self._provisioned_branches:
                    db_name = sanitize_project_db_name(proj)
                    if (db_name, schema_name) in self._sqlite_conns or (
                        db_name,
                        schema_name,
                    ) in self._pg_conns:
                        return True
                if not self._master_table_exists("master_source_branches"):
                    return False
                row = self._master_fetchone(
                    "SELECT 1 AS ok FROM master_source_branches "
                    "WHERE project_id = ? AND source_id = ? AND branch_name = ?",
                    (proj, eff_source, br),
                )
                return bool(row)

            schema_name = sanitize_branch_schema_for_project(br, proj_root)
            if (proj, schema_name) in self._provisioned_branches:
                db_name = sanitize_project_db_name(proj)
                if (db_name, schema_name) in self._sqlite_conns or (
                    db_name,
                    schema_name,
                ) in self._pg_conns:
                    return True
            if not self._master_table_exists("master_branches"):
                return False
            row = self._master_fetchone(
                "SELECT 1 AS ok FROM master_branches WHERE project_id = ? AND branch_name = ?",
                (proj, br),
            )
            return bool(row)

    def list_project_branches(
        self, project_id: str, source_id: Optional[str] = None
    ) -> List[str]:
        """Return registered branch names for `project_id` (or sub-source) from catalog."""
        with self._lock:
            self._get_master_context()
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_source = _normalize_subsource_id(source_id)
            if eff_source is not None:
                if not self._master_table_exists("master_source_branches"):
                    return []
                rows = self._master_fetchall(
                    "SELECT branch_name FROM master_source_branches "
                    "WHERE project_id = ? AND source_id = ? ORDER BY is_root DESC, created_at ASC",
                    (proj, eff_source),
                )
                return [str(r["branch_name"]) for r in rows if r.get("branch_name")]

            if not self._master_table_exists("master_branches"):
                return []
            rows = self._master_fetchall(
                "SELECT branch_name FROM master_branches "
                "WHERE project_id = ? ORDER BY is_root DESC, created_at ASC",
                (proj,),
            )
            return [str(r["branch_name"]) for r in rows if r.get("branch_name")]

    def list_all_registered_branches(self) -> List[Tuple[str, str]]:
        """Return all registered `(project_id, branch_name)` pairs from `master_branches`."""
        with self._lock:
            self._get_master_context()
            if not self._master_table_exists("master_branches"):
                return []
            rows = self._master_fetchall(
                "SELECT project_id, branch_name FROM master_branches "
                "ORDER BY project_id ASC, is_root DESC, created_at ASC"
            )
            return [
                (str(r["project_id"]), str(r["branch_name"]))
                for r in rows
                if r.get("project_id") and r.get("branch_name")
            ]

    def list_all_registered_branch_targets(self) -> List[Tuple[str, Optional[str], str]]:
        """Return all registered `(project_id, source_id_or_none, branch_name)` targets across root & sub-sources."""
        with self._lock:
            self._get_master_context()
            targets: List[Tuple[str, Optional[str], str]] = []
            if self._master_table_exists("master_branches"):
                rows = self._master_fetchall(
                    "SELECT project_id, branch_name FROM master_branches "
                    "ORDER BY project_id ASC, is_root DESC, created_at ASC"
                )
                for r in rows:
                    if r.get("project_id") and r.get("branch_name"):
                        targets.append((str(r["project_id"]), None, str(r["branch_name"])))
            if self._master_table_exists("master_source_branches"):
                srows = self._master_fetchall(
                    "SELECT project_id, source_id, branch_name FROM master_source_branches "
                    "ORDER BY project_id ASC, source_id ASC, is_root DESC, created_at ASC"
                )
                for r in srows:
                    if r.get("project_id") and r.get("source_id") and r.get("branch_name"):
                        targets.append(
                            (
                                str(r["project_id"]),
                                str(r["source_id"]),
                                str(r["branch_name"]),
                            )
                        )
            return targets

    def remember_memory_id(
        self,
        memory_id: Optional[str],
        project_id: str,
        branch_name: str,
        source_id: Optional[str] = None,
    ) -> None:
        """Cache `memory_id -> (project_id, branch_name)` (and source hint) for O(1) ID routing."""
        if not memory_id:
            return
        mid = str(memory_id).strip()
        if not mid:
            return
        with self._lock:
            self._memory_id_hints[mid] = (project_id, branch_name)
            self._memory_id_hints.move_to_end(mid)
            eff_src = _normalize_subsource_id(source_id)
            if eff_src is not None:
                self._memory_source_hints[mid] = eff_src
                self._memory_source_hints.move_to_end(mid)
            else:
                self._memory_source_hints.pop(mid, None)
            while len(self._memory_id_hints) > _MAX_MEMORY_HINTS:
                evicted_mid, _ = self._memory_id_hints.popitem(last=False)
                self._memory_source_hints.pop(evicted_mid, None)
            while len(self._memory_source_hints) > _MAX_MEMORY_HINTS:
                self._memory_source_hints.popitem(last=False)

    def lookup_memory_hint(self, memory_id: Optional[str]) -> Optional[Tuple[str, str]]:
        """Return cached `(project_id, branch_name)` for `memory_id`, if known."""
        if not memory_id:
            return None
        mid = str(memory_id).strip()
        if not mid:
            return None
        with self._lock:
            hint = self._memory_id_hints.get(mid)
            if hint is not None:
                self._memory_id_hints.move_to_end(mid)
            return hint

    def lookup_memory_source_hint(self, memory_id: Optional[str]) -> Optional[str]:
        """Return cached sub-source ID for `memory_id`, if known."""
        if not memory_id:
            return None
        mid = str(memory_id).strip()
        if not mid:
            return None
        with self._lock:
            src = self._memory_source_hints.get(mid)
            if src is not None:
                self._memory_source_hints.move_to_end(mid)
            return src

    def get_project_graph_target(
        self, project_id: str, source_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return GraphStore target descriptor for `project_id` (and optional sub-source)."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_source = _resolve_effective_subsource(project_id, source_id)
            info = self.ensure_project_database(proj)
            _, _, is_pg, master_url = self._get_master_context()
            if eff_source is not None:
                src_root = self._get_source_root_branch(
                    proj, eff_source, info["root_branch"]
                )
                root_schema = sanitize_source_schema_name(
                    eff_source, src_root, src_root
                )
                sqlite_path: Optional[str] = None
                if not is_pg:
                    sqlite_dir = info.get("sqlite_dir")
                    if sqlite_dir:
                        src_dir = (
                            Path(sqlite_dir)
                            / "sources"
                            / _source_dir_slug(eff_source)
                        )
                        src_dir.mkdir(parents=True, exist_ok=True)
                        sqlite_path = str(src_dir / "graph.sqlite")
                    else:
                        sqlite_path = ":memory:"
                return {
                    "is_postgres": is_pg,
                    "db_name": info["db_name"],
                    "dsn": _replace_pg_dbname(master_url, info["db_name"]) if is_pg else None,
                    "sqlite_path": sqlite_path,
                    "root_schema": root_schema,
                    "source_id": eff_source,
                }
            return {
                "is_postgres": is_pg,
                "db_name": info["db_name"],
                "dsn": _replace_pg_dbname(master_url, info["db_name"]) if is_pg else None,
                "sqlite_path": info["graph_sqlite_path"] if not is_pg else None,
                "root_schema": "pb_root",
            }

    def drop_branch_schema(self, project_id: str, branch_name: str) -> Dict[str, Any]:
        """Drop a non-root branch schema (or branch SQLite file) and remove catalog metadata."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            self.ensure_master_catalog()
            proj = parse_tenant_from_user_id(project_id)[0]
            proj_row = self._master_fetchone(
                "SELECT db_name, root_branch, db_descriptor FROM master_projects WHERE project_id = ?",
                (proj,),
            )
            eff_root = (
                str(proj_row["root_branch"])
                if proj_row and proj_row.get("root_branch")
                else _effective_root_branch()
            )
            br = (branch_name or "").strip()
            schema_name = sanitize_branch_schema_for_project(br, eff_root)
            if schema_name == "pb_root":
                raise ValueError("Cannot drop canonical root schema 'pb_root'")

            db_name = (
                str(proj_row["db_name"])
                if proj_row and proj_row.get("db_name")
                else sanitize_project_db_name(proj)
            )

            for cache_dict in (self._sqlite_conns, self._pg_conns):
                cached = cache_dict.pop((db_name, schema_name), None)
                if cached is not None:
                    try:
                        cached.close()
                    except Exception:
                        pass

            self._provisioned_branches.discard((proj, schema_name))
            stale_mids = [
                mid
                for mid, (p, b) in self._memory_id_hints.items()
                if p == proj and b == br
            ]
            for mid in stale_mids:
                self._memory_id_hints.pop(mid, None)

            if is_pg:
                proj_url = _replace_pg_dbname(master_url, db_name)
                conn = self._open_pg_conn(proj_url)
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            psql.SQL("DROP SCHEMA IF EXISTS {} CASCADE;").format(
                                psql.Identifier(schema_name)
                            )
                        )
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

                root_conn = self._get_or_open_pg_tenant_conn(db_name, "pb_root", master_url)
                with root_conn.cursor() as cur:
                    cur.execute("SET search_path TO pb_root, public;")
                    cur.execute(
                        "DELETE FROM memory_branch_links WHERE project_id = %s AND branch = %s",
                        (proj, br),
                    )
            else:
                projects_root = self._resolve_sqlite_projects_root(master_url)
                if projects_root is not None:
                    proj_dir = projects_root / db_name
                    for suffix in ("", "-wal", "-shm"):
                        p = proj_dir / f"{schema_name}.sqlite{suffix}"
                        if p.exists():
                            try:
                                p.unlink()
                            except Exception:
                                pass
                root_conn = self._sqlite_conns.get((db_name, "pb_root"))
                if root_conn is not None:
                    root_conn.execute(
                        "DELETE FROM memory_branch_links WHERE project_id = ? AND branch = ?",
                        (proj, br),
                    )

            self._master_execute(
                "DELETE FROM master_branches WHERE project_id = ? AND branch_name = ?",
                (proj, br),
            )
            return {
                "status": "dropped",
                "project_id": proj,
                "branch_name": br,
                "db_name": db_name,
                "schema_name": schema_name,
            }

    def drop_project_database(self, project_id: str) -> Dict[str, Any]:
        """Drop a project's physical database/directory and remove all Master catalog entries."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            self.ensure_master_catalog()
            proj = parse_tenant_from_user_id(project_id)[0]
            proj_row = self._master_fetchone(
                "SELECT db_name FROM master_projects WHERE project_id = ?", (proj,)
            )
            db_name = (
                str(proj_row["db_name"])
                if proj_row and proj_row.get("db_name")
                else sanitize_project_db_name(proj)
            )

            for cache_dict in (self._sqlite_conns, self._pg_conns):
                for key in [k for k in cache_dict if k[0] == db_name]:
                    conn = cache_dict.pop(key, None)
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass

            self._provisioned_projects.pop(proj, None)
            for key in [k for k in self._provisioned_branches if k[0] == proj]:
                self._provisioned_branches.discard(key)
            for key in [k for k in self._source_root_branches if k[0] == proj]:
                self._source_root_branches.pop(key, None)
            for mid in [m for m, (p, _) in self._memory_id_hints.items() if p == proj]:
                self._memory_id_hints.pop(mid, None)
                self._memory_source_hints.pop(mid, None)

            if is_pg:
                admin_conn = self._open_pg_conn(master_url)
                try:
                    with admin_conn.cursor() as cur:
                        try:
                            cur.execute(
                                "SELECT pg_terminate_backend(pid) "
                                "FROM pg_stat_activity "
                                "WHERE datname = %s AND pid <> pg_backend_pid()",
                                (db_name,),
                            )
                        except Exception:
                            pass
                        cur.execute(
                            psql.SQL("DROP DATABASE IF EXISTS {};").format(
                                psql.Identifier(db_name)
                            )
                        )
                finally:
                    try:
                        admin_conn.close()
                    except Exception:
                        pass
            else:
                projects_root = self._resolve_sqlite_projects_root(master_url)
                if projects_root is not None:
                    proj_dir = projects_root / db_name
                    if proj_dir.exists():
                        shutil.rmtree(proj_dir, ignore_errors=True)

            self._master_execute(
                "DELETE FROM master_branches WHERE project_id = ?", (proj,)
            )
            for tbl in (
                "master_source_branches",
                "master_project_sources",
                "master_source_upgrades",
                "master_shared_doc_bindings",
            ):
                if self._master_table_exists(tbl):
                    self._master_execute(
                        f"DELETE FROM {tbl} WHERE project_id = ?", (proj,)
                    )
            self._master_execute(
                "DELETE FROM master_projects WHERE project_id = ?", (proj,)
            )
            if self._master_table_exists("projects"):
                self._master_execute("DELETE FROM projects WHERE id = ?", (proj,))

            return {
                "status": "dropped",
                "project_id": proj,
                "db_name": db_name,
            }

    # ------------------------------------------------------------------
    # Multi-Source Project Hub, Shared Doc Bindings & Cross-Source Graph
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_json_loads(raw: Any, default: Any) -> Any:
        if raw is None:
            return default
        if isinstance(raw, (list, dict)):
            return raw
        try:
            parsed = json.loads(str(raw))
            return parsed if parsed is not None else default
        except Exception:
            return default

    def _format_source_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        out["inherit_shared_docs"] = bool(row.get("inherit_shared_docs", 1))
        out["sync_generation"] = int(row.get("sync_generation") or 0)
        out["tech_stack"] = self._safe_json_loads(row.get("tech_stack_json"), [])
        out["metadata"] = self._safe_json_loads(row.get("metadata_json"), {})
        return out

    def _format_relation_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        out["is_bidirectional"] = bool(row.get("is_bidirectional", 0))
        out["confidence_score"] = float(
            row["confidence_score"] if row.get("confidence_score") is not None else 1.0
        )
        out["contract_metadata"] = self._safe_json_loads(
            row.get("contract_metadata_json"), {}
        )
        return out

    def _get_project_root_conn(self, project_id: str) -> Tuple[Any, bool, str]:
        """Return `(conn, is_pg, clean_project_id)` targeting Project DB `pb_root` (or Master if single-DB)."""
        proj = parse_tenant_from_user_id(project_id)[0]
        if not is_multi_db_enabled():
            _, conn, is_pg, _ = self._get_master_context()
            return conn, is_pg, proj
        proj_info = self.ensure_project_database(proj)
        eff_root = str(proj_info.get("root_branch") or _effective_root_branch())
        conn, is_pg, _, _ = self.get_tenant_connection(
            proj, eff_root, source_id="__default__"
        )
        return conn, is_pg, proj

    def _project_root_execute(
        self, project_id: str, sql: str, params: tuple = ()
    ) -> int:
        conn, is_pg, _ = self._get_project_root_conn(project_id)
        if is_pg:
            pg_sql = sql.replace("?", "%s").replace("excluded.", "EXCLUDED.")
            with conn.cursor() as cur:
                if params:
                    cur.execute(pg_sql, tuple(params))
                else:
                    cur.execute(pg_sql)
                return int(getattr(cur, "rowcount", 0) or 0)
        res = conn.execute(sql, params)
        try:
            conn.commit()
        except Exception:
            pass
        return int(getattr(res, "rowcount", 0) or 0)

    def _project_root_fetchall(
        self, project_id: str, sql: str, params: tuple = ()
    ) -> List[Dict[str, Any]]:
        conn, is_pg, _ = self._get_project_root_conn(project_id)
        if is_pg:
            pg_sql = sql.replace("?", "%s")
            try:
                cur_ctx = conn.cursor(cursor_factory=RealDictCursor)
            except TypeError:
                cur_ctx = conn.cursor()
            with cur_ctx as cur:
                if params:
                    cur.execute(pg_sql, tuple(params))
                else:
                    cur.execute(pg_sql)
                rows = cur.fetchall()
                return self._normalize_pg_rows(rows, cur.description)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _project_root_fetchone(
        self, project_id: str, sql: str, params: tuple = ()
    ) -> Optional[Dict[str, Any]]:
        rows = self._project_root_fetchall(project_id, sql, params)
        return rows[0] if rows else None

    def _project_exists_in_catalog(self, proj: str) -> bool:
        if proj in self._provisioned_projects:
            return True
        if not self._master_table_exists("master_projects"):
            return False
        row = self._master_fetchone(
            "SELECT 1 AS ok FROM master_projects WHERE project_id = ?", (proj,)
        )
        return bool(row)

    def ensure_source_branch_schema(
        self,
        project_id: str,
        source_id: str,
        branch_name: str,
        base_branch: str = "main",
        author: str = "system",
        has_graph: bool = True,
        has_doc: bool = True,
        source_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Provision a dedicated schema (`pb_src_*`) or SQLite DB for `(project_id, source_id, branch_name)`."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_src = _normalize_subsource_id(source_id)
            if eff_src is None:
                return self.ensure_branch_schema(
                    proj,
                    branch_name,
                    base_branch=base_branch,
                    author=author,
                    has_graph=has_graph,
                    has_doc=has_doc,
                )

            proj_info = self.ensure_project_database(proj)
            _, _, is_pg, master_url = self._get_master_context()
            self.ensure_master_catalog()

            src_row = self._master_fetchone(
                "SELECT root_branch, current_version, sync_generation "
                "FROM master_project_sources WHERE project_id = ? AND source_id = ?",
                (proj, eff_src),
            )
            eff_root = (
                str(src_row["root_branch"]).strip()
                if src_row and src_row.get("root_branch")
                else self._get_source_root_branch(
                    proj, eff_src, str(proj_info.get("root_branch") or "main")
                )
            )
            self._source_root_branches[(proj, eff_src)] = eff_root

            br = (branch_name or eff_root).strip() or eff_root
            eff_base = (base_branch or eff_root).strip() or eff_root
            db_name = proj_info["db_name"]
            schema_name = sanitize_source_schema_name(eff_src, br, eff_root)
            root_schema_for_src = sanitize_source_schema_name(
                eff_src, eff_root, eff_root
            )
            is_root = 1 if (br == eff_root or schema_name == root_schema_for_src) else 0
            now_ms = int(time.time() * 1000)

            eff_ver = (
                source_version
                if source_version is not None
                else (
                    str(src_row["current_version"])
                    if src_row and src_row.get("current_version")
                    else "1.0.0"
                )
            )
            sync_gen = int(src_row.get("sync_generation") or 0) if src_row else 0

            sqlite_path_str: Optional[str] = None
            if is_pg:
                br_conn = self._get_or_open_pg_tenant_conn(
                    db_name, schema_name, master_url
                )
                if (proj, schema_name) not in self._provisioned_branches:
                    with br_conn.cursor() as cur:
                        cur.execute(
                            psql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(
                                psql.Identifier(schema_name)
                            )
                        )
                        cur.execute(
                            psql.SQL("SET search_path TO {}, public;").format(
                                psql.Identifier(schema_name)
                            )
                        )
                        self._init_tenant_tables_pg(cur, include_root=False)
            else:
                sqlite_dir = proj_info.get("sqlite_dir")
                if sqlite_dir:
                    src_dir = Path(sqlite_dir) / "sources" / _source_dir_slug(eff_src)
                    src_dir.mkdir(parents=True, exist_ok=True)
                    sqlite_path_str = str(src_dir / f"{schema_name}.sqlite")
                else:
                    sqlite_path_str = ":memory:"
                if (db_name, schema_name) not in self._sqlite_conns:
                    self._sqlite_conns[(db_name, schema_name)] = (
                        self._open_sqlite_tenant_conn(
                            sqlite_path_str, include_root=False
                        )
                    )

            self._provisioned_branches.add((proj, schema_name))

            self._master_execute(
                """
                INSERT INTO master_source_branches (
                    project_id, source_id, branch_name, db_name, schema_name, base_branch,
                    is_root, has_graph_schema, has_doc_schema, source_version, sync_generation,
                    commit_sha, author, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, source_id, branch_name) DO UPDATE SET
                    db_name = excluded.db_name,
                    schema_name = excluded.schema_name,
                    base_branch = excluded.base_branch,
                    is_root = excluded.is_root,
                    has_graph_schema = excluded.has_graph_schema,
                    has_doc_schema = excluded.has_doc_schema,
                    source_version = COALESCE(excluded.source_version, master_source_branches.source_version),
                    author = excluded.author,
                    updated_at = excluded.updated_at
                """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                (
                    proj,
                    eff_src,
                    br,
                    db_name,
                    schema_name,
                    eff_base,
                    is_root,
                    1 if has_graph else 0,
                    1 if has_doc else 0,
                    eff_ver,
                    sync_gen,
                    None,
                    author or "system",
                    now_ms,
                    now_ms,
                ),
            )

            return {
                "project_id": proj,
                "source_id": eff_src,
                "branch_name": br,
                "db_name": db_name,
                "schema_name": schema_name,
                "base_branch": eff_base,
                "is_root": bool(is_root),
                "has_graph_schema": bool(has_graph),
                "has_doc_schema": bool(has_doc),
                "source_version": eff_ver,
                "sync_generation": sync_gen,
                "sqlite_path": sqlite_path_str if not is_pg else None,
            }

    def register_project_source(
        self,
        project_id: str,
        source_id: str,
        display_name: Optional[str] = None,
        source_type: str = "service",
        tech_stack: Optional[Any] = None,
        repo_url: Optional[str] = None,
        workspace_subpath: Optional[str] = None,
        root_branch: str = "main",
        current_version: str = "1.0.0",
        inherit_shared_docs: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register (or update) a sub-source within `project_id` and provision its root schema."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_src = _normalize_subsource_id(source_id)
            if eff_src is None:
                raise ValueError(
                    "Invalid sub-source id: cannot be empty, '__default__', or '__shared__'"
                )

            proj_info = self.ensure_project_database(proj)
            _, _, is_pg, _ = self._get_master_context()
            self.ensure_master_catalog()

            eff_root = (
                (root_branch or "").strip()
                or str(proj_info.get("root_branch") or _effective_root_branch())
            )
            root_schema = sanitize_source_schema_name(eff_src, eff_root, eff_root)
            self._source_root_branches[(proj, eff_src)] = eff_root

            if tech_stack is None:
                tech_stack_json = "[]"
            elif isinstance(tech_stack, str):
                tech_stack_json = tech_stack
            else:
                tech_stack_json = json.dumps(tech_stack)

            if metadata is None:
                metadata_json = "{}"
            elif isinstance(metadata, str):
                metadata_json = metadata
            else:
                metadata_json = json.dumps(metadata)

            disp = (display_name or eff_src).strip() or eff_src
            eff_type = (source_type or "service").strip() or "service"
            eff_ver = (current_version or "1.0.0").strip() or "1.0.0"
            inherit_int = 1 if inherit_shared_docs else 0
            now_ms = int(time.time() * 1000)
            rec_id = (
                f"src_{hashlib.sha256(f'{proj}::{eff_src}'.encode('utf-8')).hexdigest()[:16]}"
            )

            self._master_execute(
                """
                INSERT INTO master_project_sources (
                    id, project_id, source_id, display_name, source_type, tech_stack_json,
                    repo_url, workspace_subpath, root_branch, root_schema, current_version,
                    sync_generation, last_commit_sha, last_sync_status, inherit_shared_docs,
                    status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, source_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    source_type = excluded.source_type,
                    tech_stack_json = excluded.tech_stack_json,
                    repo_url = COALESCE(excluded.repo_url, master_project_sources.repo_url),
                    workspace_subpath = COALESCE(excluded.workspace_subpath, master_project_sources.workspace_subpath),
                    root_branch = excluded.root_branch,
                    root_schema = excluded.root_schema,
                    current_version = COALESCE(excluded.current_version, master_project_sources.current_version),
                    inherit_shared_docs = excluded.inherit_shared_docs,
                    status = 'active',
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                (
                    rec_id,
                    proj,
                    eff_src,
                    disp,
                    eff_type,
                    tech_stack_json,
                    repo_url,
                    workspace_subpath,
                    eff_root,
                    root_schema,
                    eff_ver,
                    0,
                    None,
                    "pending",
                    inherit_int,
                    "active",
                    metadata_json,
                    now_ms,
                    now_ms,
                ),
            )

            branch_info = self.ensure_source_branch_schema(
                proj,
                eff_src,
                eff_root,
                base_branch=eff_root,
                author="system",
                has_graph=True,
                has_doc=True,
                source_version=eff_ver,
            )

            src_obj = self.get_project_source(proj, eff_src)
            if src_obj is not None:
                src_obj["db_name"] = branch_info["db_name"]
                src_obj["sqlite_path"] = branch_info.get("sqlite_path")
                return src_obj
            return {
                "id": rec_id,
                "project_id": proj,
                "source_id": eff_src,
                "display_name": disp,
                "source_type": eff_type,
                "tech_stack": self._safe_json_loads(tech_stack_json, []),
                "tech_stack_json": tech_stack_json,
                "repo_url": repo_url,
                "workspace_subpath": workspace_subpath,
                "root_branch": eff_root,
                "root_schema": root_schema,
                "current_version": eff_ver,
                "sync_generation": 0,
                "last_commit_sha": None,
                "last_sync_status": "pending",
                "inherit_shared_docs": bool(inherit_int),
                "status": "active",
                "metadata": self._safe_json_loads(metadata_json, {}),
                "metadata_json": metadata_json,
                "created_at": now_ms,
                "updated_at": now_ms,
                "db_name": branch_info["db_name"],
                "sqlite_path": branch_info.get("sqlite_path"),
            }

    def list_project_sources(self, project_id: str) -> List[Dict[str, Any]]:
        """List registered sub-sources for `project_id`."""
        with self._lock:
            self._get_master_context()
            if not self._master_table_exists("master_project_sources"):
                return []
            proj = parse_tenant_from_user_id(project_id)[0]
            rows = self._master_fetchall(
                "SELECT * FROM master_project_sources "
                "WHERE project_id = ? ORDER BY created_at ASC, source_id ASC",
                (proj,),
            )
            return [self._format_source_row(r) for r in rows]

    def get_project_source(
        self, project_id: str, source_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single registered sub-source descriptor for `(project_id, source_id)`."""
        with self._lock:
            self._get_master_context()
            if not self._master_table_exists("master_project_sources"):
                return None
            proj = parse_tenant_from_user_id(project_id)[0]
            src = (source_id or "").strip()
            if not src:
                return None
            row = self._master_fetchone(
                "SELECT * FROM master_project_sources WHERE project_id = ? AND source_id = ?",
                (proj, src),
            )
            if not row:
                return None
            formatted = self._format_source_row(row)
            if self._master_table_exists("master_source_branches"):
                branches = self._master_fetchall(
                    "SELECT * FROM master_source_branches "
                    "WHERE project_id = ? AND source_id = ? ORDER BY is_root DESC, created_at ASC",
                    (proj, src),
                )
                formatted["branches"] = [dict(b) for b in branches]
            return formatted

    def update_project_source(
        self, project_id: str, source_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """Update mutable attributes on a registered sub-source."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            src = (source_id or "").strip()
            existing = self.get_project_source(proj, src)
            if existing is None:
                return None

            set_clauses: List[str] = []
            params: List[Any] = []

            direct_cols = {
                "display_name",
                "source_type",
                "tech_stack_json",
                "repo_url",
                "workspace_subpath",
                "root_branch",
                "root_schema",
                "current_version",
                "sync_generation",
                "last_commit_sha",
                "last_sync_status",
                "status",
                "metadata_json",
            }
            for key, val in fields.items():
                if key == "tech_stack":
                    set_clauses.append("tech_stack_json = ?")
                    params.append(
                        val if isinstance(val, str) else json.dumps(val or [])
                    )
                elif key == "metadata":
                    set_clauses.append("metadata_json = ?")
                    params.append(
                        val if isinstance(val, str) else json.dumps(val or {})
                    )
                elif key == "inherit_shared_docs":
                    set_clauses.append("inherit_shared_docs = ?")
                    params.append(1 if val else 0)
                elif key in direct_cols:
                    set_clauses.append(f"{key} = ?")
                    params.append(val)

            if not set_clauses:
                return existing

            now_ms = int(time.time() * 1000)
            set_clauses.append("updated_at = ?")
            params.append(now_ms)
            params.extend([proj, src])

            self._master_execute(
                f"UPDATE master_project_sources SET {', '.join(set_clauses)} "
                "WHERE project_id = ? AND source_id = ?",
                tuple(params),
            )
            if "root_branch" in fields and fields["root_branch"]:
                self._source_root_branches[(proj, src)] = str(fields["root_branch"]).strip()
            return self.get_project_source(proj, src)

    def drop_project_source(self, project_id: str, source_id: str) -> Dict[str, Any]:
        """Drop a single sub-source's SQLite subdir / PG schemas and catalog entries, leaving siblings & pb_root intact."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            self.ensure_master_catalog()
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_src = _normalize_subsource_id(source_id)
            if eff_src is None:
                raise ValueError(
                    "Cannot drop canonical root/shared scope; provide an explicit sub-source id"
                )

            proj_row = self._master_fetchone(
                "SELECT db_name, root_branch FROM master_projects WHERE project_id = ?",
                (proj,),
            )
            db_name = (
                str(proj_row["db_name"])
                if proj_row and proj_row.get("db_name")
                else sanitize_project_db_name(proj)
            )
            proj_root = (
                str(proj_row["root_branch"])
                if proj_row and proj_row.get("root_branch")
                else _effective_root_branch()
            )
            src_root = self._get_source_root_branch(proj, eff_src, proj_root)
            src_prefix = f"pb_src_{_source_dir_slug(eff_src)}_"

            schemas_to_drop: Set[str] = {
                sanitize_source_schema_name(eff_src, src_root, src_root)
            }
            if self._master_table_exists("master_source_branches"):
                branch_rows = self._master_fetchall(
                    "SELECT schema_name FROM master_source_branches "
                    "WHERE project_id = ? AND source_id = ?",
                    (proj, eff_src),
                )
                for br_row in branch_rows:
                    if br_row.get("schema_name"):
                        schemas_to_drop.add(str(br_row["schema_name"]))

            for _, s_name in list(self._provisioned_branches):
                if s_name.startswith(src_prefix):
                    schemas_to_drop.add(s_name)

            for cache_dict in (self._sqlite_conns, self._pg_conns):
                for key in list(cache_dict.keys()):
                    if key[0] == db_name and (
                        key[1] in schemas_to_drop or key[1].startswith(src_prefix)
                    ):
                        schemas_to_drop.add(key[1])
                        conn = cache_dict.pop(key, None)
                        if conn is not None:
                            try:
                                conn.close()
                            except Exception:
                                pass

            for s_name in schemas_to_drop:
                self._provisioned_branches.discard((proj, s_name))
            self._source_root_branches.pop((proj, eff_src), None)

            stale_mids = [
                mid
                for mid, src_val in self._memory_source_hints.items()
                if src_val == eff_src
                and self._memory_id_hints.get(mid, (None, None))[0] == proj
            ]
            for mid in stale_mids:
                self._memory_source_hints.pop(mid, None)
                self._memory_id_hints.pop(mid, None)

            if is_pg:
                proj_url = _replace_pg_dbname(master_url, db_name)
                try:
                    conn = self._open_pg_conn(proj_url)
                    try:
                        with conn.cursor() as cur:
                            try:
                                cur.execute(
                                    "SELECT schema_name FROM information_schema.schemata "
                                    "WHERE schema_name LIKE %s",
                                    (f"{src_prefix}%",),
                                )
                                for row in cur.fetchall() or []:
                                    if row and row[0]:
                                        schemas_to_drop.add(str(row[0]))
                            except Exception:
                                pass
                            for s_name in sorted(schemas_to_drop):
                                cur.execute(
                                    psql.SQL("DROP SCHEMA IF EXISTS {} CASCADE;").format(
                                        psql.Identifier(s_name)
                                    )
                                )
                            try:
                                cur.execute(
                                    "SELECT 1 FROM information_schema.tables "
                                    "WHERE table_schema = 'pb_root' AND table_name = 'cross_source_relations'"
                                )
                                if cur.fetchone():
                                    cur.execute(
                                        "DELETE FROM pb_root.cross_source_relations "
                                        "WHERE project_id = %s AND (src_source_id = %s OR dst_source_id = %s)",
                                        (proj, eff_src, eff_src),
                                    )
                            except Exception:
                                pass
                            try:
                                cur.execute(
                                    "SELECT 1 FROM information_schema.tables "
                                    "WHERE table_schema = 'pb_root' AND table_name = 'cross_source_contract_artifacts'"
                                )
                                if cur.fetchone():
                                    cur.execute(
                                        "DELETE FROM pb_root.cross_source_contract_artifacts "
                                        "WHERE project_id = %s AND source_id = %s",
                                        (proj, eff_src),
                                    )
                            except Exception:
                                pass
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                except Exception as exc:
                    logger.debug(
                        "PG cleanup warning dropping source %s@%s: %s",
                        proj,
                        eff_src,
                        exc,
                    )
            else:
                projects_root = self._resolve_sqlite_projects_root(master_url)
                root_conn = self._sqlite_conns.get((db_name, "pb_root"))
                opened_temp_root = False
                if projects_root is not None:
                    proj_dir = projects_root / db_name
                    src_dir = proj_dir / "sources" / _source_dir_slug(eff_src)
                    if src_dir.exists():
                        shutil.rmtree(src_dir, ignore_errors=True)
                    root_db_path = proj_dir / "pb_root.sqlite"
                    if root_conn is None and root_db_path.is_file():
                        root_conn = self._open_sqlite_tenant_conn(
                            str(root_db_path), include_root=True
                        )
                        opened_temp_root = True
                if root_conn is not None:
                    try:
                        has_rel = bool(
                            root_conn.execute(
                                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cross_source_relations'"
                            ).fetchone()
                        )
                        if has_rel:
                            root_conn.execute(
                                "DELETE FROM cross_source_relations "
                                "WHERE project_id = ? AND (src_source_id = ? OR dst_source_id = ?)",
                                (proj, eff_src, eff_src),
                            )
                        has_art = bool(
                            root_conn.execute(
                                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cross_source_contract_artifacts'"
                            ).fetchone()
                        )
                        if has_art:
                            root_conn.execute(
                                "DELETE FROM cross_source_contract_artifacts "
                                "WHERE project_id = ? AND source_id = ?",
                                (proj, eff_src),
                            )
                        root_conn.commit()
                    finally:
                        if opened_temp_root:
                            root_conn.close()

            if self._master_table_exists("master_source_branches"):
                self._master_execute(
                    "DELETE FROM master_source_branches WHERE project_id = ? AND source_id = ?",
                    (proj, eff_src),
                )
            if self._master_table_exists("master_project_sources"):
                self._master_execute(
                    "DELETE FROM master_project_sources WHERE project_id = ? AND source_id = ?",
                    (proj, eff_src),
                )
            if self._master_table_exists("master_source_upgrades"):
                self._master_execute(
                    "DELETE FROM master_source_upgrades WHERE project_id = ? AND source_id = ?",
                    (proj, eff_src),
                )
            if self._master_table_exists("master_shared_doc_bindings"):
                self._master_execute(
                    "DELETE FROM master_shared_doc_bindings WHERE project_id = ? AND target_source_id = ?",
                    (proj, eff_src),
                )

            return {
                "status": "dropped",
                "deleted": True,
                "project_id": proj,
                "source_id": eff_src,
                "db_name": db_name,
                "schemas_dropped": sorted(schemas_to_drop),
            }

    def record_source_upgrade(
        self,
        project_id: str,
        source_id: str,
        branch_name: str,
        to_version: str,
        *,
        from_version: Optional[str] = None,
        upgrade_mode: str = "delta",
        nodes_delta: int = 0,
        edges_delta: int = 0,
        docs_updated: int = 0,
        affected_relations: int = 0,
        drifted_relations: int = 0,
        commit_sha: Optional[str] = None,
        actor: str = "system",
    ) -> Dict[str, Any]:
        """Record an independent source upgrade/sync event and advance `sync_generation`."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_src = _normalize_subsource_id(source_id) or (
                str(source_id or "").strip() or "__default__"
            )
            self._get_master_context()
            self.ensure_master_catalog()

            src_row = self._master_fetchone(
                "SELECT root_branch, current_version, sync_generation "
                "FROM master_project_sources WHERE project_id = ? AND source_id = ?",
                (proj, eff_src),
            )
            eff_from = (
                from_version
                if from_version is not None
                else (src_row.get("current_version") if src_row else None)
            )
            prev_gen = int(src_row.get("sync_generation") or 0) if src_row else 0
            next_gen = prev_gen + 1
            eff_br = (
                (branch_name or "").strip()
                or (str(src_row["root_branch"]).strip() if src_row and src_row.get("root_branch") else "")
                or _effective_root_branch()
            )
            eff_to = (to_version or "").strip() or (eff_from or "1.0.0")
            eff_mode = (upgrade_mode or "delta").strip() or "delta"
            eff_actor = (actor or "system").strip() or "system"
            now_ms = int(time.time() * 1000)
            upg_id = f"upg_{uuid.uuid4().hex[:16]}"

            self._master_execute(
                """
                INSERT INTO master_source_upgrades (
                    id, project_id, source_id, branch_name, from_version, to_version,
                    sync_generation, upgrade_mode, nodes_delta, edges_delta, docs_updated,
                    affected_relations, drifted_relations, commit_sha, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upg_id,
                    proj,
                    eff_src,
                    eff_br,
                    eff_from,
                    eff_to,
                    next_gen,
                    eff_mode,
                    int(nodes_delta or 0),
                    int(edges_delta or 0),
                    int(docs_updated or 0),
                    int(affected_relations or 0),
                    int(drifted_relations or 0),
                    commit_sha,
                    eff_actor,
                    now_ms,
                ),
            )

            if src_row is not None:
                self._master_execute(
                    """
                    UPDATE master_project_sources
                    SET current_version = ?,
                        sync_generation = ?,
                        last_commit_sha = COALESCE(?, last_commit_sha),
                        last_sync_status = 'synced',
                        updated_at = ?
                    WHERE project_id = ? AND source_id = ?
                    """,
                    (eff_to, next_gen, commit_sha, now_ms, proj, eff_src),
                )

            if self._master_table_exists("master_source_branches"):
                self._master_execute(
                    """
                    UPDATE master_source_branches
                    SET source_version = ?,
                        sync_generation = ?,
                        commit_sha = COALESCE(?, commit_sha),
                        updated_at = ?
                    WHERE project_id = ? AND source_id = ? AND branch_name = ?
                    """,
                    (eff_to, next_gen, commit_sha, now_ms, proj, eff_src, eff_br),
                )

            return {
                "id": upg_id,
                "project_id": proj,
                "source_id": eff_src,
                "branch_name": eff_br,
                "from_version": eff_from,
                "to_version": eff_to,
                "sync_generation": next_gen,
                "upgrade_mode": eff_mode,
                "nodes_delta": int(nodes_delta or 0),
                "edges_delta": int(edges_delta or 0),
                "docs_updated": int(docs_updated or 0),
                "affected_relations": int(affected_relations or 0),
                "drifted_relations": int(drifted_relations or 0),
                "commit_sha": commit_sha,
                "actor": eff_actor,
                "created_at": now_ms,
            }

    def list_source_upgrades(
        self,
        project_id: str,
        source_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List recorded upgrade/sync events for a project (or specific source)."""
        with self._lock:
            self._get_master_context()
            if not self._master_table_exists("master_source_upgrades"):
                return []
            proj = parse_tenant_from_user_id(project_id)[0]
            eff_limit = max(1, min(int(limit or 50), 1000))
            if source_id is not None and str(source_id).strip():
                rows = self._master_fetchall(
                    "SELECT * FROM master_source_upgrades "
                    "WHERE project_id = ? AND source_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (proj, str(source_id).strip(), eff_limit),
                )
            else:
                rows = self._master_fetchall(
                    "SELECT * FROM master_source_upgrades "
                    "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (proj, eff_limit),
                )
            return [dict(r) for r in rows]

    def bind_shared_doc(
        self,
        project_id: str,
        doc_key: str,
        target_source_id: str = "*",
        pinned_version: Optional[str] = None,
        binding_role: str = "reference",
        priority: float = 1.0,
    ) -> Dict[str, Any]:
        """Bind a project-shared document to `'*'` or a specific sub-source (with optional pinned version)."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            dkey = (doc_key or "").strip()
            if not dkey:
                raise ValueError("doc_key is required")
            target_src = (target_source_id or "*").strip() or "*"
            role = (binding_role or "reference").strip() or "reference"
            prio = float(priority if priority is not None else 1.0)
            _, _, is_pg, _ = self._get_master_context()
            self.ensure_master_catalog()
            now_ms = int(time.time() * 1000)
            bind_id = (
                f"bind_{hashlib.sha256(f'{proj}::{dkey}::{target_src}'.encode('utf-8')).hexdigest()[:16]}"
            )

            self._master_execute(
                """
                INSERT INTO master_shared_doc_bindings (
                    id, project_id, doc_key, target_source_id, pinned_version,
                    binding_role, priority, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, doc_key, target_source_id) DO UPDATE SET
                    pinned_version = excluded.pinned_version,
                    binding_role = excluded.binding_role,
                    priority = excluded.priority,
                    status = 'active',
                    updated_at = excluded.updated_at
                """.replace("excluded.", "EXCLUDED." if is_pg else "excluded."),
                (
                    bind_id,
                    proj,
                    dkey,
                    target_src,
                    pinned_version,
                    role,
                    prio,
                    "active",
                    now_ms,
                    now_ms,
                ),
            )
            row = self._master_fetchone(
                "SELECT * FROM master_shared_doc_bindings "
                "WHERE project_id = ? AND doc_key = ? AND target_source_id = ?",
                (proj, dkey, target_src),
            )
            return dict(row) if row else {
                "id": bind_id,
                "project_id": proj,
                "doc_key": dkey,
                "target_source_id": target_src,
                "pinned_version": pinned_version,
                "binding_role": role,
                "priority": prio,
                "status": "active",
                "created_at": now_ms,
                "updated_at": now_ms,
            }

    def list_shared_doc_bindings(
        self,
        project_id: str,
        target_source_id: Optional[str] = None,
        include_wildcard: bool = False,
    ) -> List[Dict[str, Any]]:
        """List shared document bindings for `project_id`, optionally filtered by `target_source_id`."""
        with self._lock:
            self._get_master_context()
            if not self._master_table_exists("master_shared_doc_bindings"):
                return []
            proj = parse_tenant_from_user_id(project_id)[0]
            if target_source_id is not None and str(target_source_id).strip():
                t_src = str(target_source_id).strip()
                if include_wildcard and t_src != "*":
                    rows = self._master_fetchall(
                        "SELECT * FROM master_shared_doc_bindings "
                        "WHERE project_id = ? AND (target_source_id = ? OR target_source_id = '*') "
                        "ORDER BY priority DESC, doc_key ASC",
                        (proj, t_src),
                    )
                else:
                    rows = self._master_fetchall(
                        "SELECT * FROM master_shared_doc_bindings "
                        "WHERE project_id = ? AND target_source_id = ? "
                        "ORDER BY priority DESC, doc_key ASC",
                        (proj, t_src),
                    )
            else:
                rows = self._master_fetchall(
                    "SELECT * FROM master_shared_doc_bindings "
                    "WHERE project_id = ? ORDER BY priority DESC, doc_key ASC, target_source_id ASC",
                    (proj,),
                )
            return [dict(r) for r in rows]

    def upsert_cross_source_relation(
        self,
        project_id: str,
        src_source_id: str,
        dst_source_id: str,
        relation_type: str,
        src_branch: str = "main",
        src_version: Optional[str] = None,
        src_node_id: str = "",
        src_file_path: Optional[str] = None,
        dst_branch: str = "main",
        dst_version: Optional[str] = None,
        dst_node_id: str = "",
        dst_doc_key: Optional[str] = None,
        origin: str = "manual",
        confidence: str = "high",
        confidence_score: float = 1.0,
        contract_key: str = "",
        contract_metadata: Optional[Any] = None,
        contract_metadata_json: Optional[str] = None,
        status: str = "active",
        is_bidirectional: bool = False,
        created_by: str = "system",
        relation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert or update a typed cross-source relation edge in Project DB `pb_root`."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            s_src = str(src_source_id or "").strip()
            d_src = str(dst_source_id or "").strip()
            rel_type = str(relation_type or "").strip()
            if not s_src or not d_src or not rel_type:
                raise ValueError(
                    "src_source_id, dst_source_id, and relation_type are required"
                )

            s_br = str(src_branch or "main").strip() or "main"
            d_br = str(dst_branch or "main").strip() or "main"
            s_node = str(src_node_id or "").strip()
            d_node = str(dst_node_id or "").strip()
            c_key = str(contract_key or "").strip()
            eff_origin = str(origin or "manual").strip() or "manual"
            eff_conf = str(confidence or "high").strip() or "high"
            eff_score = float(confidence_score if confidence_score is not None else 1.0)
            eff_status = str(status or "active").strip() or "active"
            eff_bidir = 1 if is_bidirectional else 0
            eff_actor = str(created_by or "system").strip() or "system"

            if contract_metadata_json is not None:
                meta_json = str(contract_metadata_json)
            elif contract_metadata is not None:
                meta_json = (
                    contract_metadata
                    if isinstance(contract_metadata, str)
                    else json.dumps(contract_metadata)
                )
            else:
                meta_json = "{}"

            existing = self._project_root_fetchone(
                proj,
                "SELECT * FROM cross_source_relations WHERE "
                "project_id = ? AND src_source_id = ? AND src_branch = ? "
                "AND dst_source_id = ? AND dst_branch = ? AND relation_type = ? "
                "AND contract_key = ? AND src_node_id = ? AND dst_node_id = ?",
                (proj, s_src, s_br, d_src, d_br, rel_type, c_key, s_node, d_node),
            )
            if existing is not None and eff_origin != "manual":
                existing_status = str(existing.get("status") or "")
                existing_origin = str(existing.get("origin") or "")
                if existing_status in ("pinned", "rejected"):
                    eff_status = existing_status
                if existing_origin == "manual":
                    eff_origin = existing_origin

            now_ms = int(time.time() * 1000)
            rel_id = (
                (relation_id or "").strip()
                or (str(existing["id"]) if existing and existing.get("id") else None)
                or f"rel_{uuid.uuid4().hex[:16]}"
            )

            self._project_root_execute(
                proj,
                """
                INSERT INTO cross_source_relations (
                    id, project_id, src_source_id, src_branch, src_version, src_node_id,
                    src_file_path, dst_source_id, dst_branch, dst_version, dst_node_id,
                    dst_doc_key, relation_type, origin, confidence, confidence_score,
                    contract_key, contract_metadata_json, status, is_bidirectional,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    project_id, src_source_id, src_branch, dst_source_id, dst_branch,
                    relation_type, contract_key, src_node_id, dst_node_id
                ) DO UPDATE SET
                    src_version = COALESCE(excluded.src_version, cross_source_relations.src_version),
                    src_file_path = COALESCE(excluded.src_file_path, cross_source_relations.src_file_path),
                    dst_version = COALESCE(excluded.dst_version, cross_source_relations.dst_version),
                    dst_doc_key = COALESCE(excluded.dst_doc_key, cross_source_relations.dst_doc_key),
                    origin = excluded.origin,
                    confidence = excluded.confidence,
                    confidence_score = excluded.confidence_score,
                    contract_metadata_json = excluded.contract_metadata_json,
                    status = excluded.status,
                    is_bidirectional = excluded.is_bidirectional,
                    updated_at = excluded.updated_at
                """,
                (
                    rel_id,
                    proj,
                    s_src,
                    s_br,
                    src_version,
                    s_node,
                    src_file_path,
                    d_src,
                    d_br,
                    dst_version,
                    d_node,
                    dst_doc_key,
                    rel_type,
                    eff_origin,
                    eff_conf,
                    eff_score,
                    c_key,
                    meta_json,
                    eff_status,
                    eff_bidir,
                    eff_actor,
                    now_ms,
                    now_ms,
                ),
            )
            row = self._project_root_fetchone(
                proj,
                "SELECT * FROM cross_source_relations WHERE "
                "project_id = ? AND src_source_id = ? AND src_branch = ? "
                "AND dst_source_id = ? AND dst_branch = ? AND relation_type = ? "
                "AND contract_key = ? AND src_node_id = ? AND dst_node_id = ?",
                (proj, s_src, s_br, d_src, d_br, rel_type, c_key, s_node, d_node),
            )
            return self._format_relation_row(row) if row else {
                "id": rel_id,
                "project_id": proj,
                "src_source_id": s_src,
                "src_branch": s_br,
                "src_version": src_version,
                "src_node_id": s_node,
                "src_file_path": src_file_path,
                "dst_source_id": d_src,
                "dst_branch": d_br,
                "dst_version": dst_version,
                "dst_node_id": d_node,
                "dst_doc_key": dst_doc_key,
                "relation_type": rel_type,
                "origin": eff_origin,
                "confidence": eff_conf,
                "confidence_score": eff_score,
                "contract_key": c_key,
                "contract_metadata_json": meta_json,
                "contract_metadata": self._safe_json_loads(meta_json, {}),
                "status": eff_status,
                "is_bidirectional": bool(eff_bidir),
                "created_by": eff_actor,
                "created_at": now_ms,
                "updated_at": now_ms,
            }

    def list_cross_source_relations(
        self,
        project_id: str,
        source_id: Optional[str] = None,
        branch: Optional[str] = None,
        relation_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List cross-source relation edges stored in Project DB `pb_root`."""
        with self._lock:
            self._get_master_context()
            proj = parse_tenant_from_user_id(project_id)[0]
            if is_multi_db_enabled() and not self._project_exists_in_catalog(proj):
                return []

            clauses = ["project_id = ?"]
            params: List[Any] = [proj]

            clean_src = (
                str(source_id).strip()
                if source_id is not None and str(source_id).strip() not in ("", "*")
                else None
            )
            clean_br = (
                str(branch).strip()
                if branch is not None and str(branch).strip()
                else None
            )

            if clean_src and clean_br:
                clauses.append(
                    "((src_source_id = ? AND src_branch = ?) OR (dst_source_id = ? AND dst_branch = ?))"
                )
                params.extend([clean_src, clean_br, clean_src, clean_br])
            elif clean_src:
                clauses.append("(src_source_id = ? OR dst_source_id = ?)")
                params.extend([clean_src, clean_src])
            elif clean_br:
                clauses.append("(src_branch = ? OR dst_branch = ?)")
                params.extend([clean_br, clean_br])

            if relation_type is not None and str(relation_type).strip():
                clauses.append("relation_type = ?")
                params.append(str(relation_type).strip())
            if status is not None and str(status).strip():
                clauses.append("status = ?")
                params.append(str(status).strip())

            try:
                rows = self._project_root_fetchall(
                    proj,
                    f"SELECT * FROM cross_source_relations WHERE {' AND '.join(clauses)} "
                    "ORDER BY updated_at DESC, created_at DESC",
                    tuple(params),
                )
                return [self._format_relation_row(r) for r in rows]
            except Exception as exc:
                logger.debug("list_cross_source_relations warning for %s: %s", proj, exc)
                return []

    def _resolve_project_for_relation(
        self, relation_id: str, project_id: Optional[str]
    ) -> Optional[str]:
        if project_id is not None and str(project_id).strip():
            return parse_tenant_from_user_id(project_id)[0]
        active = get_current_tenant()
        candidate_projs: List[str] = []
        if active is not None and active[0]:
            candidate_projs.append(active[0])
        if self._master_table_exists("master_projects"):
            for r in self._master_fetchall("SELECT project_id FROM master_projects"):
                pid = str(r.get("project_id") or "")
                if pid and pid not in candidate_projs:
                    candidate_projs.append(pid)
        for pid in candidate_projs:
            try:
                hit = self._project_root_fetchone(
                    pid,
                    "SELECT id FROM cross_source_relations WHERE id = ?",
                    (relation_id,),
                )
                if hit is not None:
                    return pid
            except Exception:
                continue
        if not is_multi_db_enabled():
            return "default"
        return None

    def update_cross_source_relation(
        self,
        relation_id: str,
        project_id: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        """Update fields on an existing cross-source relation edge."""
        with self._lock:
            rid = (relation_id or "").strip()
            if not rid:
                return None
            self._get_master_context()
            proj = self._resolve_project_for_relation(rid, project_id)
            if not proj:
                return None
            if is_multi_db_enabled() and not self._project_exists_in_catalog(proj):
                return None

            existing = self._project_root_fetchone(
                proj,
                "SELECT * FROM cross_source_relations WHERE id = ? AND project_id = ?",
                (rid, proj),
            )
            if existing is None:
                return None

            set_clauses: List[str] = []
            params: List[Any] = []
            direct_cols = {
                "src_branch",
                "src_version",
                "src_node_id",
                "src_file_path",
                "dst_branch",
                "dst_version",
                "dst_node_id",
                "dst_doc_key",
                "relation_type",
                "origin",
                "confidence",
                "confidence_score",
                "contract_key",
                "contract_metadata_json",
                "status",
            }
            for key, val in fields.items():
                if key == "contract_metadata":
                    set_clauses.append("contract_metadata_json = ?")
                    params.append(
                        val if isinstance(val, str) else json.dumps(val or {})
                    )
                elif key == "is_bidirectional":
                    set_clauses.append("is_bidirectional = ?")
                    params.append(1 if val else 0)
                elif key in direct_cols:
                    set_clauses.append(f"{key} = ?")
                    params.append(val)

            if not set_clauses:
                return self._format_relation_row(existing)

            now_ms = int(time.time() * 1000)
            set_clauses.append("updated_at = ?")
            params.append(now_ms)
            params.extend([rid, proj])

            self._project_root_execute(
                proj,
                f"UPDATE cross_source_relations SET {', '.join(set_clauses)} "
                "WHERE id = ? AND project_id = ?",
                tuple(params),
            )
            updated = self._project_root_fetchone(
                proj,
                "SELECT * FROM cross_source_relations WHERE id = ? AND project_id = ?",
                (rid, proj),
            )
            return self._format_relation_row(updated) if updated else None

    def delete_cross_source_relation(
        self, relation_id: str, project_id: Optional[str] = None
    ) -> bool:
        """Delete a cross-source relation edge by ID."""
        with self._lock:
            rid = (relation_id or "").strip()
            if not rid:
                return False
            self._get_master_context()
            proj = self._resolve_project_for_relation(rid, project_id)
            if not proj:
                return False
            if is_multi_db_enabled() and not self._project_exists_in_catalog(proj):
                return False
            rc = self._project_root_execute(
                proj,
                "DELETE FROM cross_source_relations WHERE id = ? AND project_id = ?",
                (rid, proj),
            )
            return rc > 0

    def _query_branch_counts(
        self,
        proj: str,
        branch_name: str,
        db_name: str,
        schema_name: str,
        is_pg: bool,
        master_url: str,
        root_branch: str = "main",
        source_id: Optional[str] = None,
    ) -> Dict[str, int]:
        mem_cnt = 0
        doc_cnt = 0
        node_cnt = 0

        eff_source = _normalize_subsource_id(source_id)
        try:
            conn, _, _, _ = self.get_tenant_connection(
                proj, branch_name, source_id=eff_source if eff_source else "__default__"
            )
            is_root_schema = (
                schema_name == "pb_root"
                if eff_source is None
                else schema_name
                == sanitize_source_schema_name(eff_source, root_branch, root_branch)
            )
            if is_pg:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM memories")
                    r = cur.fetchone()
                    mem_cnt = int(r[0]) if r else 0
                    cur.execute("SELECT COUNT(*) FROM document_versions")
                    r = cur.fetchone()
                    doc_cnt = int(r[0]) if r else 0
                    try:
                        if is_root_schema:
                            cur.execute(
                                psql.SQL(
                                    "SELECT COUNT(*) FROM {}.nodes WHERE project_id = %s"
                                ).format(psql.Identifier(schema_name)),
                                (proj,),
                            )
                        else:
                            cur.execute(
                                psql.SQL(
                                    "SELECT COUNT(*) FROM {}.v_resolved_nodes WHERE project_id = %s"
                                ).format(psql.Identifier(schema_name)),
                                (proj,),
                            )
                        r = cur.fetchone()
                        node_cnt = int(r[0]) if r else 0
                    except Exception:
                        try:
                            cur.execute(
                                "SELECT COUNT(*) FROM project_nodes WHERE project_id = %s",
                                (proj,),
                            )
                            r = cur.fetchone()
                            node_cnt = int(r[0]) if r else 0
                        except Exception:
                            node_cnt = 0
            else:
                r = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()
                mem_cnt = int(r["c"]) if r else 0
                r = conn.execute("SELECT COUNT(*) AS c FROM document_versions").fetchone()
                doc_cnt = int(r["c"]) if r else 0

                projects_root = self._resolve_sqlite_projects_root(master_url)
                if projects_root is not None:
                    if eff_source is not None:
                        graph_file = (
                            projects_root
                            / db_name
                            / "sources"
                            / _source_dir_slug(eff_source)
                            / "graph.sqlite"
                        )
                    else:
                        graph_file = projects_root / db_name / "graph.sqlite"
                    if graph_file.exists():
                        gconn = sqlite3.connect(str(graph_file))
                        gconn.row_factory = sqlite3.Row
                        try:
                            has_root_nodes = bool(
                                gconn.execute(
                                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='root_nodes'"
                                ).fetchone()
                            )
                            if has_root_nodes:
                                if is_root_schema:
                                    gr = gconn.execute(
                                        "SELECT COUNT(*) FROM root_nodes WHERE project_id = ?",
                                        (proj,),
                                    ).fetchone()
                                    node_cnt = int(gr[0]) if gr else 0
                                else:
                                    from ..graphify_engine.store import SqliteOverlayGraphStore

                                    gstore = SqliteOverlayGraphStore(
                                        conn=gconn,
                                        root_branch=root_branch or "main",
                                        project_scoped_schemas=True,
                                    )
                                    stats = gstore.get_stats(proj, branch_name)
                                    node_cnt = int(stats.get("node_count", 0) or 0)
                        except Exception:
                            node_cnt = 0
                        finally:
                            gconn.close()
                if node_cnt == 0:
                    try:
                        pr = conn.execute(
                            "SELECT COUNT(*) AS c FROM project_nodes WHERE project_id = ?",
                            (proj,),
                        ).fetchone()
                        node_cnt = int(pr["c"]) if pr else 0
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug(
                "Topology count query warning for %s:%s (%s)", proj, branch_name, exc
            )

        return {
            "memories": mem_cnt,
            "documents": doc_cnt,
            "graph_nodes": node_cnt,
        }

    def _query_project_hub_counts(
        self, proj: str, proj_root: str
    ) -> Tuple[int, int]:
        """Return `(shared_doc_count, relation_count)` from Project DB `pb_root`."""
        shared_doc_cnt = 0
        relation_cnt = 0
        try:
            row_doc = self._project_root_fetchone(
                proj,
                "SELECT COUNT(*) AS c FROM document_versions "
                "WHERE project_id = ? AND status = 'active' "
                "AND (scope = 'project_shared' OR source_id = '__shared__')",
                (proj,),
            )
            if row_doc:
                shared_doc_cnt = int(
                    row_doc.get("c")
                    if row_doc.get("c") is not None
                    else next(iter(row_doc.values()), 0)
                )
        except Exception:
            shared_doc_cnt = 0

        try:
            row_rel = self._project_root_fetchone(
                proj,
                "SELECT COUNT(*) AS c FROM cross_source_relations WHERE project_id = ?",
                (proj,),
            )
            if row_rel:
                relation_cnt = int(
                    row_rel.get("c")
                    if row_rel.get("c") is not None
                    else next(iter(row_rel.values()), 0)
                )
        except Exception:
            relation_cnt = 0

        return shared_doc_cnt, relation_cnt

    def get_master_topology(self) -> Dict[str, Any]:
        """Return Master DB -> Project DBs -> Branch & Source Schemas topology with live row counts."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            self.ensure_master_catalog()

            projects = self._master_fetchall(
                "SELECT * FROM master_projects ORDER BY created_at ASC, project_id ASC"
            )
            branches = self._master_fetchall(
                "SELECT * FROM master_branches ORDER BY project_id ASC, is_root DESC, created_at ASC"
            )
            sources = (
                self._master_fetchall(
                    "SELECT * FROM master_project_sources ORDER BY project_id ASC, created_at ASC, source_id ASC"
                )
                if self._master_table_exists("master_project_sources")
                else []
            )
            source_branches = (
                self._master_fetchall(
                    "SELECT * FROM master_source_branches ORDER BY project_id ASC, source_id ASC, is_root DESC, created_at ASC"
                )
                if self._master_table_exists("master_source_branches")
                else []
            )

            branches_by_proj: Dict[str, List[Dict[str, Any]]] = {}
            for b in branches:
                branches_by_proj.setdefault(str(b["project_id"]), []).append(b)

            sources_by_proj: Dict[str, List[Dict[str, Any]]] = {}
            for s in sources:
                sources_by_proj.setdefault(str(s["project_id"]), []).append(s)

            src_branches_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
            for sb in source_branches:
                src_branches_by_key.setdefault(
                    (str(sb["project_id"]), str(sb["source_id"])), []
                ).append(sb)

            project_nodes: List[Dict[str, Any]] = []
            for p in projects:
                pid = str(p["project_id"])
                db_name = str(p["db_name"])
                proj_root = str(p.get("root_branch") or "main")
                proj_branches: List[Dict[str, Any]] = []
                for b in branches_by_proj.get(pid, []):
                    br_name = str(b["branch_name"])
                    schema_name = str(b["schema_name"])
                    counts = self._query_branch_counts(
                        pid,
                        br_name,
                        db_name,
                        schema_name,
                        is_pg,
                        master_url,
                        root_branch=proj_root,
                    )
                    proj_branches.append(
                        {
                            "branch_name": br_name,
                            "schema_name": schema_name,
                            "base_branch": b.get("base_branch"),
                            "is_root": bool(b.get("is_root")),
                            "has_graph_schema": bool(b.get("has_graph_schema", 1)),
                            "has_doc_schema": bool(b.get("has_doc_schema", 1)),
                            "author": b.get("author") or "system",
                            "updated_at": b.get("updated_at"),
                            "counts": counts,
                        }
                    )

                proj_sources: List[Dict[str, Any]] = []
                for s in sources_by_proj.get(pid, []):
                    sid = str(s["source_id"])
                    src_root = str(s.get("root_branch") or proj_root)
                    formatted_src = self._format_source_row(s)
                    s_branches_out: List[Dict[str, Any]] = []
                    for sb in src_branches_by_key.get((pid, sid), []):
                        sb_name = str(sb["branch_name"])
                        sb_schema = str(sb["schema_name"])
                        sb_counts = self._query_branch_counts(
                            pid,
                            sb_name,
                            db_name,
                            sb_schema,
                            is_pg,
                            master_url,
                            root_branch=src_root,
                            source_id=sid,
                        )
                        s_branches_out.append(
                            {
                                "branch_name": sb_name,
                                "schema_name": sb_schema,
                                "base_branch": sb.get("base_branch"),
                                "is_root": bool(sb.get("is_root")),
                                "has_graph_schema": bool(sb.get("has_graph_schema", 1)),
                                "has_doc_schema": bool(sb.get("has_doc_schema", 1)),
                                "source_version": sb.get("source_version"),
                                "sync_generation": int(sb.get("sync_generation") or 0),
                                "author": sb.get("author") or "system",
                                "updated_at": sb.get("updated_at"),
                                "counts": sb_counts,
                            }
                        )
                    formatted_src["branches"] = s_branches_out
                    proj_sources.append(formatted_src)

                shared_doc_cnt, relation_cnt = self._query_project_hub_counts(
                    pid, proj_root
                )

                project_nodes.append(
                    {
                        "project_id": pid,
                        "name": p.get("name") or pid,
                        "description": p.get("description"),
                        "db_name": db_name,
                        "root_branch": proj_root,
                        "root_schema": p.get("root_schema") or "pb_root",
                        "backend_type": p.get("backend_type"),
                        "db_descriptor": p.get("db_descriptor"),
                        "status": p.get("status") or "active",
                        "created_at": p.get("created_at"),
                        "updated_at": p.get("updated_at"),
                        "branches": proj_branches,
                        "sources": proj_sources,
                        "shared_doc_count": shared_doc_cnt,
                        "relation_count": relation_cnt,
                    }
                )

            return {
                "master": {
                    "backend": "postgresql" if is_pg else "sqlite",
                    "url": _mask_url(master_url),
                    "multi_db_enabled": is_multi_db_enabled(),
                    "projects_count": len(project_nodes),
                    "branches_count": len(branches),
                    "sources_count": len(sources),
                },
                "projects": project_nodes,
            }


tenant_manager = ProjectDatabaseManager()
