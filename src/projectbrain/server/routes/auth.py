import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ...core.config import env
from ...core.db import db

logger = logging.getLogger("auth")

router = APIRouter(tags=["auth"])

_PBKDF2_ITERATIONS = 310_000
_MIN_PASSWORD_LENGTH = 8
_ROLE_RANK = {"reader": 1, "collaborator": 2, "admin": 3}
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,48}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_MCP_ACCOUNT_EXTRA_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("email", "TEXT"),
    ("display_name", "TEXT"),
    ("password_hash", "TEXT"),
    ("status", "TEXT DEFAULT 'active'"),
    ("allowed_projects", "TEXT DEFAULT '*'"),
    ("failed_login_count", "INTEGER DEFAULT 0"),
    ("locked_until", "BIGINT DEFAULT 0"),
    ("last_login_at", "BIGINT"),
    ("updated_at", "BIGINT"),
)

_schema_lock = threading.Lock()
_ensured_conn: Any = None
_ensured_db_url: Optional[str] = None


def _parse_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def _parse_int(raw: Any, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _mask_token(token: str) -> str:
    """Mask middle characters of API token for list views."""
    if not token:
        return ""
    if len(token) <= 8:
        return "********"
    return token[:4] + "*" * (len(token) - 8) + token[-4:]


def _hash_token(raw_token: str) -> str:
    """Compute SHA-256 hex digest for bearer/session/PAT token storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 (310,000 iterations)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored_hash: Optional[str]) -> bool:
    """Verify password in constant time against PBKDF2-HMAC-SHA256 hash."""
    if not password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hex = parts[3]
        if iterations <= 0 or not salt or not expected_hex:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        ).hex()
        return hmac.compare_digest(dk, expected_hex)
    except (ValueError, TypeError):
        return False


def _validate_password(password: Optional[str]) -> str:
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long.",
        )
    return password


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    cleaned = email.strip()
    if not cleaned:
        return None
    if not _EMAIL_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="Invalid email address format.")
    return cleaned


def _ensure_email_unique(email: Optional[str], exclude_username: Optional[str] = None) -> None:
    if not email:
        return
    if exclude_username:
        existing = db.fetchone(
            "SELECT username FROM mcp_accounts WHERE LOWER(email) = LOWER(?) AND username != ?",
            (email, exclude_username),
        )
    else:
        existing = db.fetchone(
            "SELECT username FROM mcp_accounts WHERE LOWER(email) = LOWER(?)",
            (email,),
        )
    if existing:
        raise HTTPException(status_code=409, detail="Email address is already in use.")


def _lower_role(role_a: Optional[str], role_b: Optional[str]) -> str:
    ra = (role_a or "reader").strip().lower()
    rb = (role_b or "reader").strip().lower()
    if ra not in _ROLE_RANK:
        ra = "reader"
    if rb not in _ROLE_RANK:
        rb = "reader"
    return ra if _ROLE_RANK[ra] <= _ROLE_RANK[rb] else rb


def _intersect_scopes(scope_a: Optional[str], scope_b: Optional[str]) -> str:
    """Intersect two comma-separated scope strings ('*' = wildcard, '__none__' = empty)."""
    a = (scope_a or "*").strip() or "*"
    b = (scope_b or "*").strip() or "*"
    if a == "*":
        return b
    if b == "*":
        return a
    if a == "__none__" or b == "__none__":
        return "__none__"
    list_a = [item.strip() for item in a.split(",") if item.strip()]
    set_b = {item.strip() for item in b.split(",") if item.strip()}
    if "*" in list_a:
        return b
    if "*" in set_b:
        return a
    seen = set()
    intersected: List[str] = []
    for item in list_a:
        if item in set_b and item not in seen:
            seen.add(item)
            intersected.append(item)
    return ",".join(intersected) if intersected else "__none__"


def is_project_allowed(project_id: Optional[str], allowed_projects_str: Optional[str]) -> bool:
    """Check if project_id (or its base project before ':branch') is permitted."""
    if not project_id:
        return True
    raw_allowed = (allowed_projects_str or "*").strip()
    if not raw_allowed or raw_allowed == "*":
        return True
    if raw_allowed == "__none__":
        return False
    allowed_items = {item.strip() for item in raw_allowed.split(",") if item.strip()}
    if not allowed_items:
        return False
    if "*" in allowed_items:
        return True
    candidate = project_id.strip()
    base_project = candidate.split(":", 1)[0].strip()
    return candidate in allowed_items or base_project in allowed_items


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if not request:
        return None
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return None


def _user_agent(request: Optional[Request]) -> Optional[str]:
    if not request:
        return None
    ua = request.headers.get("User-Agent", "").strip()
    return ua[:512] if ua else None


def _execute_rowcount(sql: str, params: tuple = ()) -> int:
    res = db.execute(sql, params)
    if db.is_postgres:
        return int(getattr(db, "_last_rowcount", 0) or 0)
    return int(getattr(res, "rowcount", 0) or 0)


def ensure_auth_schema() -> None:
    """Idempotently ensure full IAM tables and mcp_accounts columns exist on active DB."""
    global _ensured_conn, _ensured_db_url
    db.connect()
    current_url = getattr(env, "database_url", None)
    if db.conn is _ensured_conn and current_url == _ensured_db_url:
        return

    with _schema_lock:
        db.connect()
        current_url = getattr(env, "database_url", None)
        if db.conn is _ensured_conn and current_url == _ensured_db_url:
            return

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_accounts (
                username TEXT PRIMARY KEY,
                token TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                allowed_tools TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id TEXT PRIMARY KEY,
                token_hash TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                created_at BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                last_seen_at BIGINT NOT NULL,
                revoked_at BIGINT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_username ON auth_sessions(username)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at)")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_keys (
                key_id TEXT PRIMARY KEY,
                key_prefix TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                username TEXT NOT NULL,
                role_scope TEXT NOT NULL,
                allowed_tools TEXT DEFAULT '*',
                allowed_projects TEXT DEFAULT '*',
                expires_at BIGINT,
                last_used_at BIGINT,
                created_at BIGINT NOT NULL,
                revoked_at BIGINT
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_auth_api_keys_username ON auth_api_keys(username)")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_audit_logs (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_username TEXT,
                target_username TEXT,
                ip_address TEXT,
                user_agent TEXT,
                status TEXT NOT NULL,
                details TEXT,
                created_at BIGINT NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_auth_audit_logs_created ON auth_audit_logs(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_auth_audit_logs_event ON auth_audit_logs(event_type)")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at BIGINT NOT NULL
            )
            """
        )

        if db.is_postgres:
            for col_name, col_def in _MCP_ACCOUNT_EXTRA_COLUMNS:
                db.execute(f"ALTER TABLE mcp_accounts ADD COLUMN IF NOT EXISTS {col_name} {col_def}")
        else:
            cols_rows = db.fetchall("PRAGMA table_info(mcp_accounts)")
            existing_cols = {r.get("name") for r in cols_rows if r.get("name")}
            for col_name, col_def in _MCP_ACCOUNT_EXTRA_COLUMNS:
                if col_name not in existing_cols:
                    db.execute(f"ALTER TABLE mcp_accounts ADD COLUMN {col_name} {col_def}")

        db.commit()
        _ensured_conn = db.conn
        _ensured_db_url = current_url


def _log_audit(
    event_type: str,
    status: str,
    *,
    actor_username: Optional[str] = None,
    target_username: Optional[str] = None,
    request: Optional[Request] = None,
    details: Optional[Any] = None,
) -> None:
    try:
        ensure_auth_schema()
        audit_id = f"aud_{secrets.token_hex(12)}"
        now = int(time.time())
        if isinstance(details, (dict, list)):
            details_str = json.dumps(details)
        elif details is not None:
            details_str = str(details)
        else:
            details_str = None

        db.execute(
            """
            INSERT INTO auth_audit_logs (
                id, event_type, actor_username, target_username,
                ip_address, user_agent, status, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                event_type,
                actor_username,
                target_username,
                _client_ip(request),
                _user_agent(request),
                status,
                details_str,
                now,
            ),
        )
        db.commit()
    except Exception as exc:
        logger.debug(f"[AUTH] Audit logging skipped for {event_type}: {exc}")


def _read_db_auth_settings() -> Dict[str, str]:
    ensure_auth_schema()
    rows = db.fetchall("SELECT setting_key, setting_value FROM auth_settings")
    return {
        str(r["setting_key"]): str(r["setting_value"])
        for r in rows
        if r.get("setting_key") is not None
    }


def _is_rest_auth_required() -> bool:
    """Resolve runtime PB_REQUIRE_REST_AUTH (env overrides take precedence for test isolation)."""
    env_val = os.getenv("PB_REQUIRE_REST_AUTH")
    if env_val is not None:
        return _parse_bool(env_val, False)
    try:
        db_map = _read_db_auth_settings()
        if "require_rest_auth" in db_map:
            return _parse_bool(db_map["require_rest_auth"], False)
    except Exception:
        pass
    return bool(getattr(env, "require_rest_auth", False))


def _get_auth_settings() -> Dict[str, Any]:
    db_map = _read_db_auth_settings()

    if "allow_registration" in db_map:
        allow_reg = _parse_bool(db_map["allow_registration"], True)
    elif os.getenv("PB_ALLOW_REGISTRATION") is not None:
        allow_reg = _parse_bool(os.getenv("PB_ALLOW_REGISTRATION"), True)
    else:
        allow_reg = bool(getattr(env, "allow_registration", True))

    if "default_signup_role" in db_map:
        raw_role = db_map["default_signup_role"].strip().lower()
    elif os.getenv("PB_DEFAULT_SIGNUP_ROLE") is not None:
        raw_role = (os.getenv("PB_DEFAULT_SIGNUP_ROLE") or "collaborator").strip().lower()
    else:
        raw_role = str(getattr(env, "default_signup_role", "collaborator")).strip().lower()
    default_role = raw_role if raw_role in ("collaborator", "reader") else "collaborator"

    if "session_ttl_hours" in db_map:
        ttl_hours = max(1, _parse_int(db_map["session_ttl_hours"], 24))
    elif os.getenv("PB_SESSION_TTL_HOURS") is not None:
        ttl_hours = max(1, _parse_int(os.getenv("PB_SESSION_TTL_HOURS"), 24))
    else:
        ttl_hours = max(1, _parse_int(getattr(env, "session_ttl_hours", 24), 24))

    require_rest = _is_rest_auth_required()

    return {
        "allow_registration": allow_reg,
        "default_signup_role": default_role,
        "session_ttl_hours": ttl_hours,
        "require_rest_auth": require_rest,
    }


def sync_auth_settings_on_startup() -> None:
    """Apply persisted auth_settings overrides into os.environ and runtime config on startup."""
    try:
        db_map = _read_db_auth_settings()
        if "require_rest_auth" in db_map:
            val = _parse_bool(db_map["require_rest_auth"], False)
            os.environ["PB_REQUIRE_REST_AUTH"] = "true" if val else "false"
            env.require_rest_auth = val
        if "allow_registration" in db_map:
            val = _parse_bool(db_map["allow_registration"], True)
            os.environ["PB_ALLOW_REGISTRATION"] = "true" if val else "false"
            env.allow_registration = val
        if "default_signup_role" in db_map:
            role_val = db_map["default_signup_role"].strip().lower()
            if role_val in ("collaborator", "reader"):
                os.environ["PB_DEFAULT_SIGNUP_ROLE"] = role_val
                env.default_signup_role = role_val
        if "session_ttl_hours" in db_map:
            ttl_val = max(1, _parse_int(db_map["session_ttl_hours"], 24))
            os.environ["PB_SESSION_TTL_HOURS"] = str(ttl_val)
            env.session_ttl_hours = ttl_val
    except Exception as exc:
        logger.debug(f"[AUTH] Failed to sync auth settings on startup: {exc}")


def init_default_admin() -> None:
    """Seed default admin account if mcp_accounts is empty."""
    ensure_auth_schema()
    try:
        row = db.fetchone("SELECT COUNT(*) AS cnt FROM mcp_accounts")
        count = int((row or {}).get("cnt") or 0)
        if count == 0:
            ts = int(time.time())
            db.execute(
                """
                INSERT INTO mcp_accounts (
                    username, token, role, allowed_tools, allowed_projects,
                    status, failed_login_count, locked_until, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', 0, 0, ?, ?)
                ON CONFLICT (username) DO NOTHING
                """,
                ("admin", "pb_tok_admin", "admin", "*", "*", ts, ts),
            )
            db.commit()
    except Exception as exc:
        logger.warning(f"[AUTH] Failed to initialize default admin account: {exc}")


def init_auth_system() -> None:
    """Initialize auth schema, default admin account, and persisted settings."""
    ensure_auth_schema()
    init_default_admin()
    sync_auth_settings_on_startup()


# Automatically initialize schema & default admin when route module loads
try:
    init_auth_system()
except Exception:
    pass


def extract_bearer_token(request: Optional[Request]) -> Optional[str]:
    """Extract token from Authorization Bearer header, query params, or pb_session cookie."""
    if not request:
        return None
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.query_params.get("token") or request.query_params.get("api_key")
        if token:
            token = token.strip()
    if not token:
        cookie_tok = request.cookies.get("pb_session")
        if cookie_tok:
            token = cookie_tok.strip()
    return token or None


def _build_account_dict(
    row: Dict[str, Any],
    *,
    auth_method: str,
    session_id: Optional[str] = None,
    effective_role: Optional[str] = None,
    effective_allowed_tools: Optional[str] = None,
    effective_allowed_projects: Optional[str] = None,
) -> Dict[str, Any]:
    username = row.get("username")
    account_token = row.get("account_token") if "account_token" in row else row.get("token")
    status = (row.get("status") or "active").strip().lower()
    role = effective_role or (row.get("user_role") or row.get("role") or "reader").strip().lower()
    allowed_tools = (
        effective_allowed_tools
        if effective_allowed_tools is not None
        else (row.get("allowed_tools") or "*")
    )
    allowed_projects = (
        effective_allowed_projects
        if effective_allowed_projects is not None
        else (row.get("allowed_projects") or "*")
    )
    account: Dict[str, Any] = {
        "username": username,
        "role": role,
        "allowed_tools": allowed_tools,
        "allowed_projects": allowed_projects,
        "email": row.get("email"),
        "display_name": row.get("display_name"),
        "status": status,
        "has_password": bool(row.get("password_hash")),
        "has_default_token": bool(username == "admin" and account_token == "pb_tok_admin"),
        "auth_method": auth_method,
    }
    if session_id is not None:
        account["session_id"] = session_id
    return account


def _resolve_token(
    token: Optional[str],
    *,
    allow_suspended: bool = False,
    touch_timestamps: bool = True,
) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    ensure_auth_schema()
    now = int(time.time())

    if token.startswith("pb_sess_"):
        token_hash = _hash_token(token)
        row = db.fetchone(
            """
            SELECT
                s.session_id, s.expires_at, s.revoked_at,
                a.username, a.token AS account_token, a.role,
                a.allowed_tools, a.allowed_projects, a.email,
                a.display_name, a.password_hash, a.status
            FROM auth_sessions s
            JOIN mcp_accounts a ON a.username = s.username
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        )
        if not row:
            return None
        if row.get("revoked_at") is not None or int(row.get("expires_at") or 0) <= now:
            return None
        status = (row.get("status") or "active").strip().lower()
        if status != "active":
            if allow_suspended:
                return _build_account_dict(
                    row,
                    auth_method="session",
                    session_id=row.get("session_id"),
                )
            return None
        if touch_timestamps:
            db.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
                (now, row["session_id"]),
            )
            db.commit()
        return _build_account_dict(
            row,
            auth_method="session",
            session_id=row.get("session_id"),
        )

    if token.startswith("pb_pat_"):
        token_hash = _hash_token(token)
        row = db.fetchone(
            """
            SELECT
                k.key_id, k.role_scope,
                k.allowed_tools AS key_allowed_tools,
                k.allowed_projects AS key_allowed_projects,
                k.expires_at, k.revoked_at,
                a.username, a.token AS account_token, a.role AS user_role,
                a.allowed_tools AS user_allowed_tools,
                a.allowed_projects AS user_allowed_projects,
                a.email, a.display_name, a.password_hash, a.status
            FROM auth_api_keys k
            JOIN mcp_accounts a ON a.username = k.username
            WHERE k.token_hash = ?
            """,
            (token_hash,),
        )
        if not row:
            return None
        if row.get("revoked_at") is not None:
            return None
        exp = row.get("expires_at")
        if exp is not None and int(exp) <= now:
            return None
        status = (row.get("status") or "active").strip().lower()
        eff_role = _lower_role(row.get("role_scope"), row.get("user_role"))
        eff_tools = _intersect_scopes(row.get("key_allowed_tools"), row.get("user_allowed_tools"))
        eff_projects = _intersect_scopes(
            row.get("key_allowed_projects"),
            row.get("user_allowed_projects"),
        )
        if status != "active":
            if allow_suspended:
                return _build_account_dict(
                    row,
                    auth_method="api_key",
                    effective_role=eff_role,
                    effective_allowed_tools=eff_tools,
                    effective_allowed_projects=eff_projects,
                )
            return None
        if touch_timestamps:
            db.execute(
                "UPDATE auth_api_keys SET last_used_at = ? WHERE key_id = ?",
                (now, row["key_id"]),
            )
            db.commit()
        return _build_account_dict(
            row,
            auth_method="api_key",
            effective_role=eff_role,
            effective_allowed_tools=eff_tools,
            effective_allowed_projects=eff_projects,
        )

    row = db.fetchone(
        """
        SELECT
            username, token AS account_token, role,
            allowed_tools, allowed_projects, email,
            display_name, password_hash, status
        FROM mcp_accounts
        WHERE token = ?
        """,
        (token,),
    )
    if not row:
        return None
    status = (row.get("status") or "active").strip().lower()
    if status != "active" and not allow_suspended:
        return None
    return _build_account_dict(row, auth_method="account_token")


def get_authenticated_account(request: Optional[Request]) -> Optional[Dict[str, Any]]:
    """Look up authenticated active account via session, PAT API key, or primary token."""
    token = extract_bearer_token(request)
    if not token:
        return None
    try:
        return _resolve_token(token, allow_suspended=False, touch_timestamps=True)
    except Exception as exc:
        logger.debug(f"[AUTH] Token lookup error: {exc}")
        return None


def require_authenticated_user(request: Request) -> Dict[str, Any]:
    """Require any valid active authenticated user (admin, collaborator, or reader)."""
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    account = _resolve_token(token, allow_suspended=True, touch_timestamps=True)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid or expired credentials.")
    if account.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Account is suspended.")
    return account


def _check_admin_auth(request: Optional[Request]) -> bool:
    """Check if the incoming request provides a valid active admin credential."""
    account = get_authenticated_account(request)
    return bool(account and account.get("role") == "admin")


def _enforce_rest_auth_if_configured(request: Optional[Request]) -> Optional[Dict[str, Any]]:
    """Enforce admin authorization on admin routes.

    When PB_REQUIRE_REST_AUTH is enabled, requires valid admin credentials (401/403).
    Even when PB_REQUIRE_REST_AUTH is disabled, rejects authenticated non-admin callers
    with 403 while preserving anonymous backward compatibility for legacy callers/tests.
    """
    ensure_auth_schema()
    account = get_authenticated_account(request)
    if _is_rest_auth_required():
        if not account:
            raise HTTPException(
                status_code=401,
                detail="Authentication required: Valid admin token required.",
            )
        if account.get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail="Permission denied: Admin role required.",
            )
        return account

    if account and account.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Permission denied: Admin role required.",
        )
    return account


def _create_session(
    username: str,
    request: Optional[Request],
    response: Optional[Response],
    *,
    remember_me: bool = False,
) -> Tuple[str, str, int]:
    now = int(time.time())
    settings = _get_auth_settings()
    ttl_hours = int(settings["session_ttl_hours"])
    ttl_seconds = (30 * 24 * 3600) if remember_me else (ttl_hours * 3600)
    expires_at = now + ttl_seconds
    session_id = f"sess_{secrets.token_hex(12)}"
    raw_sess_token = f"pb_sess_{secrets.token_hex(16)}"
    token_hash = _hash_token(raw_sess_token)

    db.execute(
        """
        INSERT INTO auth_sessions (
            session_id, token_hash, username, ip_address, user_agent,
            created_at, expires_at, last_seen_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            session_id,
            token_hash,
            username,
            _client_ip(request),
            _user_agent(request),
            now,
            expires_at,
            now,
        ),
    )
    db.commit()

    if response is not None:
        response.set_cookie(
            key="pb_session",
            value=raw_sess_token,
            httponly=True,
            samesite="lax",
            max_age=ttl_seconds,
            path="/",
        )
    return session_id, raw_sess_token, expires_at


def _count_other_active_admins(exclude_username: str) -> int:
    row = db.fetchone(
        """
        SELECT COUNT(*) AS cnt
        FROM mcp_accounts
        WHERE role = 'admin'
          AND COALESCE(NULLIF(status, ''), 'active') = 'active'
          AND username != ?
        """,
        (exclude_username,),
    )
    return int((row or {}).get("cnt") or 0)


def _load_account_row_by_username(username: str) -> Optional[Dict[str, Any]]:
    return db.fetchone(
        """
        SELECT
            username, token AS account_token, role, allowed_tools, allowed_projects,
            email, display_name, password_hash, status, failed_login_count,
            locked_until, last_login_at, created_at, updated_at
        FROM mcp_accounts
        WHERE username = ?
        """,
        (username,),
    )


class AccountRequest(BaseModel):
    username: str
    token: Optional[str] = None
    role: str  # 'admin', 'collaborator', 'reader'
    allowed_tools: Optional[str] = "*"
    allowed_projects: Optional[str] = "*"
    email: Optional[str] = None
    display_name: Optional[str] = None
    password: Optional[str] = None
    status: Optional[str] = None


class LoginRequest(BaseModel):
    identifier: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    remember_me: bool = False


class RegisterRequest(BaseModel):
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    password: str


class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = None
    new_password: str
    revoke_other_sessions: bool = True


class CreateApiKeyRequest(BaseModel):
    name: str
    expires_in_days: Optional[int] = None
    allowed_tools: str = "*"
    allowed_projects: str = "*"
    role_scope: Optional[str] = None


class AccountStatusRequest(BaseModel):
    status: str


class ResetPasswordRequest(BaseModel):
    new_password: str


class AuthSettingsUpdateRequest(BaseModel):
    allow_registration: Optional[bool] = None
    default_signup_role: Optional[str] = None
    session_ttl_hours: Optional[int] = None
    require_rest_auth: Optional[bool] = None


# ---------------------------------------------------------------------------
# Public Authentication Endpoints
# ---------------------------------------------------------------------------


@router.get("/config")
async def get_public_auth_config():
    ensure_auth_schema()
    settings = _get_auth_settings()
    return {
        "allow_registration": settings["allow_registration"],
        "default_signup_role": settings["default_signup_role"],
        "require_rest_auth": settings["require_rest_auth"],
        "session_ttl_hours": settings["session_ttl_hours"],
        "password_login_enabled": True,
        "token_login_enabled": True,
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    ensure_auth_schema()
    now = int(time.time())
    raw_token = (req.token or "").strip()
    identifier = (req.identifier or req.username or "").strip()

    # Mode B: Token login (pb_tok_... / pb_pat_... / pb_sess_...)
    if raw_token and not (identifier and req.password):
        account = _resolve_token(raw_token, allow_suspended=True, touch_timestamps=False)
        if not account:
            _log_audit(
                "login_failed",
                "failure",
                request=request,
                details={"mode": "token", "reason": "invalid_token"},
            )
            raise HTTPException(status_code=401, detail="Invalid access token.")
        if account.get("status") == "suspended":
            _log_audit(
                "login_failed",
                "failure",
                actor_username=account["username"],
                target_username=account["username"],
                request=request,
                details={"mode": "token", "reason": "suspended"},
            )
            raise HTTPException(status_code=403, detail="Account is suspended.")

        if account["auth_method"] == "session":
            sess_row = db.fetchone(
                "SELECT session_id, expires_at FROM auth_sessions WHERE token_hash = ?",
                (_hash_token(raw_token),),
            )
            expires_at = int((sess_row or {}).get("expires_at") or (now + 3600))
            max_age = max(1, expires_at - now)
            db.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE session_id = ?",
                (now, account["session_id"]),
            )
            db.commit()
            response.set_cookie(
                key="pb_session",
                value=raw_token,
                httponly=True,
                samesite="lax",
                max_age=max_age,
                path="/",
            )
            raw_sess_token = raw_token
            out_account = account
        else:
            session_id, raw_sess_token, expires_at = _create_session(
                account["username"],
                request,
                response,
                remember_me=req.remember_me,
            )
            db.execute(
                "UPDATE mcp_accounts SET last_login_at = ?, updated_at = ? WHERE username = ?",
                (now, now, account["username"]),
            )
            db.commit()
            row = _load_account_row_by_username(account["username"])
            out_account = _build_account_dict(
                row,
                auth_method="session",
                session_id=session_id,
                effective_role=account["role"],
                effective_allowed_tools=account["allowed_tools"],
                effective_allowed_projects=account["allowed_projects"],
            )

        _log_audit(
            "login_success",
            "success",
            actor_username=out_account["username"],
            target_username=out_account["username"],
            request=request,
            details={"mode": "token", "source_auth_method": account["auth_method"]},
        )
        return {
            "status": "success",
            "session_token": raw_sess_token,
            "expires_at": expires_at,
            "account": out_account,
        }

    # Mode A: Identifier (username or email) + Password
    if not identifier or not req.password:
        raise HTTPException(
            status_code=400,
            detail="Please provide both identifier and password, or an access token.",
        )

    row = db.fetchone(
        """
        SELECT
            username, token AS account_token, role, allowed_tools, allowed_projects,
            email, display_name, password_hash, status, failed_login_count,
            locked_until, last_login_at, created_at, updated_at
        FROM mcp_accounts
        WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)
        """,
        (identifier, identifier),
    )
    if not row:
        _log_audit(
            "login_failed",
            "failure",
            actor_username=identifier,
            target_username=identifier,
            request=request,
            details={"mode": "password", "reason": "unknown_account"},
        )
        raise HTTPException(status_code=401, detail="Invalid username, email, or password.")

    username = row["username"]
    status = (row.get("status") or "active").strip().lower()
    if status == "suspended":
        _log_audit(
            "login_failed",
            "failure",
            actor_username=username,
            target_username=username,
            request=request,
            details={"mode": "password", "reason": "suspended"},
        )
        raise HTTPException(status_code=403, detail="Account is suspended.")

    locked_until = int(row.get("locked_until") or 0)
    if locked_until > now:
        retry_after = max(1, locked_until - now)
        _log_audit(
            "login_failed",
            "failure",
            actor_username=username,
            target_username=username,
            request=request,
            details={"mode": "password", "reason": "locked", "retry_after": retry_after},
        )
        raise HTTPException(
            status_code=429,
            detail=f"Account is temporarily locked. Retry in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    if not row.get("password_hash") or not _verify_password(req.password, row.get("password_hash")):
        old_count = int(row.get("failed_login_count") or 0)
        if locked_until > 0 and locked_until <= now:
            old_count = 0
        new_count = old_count + 1
        max_attempts = max(1, int(getattr(env, "auth_lockout_max_attempts", 5)))
        lock_seconds = max(1, int(getattr(env, "auth_lockout_seconds", 300)))
        new_locked_until = (now + lock_seconds) if new_count >= max_attempts else 0

        db.execute(
            "UPDATE mcp_accounts SET failed_login_count = ?, locked_until = ?, updated_at = ? WHERE username = ?",
            (new_count, new_locked_until, now, username),
        )
        db.commit()

        _log_audit(
            "login_failed",
            "failure",
            actor_username=username,
            target_username=username,
            request=request,
            details={"mode": "password", "failed_login_count": new_count},
        )
        if new_count >= max_attempts:
            _log_audit(
                "account_locked",
                "warning",
                actor_username=username,
                target_username=username,
                request=request,
                details={"failed_login_count": new_count, "locked_until": new_locked_until},
            )
        raise HTTPException(status_code=401, detail="Invalid username, email, or password.")

    # Successful password login: clear lockout counters and mint session
    db.execute(
        """
        UPDATE mcp_accounts
        SET failed_login_count = 0, locked_until = 0, last_login_at = ?, updated_at = ?
        WHERE username = ?
        """,
        (now, now, username),
    )
    db.commit()

    session_id, raw_sess_token, expires_at = _create_session(
        username,
        request,
        response,
        remember_me=req.remember_me,
    )
    account_dict = _build_account_dict(row, auth_method="session", session_id=session_id)
    _log_audit(
        "login_success",
        "success",
        actor_username=username,
        target_username=username,
        request=request,
        details={"mode": "password", "remember_me": req.remember_me},
    )
    return {
        "status": "success",
        "session_token": raw_sess_token,
        "expires_at": expires_at,
        "account": account_dict,
    }


@router.post("/register")
async def register(req: RegisterRequest, request: Request, response: Response):
    ensure_auth_schema()
    settings = _get_auth_settings()
    if not settings["allow_registration"]:
        raise HTTPException(status_code=403, detail="Self-registration is currently disabled.")

    username = (req.username or "").strip()
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-48 characters using letters, numbers, '_', '.', or '-'.",
        )
    _validate_password(req.password)
    email = _normalize_email(req.email)
    display_name = (req.display_name or "").strip() or None

    existing_user = db.fetchone(
        "SELECT username FROM mcp_accounts WHERE LOWER(username) = LOWER(?)",
        (username,),
    )
    if existing_user:
        raise HTTPException(status_code=409, detail=f"Username '{username}' is already taken.")
    _ensure_email_unique(email)

    signup_role = settings["default_signup_role"]
    if signup_role not in ("collaborator", "reader"):
        signup_role = "collaborator"

    now = int(time.time())
    primary_token = f"pb_tok_{secrets.token_hex(12)}"
    pw_hash = _hash_password(req.password)

    db.execute(
        """
        INSERT INTO mcp_accounts (
            username, token, role, allowed_tools, allowed_projects,
            email, display_name, password_hash, status,
            failed_login_count, locked_until, last_login_at, created_at, updated_at
        ) VALUES (?, ?, ?, '*', '*', ?, ?, ?, 'active', 0, 0, ?, ?, ?)
        """,
        (username, primary_token, signup_role, email, display_name, pw_hash, now, now, now),
    )
    db.commit()

    session_id, raw_sess_token, expires_at = _create_session(username, request, response)
    row = _load_account_row_by_username(username)
    account_dict = _build_account_dict(row, auth_method="session", session_id=session_id)

    _log_audit(
        "register",
        "success",
        actor_username=username,
        target_username=username,
        request=request,
        details={"role": signup_role},
    )
    return {
        "status": "success",
        "session_token": raw_sess_token,
        "expires_at": expires_at,
        "account": account_dict,
    }


@router.post("/logout")
async def logout(request: Request, response: Response):
    ensure_auth_schema()
    now = int(time.time())
    actor_username: Optional[str] = None
    tokens_to_revoke = set()

    bearer_or_query = extract_bearer_token(request)
    if bearer_or_query and bearer_or_query.startswith("pb_sess_"):
        tokens_to_revoke.add(bearer_or_query)
    cookie_tok = request.cookies.get("pb_session")
    if cookie_tok and cookie_tok.strip().startswith("pb_sess_"):
        tokens_to_revoke.add(cookie_tok.strip())

    for raw_sess in tokens_to_revoke:
        thash = _hash_token(raw_sess)
        row = db.fetchone(
            "SELECT session_id, username FROM auth_sessions WHERE token_hash = ?",
            (thash,),
        )
        if row:
            actor_username = actor_username or row.get("username")
            db.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (now, row["session_id"]),
            )
    if tokens_to_revoke:
        db.commit()

    response.delete_cookie("pb_session", path="/")
    _log_audit(
        "logout",
        "success",
        actor_username=actor_username,
        target_username=actor_username,
        request=request,
    )
    return {"status": "success"}


@router.get("/verify")
async def verify_token(request: Request):
    token = extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required. Please provide a token.")

    try:
        account = _resolve_token(token, allow_suspended=True, touch_timestamps=True)
        if not account:
            raise HTTPException(status_code=401, detail="Invalid token. Access denied.")
        if account.get("status") == "suspended":
            raise HTTPException(status_code=403, detail="Account is suspended.")
        return {"status": "success", "account": account}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Authentication check failed: {str(exc)}")


# ---------------------------------------------------------------------------
# Self-Service Endpoints (All Authenticated Roles: admin, collaborator, reader)
# ---------------------------------------------------------------------------


@router.get("/me")
async def get_me(request: Request):
    account = require_authenticated_user(request)
    return {"status": "success", "account": account}


@router.put("/me")
async def update_me(req: ProfileUpdateRequest, request: Request):
    account = require_authenticated_user(request)
    username = account["username"]
    row = _load_account_row_by_username(username)
    if not row:
        raise HTTPException(status_code=404, detail="Account not found.")

    fields_set = getattr(req, "model_fields_set", set())
    new_email = row.get("email")
    if "email" in fields_set or req.email is not None:
        new_email = _normalize_email(req.email)
        _ensure_email_unique(new_email, exclude_username=username)

    new_display_name = row.get("display_name")
    if "display_name" in fields_set or req.display_name is not None:
        new_display_name = (req.display_name or "").strip() or None

    now = int(time.time())
    db.execute(
        "UPDATE mcp_accounts SET display_name = ?, email = ?, updated_at = ? WHERE username = ?",
        (new_display_name, new_email, now, username),
    )
    db.commit()

    updated_row = _load_account_row_by_username(username)
    updated_account = _build_account_dict(
        updated_row,
        auth_method=account["auth_method"],
        session_id=account.get("session_id"),
        effective_role=account["role"],
        effective_allowed_tools=account["allowed_tools"],
        effective_allowed_projects=account["allowed_projects"],
    )
    _log_audit(
        "profile_updated",
        "success",
        actor_username=username,
        target_username=username,
        request=request,
        details={"email_updated": new_email is not None, "display_name_updated": new_display_name is not None},
    )
    return {"status": "success", "account": updated_account}


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, request: Request):
    account = require_authenticated_user(request)
    username = account["username"]
    _validate_password(req.new_password)

    row = _load_account_row_by_username(username)
    if not row:
        raise HTTPException(status_code=404, detail="Account not found.")

    existing_hash = row.get("password_hash")
    if existing_hash:
        if not req.current_password or not _verify_password(req.current_password, existing_hash):
            _log_audit(
                "password_change_failed",
                "failure",
                actor_username=username,
                target_username=username,
                request=request,
                details={"reason": "invalid_current_password"},
            )
            raise HTTPException(status_code=401, detail="Current password is incorrect.")

    now = int(time.time())
    new_hash = _hash_password(req.new_password)
    db.execute(
        "UPDATE mcp_accounts SET password_hash = ?, updated_at = ? WHERE username = ?",
        (new_hash, now, username),
    )

    if req.revoke_other_sessions:
        current_sid = account.get("session_id")
        if current_sid:
            db.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL AND session_id != ?",
                (now, username, current_sid),
            )
        else:
            db.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
                (now, username),
            )

    db.commit()
    _log_audit(
        "password_changed",
        "success",
        actor_username=username,
        target_username=username,
        request=request,
        details={"revoked_other_sessions": bool(req.revoke_other_sessions)},
    )
    return {"status": "success", "has_password": True}


@router.get("/sessions")
async def list_my_sessions(request: Request):
    account = require_authenticated_user(request)
    now = int(time.time())
    rows = db.fetchall(
        """
        SELECT session_id, username, ip_address, user_agent, created_at, expires_at, last_seen_at
        FROM auth_sessions
        WHERE username = ? AND revoked_at IS NULL AND expires_at > ?
        ORDER BY last_seen_at DESC
        """,
        (account["username"], now),
    )
    current_sid = account.get("session_id")
    sessions = [
        {
            **r,
            "is_current": bool(current_sid and r.get("session_id") == current_sid),
        }
        for r in rows
    ]
    return {"status": "success", "sessions": sessions}


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(request: Request):
    account = require_authenticated_user(request)
    now = int(time.time())
    current_sid = account.get("session_id")
    if current_sid:
        revoked_count = _execute_rowcount(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE username = ? AND revoked_at IS NULL AND expires_at > ? AND session_id != ?
            """,
            (now, account["username"], now, current_sid),
        )
    else:
        revoked_count = _execute_rowcount(
            """
            UPDATE auth_sessions
            SET revoked_at = ?
            WHERE username = ? AND revoked_at IS NULL AND expires_at > ?
            """,
            (now, account["username"], now),
        )
    db.commit()
    _log_audit(
        "session_revoked",
        "success",
        actor_username=account["username"],
        target_username=account["username"],
        request=request,
        details={"scope": "others", "revoked_count": revoked_count},
    )
    return {"status": "success", "revoked_count": revoked_count}


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, request: Request):
    account = require_authenticated_user(request)
    sess = db.fetchone(
        "SELECT session_id, username, revoked_at FROM auth_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess["username"] != account["username"] and account["role"] != "admin":
        raise HTTPException(status_code=403, detail="Permission denied: Cannot revoke another user's session.")

    now = int(time.time())
    db.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
        (now, session_id),
    )
    db.commit()
    _log_audit(
        "session_revoked",
        "success",
        actor_username=account["username"],
        target_username=sess["username"],
        request=request,
        details={"session_id": session_id},
    )
    return {"status": "success", "session_id": session_id}


@router.get("/api-keys")
async def list_api_keys(request: Request, all_users: bool = Query(default=False)):
    account = require_authenticated_user(request)
    if all_users:
        if account["role"] != "admin":
            raise HTTPException(status_code=403, detail="Permission denied: Admin role required to list all users' API keys.")
        rows = db.fetchall(
            """
            SELECT key_id, key_prefix, name, username, role_scope, allowed_tools,
                   allowed_projects, expires_at, last_used_at, created_at, revoked_at
            FROM auth_api_keys
            ORDER BY created_at DESC
            """
        )
    else:
        rows = db.fetchall(
            """
            SELECT key_id, key_prefix, name, username, role_scope, allowed_tools,
                   allowed_projects, expires_at, last_used_at, created_at, revoked_at
            FROM auth_api_keys
            WHERE username = ?
            ORDER BY created_at DESC
            """,
            (account["username"],),
        )
    return {"status": "success", "api_keys": rows}


@router.post("/api-keys")
async def create_api_key(req: CreateApiKeyRequest, request: Request):
    account = require_authenticated_user(request)
    key_name = (req.name or "").strip()
    if not key_name:
        raise HTTPException(status_code=400, detail="API key name is required.")

    req_role = (req.role_scope or account["role"]).strip().lower()
    if req_role not in _ROLE_RANK:
        raise HTTPException(status_code=400, detail="Invalid role_scope. Must be 'admin', 'collaborator', or 'reader'.")
    if _ROLE_RANK[req_role] > _ROLE_RANK.get(account["role"], 1):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: API key role_scope cannot exceed your own account role.",
        )

    now = int(time.time())
    expires_at: Optional[int] = None
    if req.expires_in_days is not None:
        if req.expires_in_days < 1:
            raise HTTPException(status_code=400, detail="expires_in_days must be >= 1.")
        expires_at = now + (req.expires_in_days * 86400)

    eff_tools = _intersect_scopes(req.allowed_tools, account.get("allowed_tools"))
    eff_projects = _intersect_scopes(req.allowed_projects, account.get("allowed_projects"))

    raw_key = f"pb_pat_{secrets.token_hex(16)}"
    key_prefix = raw_key[:11] + "..."
    token_hash = _hash_token(raw_key)
    key_id = f"key_{secrets.token_hex(8)}"

    db.execute(
        """
        INSERT INTO auth_api_keys (
            key_id, key_prefix, token_hash, name, username, role_scope,
            allowed_tools, allowed_projects, expires_at, last_used_at, created_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
        """,
        (
            key_id,
            key_prefix,
            token_hash,
            key_name,
            account["username"],
            req_role,
            eff_tools,
            eff_projects,
            expires_at,
            now,
        ),
    )
    db.commit()

    key_meta = {
        "key_id": key_id,
        "key_prefix": key_prefix,
        "name": key_name,
        "username": account["username"],
        "role_scope": req_role,
        "allowed_tools": eff_tools,
        "allowed_projects": eff_projects,
        "expires_at": expires_at,
        "last_used_at": None,
        "created_at": now,
        "revoked_at": None,
    }
    _log_audit(
        "api_key_created",
        "success",
        actor_username=account["username"],
        target_username=account["username"],
        request=request,
        details={"key_id": key_id, "role_scope": req_role, "name": key_name},
    )
    return {"status": "success", "api_key": raw_key, "key": key_meta}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request):
    account = require_authenticated_user(request)
    row = db.fetchone(
        "SELECT key_id, username, revoked_at FROM auth_api_keys WHERE key_id = ?",
        (key_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found.")
    if row["username"] != account["username"] and account["role"] != "admin":
        raise HTTPException(status_code=403, detail="Permission denied: Cannot revoke another user's API key.")

    now = int(time.time())
    db.execute(
        "UPDATE auth_api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
        (now, key_id),
    )
    db.commit()
    _log_audit(
        "api_key_revoked",
        "success",
        actor_username=account["username"],
        target_username=row["username"],
        request=request,
        details={"key_id": key_id},
    )
    return {"status": "success", "key_id": key_id}


# ---------------------------------------------------------------------------
# Admin Identity & Governance Endpoints
# ---------------------------------------------------------------------------


@router.get("/accounts")
async def list_accounts(request: Request = None):
    _enforce_rest_auth_if_configured(request)
    is_admin = _check_admin_auth(request)
    try:
        rows = db.fetchall(
            """
            SELECT
                username, token, role, allowed_tools, allowed_projects,
                email, display_name, password_hash, status,
                failed_login_count, locked_until, last_login_at, created_at, updated_at
            FROM mcp_accounts
            ORDER BY username ASC
            """
        )
        accounts = []
        for r in rows:
            raw_token = r.get("token") or ""
            accounts.append(
                {
                    "username": r.get("username"),
                    "token": raw_token if is_admin else _mask_token(raw_token),
                    "role": r.get("role"),
                    "allowed_tools": r.get("allowed_tools") or "*",
                    "allowed_projects": r.get("allowed_projects") or "*",
                    "email": r.get("email"),
                    "display_name": r.get("display_name"),
                    "status": (r.get("status") or "active").strip().lower(),
                    "has_password": bool(r.get("password_hash")),
                    "failed_login_count": int(r.get("failed_login_count") or 0),
                    "locked_until": int(r.get("locked_until") or 0),
                    "last_login_at": r.get("last_login_at"),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at"),
                }
            )
        return {"accounts": accounts}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load accounts: {str(exc)}")


@router.post("/accounts")
async def create_or_update_account(req: AccountRequest, request: Request = None):
    actor = _enforce_rest_auth_if_configured(request)
    ts = int(time.time())

    username = (req.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    role = req.role.lower().strip()
    if role not in _ROLE_RANK:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'admin', 'collaborator', or 'reader'")

    fields_set = getattr(req, "model_fields_set", set())
    existing = _load_account_row_by_username(username)

    req_status: Optional[str] = None
    if req.status is not None:
        req_status = req.status.strip().lower()
        if req_status not in ("active", "suspended"):
            raise HTTPException(status_code=400, detail="Invalid status. Must be 'active' or 'suspended'.")

    new_pw_hash: Optional[str] = None
    if req.password is not None and req.password != "":
        _validate_password(req.password)
        new_pw_hash = _hash_password(req.password)

    req_email: Optional[str] = None
    has_email_update = "email" in fields_set or req.email is not None
    if has_email_update:
        req_email = _normalize_email(req.email)
        _ensure_email_unique(req_email, exclude_username=username if existing else None)

    try:
        if existing:
            token = (req.token or "").strip() or existing["account_token"]
            password_hash = new_pw_hash if new_pw_hash is not None else existing.get("password_hash")
            email = req_email if has_email_update else existing.get("email")
            if "display_name" in fields_set or req.display_name is not None:
                display_name = (req.display_name or "").strip() or None
            else:
                display_name = existing.get("display_name")

            status = req_status if req_status is not None else ((existing.get("status") or "active").strip().lower())
            if (
                existing.get("role") == "admin"
                and (existing.get("status") or "active").strip().lower() == "active"
                and (role != "admin" or status == "suspended")
                and _count_other_active_admins(username) == 0
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote or suspend the last active admin account.",
                )

            allowed_tools = (
                (req.allowed_tools or "*")
                if ("allowed_tools" in fields_set or req.allowed_tools != "*")
                else (existing.get("allowed_tools") or "*")
            )
            allowed_projects = (
                (req.allowed_projects or "*")
                if ("allowed_projects" in fields_set or req.allowed_projects != "*")
                else (existing.get("allowed_projects") or "*")
            )

            db.execute(
                """
                UPDATE mcp_accounts
                SET token = ?, role = ?, allowed_tools = ?, allowed_projects = ?,
                    email = ?, display_name = ?, password_hash = ?, status = ?, updated_at = ?
                WHERE username = ?
                """,
                (
                    token,
                    role,
                    allowed_tools,
                    allowed_projects,
                    email,
                    display_name,
                    password_hash,
                    status,
                    ts,
                    username,
                ),
            )
            if status == "suspended":
                db.execute(
                    "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
                    (ts, username),
                )
        else:
            token = (req.token or "").strip() or f"pb_tok_{secrets.token_hex(12)}"
            status = req_status or "active"
            allowed_tools = (req.allowed_tools or "*").strip() or "*"
            allowed_projects = (req.allowed_projects or "*").strip() or "*"
            display_name = (req.display_name or "").strip() or None

            db.execute(
                """
                INSERT INTO mcp_accounts (
                    username, token, role, allowed_tools, allowed_projects,
                    email, display_name, password_hash, status,
                    failed_login_count, locked_until, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    username,
                    token,
                    role,
                    allowed_tools,
                    allowed_projects,
                    req_email,
                    display_name,
                    new_pw_hash,
                    status,
                    ts,
                    ts,
                ),
            )
        db.commit()

        _log_audit(
            "account_upserted",
            "success",
            actor_username=(actor or {}).get("username"),
            target_username=username,
            request=request,
            details={"role": role, "status": status, "is_update": bool(existing)},
        )
        return {"status": "success", "username": username, "token": token}
    except HTTPException:
        raise
    except Exception as exc:
        if db.conn:
            try:
                db.conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to save account: {str(exc)}")


@router.patch("/accounts/{username}/status")
async def update_account_status(username: str, req: AccountStatusRequest, request: Request = None):
    actor = _enforce_rest_auth_if_configured(request)
    new_status = (req.status or "").strip().lower()
    if new_status not in ("active", "suspended"):
        raise HTTPException(status_code=400, detail="Invalid status. Must be 'active' or 'suspended'.")

    existing = _load_account_row_by_username(username)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Account '{username}' not found.")

    now = int(time.time())
    if new_status == "suspended":
        if (
            existing.get("role") == "admin"
            and (existing.get("status") or "active").strip().lower() == "active"
            and _count_other_active_admins(username) == 0
        ):
            raise HTTPException(status_code=400, detail="Cannot suspend the last active admin account.")

        db.execute(
            "UPDATE mcp_accounts SET status = 'suspended', updated_at = ? WHERE username = ?",
            (now, username),
        )
        db.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
            (now, username),
        )
    else:
        db.execute(
            """
            UPDATE mcp_accounts
            SET status = 'active', failed_login_count = 0, locked_until = 0, updated_at = ?
            WHERE username = ?
            """,
            (now, username),
        )
    db.commit()

    _log_audit(
        "account_status_changed",
        "success",
        actor_username=(actor or {}).get("username"),
        target_username=username,
        request=request,
        details={"status": new_status},
    )
    return {"status": "success", "username": username, "account_status": new_status}


@router.post("/accounts/{username}/rotate-token")
async def rotate_account_token(username: str, request: Request = None):
    actor = _enforce_rest_auth_if_configured(request)
    existing = _load_account_row_by_username(username)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Account '{username}' not found.")

    now = int(time.time())
    new_tok = f"pb_tok_{secrets.token_hex(12)}"
    db.execute(
        "UPDATE mcp_accounts SET token = ?, updated_at = ? WHERE username = ?",
        (new_tok, now, username),
    )
    db.commit()

    _log_audit(
        "account_token_rotated",
        "success",
        actor_username=(actor or {}).get("username"),
        target_username=username,
        request=request,
    )
    return {"status": "success", "username": username, "token": new_tok}


@router.post("/accounts/{username}/reset-password")
async def reset_account_password(username: str, req: ResetPasswordRequest, request: Request = None):
    actor = _enforce_rest_auth_if_configured(request)
    existing = _load_account_row_by_username(username)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Account '{username}' not found.")

    _validate_password(req.new_password)
    pw_hash = _hash_password(req.new_password)
    now = int(time.time())

    db.execute(
        """
        UPDATE mcp_accounts
        SET password_hash = ?, failed_login_count = 0, locked_until = 0, updated_at = ?
        WHERE username = ?
        """,
        (pw_hash, now, username),
    )
    db.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
        (now, username),
    )
    db.commit()

    _log_audit(
        "account_password_reset",
        "success",
        actor_username=(actor or {}).get("username"),
        target_username=username,
        request=request,
    )
    return {"status": "success", "username": username}


@router.delete("/accounts/{username}")
async def delete_account(username: str, request: Request = None):
    actor = _enforce_rest_auth_if_configured(request)
    try:
        if username == "admin" and _count_other_active_admins(username) == 0:
            raise HTTPException(status_code=400, detail="Cannot delete the last active admin account.")

        now = int(time.time())
        db.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
            (now, username),
        )
        db.execute(
            "UPDATE auth_api_keys SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
            (now, username),
        )
        db.execute("DELETE FROM mcp_accounts WHERE username = ?", (username,))
        db.commit()

        _log_audit(
            "account_deleted",
            "success",
            actor_username=(actor or {}).get("username"),
            target_username=username,
            request=request,
        )
        return {"status": "success", "message": f"Account '{username}' deleted."}
    except HTTPException:
        raise
    except Exception as exc:
        if db.conn:
            try:
                db.conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to delete account: {str(exc)}")


@router.get("/audit-logs")
async def get_audit_logs(
    request: Request = None,
    limit: int = Query(default=100),
    event_type: Optional[str] = Query(default=None),
    username: Optional[str] = Query(default=None),
):
    _enforce_rest_auth_if_configured(request)
    clauses: List[str] = []
    params: List[Any] = []

    if event_type and event_type.strip():
        clauses.append("event_type = ?")
        params.append(event_type.strip())
    if username and username.strip():
        clauses.append("(actor_username = ? OR target_username = ?)")
        uname = username.strip()
        params.extend([uname, uname])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, min(int(limit), 500))
    params.append(safe_limit)

    rows = db.fetchall(
        f"""
        SELECT id, event_type, actor_username, target_username,
               ip_address, user_agent, status, details, created_at
        FROM auth_audit_logs
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return {"status": "success", "logs": rows}


@router.get("/settings")
async def get_auth_settings_endpoint(request: Request = None):
    _enforce_rest_auth_if_configured(request)
    settings = _get_auth_settings()
    return {"status": "success", "settings": settings, **settings}


@router.put("/settings")
async def update_auth_settings_endpoint(req: AuthSettingsUpdateRequest, request: Request = None):
    actor = _enforce_rest_auth_if_configured(request)
    now = int(time.time())
    updates: Dict[str, str] = {}

    if req.allow_registration is not None:
        val_str = "true" if req.allow_registration else "false"
        updates["allow_registration"] = val_str
        os.environ["PB_ALLOW_REGISTRATION"] = val_str
        env.allow_registration = bool(req.allow_registration)

    if req.default_signup_role is not None:
        role_val = req.default_signup_role.strip().lower()
        if role_val not in ("collaborator", "reader"):
            raise HTTPException(
                status_code=400,
                detail="Invalid default_signup_role. Must be 'collaborator' or 'reader'.",
            )
        updates["default_signup_role"] = role_val
        os.environ["PB_DEFAULT_SIGNUP_ROLE"] = role_val
        env.default_signup_role = role_val

    if req.session_ttl_hours is not None:
        if req.session_ttl_hours < 1:
            raise HTTPException(status_code=400, detail="session_ttl_hours must be >= 1.")
        ttl_val = int(req.session_ttl_hours)
        updates["session_ttl_hours"] = str(ttl_val)
        os.environ["PB_SESSION_TTL_HOURS"] = str(ttl_val)
        env.session_ttl_hours = ttl_val

    if req.require_rest_auth is not None:
        req_str = "true" if req.require_rest_auth else "false"
        updates["require_rest_auth"] = req_str
        os.environ["PB_REQUIRE_REST_AUTH"] = req_str
        env.require_rest_auth = bool(req.require_rest_auth)

    for key, val in updates.items():
        db.execute(
            """
            INSERT INTO auth_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (setting_key) DO UPDATE SET
                setting_value = EXCLUDED.setting_value,
                updated_at = EXCLUDED.updated_at
            """,
            (key, val, now),
        )
    if updates:
        db.commit()

    settings = _get_auth_settings()
    _log_audit(
        "auth_settings_updated",
        "success",
        actor_username=(actor or {}).get("username"),
        request=request,
        details=updates,
    )
    return {"status": "success", "settings": settings, **settings}


@router.get("/tools")
async def list_available_tools(request: Request = None):
    _enforce_rest_auth_if_configured(request)
    try:
        from ...ai.mcp import mcp_server
        tools = mcp_server._tool_manager.list_tools()
        return {"tools": [t.name for t in tools]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tools: {str(exc)}")
