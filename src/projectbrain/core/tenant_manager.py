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
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
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

_current_tenant: ContextVar[Optional[Tuple[str, str]]] = ContextVar(
    "pb_current_tenant", default=None
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


def parse_tenant_from_user_id(
    user_id: Optional[str], default_branch: str = "main"
) -> Tuple[str, str]:
    """Parse composite `user_id` into `(project_id, branch)`.

    Examples:
        - `"demo/app:feat-v3"` -> `("demo/app", "feat-v3")`
        - `"demo/app:%"` -> `("demo/app", "%")`
        - `"demo/app"` -> `("demo/app", "main")`
        - `None` / `""` / `"default"` -> `("default", "main")`
    """
    eff_default = (default_branch or "main").strip() or "main"
    if user_id is None:
        return ("default", eff_default)
    raw = str(user_id).strip()
    if not raw or raw == "default":
        return ("default", eff_default)
    if ":" in raw:
        proj_part, branch_part = raw.split(":", 1)
        proj = proj_part.strip() or "default"
        br = branch_part.strip() or eff_default
        return (proj, br)
    return (raw, eff_default)


def get_current_tenant() -> Optional[Tuple[str, str]]:
    """Return active `(project_id, branch)` from ContextVar, if set."""
    return _current_tenant.get()


@contextmanager
def tenant_context(
    project_id: Optional[str], branch: Optional[str] = "main"
) -> Iterator[Optional[Tuple[str, str]]]:
    """Context manager scoping DB operations to `(project_id, branch)`."""
    if project_id is None:
        token = _current_tenant.set(None)
    else:
        raw_proj = str(project_id).strip()
        if ":" in raw_proj and (branch is None or branch == "main"):
            proj, br = parse_tenant_from_user_id(raw_proj, default_branch=branch or "main")
        else:
            proj = raw_proj.split(":", 1)[0].strip() or "default"
            br = (branch or "main").strip() or "main"
        token = _current_tenant.set((proj, br))
    try:
        yield _current_tenant.get()
    finally:
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
        feedback_score REAL DEFAULT 0
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
        memory_ids_json TEXT DEFAULT '[]'
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
        PRIMARY KEY (project_id, branch)
    )
    """,
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
        self._memory_id_hints: OrderedDict[str, Tuple[str, str]] = OrderedDict()

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
        self._memory_id_hints.clear()
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

    def _init_tenant_tables_sqlite(
        self, conn: sqlite3.Connection, include_root: bool
    ) -> None:
        for stmt in _TENANT_COMMON_STATEMENTS:
            conn.execute(stmt.strip())
        if include_root:
            for stmt in _ROOT_ONLY_STATEMENTS:
                conn.execute(stmt.strip())

    def _init_tenant_tables_pg(self, cur: Any, include_root: bool) -> None:
        for stmt in _TENANT_COMMON_STATEMENTS:
            cur.execute(_adapt_ddl_for_postgres(stmt.strip()))
        if include_root:
            for stmt in _ROOT_ONLY_STATEMENTS:
                cur.execute(_adapt_ddl_for_postgres(stmt.strip()))

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
            if ":" in raw_proj:
                proj, extra_branch = parse_tenant_from_user_id(
                    raw_proj, default_branch=eff_root
                )
                if extra_branch == eff_root or extra_branch == "%":
                    extra_branch = None
            else:
                proj = raw_proj

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

    def get_tenant_connection(
        self,
        project_id: str,
        branch_name: str = "main",
        *,
        record_lineage: bool = False,
    ) -> Tuple[Any, bool, str, str]:
        """Ensure project DB + branch schema exist and return `(conn, is_postgres, db_name, schema_name)`."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            eff_root = _effective_root_branch()
            proj = parse_tenant_from_user_id(project_id, default_branch=eff_root)[0]
            br = (branch_name or eff_root).strip() or eff_root

            if proj in self._provisioned_projects:
                proj_root = self._provisioned_projects[proj]["root_branch"]
            else:
                proj_root = eff_root

            schema_name = sanitize_branch_schema_for_project(br, proj_root)
            db_name = sanitize_project_db_name(proj)

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

    def has_branch(self, project_id: str, branch_name: str = "main") -> bool:
        """Check whether `(project_id, branch_name)` has already been provisioned."""
        with self._lock:
            self._get_master_context()
            eff_root = _effective_root_branch()
            proj = parse_tenant_from_user_id(project_id, default_branch=eff_root)[0]
            br = (branch_name or eff_root).strip() or eff_root
            proj_root = (
                self._provisioned_projects[proj]["root_branch"]
                if proj in self._provisioned_projects
                else eff_root
            )
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

    def list_project_branches(self, project_id: str) -> List[str]:
        """Return registered branch names for `project_id` from `master_branches`."""
        with self._lock:
            self._get_master_context()
            if not self._master_table_exists("master_branches"):
                return []
            proj = parse_tenant_from_user_id(project_id)[0]
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

    def remember_memory_id(
        self, memory_id: Optional[str], project_id: str, branch_name: str
    ) -> None:
        """Cache `memory_id -> (project_id, branch_name)` hint for O(1) ID routing."""
        if not memory_id:
            return
        mid = str(memory_id).strip()
        if not mid:
            return
        with self._lock:
            self._memory_id_hints[mid] = (project_id, branch_name)
            self._memory_id_hints.move_to_end(mid)
            while len(self._memory_id_hints) > _MAX_MEMORY_HINTS:
                self._memory_id_hints.popitem(last=False)

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

    def get_project_graph_target(self, project_id: str) -> Dict[str, Any]:
        """Return GraphStore target descriptor for `project_id`."""
        with self._lock:
            proj = parse_tenant_from_user_id(project_id)[0]
            info = self.ensure_project_database(proj)
            _, _, is_pg, master_url = self._get_master_context()
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
            for mid in [m for m, (p, _) in self._memory_id_hints.items() if p == proj]:
                self._memory_id_hints.pop(mid, None)

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

    def _query_branch_counts(
        self,
        proj: str,
        branch_name: str,
        db_name: str,
        schema_name: str,
        is_pg: bool,
        master_url: str,
        root_branch: str = "main",
    ) -> Dict[str, int]:
        mem_cnt = 0
        doc_cnt = 0
        node_cnt = 0

        try:
            conn, _, _, _ = self.get_tenant_connection(proj, branch_name)
            if is_pg:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM memories")
                    r = cur.fetchone()
                    mem_cnt = int(r[0]) if r else 0
                    cur.execute("SELECT COUNT(*) FROM document_versions")
                    r = cur.fetchone()
                    doc_cnt = int(r[0]) if r else 0
                    try:
                        if schema_name == "pb_root":
                            cur.execute(
                                "SELECT COUNT(*) FROM pb_root.nodes WHERE project_id = %s",
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
                                if schema_name == "pb_root":
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

    def get_master_topology(self) -> Dict[str, Any]:
        """Return Master DB -> Project DBs -> Branch Schemas topology with live row counts."""
        with self._lock:
            _, _, is_pg, master_url = self._get_master_context()
            self.ensure_master_catalog()

            projects = self._master_fetchall(
                "SELECT * FROM master_projects ORDER BY created_at ASC, project_id ASC"
            )
            branches = self._master_fetchall(
                "SELECT * FROM master_branches ORDER BY project_id ASC, is_root DESC, created_at ASC"
            )

            branches_by_proj: Dict[str, List[Dict[str, Any]]] = {}
            for b in branches:
                branches_by_proj.setdefault(str(b["project_id"]), []).append(b)

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
                    }
                )

            return {
                "master": {
                    "backend": "postgresql" if is_pg else "sqlite",
                    "url": _mask_url(master_url),
                    "multi_db_enabled": is_multi_db_enabled(),
                    "projects_count": len(project_nodes),
                    "branches_count": len(branches),
                },
                "projects": project_nodes,
            }


tenant_manager = ProjectDatabaseManager()
