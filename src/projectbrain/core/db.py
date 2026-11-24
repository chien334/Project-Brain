import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from .config import env
from .tenant_manager import (
    _effective_root_branch,
    get_current_source,
    get_current_tenant,
    is_multi_db_enabled,
    parse_tenant_from_user_id,
    parse_tenant_spec,
    resolve_effective_master_url,
    tenant_manager,
)
from .types import MemRow

logger = logging.getLogger("db")
logger.setLevel(logging.INFO)

_MASTER_ONLY_TABLES = (
    "mcp_accounts",
    "auth_sessions",
    "auth_api_keys",
    "auth_audit_logs",
    "auth_settings",
    "master_projects",
    "master_branches",
    "master_project_sources",
    "master_source_branches",
    "master_source_upgrades",
    "master_shared_doc_bindings",
    "projects",
    "_migrations",
    "stats",
    "embed_logs",
)

_PROJECT_ROOT_TABLES = (
    "memory_branch_links",
    "cross_source_relations",
    "cross_source_contract_artifacts",
)

_PROJECT_NO_BRANCH_TABLES = (
    "plugin_project_configs",
    "v3_rca_incidents",
    "udcp_script_cache",
    "telemetry_artifacts",
    "v3_script_cache",
    "v3_telemetry_artifacts",
    "project_nodes",
    "project_edges",
)

_DEDUP_ID_TABLES = frozenset(
    {
        "memories",
        "document_versions",
        "document_units",
        "temporal_facts",
        "temporal_edges",
    }
)

_SQL_TABLE_RE = re.compile(
    r'\b(?:from|join|into|update|table(?:\s+if\s+not\s+exists)?)\s+(["`\[]?[a-zA-Z_][a-zA-Z0-9_\.]*["`\]]?)',
    re.IGNORECASE,
)
_SQL_INDEX_ON_RE = re.compile(
    r'\bon\s+(["`\[]?[a-zA-Z_][a-zA-Z0-9_\.]*["`\]]?)\s*\(',
    re.IGNORECASE,
)
_INSERT_HEAD_RE = re.compile(
    r'^\s*insert\s+(?:or\s+(?:ignore|replace)\s+)?into\s+(["`\[]?[a-zA-Z_][a-zA-Z0-9_\.]*["`\]]?)\s*\(([^)]+)\)\s*values\s*\(',
    re.IGNORECASE | re.DOTALL,
)
_COUNT_STAR_RE = re.compile(
    r"^\s*select\s+count\s*\(\s*\*\s*\)\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+from\b",
    re.IGNORECASE,
)
_MAX_SEG_RE = re.compile(
    r"^\s*select\s+coalesce\s*\(\s*max\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)\s*,\s*0\s*\)\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+from\b",
    re.IGNORECASE,
)
_SECTOR_GROUP_RE = re.compile(
    r"^\s*select\s+primary_sector\s*,\s*count\s*\(\s*\*\s*\)\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+from\b.*\bgroup\s+by\s+primary_sector\b",
    re.IGNORECASE | re.DOTALL,
)
_ORDER_BY_RE = re.compile(
    r"\border\s+by\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+(asc|desc))?",
    re.IGNORECASE,
)
_LIMIT_OFFSET_TAIL_RE = re.compile(
    r"\blimit\s+(\?|%s|\d+)(?:\s+offset\s+(\?|%s|\d+))?\s*;?\s*$",
    re.IGNORECASE,
)


@dataclass
class _RouteDecision:
    mode: str  # "master" | "single" | "multi" | "project_wildcard" | "id_scan" | "fanout"
    targets: List[Tuple[str, str]] = field(default_factory=list)
    source_id: Optional[str] = None
    target_sources: Optional[List[Optional[str]]] = None
    project_id: Optional[str] = None
    branch_like: str = "%"
    lookup_ids: List[str] = field(default_factory=list)
    memory_hint: Optional[Tuple[str, str, str]] = None
    memory_hint_source: Optional[str] = None
    primary_table: str = ""
    op: str = ""


def _strip_sql_comments(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", no_block)


def _normalize_ident(raw: str) -> str:
    cleaned = raw.strip().strip('"`[]')
    return cleaned.split(".")[-1].lower()


def _extract_op_and_tables(sql: str) -> Tuple[str, List[str]]:
    cleaned = _strip_sql_comments(sql).strip()
    m_op = re.match(r"^([a-zA-Z]+)", cleaned)
    op = m_op.group(1).upper() if m_op else ""
    tables: List[str] = []
    for match in _SQL_TABLE_RE.finditer(cleaned):
        t = _normalize_ident(match.group(1))
        if t and t not in tables:
            tables.append(t)
    for match in _SQL_INDEX_ON_RE.finditer(cleaned):
        t = _normalize_ident(match.group(1))
        if t and t not in tables:
            tables.append(t)
    return op, tables


def _find_placeholders(sql: str) -> List[int]:
    positions: List[int] = []
    in_sq = False
    in_dq = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'" and not in_dq:
            if in_sq and i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            in_sq = not in_sq
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
        elif not in_sq and not in_dq:
            if ch == "?":
                positions.append(i)
            elif ch == "%" and i + 1 < n and sql[i + 1] == "s":
                positions.append(i)
                i += 2
                continue
        i += 1
    return positions


def _split_top_level_csv(text: str) -> List[str]:
    items: List[str] = []
    buf: List[str] = []
    depth = 0
    in_sq = False
    in_dq = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "'" and not in_dq:
            if in_sq and i + 1 < n and text[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_sq = not in_sq
            buf.append(ch)
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
            buf.append(ch)
        elif not in_sq and not in_dq:
            if ch == "(":
                depth += 1
                buf.append(ch)
            elif ch == ")":
                depth = max(0, depth - 1)
                buf.append(ch)
            elif ch == "," and depth == 0:
                items.append("".join(buf).strip())
                buf.clear()
                i += 1
                continue
            else:
                buf.append(ch)
        else:
            buf.append(ch)
        i += 1
    if buf:
        items.append("".join(buf).strip())
    return items


def _extract_balanced_parens(sql: str, open_paren_idx: int) -> str:
    depth = 0
    in_sq = False
    in_dq = False
    i = open_paren_idx
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'" and not in_dq:
            if in_sq and i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            in_sq = not in_sq
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
        elif not in_sq and not in_dq:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return sql[open_paren_idx + 1 : i]
        i += 1
    return sql[open_paren_idx + 1 :]


def _parse_insert_col_values(
    sql: str, params: tuple
) -> Optional[Tuple[str, Dict[str, Any]]]:
    m = _INSERT_HEAD_RE.match(sql)
    if not m:
        return None
    table = _normalize_ident(m.group(1))
    cols = [_normalize_ident(c) for c in m.group(2).split(",")]
    open_idx = m.end() - 1
    values_inner = _extract_balanced_parens(sql, open_idx)
    val_exprs = _split_top_level_csv(values_inner)
    col_values: Dict[str, Any] = {}
    p_idx = 0
    for col, expr in zip(cols, val_exprs):
        tok = expr.strip()
        if tok in ("?", "%s"):
            if p_idx < len(params):
                col_values[col] = params[p_idx]
            p_idx += 1
        else:
            if tok.startswith("'") and tok.endswith("'") and len(tok) >= 2:
                col_values[col] = tok[1:-1]
            p_idx += len(_find_placeholders(tok))
    return table, col_values


def _like_to_regex(pattern: str) -> re.Pattern:
    parts = ["^"]
    for ch in pattern:
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    parts.append("$")
    return re.compile("".join(parts))


def _rewrite_fanout_limit_offset(
    sql: str, params: tuple
) -> Tuple[str, tuple, Optional[int], int]:
    """Rewrite trailing LIMIT/OFFSET so fan-out merges before slicing."""
    m = _LIMIT_OFFSET_TAIL_RE.search(sql)
    if not m:
        return sql, params, None, 0

    placeholders = _find_placeholders(sql)
    lim_tok = m.group(1)
    off_tok = m.group(2)

    lim_val: Optional[int] = None
    off_val = 0
    new_params = list(params)

    if lim_tok in ("?", "%s"):
        lim_pos = m.start(1)
        if lim_pos in placeholders:
            idx = placeholders.index(lim_pos)
            if idx < len(new_params) and new_params[idx] is not None:
                lim_val = max(0, int(new_params[idx]))
    else:
        lim_val = max(0, int(lim_tok))

    if off_tok is not None:
        if off_tok in ("?", "%s"):
            off_pos = m.start(2)
            if off_pos in placeholders:
                idx = placeholders.index(off_pos)
                if idx < len(new_params) and new_params[idx] is not None:
                    off_val = max(0, int(new_params[idx]))
                    new_params[idx] = 0
        else:
            off_val = max(0, int(off_tok))

    if lim_val is None:
        return sql, params, None, 0

    combined_lim = lim_val + off_val
    if lim_tok in ("?", "%s"):
        lim_pos = m.start(1)
        idx = placeholders.index(lim_pos)
        new_params[idx] = combined_lim
        rewritten_sql = sql
        if off_tok is not None and off_tok not in ("?", "%s"):
            rewritten_sql = (
                sql[: m.start(2)] + "0" + sql[m.end(2) :]
            )
    else:
        rewritten_sql = sql[: m.start(1)] + str(combined_lim)
        if off_tok is not None:
            if off_tok in ("?", "%s"):
                rewritten_sql += sql[m.end(1) :]
            else:
                rewritten_sql += sql[m.end(1) : m.start(2)] + "0" + sql[m.end(2) :]
        else:
            rewritten_sql += sql[m.end(1) :]

    return rewritten_sql, tuple(new_params), lim_val, off_val


class DB:
    def __init__(self):
        self.conn = None
        self.is_postgres = False
        self._last_rowcount = 0

    def connect(self):
        if self.conn:
            tenant_manager.bind_master_db(self)
            return
        url = resolve_effective_master_url()
        if url.startswith("sqlite:///"):
            path = Path(url.replace("sqlite:///", ""))
            if str(path) != ":memory:" and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"[DB] Connecting to SQLite: {path}")
            self.conn = sqlite3.connect(
                str(path), check_same_thread=False, isolation_level=None
            )
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=-8000")
            self.conn.execute("PRAGMA foreign_keys=OFF")
            self.is_postgres = False
        elif url.startswith("postgresql://") or url.startswith("postgres://"):
            logger.info("[DB] Connecting to PostgreSQL...")
            self.conn = psycopg2.connect(url)
            self.conn.autocommit = True
            self.is_postgres = True
        else:
            raise ValueError(f"Unsupported database URL schema: {url}")

        self.run_migrations()
        tenant_manager.bind_master_db(self)

    def run_migrations(self):
        c = self.conn
        if self.is_postgres:
            with c.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at INTEGER)"
                )
        else:
            c.execute(
                "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at INTEGER)"
            )

        files = []
        try:
            from importlib import resources

            files = [
                p.name
                for p in resources.files("projectbrain.migrations").iterdir()
                if p.name.endswith(".sql")
            ]
        except (ImportError, TypeError, AttributeError):
            import os

            mig_path = Path(__file__).parent.parent / "migrations"
            if mig_path.exists():
                files = [f for f in os.listdir(mig_path) if f.endswith(".sql")]

        files.sort()

        for f in files:
            already_applied = False
            if self.is_postgres:
                with c.cursor() as cur:
                    cur.execute("SELECT 1 FROM _migrations WHERE name=%s", (f,))
                    already_applied = bool(cur.fetchone())
            else:
                row = c.execute(
                    "SELECT 1 FROM _migrations WHERE name=?", (f,)
                ).fetchone()
                already_applied = bool(row)

            if not already_applied:
                logger.info(f"[DB] Applying migration {f}")
                sql = None
                try:
                    from importlib import resources

                    sql = (
                        resources.files("projectbrain.migrations")
                        .joinpath(f)
                        .read_text(encoding="utf-8")
                    )
                except Exception:
                    pass
                if not sql:
                    sql = (Path(__file__).parent.parent / "migrations" / f).read_text(
                        encoding="utf-8"
                    )

                if self.is_postgres:
                    sql = sql.replace("AUTOINCREMENT", "")
                    sql = sql.replace(
                        "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
                    )
                    sql = sql.replace("BLOB", "BYTEA")
                    sql = sql.replace("REAL", "DOUBLE PRECISION")
                    with c.cursor() as cur:
                        try:
                            cur.execute(sql)
                            cur.execute(
                                "INSERT INTO _migrations (name, applied_at) VALUES (%s, %s)",
                                (f, int(time.time())),
                            )
                        except Exception as e:
                            logger.error(f"[DB] PostgreSQL Migration {f} failed: {e}")
                            raise e
                else:
                    try:
                        c.executescript(sql)
                        c.execute(
                            "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
                            (f, int(time.time())),
                        )
                    except Exception as e:
                        logger.error(f"[DB] SQLite Migration {f} failed: {e}")
                        raise e

    def init_schema(self):
        self.run_migrations()

    def translate_query(self, sql: str) -> str:
        if not self.is_postgres:
            return sql

        # 1. Replace placeholder ? with %s
        sql = sql.replace("?", "%s")

        # 2. Replace SQLite-specific INSERT OR IGNORE / INSERT OR REPLACE
        if "INSERT OR IGNORE INTO users" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO users", "INSERT INTO users")
            sql += " ON CONFLICT (user_id) DO NOTHING"

        elif "INSERT OR REPLACE INTO waypoints" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO waypoints", "INSERT INTO waypoints")
            sql += " ON CONFLICT (src_id, dst_id) DO UPDATE SET weight=EXCLUDED.weight, updated_at=EXCLUDED.updated_at"

        elif "INSERT OR REPLACE INTO vectors" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO vectors", "INSERT INTO vectors")
            sql += " ON CONFLICT (id, sector) DO UPDATE SET user_id=EXCLUDED.user_id, v=EXCLUDED.v, dim=EXCLUDED.dim"

        elif "INSERT OR REPLACE INTO memories" in sql:
            sql = sql.replace("INSERT OR REPLACE INTO memories", "INSERT INTO memories")
            sql += """ ON CONFLICT (id) DO UPDATE SET 
                user_id=EXCLUDED.user_id, segment=EXCLUDED.segment, content=EXCLUDED.content, 
                simhash=EXCLUDED.simhash, primary_sector=EXCLUDED.primary_sector, tags=EXCLUDED.tags, 
                meta=EXCLUDED.meta, created_at=EXCLUDED.created_at, updated_at=EXCLUDED.updated_at, 
                last_seen_at=EXCLUDED.last_seen_at, salience=EXCLUDED.salience, decay_lambda=EXCLUDED.decay_lambda, 
                version=EXCLUDED.version, mean_dim=EXCLUDED.mean_dim, mean_vec=EXCLUDED.mean_vec, 
                compressed_vec=EXCLUDED.compressed_vec, feedback_score=EXCLUDED.feedback_score"""

        elif "INSERT OR REPLACE INTO memory_branch_links" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO memory_branch_links",
                "INSERT INTO memory_branch_links",
            )
            sql += " ON CONFLICT (project_id, branch) DO UPDATE SET base_branch=EXCLUDED.base_branch, updated_at=EXCLUDED.updated_at"

        elif "INSERT OR REPLACE INTO memory_overlay_tombstones" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO memory_overlay_tombstones",
                "INSERT INTO memory_overlay_tombstones",
            )
            sql += " ON CONFLICT (project_id, branch, target_type, target_key) DO UPDATE SET created_at=EXCLUDED.created_at"

        elif "INSERT OR REPLACE INTO document_versions" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO document_versions",
                "INSERT INTO document_versions",
            )
            sql += " ON CONFLICT (project_id, branch, doc_key, version) DO UPDATE SET parent_version=EXCLUDED.parent_version, base_branch=EXCLUDED.base_branch, filename=EXCLUDED.filename, file_sha256=EXCLUDED.file_sha256, total_units=EXCLUDED.total_units, reused_units=EXCLUDED.reused_units, new_units=EXCLUDED.new_units, deleted_units=EXCLUDED.deleted_units, status=EXCLUDED.status, created_at=EXCLUDED.created_at"

        elif "INSERT OR REPLACE INTO document_units" in sql:
            sql = sql.replace(
                "INSERT OR REPLACE INTO document_units", "INSERT INTO document_units"
            )
            sql += " ON CONFLICT (id) DO UPDATE SET project_id=EXCLUDED.project_id, branch=EXCLUDED.branch, doc_key=EXCLUDED.doc_key, version=EXCLUDED.version, unit_index=EXCLUDED.unit_index, unit_type=EXCLUDED.unit_type, unit_title=EXCLUDED.unit_title, unit_sha256=EXCLUDED.unit_sha256, markdown_text=EXCLUDED.markdown_text, memory_ids_json=EXCLUDED.memory_ids_json"

        return sql

    def _translate_for_dialect(self, sql: str, is_pg: bool) -> str:
        if not is_pg:
            return sql
        prev = self.is_postgres
        self.is_postgres = True
        try:
            return self.translate_query(sql)
        finally:
            self.is_postgres = prev

    def _analyze_route(self, sql: str, params: tuple) -> _RouteDecision:
        if not is_multi_db_enabled():
            return _RouteDecision(mode="master")

        op, tables = _extract_op_and_tables(sql)
        primary_table = tables[0] if tables else ""

        if op == "PRAGMA" or any(t in _MASTER_ONLY_TABLES for t in tables):
            return _RouteDecision(mode="master", primary_table=primary_table, op=op)

        active_tenant = get_current_tenant()
        active_source = get_current_source()
        root_branch = _effective_root_branch()

        if op in ("CREATE", "ALTER", "DROP") and active_tenant is None:
            return _RouteDecision(mode="master", primary_table=primary_table, op=op)

        if active_tenant is not None:
            proj, br = active_tenant
            if any(t in _PROJECT_ROOT_TABLES for t in tables):
                return _RouteDecision(
                    mode="single",
                    targets=[(proj, root_branch)],
                    source_id="__default__",
                    primary_table=primary_table,
                    op=op,
                )
            if br == "%":
                return _RouteDecision(
                    mode="project_wildcard",
                    project_id=proj,
                    branch_like="%",
                    source_id=active_source,
                    primary_table=primary_table,
                    op=op,
                )
            return _RouteDecision(
                mode="single",
                targets=[(proj, br)],
                source_id=active_source,
                primary_table=primary_table,
                op=op,
            )

        if op in ("BEGIN", "COMMIT", "ROLLBACK"):
            return _RouteDecision(
                mode="single",
                targets=[("default", root_branch)],
                primary_table=primary_table,
                op=op,
            )

        if op == "INSERT":
            parsed = _parse_insert_col_values(sql, params)
            if parsed is not None:
                tbl, col_vals = parsed
                col_source = (
                    str(col_vals["source_id"]).strip()
                    if col_vals.get("source_id") is not None
                    and str(col_vals["source_id"]).strip()
                    else None
                )
                if "project_id" in col_vals:
                    raw_p = str(col_vals["project_id"] or "default").strip() or "default"
                    proj, inf_src, inferred_br = parse_tenant_spec(
                        raw_p, default_branch=root_branch
                    )
                    if tbl in _PROJECT_ROOT_TABLES:
                        return _RouteDecision(
                            mode="single",
                            targets=[(proj, root_branch)],
                            source_id="__default__",
                            primary_table=tbl,
                            op=op,
                        )
                    if tbl in _PROJECT_NO_BRANCH_TABLES:
                        br = root_branch
                    elif "branch" in col_vals and col_vals["branch"] is not None:
                        br = str(col_vals["branch"]).strip() or root_branch
                    else:
                        br = inferred_br
                    eff_src = inf_src if inf_src is not None else (
                        col_source if col_source is not None else active_source
                    )
                    return _RouteDecision(
                        mode="single",
                        targets=[(proj, br)],
                        source_id=eff_src,
                        primary_table=tbl,
                        op=op,
                    )
                if "user_id" in col_vals:
                    uid = col_vals["user_id"]
                    if tbl == "waypoints" and col_vals.get("src_id"):
                        src_hint = tenant_manager.lookup_memory_hint(
                            str(col_vals["src_id"])
                        )
                        src_hint_source = tenant_manager.lookup_memory_source_hint(
                            str(col_vals["src_id"])
                        )
                        if src_hint and (
                            uid is None
                            or str(uid).strip() in ("", "system", "anonymous")
                            or parse_tenant_from_user_id(
                                str(uid), default_branch=root_branch
                            )
                            == src_hint
                        ):
                            return _RouteDecision(
                                mode="single",
                                targets=[src_hint],
                                source_id=src_hint_source,
                                primary_table=tbl,
                                op=op,
                            )
                    proj, inf_src, br = parse_tenant_spec(
                        None if uid is None else str(uid), default_branch=root_branch
                    )
                    eff_src = inf_src if inf_src is not None else (
                        col_source if col_source is not None else active_source
                    )
                    hint = (
                        (str(col_vals["id"]), proj, br)
                        if tbl == "memories" and col_vals.get("id")
                        else None
                    )
                    return _RouteDecision(
                        mode="single",
                        targets=[(proj, br)],
                        source_id=eff_src,
                        memory_hint=hint,
                        memory_hint_source=eff_src,
                        primary_table=tbl,
                        op=op,
                    )
            return _RouteDecision(
                mode="single",
                targets=[("default", root_branch)],
                source_id=active_source,
                primary_table=primary_table,
                op=op,
            )

        # SELECT / UPDATE / DELETE parameter inspection
        placeholders = _find_placeholders(sql)
        explicit_targets: List[Tuple[str, str]] = []
        explicit_target_sources: List[Optional[str]] = []
        found_project_id: Optional[str] = None
        found_source_id: Optional[str] = None
        found_branch: Optional[str] = None
        found_ids: List[str] = []

        for idx, pos in enumerate(placeholders):
            if idx >= len(params):
                break
            prefix = sql[:pos].lower()
            val = params[idx]

            if re.search(r"\buser_id\s*=\s*$", prefix):
                p_val, s_val, b_val = parse_tenant_spec(
                    None if val is None else str(val), default_branch=root_branch
                )
                t = (p_val, b_val)
                if t not in explicit_targets:
                    explicit_targets.append(t)
                    explicit_target_sources.append(s_val)
            elif re.search(r"\buser_id\s+like\s+$", prefix):
                pat = "" if val is None else str(val)
                if ":" in pat:
                    left_part, b_part = pat.split(":", 1)
                    p_clean, s_clean, _ = parse_tenant_spec(
                        left_part, default_branch=root_branch
                    )
                    if "%" in p_clean:
                        return _RouteDecision(
                            mode="fanout", primary_table=primary_table, op=op
                        )
                    if "%" in b_part or "_" in b_part:
                        return _RouteDecision(
                            mode="project_wildcard",
                            project_id=p_clean,
                            branch_like=b_part or "%",
                            source_id=s_clean if s_clean is not None else active_source,
                            primary_table=primary_table,
                            op=op,
                        )
                    t = (p_clean, b_part.strip() or root_branch)
                    if t not in explicit_targets:
                        explicit_targets.append(t)
                        explicit_target_sources.append(s_clean)
                else:
                    if "%" in pat:
                        return _RouteDecision(
                            mode="fanout", primary_table=primary_table, op=op
                        )
                    p_val, s_val, b_val = parse_tenant_spec(
                        pat, default_branch=root_branch
                    )
                    t = (p_val, b_val)
                    if t not in explicit_targets:
                        explicit_targets.append(t)
                        explicit_target_sources.append(s_val)
            elif re.search(
                r"\buser_id\s+in\s*\(\s*(?:(?:\?|%s)\s*,\s*)*$", prefix
            ):
                p_val, s_val, b_val = parse_tenant_spec(
                    None if val is None else str(val), default_branch=root_branch
                )
                t = (p_val, b_val)
                if t not in explicit_targets:
                    explicit_targets.append(t)
                    explicit_target_sources.append(s_val)
            elif re.search(r"\bproject_id\s*=\s*$", prefix):
                raw_p = "" if val is None else str(val).strip()
                if raw_p:
                    found_project_id, inf_s, inf_b = parse_tenant_spec(
                        raw_p, default_branch=root_branch
                    )
                    if inf_s is not None and found_source_id is None:
                        found_source_id = inf_s
                    if ":" in raw_p and found_branch is None:
                        found_branch = inf_b
            elif re.search(r"\bsource_id\s*=\s*$", prefix):
                raw_s = "" if val is None else str(val).strip()
                if raw_s:
                    found_source_id = raw_s
            elif re.search(r"\bbranch(?:_name)?\s*=\s*$", prefix):
                raw_b = "" if val is None else str(val).strip()
                if raw_b:
                    found_branch = raw_b
            elif re.search(r"\b(?:id|src_id|dst_id|memory_id)\s*=\s*$", prefix):
                if val is not None:
                    found_ids.append(str(val))

        if any(t in _PROJECT_ROOT_TABLES for t in tables):
            if found_project_id:
                return _RouteDecision(
                    mode="single",
                    targets=[(found_project_id, root_branch)],
                    source_id="__default__",
                    primary_table=primary_table,
                    op=op,
                )
            return _RouteDecision(
                mode="fanout",
                source_id="__default__",
                primary_table=primary_table,
                op=op,
            )

        if explicit_targets:
            if len(explicit_targets) == 1:
                eff_s = (
                    explicit_target_sources[0]
                    if explicit_target_sources[0] is not None
                    else (found_source_id if found_source_id is not None else active_source)
                )
                return _RouteDecision(
                    mode="single",
                    targets=explicit_targets,
                    source_id=eff_s,
                    primary_table=primary_table,
                    op=op,
                )
            resolved_sources = [
                s if s is not None else (found_source_id if found_source_id is not None else active_source)
                for s in explicit_target_sources
            ]
            return _RouteDecision(
                mode="multi",
                targets=explicit_targets,
                target_sources=resolved_sources,
                primary_table=primary_table,
                op=op,
            )

        eff_found_source = found_source_id if found_source_id is not None else active_source

        if found_project_id:
            if found_branch:
                return _RouteDecision(
                    mode="single",
                    targets=[(found_project_id, found_branch)],
                    source_id=eff_found_source,
                    primary_table=primary_table,
                    op=op,
                )
            lit_br = re.search(r"\bbranch\s*=\s*'([^']*)'", sql, re.IGNORECASE)
            if lit_br:
                return _RouteDecision(
                    mode="single",
                    targets=[(found_project_id, lit_br.group(1) or root_branch)],
                    source_id=eff_found_source,
                    primary_table=primary_table,
                    op=op,
                )
            if primary_table in _PROJECT_NO_BRANCH_TABLES:
                return _RouteDecision(
                    mode="single",
                    targets=[(found_project_id, root_branch)],
                    source_id=eff_found_source,
                    primary_table=primary_table,
                    op=op,
                )
            return _RouteDecision(
                mode="project_wildcard",
                project_id=found_project_id,
                branch_like="%",
                source_id=eff_found_source,
                primary_table=primary_table,
                op=op,
            )

        if found_ids:
            hinted_targets: List[Tuple[str, str]] = []
            hinted_sources: List[Optional[str]] = []
            all_found = True
            for fid in found_ids:
                h = tenant_manager.lookup_memory_hint(fid)
                if h is None:
                    all_found = False
                else:
                    hs = tenant_manager.lookup_memory_source_hint(fid)
                    if h not in hinted_targets:
                        hinted_targets.append(h)
                        hinted_sources.append(hs)
            if hinted_targets and all_found:
                if len(hinted_targets) == 1:
                    return _RouteDecision(
                        mode="single",
                        targets=hinted_targets,
                        source_id=hinted_sources[0],
                        lookup_ids=found_ids,
                        primary_table=primary_table,
                        op=op,
                    )
                return _RouteDecision(
                    mode="multi",
                    targets=hinted_targets,
                    target_sources=hinted_sources,
                    lookup_ids=found_ids,
                    primary_table=primary_table,
                    op=op,
                )
            return _RouteDecision(
                mode="id_scan",
                lookup_ids=found_ids,
                source_id=active_source,
                primary_table=primary_table,
                op=op,
            )

        return _RouteDecision(
            mode="fanout",
            source_id=active_source,
            primary_table=primary_table,
            op=op,
        )

    def _exec_on_conn(
        self, conn: Any, is_pg: bool, sql: str, params: tuple
    ) -> Tuple[Any, int]:
        eff_sql = self._translate_for_dialect(sql, is_pg)
        if is_pg:
            with conn.cursor() as cur:
                if params:
                    cur.execute(eff_sql, tuple(params))
                else:
                    cur.execute(eff_sql)
                rc = int(getattr(cur, "rowcount", 0) or 0)
            return None, rc
        res = conn.execute(eff_sql, params)
        rc = int(getattr(res, "rowcount", 0) or 0)
        return res, rc

    def _fetchall_on_conn(
        self, conn: Any, is_pg: bool, sql: str, params: tuple
    ) -> List[Dict[str, Any]]:
        eff_sql = self._translate_for_dialect(sql, is_pg)
        if is_pg:
            try:
                cur_ctx = conn.cursor(cursor_factory=RealDictCursor)
            except TypeError:
                cur_ctx = conn.cursor()
            with cur_ctx as cur:
                if params:
                    cur.execute(eff_sql, tuple(params))
                else:
                    cur.execute(eff_sql)
                rows = cur.fetchall()
                return tenant_manager._normalize_pg_rows(rows, cur.description)
        rows = conn.execute(eff_sql, params).fetchall()
        return [dict(r) for r in rows]

    def _collect_existing_targets(
        self,
        branches: List[Any],
        include_master: bool = True,
        source_id: Optional[str] = None,
    ) -> List[Tuple[Optional[Tuple[str, str]], Optional[str], Any, bool]]:
        targets: List[Tuple[Optional[Tuple[str, str]], Optional[str], Any, bool]] = []
        seen_conn_ids: set = set()
        for item in branches:
            if len(item) == 3:
                proj, item_src, br = item
                eff_src = item_src if item_src is not None else source_id
            else:
                proj, br = item
                eff_src = source_id
            try:
                t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(
                    proj, br, source_id=eff_src
                )
                cid = id(t_conn)
                if cid not in seen_conn_ids:
                    seen_conn_ids.add(cid)
                    targets.append(((proj, br), eff_src, t_conn, t_is_pg))
            except Exception as exc:
                logger.debug("Skipping tenant %s:%s during fanout: %s", proj, br, exc)
        if include_master and self.conn is not None and id(self.conn) not in seen_conn_ids:
            targets.append((None, None, self.conn, self.is_postgres))
        return targets

    def _resolve_wildcard_branches(
        self,
        project_id: str,
        branch_like: str,
        source_id: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        branches = tenant_manager.list_project_branches(project_id, source_id=source_id)
        if not branches:
            return []
        if not branch_like or branch_like == "%":
            return [(project_id, b) for b in branches]
        rx = _like_to_regex(branch_like)
        matched = [
            (project_id, b)
            for b in branches
            if rx.match(b) or rx.match(f"{project_id}:{b}")
        ]
        # Fallback to all branches of project so SQL LIKE on stored user_id decides
        return matched if matched else [(project_id, b) for b in branches]

    def _merge_fanout_rows(
        self,
        sql: str,
        per_target_rows: List[Tuple[Optional[Tuple[str, str]], List[Dict[str, Any]]]],
        primary_table: str,
        lim_val: Optional[int],
        off_val: int,
        target_sources: Optional[List[Optional[str]]] = None,
    ) -> List[Dict[str, Any]]:
        cleaned_sql = _strip_sql_comments(sql).strip()

        m_cnt = _COUNT_STAR_RE.match(cleaned_sql)
        if m_cnt and not _SECTOR_GROUP_RE.match(cleaned_sql) and "group by" not in cleaned_sql.lower():
            alias = m_cnt.group(1)
            total = 0
            for _, rows in per_target_rows:
                for r in rows:
                    total += int(r.get(alias) or 0)
            return [{alias: total}]

        m_max = _MAX_SEG_RE.match(cleaned_sql)
        if m_max:
            alias = m_max.group(2)
            vals = [
                int(r.get(alias) or 0)
                for _, rows in per_target_rows
                for r in rows
                if r.get(alias) is not None
            ]
            return [{alias: max(vals) if vals else 0}]

        m_grp = _SECTOR_GROUP_RE.match(cleaned_sql)
        if m_grp:
            alias = m_grp.group(1)
            agg: Dict[Any, int] = {}
            for _, rows in per_target_rows:
                for r in rows:
                    sec = r.get("primary_sector")
                    agg[sec] = agg.get(sec, 0) + int(r.get(alias) or 0)
            return [{"primary_sector": k, alias: v} for k, v in agg.items()]

        merged: List[Dict[str, Any]] = []
        seen_ids: set = set()
        use_id_dedup = primary_table in _DEDUP_ID_TABLES
        is_distinct = bool(re.match(r"^\s*select\s+distinct\b", cleaned_sql, re.IGNORECASE))
        seen_tuples: set = set()

        for idx, (tenant_pair, rows) in enumerate(per_target_rows):
            t_src = (
                target_sources[idx]
                if target_sources is not None and idx < len(target_sources)
                else None
            )
            for r in rows:
                if tenant_pair and primary_table == "memories" and r.get("id"):
                    eff_r_src = t_src if t_src is not None else r.get("source_id")
                    tenant_manager.remember_memory_id(
                        str(r["id"]),
                        tenant_pair[0],
                        tenant_pair[1],
                        source_id=eff_r_src,
                    )
                if use_id_dedup and r.get("id") is not None:
                    rid = str(r["id"])
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                elif is_distinct:
                    key = tuple(sorted(r.items()))
                    if key in seen_tuples:
                        continue
                    seen_tuples.add(key)
                merged.append(r)

        m_ord = _ORDER_BY_RE.search(cleaned_sql)
        if m_ord and merged:
            ord_col = m_ord.group(1)
            ord_dir = (m_ord.group(2) or "ASC").upper()
            if ord_col in merged[0]:
                reverse = ord_dir == "DESC"
                merged.sort(
                    key=lambda row: (
                        row.get(ord_col) is None,
                        row.get(ord_col) if row.get(ord_col) is not None else 0,
                    ),
                    reverse=reverse,
                )

        if lim_val is not None:
            return merged[off_val : off_val + lim_val]
        return merged

    def execute(self, sql: str, params: tuple = ()):
        self.connect()
        route = self._analyze_route(sql, params)

        if route.mode == "master":
            res, rc = self._exec_on_conn(self.conn, self.is_postgres, sql, params)
            self._last_rowcount = rc
            return res

        if route.mode in ("single", "multi"):
            last_res = None
            total_rc = 0
            for idx, (proj, br) in enumerate(route.targets):
                t_src = (
                    route.target_sources[idx]
                    if route.target_sources is not None and idx < len(route.target_sources)
                    else route.source_id
                )
                if route.op in ("SELECT", "UPDATE", "DELETE") and not tenant_manager.has_branch(
                    proj, br, source_id=t_src
                ):
                    res, rc = self._exec_on_conn(self.conn, self.is_postgres, sql, params)
                    last_res = res
                    total_rc += rc
                    continue
                t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(
                    proj, br, source_id=t_src
                )
                res, rc = self._exec_on_conn(t_conn, t_is_pg, sql, params)
                last_res = res
                total_rc += rc
                if route.memory_hint and rc >= 0:
                    mid, h_proj, h_br = route.memory_hint
                    tenant_manager.remember_memory_id(
                        mid, h_proj, h_br, source_id=route.memory_hint_source
                    )
            self._last_rowcount = total_rc
            return last_res

        if route.mode == "project_wildcard":
            branches = self._resolve_wildcard_branches(
                route.project_id or "default",
                route.branch_like,
                source_id=route.source_id,
            )
            targets = self._collect_existing_targets(
                branches, include_master=True, source_id=route.source_id
            )
            last_res = None
            total_rc = 0
            for _, _, t_conn, t_is_pg in targets:
                res, rc = self._exec_on_conn(t_conn, t_is_pg, sql, params)
                last_res = res
                total_rc += rc
            self._last_rowcount = total_rc
            return last_res

        if route.mode == "id_scan":
            branches = (
                [(p, "__default__", b) for p, b in tenant_manager.list_all_registered_branches()]
                if route.source_id == "__default__"
                else tenant_manager.list_all_registered_branch_targets()
            )
            targets = self._collect_existing_targets(branches, include_master=True)
            last_res = None
            total_rc = 0
            stop_on_hit = route.primary_table in ("memories", "temporal_facts", "temporal_edges")
            for tenant_pair, t_src, t_conn, t_is_pg in targets:
                res, rc = self._exec_on_conn(t_conn, t_is_pg, sql, params)
                last_res = res
                total_rc += rc
                if rc > 0 and tenant_pair and route.lookup_ids:
                    for fid in route.lookup_ids:
                        tenant_manager.remember_memory_id(
                            fid, tenant_pair[0], tenant_pair[1], source_id=t_src
                        )
                    if stop_on_hit:
                        break
            self._last_rowcount = total_rc
            return last_res

        # fanout
        branches = (
            [(p, "__default__", b) for p, b in tenant_manager.list_all_registered_branches()]
            if route.source_id == "__default__"
            else tenant_manager.list_all_registered_branch_targets()
        )
        targets = self._collect_existing_targets(branches, include_master=True)
        last_res = None
        total_rc = 0
        for _, _, t_conn, t_is_pg in targets:
            res, rc = self._exec_on_conn(t_conn, t_is_pg, sql, params)
            last_res = res
            total_rc += rc
        self._last_rowcount = total_rc
        return last_res

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self.connect()
        route = self._analyze_route(sql, params)

        if route.mode == "master":
            rows = self._fetchall_on_conn(self.conn, self.is_postgres, sql, params)
            self._last_rowcount = len(rows)
            return rows

        if route.mode == "single":
            proj, br = route.targets[0]
            if not tenant_manager.has_branch(proj, br, source_id=route.source_id):
                rows = self._fetchall_on_conn(self.conn, self.is_postgres, sql, params)
                self._last_rowcount = len(rows)
                return rows
            t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(
                proj, br, source_id=route.source_id
            )
            rows = self._fetchall_on_conn(t_conn, t_is_pg, sql, params)
            if route.primary_table == "memories":
                for r in rows:
                    if r.get("id"):
                        tenant_manager.remember_memory_id(
                            str(r["id"]), proj, br, source_id=route.source_id
                        )
            self._last_rowcount = len(rows)
            return rows

        if route.mode == "multi":
            per_target_rows: List[Tuple[Optional[Tuple[str, str]], List[Dict[str, Any]]]] = []
            per_target_sources: List[Optional[str]] = []
            eff_sql, eff_params, lim_val, off_val = _rewrite_fanout_limit_offset(sql, params)
            for idx, (proj, br) in enumerate(route.targets):
                t_src = (
                    route.target_sources[idx]
                    if route.target_sources is not None and idx < len(route.target_sources)
                    else route.source_id
                )
                if not tenant_manager.has_branch(proj, br, source_id=t_src):
                    continue
                t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(
                    proj, br, source_id=t_src
                )
                per_target_rows.append(
                    ((proj, br), self._fetchall_on_conn(t_conn, t_is_pg, eff_sql, eff_params))
                )
                per_target_sources.append(t_src)
            if not per_target_rows:
                per_target_rows.append(
                    (None, self._fetchall_on_conn(self.conn, self.is_postgres, eff_sql, eff_params))
                )
                per_target_sources.append(None)
            merged = self._merge_fanout_rows(
                sql,
                per_target_rows,
                route.primary_table,
                lim_val,
                off_val,
                target_sources=per_target_sources,
            )
            self._last_rowcount = len(merged)
            return merged

        if route.mode == "project_wildcard":
            branches = self._resolve_wildcard_branches(
                route.project_id or "default",
                route.branch_like,
                source_id=route.source_id,
            )
            targets = self._collect_existing_targets(
                branches, include_master=True, source_id=route.source_id
            )
        else:
            branches = (
                [(p, "__default__", b) for p, b in tenant_manager.list_all_registered_branches()]
                if route.source_id == "__default__"
                else tenant_manager.list_all_registered_branch_targets()
            )
            targets = self._collect_existing_targets(branches, include_master=True)

        eff_sql, eff_params, lim_val, off_val = _rewrite_fanout_limit_offset(sql, params)
        per_target_rows = []
        per_target_sources = []
        for tenant_pair, t_src, t_conn, t_is_pg in targets:
            try:
                rows = self._fetchall_on_conn(t_conn, t_is_pg, eff_sql, eff_params)
            except Exception as exc:
                if tenant_pair is None:
                    logger.debug("Master fallback query skipped (%s)", exc)
                    rows = []
                else:
                    raise
            if route.mode == "id_scan" and rows and tenant_pair and route.lookup_ids:
                for fid in route.lookup_ids:
                    tenant_manager.remember_memory_id(
                        fid, tenant_pair[0], tenant_pair[1], source_id=t_src
                    )
            per_target_rows.append((tenant_pair, rows))
            per_target_sources.append(t_src)

        merged = self._merge_fanout_rows(
            sql,
            per_target_rows,
            route.primary_table,
            lim_val,
            off_val,
            target_sources=per_target_sources,
        )
        self._last_rowcount = len(merged)
        return merged

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        self.connect()
        route = self._analyze_route(sql, params)

        if route.mode == "master":
            rows = self._fetchall_on_conn(self.conn, self.is_postgres, sql, params)
            self._last_rowcount = 1 if rows else 0
            return rows[0] if rows else None

        if route.mode == "single":
            proj, br = route.targets[0]
            if not tenant_manager.has_branch(proj, br, source_id=route.source_id):
                rows = self._fetchall_on_conn(self.conn, self.is_postgres, sql, params)
                self._last_rowcount = 1 if rows else 0
                return rows[0] if rows else None
            t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(
                proj, br, source_id=route.source_id
            )
            rows = self._fetchall_on_conn(t_conn, t_is_pg, sql, params)
            if rows and route.primary_table == "memories" and rows[0].get("id"):
                tenant_manager.remember_memory_id(
                    str(rows[0]["id"]), proj, br, source_id=route.source_id
                )
            self._last_rowcount = 1 if rows else 0
            return rows[0] if rows else None

        if route.mode == "id_scan":
            branches = (
                [(p, "__default__", b) for p, b in tenant_manager.list_all_registered_branches()]
                if route.source_id == "__default__"
                else tenant_manager.list_all_registered_branch_targets()
            )
            targets = self._collect_existing_targets(branches, include_master=True)
            for tenant_pair, t_src, t_conn, t_is_pg in targets:
                rows = self._fetchall_on_conn(t_conn, t_is_pg, sql, params)
                if rows:
                    if tenant_pair and route.lookup_ids:
                        for fid in route.lookup_ids:
                            tenant_manager.remember_memory_id(
                                fid, tenant_pair[0], tenant_pair[1], source_id=t_src
                            )
                    self._last_rowcount = 1
                    return rows[0]
            self._last_rowcount = 0
            return None

        rows = self.fetchall(sql, params)
        self._last_rowcount = 1 if rows else 0
        return rows[0] if rows else None

    def commit(self):
        if self.conn:
            try:
                self.conn.commit()
            except Exception:
                pass
        for t_conn in list(tenant_manager._sqlite_conns.values()):
            try:
                if getattr(t_conn, "in_transaction", False):
                    t_conn.commit()
            except Exception:
                pass

    @property
    def total_changes(self) -> int:
        if self.is_postgres or is_multi_db_enabled():
            return self._last_rowcount
        return self.conn.total_changes if self.conn else 0


db = DB()


class Queries:
    def ins_mem(self, **k):
        sql = """
        INSERT INTO memories(id, user_id, segment, content, simhash, primary_sector, tags, meta, created_at, updated_at, last_seen_at, salience, decay_lambda, version, mean_dim, mean_vec, compressed_vec, feedback_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        user_id=excluded.user_id, segment=excluded.segment, content=excluded.content, simhash=excluded.simhash, primary_sector=excluded.primary_sector,
        tags=excluded.tags, meta=excluded.meta, created_at=excluded.created_at, updated_at=excluded.updated_at, last_seen_at=excluded.last_seen_at,
        salience=excluded.salience, decay_lambda=excluded.decay_lambda, version=excluded.version, mean_dim=excluded.mean_dim,
        mean_vec=excluded.mean_vec, compressed_vec=excluded.compressed_vec, feedback_score=excluded.feedback_score
        """
        vals = (
            k.get("id"),
            k.get("user_id"),
            k.get("segment", 0),
            k.get("content"),
            k.get("simhash"),
            k.get("primary_sector"),
            k.get("tags"),
            k.get("meta"),
            k.get("created_at"),
            k.get("updated_at"),
            k.get("last_seen_at"),
            k.get("salience", 1.0),
            k.get("decay_lambda", 0.02),
            k.get("version", 1),
            k.get("mean_dim"),
            k.get("mean_vec"),
            k.get("compressed_vec"),
            k.get("feedback_score", 0),
        )
        db.execute(sql, vals)
        db.commit()

    def get_mem(self, mid: str):
        return db.fetchone("SELECT * FROM memories WHERE id=?", (mid,))

    def all_mem(self, limit=10, offset=0):
        return db.fetchall(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    def ins_log(
        self, id: str, model: str, status: str, ts: int, err: Optional[str] = None
    ):
        db.execute(
            "INSERT INTO embed_logs(id, model, status, ts, err) VALUES (?,?,?,?,?)",
            (id, model, status, ts, err),
        )
        db.commit()

    def upd_log(self, id: str, status: str, err: Optional[str] = None):
        db.execute(
            "UPDATE embed_logs SET status=?, err=? WHERE id=?", (status, err, id)
        )
        db.commit()

    def all_mem_by_user(self, user_id: str, limit=10, offset=0):
        if user_id and ("%" in user_id or "_" in user_id):
            return db.fetchall(
                "SELECT * FROM memories WHERE user_id LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            )
        return db.fetchall(
            "SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )

    def get_waypoints_by_src(self, src_id: str):
        return db.fetchall("SELECT * FROM waypoints WHERE src_id=?", (src_id,))

    def del_mem(self, mid: str):
        db.execute("DELETE FROM memories WHERE id=?", (mid,))
        db.execute("DELETE FROM vectors WHERE id=?", (mid,))
        db.execute("DELETE FROM waypoints WHERE src_id=? OR dst_id=?", (mid, mid))
        db.commit()

    def del_mem_by_user(self, uid: str):
        if uid and ("%" in uid or "_" in uid):
            db.execute(
                "DELETE FROM vectors WHERE id IN (SELECT id FROM memories WHERE user_id LIKE ?)",
                (uid,),
            )
            db.execute(
                "DELETE FROM waypoints WHERE src_id IN (SELECT id FROM memories WHERE user_id LIKE ?) OR dst_id IN (SELECT id FROM memories WHERE user_id LIKE ?)",
                (uid, uid),
            )
            db.execute("DELETE FROM memories WHERE user_id LIKE ?", (uid,))
        else:
            db.execute(
                "DELETE FROM vectors WHERE id IN (SELECT id FROM memories WHERE user_id=?)",
                (uid,),
            )
            db.execute(
                "DELETE FROM waypoints WHERE src_id IN (SELECT id FROM memories WHERE user_id=?) OR dst_id IN (SELECT id FROM memories WHERE user_id=?)",
                (uid, uid),
            )
            db.execute("DELETE FROM memories WHERE user_id=?", (uid,))
        db.commit()


q = Queries()


def log_maint_op(op_type: str, count: int = 0) -> None:
    ts = int(time.time() * 1000)
    logger.info(f"[MAINT] op={op_type} count={count}")
    if db.conn:
        try:
            metrics = json.dumps({"op": op_type, "count": count})
            db.execute("INSERT INTO stats(ts, metrics) VALUES (?, ?)", (ts, metrics))
            db.commit()
        except Exception as e:
            logger.debug(f"[MAINT] Failed to log stats for {op_type}: {e}")


def transaction():
    return db.conn


def _normalize_overlay_key(target_type: str, target_key: str) -> str:
    raw = str(target_key or "").strip()
    if target_type == "file":
        norm = raw.replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        return norm.lstrip("/")
    return raw


def ensure_memory_overlay_tables() -> None:
    if db.conn is None:
        db.connect()
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_branch_links "
        "(project_id TEXT NOT NULL, branch TEXT NOT NULL, base_branch TEXT NOT NULL, "
        "updated_at BIGINT, PRIMARY KEY (project_id, branch))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS memory_overlay_tombstones "
        "(project_id TEXT NOT NULL, branch TEXT NOT NULL, target_type TEXT NOT NULL, "
        "target_key TEXT NOT NULL, created_at BIGINT, "
        "PRIMARY KEY (project_id, branch, target_type, target_key))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_tomb_proj_branch "
        "ON memory_overlay_tombstones(project_id, branch)"
    )
    db.commit()


def register_memory_branch(
    project_id: str, branch: str, base_branch: str, max_depth: int = 8
) -> None:
    proj = (project_id or "").strip()
    br = (branch or "").strip()
    base = (base_branch or "").strip()
    if not proj or not br or not base or br == base:
        return

    ensure_memory_overlay_tables()
    rows = db.fetchall(
        "SELECT branch, base_branch FROM memory_branch_links WHERE project_id=?",
        (proj,),
    )
    parent_map: Dict[str, str] = {
        r["branch"]: r["base_branch"]
        for r in rows
        if r.get("branch") and r.get("base_branch")
    }
    parent_map[br] = base

    for start_branch in parent_map:
        visited = {start_branch}
        cur = parent_map.get(start_branch)
        depth = 1
        while cur:
            if cur in visited:
                raise ValueError(
                    f"Cycle detected in memory branch hierarchy: '{br}' -> '{base}'"
                )
            visited.add(cur)
            if depth > max_depth:
                raise ValueError(
                    f"Memory branch hierarchy depth exceeds maximum of {max_depth} for branch '{start_branch}'"
                )
            nxt = parent_map.get(cur)
            if not nxt or nxt == cur:
                break
            depth += 1
            cur = nxt

    now_ms = int(time.time() * 1000)
    db.execute(
        "INSERT OR REPLACE INTO memory_branch_links(project_id, branch, base_branch, updated_at) VALUES (?,?,?,?)",
        (proj, br, base, now_ms),
    )
    db.commit()

    if is_multi_db_enabled():
        tenant_manager.ensure_branch_schema(
            proj, br, base_branch=base, record_lineage=False
        )


def record_memory_tombstone(
    project_id: str, branch: str, target_type: str, target_key: str
) -> None:
    proj = (project_id or "").strip()
    br = (branch or "").strip()
    ttype = (target_type or "").strip()
    norm_key = _normalize_overlay_key(ttype, target_key)
    if not proj or not br or not ttype or not norm_key:
        return

    ensure_memory_overlay_tables()
    now_ms = int(time.time() * 1000)
    db.execute(
        "INSERT OR REPLACE INTO memory_overlay_tombstones(project_id, branch, target_type, target_key, created_at) VALUES (?,?,?,?,?)",
        (proj, br, ttype, norm_key, now_ms),
    )
    db.commit()


def get_memory_branch_lineage(
    project_id: str, branch: str, max_depth: int = 8
) -> List[str]:
    proj = (project_id or "").strip()
    br = (branch or "").strip()
    if not br:
        return []
    if not proj:
        return [br]

    ensure_memory_overlay_tables()
    lineage: List[str] = [br]
    visited = {br}
    cur = br

    while len(lineage) <= max_depth:
        row = db.fetchone(
            "SELECT base_branch FROM memory_branch_links WHERE project_id=? AND branch=?",
            (proj, cur),
        )
        parent = row["base_branch"].strip() if row and row.get("base_branch") else None
        if not parent:
            try:
                gb_row = db.fetchone(
                    "SELECT base_branch FROM graph_branches WHERE project_id=? AND branch_name=?",
                    (proj, cur),
                )
                if gb_row and gb_row.get("base_branch"):
                    parent = str(gb_row["base_branch"]).strip()
            except Exception:
                parent = None

        if not parent or parent == cur or parent in visited:
            break
        lineage.append(parent)
        visited.add(parent)
        cur = parent

    return lineage


def get_memory_tombstones(project_id: str, branch: str) -> Dict[str, set]:
    res: Dict[str, set] = {"file": set(), "doc": set()}
    proj = (project_id or "").strip()
    br = (branch or "").strip()
    if not proj or not br:
        return res

    ensure_memory_overlay_tables()
    rows = db.fetchall(
        "SELECT target_type, target_key FROM memory_overlay_tombstones WHERE project_id=? AND branch=?",
        (proj, br),
    )
    for r in rows:
        ttype = r.get("target_type")
        tkey = r.get("target_key")
        if ttype and tkey:
            res.setdefault(ttype, set()).add(_normalize_overlay_key(ttype, tkey))
    return res


def ensure_doc_versioning_tables() -> None:
    if db.conn is None:
        db.connect()
    db.execute(
        "CREATE TABLE IF NOT EXISTS document_versions "
        "(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch TEXT NOT NULL, "
        "doc_key TEXT NOT NULL, version TEXT NOT NULL, parent_version TEXT, "
        "base_branch TEXT, filename TEXT NOT NULL, file_sha256 TEXT NOT NULL, "
        "total_units INTEGER DEFAULT 0, reused_units INTEGER DEFAULT 0, "
        "new_units INTEGER DEFAULT 0, deleted_units INTEGER DEFAULT 0, "
        "status TEXT DEFAULT 'active', created_at BIGINT NOT NULL, "
        "source_id TEXT DEFAULT '__default__', scope TEXT DEFAULT 'source', "
        "doc_role TEXT DEFAULT 'general', "
        "UNIQUE(project_id, branch, doc_key, version))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS document_units "
        "(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, branch TEXT NOT NULL, "
        "doc_key TEXT NOT NULL, version TEXT NOT NULL, unit_index INTEGER NOT NULL, "
        "unit_type TEXT NOT NULL, unit_title TEXT, unit_sha256 TEXT NOT NULL, "
        "markdown_text TEXT NOT NULL, memory_ids_json TEXT DEFAULT '[]', "
        "source_id TEXT DEFAULT '__default__', scope TEXT DEFAULT 'source', "
        "doc_role TEXT DEFAULT 'general')"
    )
    if is_multi_db_enabled() and get_current_tenant() is not None:
        proj, br = get_current_tenant()  # type: ignore[misc]
        t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(
            proj, br, source_id=get_current_source()
        )
        tenant_manager._ensure_source_columns_on_conn(
            t_conn, t_is_pg, tables=("document_versions", "document_units")
        )
    elif db.conn is not None:
        tenant_manager._ensure_source_columns_on_conn(
            db.conn, db.is_postgres, tables=("document_versions", "document_units")
        )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_units_lookup "
        "ON document_units(project_id, doc_key, unit_sha256)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_doc_versions_lookup "
        "ON document_versions(project_id, branch, doc_key, status)"
    )
    db.commit()
