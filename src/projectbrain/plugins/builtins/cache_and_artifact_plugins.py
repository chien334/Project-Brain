"""Layer 1 Built-in Plugins: Verified UDCP Script Cache & Synchronized Telemetry Artifact Stores.

Implements:
1. ``SqlitePgScriptCachePlugin`` (``cache.sqlite_pg``, aliased ``VerifiedUDCPScriptCachePlugin``)
2. ``RedisScriptCachePlugin`` (``cache.redis``)
3. ``LocalSqliteArtifactStorePlugin`` (``artifacts.local_sqlite``, aliased ``TelemetryArtifactStorePlugin``)
4. ``MinioClickHouseArtifactStorePlugin`` (``artifacts.minio_clickhouse``)

All SQL statements use ANSI ``INSERT INTO ... VALUES (...) ON CONFLICT (...) DO UPDATE SET ...``
for full SQLite (>= 3.24) and PostgreSQL compatibility.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import httpx

# ---------------------------------------------------------------------------
# Concurrency-Safe Defensive Import Pattern (Section 3.2)
# ---------------------------------------------------------------------------
try:
    from projectbrain.plugins.base import (
        ArtifactStorePlugin,
        BasePlugin,
        BasePluginModel,
        PluginCategory,
        PluginContext,
        PluginMetadata,
        ScriptCachePlugin,
        TelemetryArtifactRecord,
        UDCPScriptCacheEntry,
    )
except ImportError:  # pragma: no cover - defensive fallback for concurrent Stage 3 build
    from abc import ABC, abstractmethod
    from enum import Enum
    from pydantic import BaseModel, Field

    try:
        from pydantic import ConfigDict
        _HAS_CONFIG_DICT = True
    except ImportError:
        ConfigDict = None  # type: ignore[assignment]
        _HAS_CONFIG_DICT = False

    class PluginCategory(str, Enum):  # type: ignore[no-redef]
        GRAPH_BACKEND = "graph_backend"
        VECTOR_BACKEND = "vector_backend"
        ETL_PARSER = "etl_parser"
        KNOWLEDGE_LINKER = "knowledge_linker"
        DOC_EXPORTER = "doc_exporter"
        SCRIPT_CACHE = "script_cache"
        ARTIFACT_STORE = "artifact_store"
        BRAIN_CONSUMER = "brain_consumer"
        WEBHOOK = "webhook"
        SOURCE_CONNECTOR = "source_connector"

    class BasePluginModel(BaseModel):  # type: ignore[no-redef]
        if _HAS_CONFIG_DICT:
            model_config = ConfigDict(extra="allow")
        else:
            class Config:
                extra = "allow"

        def to_dict(self) -> Dict[str, Any]:
            if hasattr(self, "model_dump"):
                return self.model_dump()
            return self.dict()

    class PluginMetadata(BasePluginModel):  # type: ignore[no-redef]
        plugin_id: str
        name: str
        version: str = "1.0.0"
        category: str
        description: str = ""
        author: str = "projectbrain"
        priority: int = 100
        capabilities: List[str] = Field(default_factory=list)
        requires_env: List[str] = Field(default_factory=list)
        config_schema: Dict[str, Any] = Field(default_factory=dict)
        default_enabled: bool = True
        entrypoint: Optional[str] = None

    class PluginContext(BasePluginModel):  # type: ignore[no-redef]
        project_id: str = "default"
        branch: str = "main"
        version_tag: Optional[str] = None
        workspace_path: str = "."
        config: Dict[str, Any] = Field(default_factory=dict)
        registry: Optional[Any] = None

    class UDCPScriptCacheEntry(BasePluginModel):  # type: ignore[no-redef]
        cache_id: str
        project_id: str
        intent_id: str
        platform: str
        build_version: str
        screen_hash: str = ""
        ui_fingerprint: Dict[str, Any] = Field(default_factory=dict)
        udcp_commands: List[Dict[str, Any]] = Field(default_factory=list)
        hit_count: int = 0
        fallback_count: int = 0
        fallback_rate: float = 0.0
        invalidated: bool = False
        invalidation_reason: Optional[str] = None
        self_heal_history: List[Dict[str, Any]] = Field(default_factory=list)
        ttl_seconds: int = 2592000

    class TelemetryArtifactRecord(BasePluginModel):  # type: ignore[no-redef]
        artifact_id: str
        run_id: str
        step_id: str
        project_id: str
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

    class BasePlugin(ABC):  # type: ignore[no-redef]
        metadata: PluginMetadata

        def __init__(self, config: Optional[Dict[str, Any]] = None):
            self.config: Dict[str, Any] = dict(config or {})
            self._initialized: bool = False

        def on_load(self, config: Optional[Dict[str, Any]] = None) -> None:
            if config:
                self.config.update(config)

        def initialize(self, context: Optional[PluginContext] = None) -> None:
            if context and context.config:
                self.config.update(context.config)
            self._initialized = True

        def shutdown(self) -> None:
            self._initialized = False

        def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
            return dict(config)

        def health_check(self) -> Dict[str, Any]:
            return {
                "plugin_id": self.metadata.plugin_id,
                "category": self.metadata.category,
                "status": "healthy",
                "initialized": self._initialized,
            }

    class ScriptCachePlugin(BasePlugin):  # type: ignore[no-redef]
        @abstractmethod
        def lookup_script(
            self,
            project_id: str,
            intent_id: str,
            platform: str,
            build_version: str,
            screen_hash: Optional[str] = None,
            include_invalidated: bool = False,
        ) -> Optional[Dict[str, Any]]:
            pass

        @abstractmethod
        def store_script(self, entry: Dict[str, Any]) -> Dict[str, Any]:
            pass

        @abstractmethod
        def record_execution(
            self,
            cache_id: str,
            success: bool,
            fallback_reason: Optional[str] = None,
            self_heal_patch: Optional[Dict[str, Any]] = None,
            project_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            pass

        @abstractmethod
        def invalidate_script(
            self, cache_id: str, reason: str, project_id: Optional[str] = None
        ) -> bool:
            pass

        @abstractmethod
        def list_scripts(
            self,
            project_id: str,
            platform: Optional[str] = None,
            build_version: Optional[str] = None,
            include_invalidated: bool = True,
        ) -> List[Dict[str, Any]]:
            pass

    class ArtifactStorePlugin(BasePlugin):  # type: ignore[no-redef]
        @abstractmethod
        def index_artifact(self, record: Dict[str, Any]) -> Dict[str, Any]:
            pass

        @abstractmethod
        def query_artifacts(
            self,
            run_id: Optional[str] = None,
            step_id: Optional[str] = None,
            artifact_type: Optional[str] = None,
            build_version: Optional[str] = None,
            limit: int = 100,
            project_id: Optional[str] = None,
        ) -> List[Dict[str, Any]]:
            pass

        @abstractmethod
        def query_evidence_window(
            self,
            run_id: str,
            center_ts_ms: int,
            window_ms: int = 5000,
            project_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            pass

        @abstractmethod
        def purge_expired(
            self,
            now_ms: Optional[int] = None,
            project_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            pass


# ---------------------------------------------------------------------------
# Constants & Shared Helpers
# ---------------------------------------------------------------------------
DEFAULT_CACHE_TTL_SECONDS = 2592000  # 30 days
DEFAULT_FALLBACK_THRESHOLD = 0.25
DEFAULT_MIN_SAMPLES = 4
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 90
MS_PER_DAY = 86400 * 1000

SUPPORTED_ARTIFACT_TYPES: Tuple[str, ...] = (
    "video_h264",
    "har",
    "pcap",
    "crashpad_dmp",
    "gdb_hardfault",
    "uart_log",
    "power_trace_ma",
    "screenshot_png",
)


def _now_ms(clock: Optional[Callable[[], int]] = None) -> int:
    if clock is not None:
        return int(clock())
    return int(time.time() * 1000)


def _to_dict(obj: Union[Dict[str, Any], BasePluginModel]) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "to_dict"):
        return dict(obj.to_dict())
    if hasattr(obj, "model_dump"):
        return dict(obj.model_dump())
    return dict(obj.dict())  # type: ignore[union-attr]


def _json_loads_safe(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _compute_cache_id(
    project_id: str,
    intent_id: str,
    platform: str,
    build_version: str,
    screen_hash: str = "",
) -> str:
    raw = f"{project_id}|{intent_id}|{platform}|{build_version}|{screen_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _compute_artifact_id(
    run_id: str,
    step_id: str,
    artifact_type: str,
    ntp_timestamp_ms: int,
    uri: str,
    project_id: str = "default",
) -> str:
    raw = f"{project_id}|{run_id}|{step_id}|{artifact_type}|{ntp_timestamp_ms}|{uri}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


_CH_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
_REDIS_GLOB_RE = re.compile(r"([*?\[\]\\])")


def _validate_ch_identifier(value: str, field_name: str) -> str:
    ident = str(value or "").strip()
    if not _CH_IDENTIFIER_RE.match(ident):
        raise ValueError(
            f"Invalid ClickHouse identifier for {field_name}: {value!r} "
            "(must match ^[a-zA-Z_][a-zA-Z0-9_]{0,62}$)"
        )
    return ident


def _sanitize_s3_segment(segment: str, default: str = "item") -> str:
    clean = str(segment or "").strip().replace("\\", "/")
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", clean)
    while ".." in clean:
        clean = clean.replace("..", "_")
    clean = clean.strip(".")
    return clean or default


def _escape_redis_glob(value: str) -> str:
    return _REDIS_GLOB_RE.sub(r"\\\1", str(value))


def _fingerprint_similarity(expected: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    """Compute similarity ratio [0.0, 1.0] between two UI fingerprint dicts."""
    if not expected:
        return 1.0
    if not candidate:
        return 0.0
    keys = set(expected.keys()) | set(candidate.keys())
    if not keys:
        return 1.0
    matches = sum(1 for k in expected if k in candidate and candidate[k] == expected[k])
    return matches / len(expected)


def _clamp_retention_days(days: Optional[int], default_days: int = MIN_RETENTION_DAYS) -> int:
    val = int(default_days if days is None else days)
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, val))


def _apply_execution_update(
    entry: Dict[str, Any],
    success: bool,
    fallback_reason: Optional[str],
    self_heal_patch: Optional[Dict[str, Any]],
    fallback_threshold: float,
    min_samples: int,
    now_ms: int,
) -> Dict[str, Any]:
    """Shared state transition for hit/fallback counters, auto-invalidation, and self-healing."""
    updated = dict(entry)
    hit_count = int(updated.get("hit_count", 0))
    fallback_count = int(updated.get("fallback_count", 0))

    if success:
        hit_count += 1
    else:
        fallback_count += 1

    total = hit_count + fallback_count
    fallback_rate = fallback_count / max(1, total)

    invalidated = bool(updated.get("invalidated", False))
    invalidation_reason = updated.get("invalidation_reason")

    if not success and total >= min_samples and fallback_rate > fallback_threshold:
        invalidated = True
        invalidation_reason = (
            f"fallback_rate {fallback_rate:.2f} > {fallback_threshold:.2f} "
            f"({fallback_reason or 'threshold'})"
        )

    history = list(updated.get("self_heal_history") or [])
    if self_heal_patch is not None:
        patch_dict = dict(self_heal_patch)
        history.append(
            {
                "timestamp_ms": now_ms,
                "reason": fallback_reason,
                "patch": patch_dict,
                "verified": bool(patch_dict.get("verified", False)),
            }
        )
        if "udcp_commands" in patch_dict:
            updated["udcp_commands"] = list(patch_dict["udcp_commands"])
        if "screen_hash" in patch_dict:
            updated["screen_hash"] = str(patch_dict["screen_hash"])
        if "ui_fingerprint" in patch_dict:
            updated["ui_fingerprint"] = dict(patch_dict["ui_fingerprint"])
        if patch_dict.get("verified") is True:
            invalidated = False
            invalidation_reason = None

    updated["hit_count"] = hit_count
    updated["fallback_count"] = fallback_count
    updated["fallback_rate"] = fallback_rate
    updated["invalidated"] = invalidated
    updated["invalidation_reason"] = invalidation_reason
    updated["self_heal_history"] = history
    updated["updated_at"] = now_ms
    updated["found"] = True
    return updated


def _build_evidence_window(
    run_id: str,
    center_ts_ms: int,
    window_ms: int,
    matched_items: List[Dict[str, Any]],
    video_start_ms: Optional[int],
) -> Dict[str, Any]:
    """Build synchronized multimodal evidence window with video_offset_ms alignment."""
    timeline: List[Dict[str, Any]] = []
    streams_by_type: Dict[str, List[Dict[str, Any]]] = {}

    sorted_items = sorted(
        matched_items,
        key=lambda item: (int(item["ntp_timestamp_ms"]), str(item.get("artifact_id", ""))),
    )

    for raw in sorted_items:
        item = dict(raw)
        ts_ms = int(item["ntp_timestamp_ms"])
        item["delta_from_center_ms"] = ts_ms - center_ts_ms
        if video_start_ms is not None:
            item["video_offset_ms"] = max(0, ts_ms - video_start_ms)
        else:
            item["video_offset_ms"] = None
        timeline.append(item)
        streams_by_type.setdefault(item["artifact_type"], []).append(item)

    return {
        "run_id": run_id,
        "center_ts_ms": center_ts_ms,
        "window_ms": window_ms,
        "window_start_ms": center_ts_ms - window_ms,
        "window_end_ms": center_ts_ms + window_ms,
        "video_start_ms": video_start_ms,
        "streams_by_type": streams_by_type,
        "timeline": timeline,
        "summary": {
            "total_artifacts": len(timeline),
            "artifact_types": sorted(streams_by_type.keys()),
            "counts_by_type": {k: len(v) for k, v in streams_by_type.items()},
            "has_video": video_start_ms is not None,
        },
    }


class _SqlBackendAdapter:
    """Unified SQLite / Project-Brain DB adapter supporting ? placeholders and ANSI SQL."""

    def __init__(self, config: Dict[str, Any], db: Optional[Any] = None, db_path: Optional[Union[str, Path]] = None):
        self._explicit_db = db or config.get("db")
        self._db_path = db_path or config.get("db_path")
        if self._db_path is None and config.get("database_url"):
            url = str(config["database_url"])
            if url.startswith("sqlite:///"):
                self._db_path = url.replace("sqlite:///", "", 1)
        self._use_core_db = bool(config.get("use_core_db", False))
        self._conn: Optional[sqlite3.Connection] = None
        self._core_db: Optional[Any] = None

    @property
    def backend_name(self) -> str:
        if self._explicit_db is not None:
            return "postgres" if getattr(self._explicit_db, "is_postgres", False) else "sqlite"
        if self._use_core_db:
            return "core_db"
        if self._db_path and str(self._db_path) != ":memory:":
            return "sqlite"
        return "sqlite_memory"

    def reconfigure(self, config: Dict[str, Any]) -> None:
        if "db" in config and config["db"] is not None:
            self._explicit_db = config["db"]
        if "db_path" in config and config["db_path"] is not None:
            if self._conn is not None and str(config["db_path"]) != str(self._db_path):
                self._conn.close()
                self._conn = None
            self._db_path = config["db_path"]
        if "use_core_db" in config:
            self._use_core_db = bool(config["use_core_db"])

    def _get_target(self) -> Tuple[str, Any]:
        if self._explicit_db is not None:
            return ("db", self._explicit_db)
        if self._use_core_db:
            if self._core_db is None:
                from projectbrain.core.db import db as core_db
                self._core_db = core_db
            return ("db", self._core_db)
        if self._conn is None:
            target_path = (
                str(self._db_path)
                if self._db_path is not None
                else (os.getenv("PB_DB_PATH") or os.getenv("OM_DB_PATH") or ":memory:")
            )
            if target_path != ":memory:":
                Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(target_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return ("sqlite", self._conn)

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        kind, target = self._get_target()
        if kind == "db":
            target.execute(sql, params)
            target.commit()
        else:
            target.execute(sql, params)
            target.commit()

    def fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        kind, target = self._get_target()
        if kind == "db":
            return [dict(r) for r in target.fetchall(sql, params)]
        rows = target.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        kind, target = self._get_target()
        if kind == "db":
            row = target.fetchone(sql, params)
            return dict(row) if row else None
        row = target.execute(sql, params).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# 1. SqlitePgScriptCachePlugin (VerifiedUDCPScriptCachePlugin)
# ---------------------------------------------------------------------------
class SqlitePgScriptCachePlugin(ScriptCachePlugin):
    """SQLite & PostgreSQL backed Verified UDCP Script Cache plugin."""

    metadata = PluginMetadata(
        plugin_id="cache.sqlite_pg",
        name="SQLite/PostgreSQL Verified UDCP Script Cache",
        version="1.0.0",
        category=PluginCategory.SCRIPT_CACHE.value,
        description="Deterministic UDCP script cache with hit/fallback telemetry, auto-invalidation, and self-heal history.",
        priority=100,
        capabilities=[
            "script_lookup",
            "script_store",
            "execution_telemetry",
            "auto_invalidation",
            "self_heal",
        ],
    )

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        db_path: Optional[Union[str, Path]] = None,
        db: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
    ):
        super().__init__(config=config)
        self.fallback_threshold = float(self.config.get("fallback_threshold", DEFAULT_FALLBACK_THRESHOLD))
        self.min_samples = int(self.config.get("min_samples", DEFAULT_MIN_SAMPLES))
        self.default_ttl_seconds = int(self.config.get("ttl_seconds", DEFAULT_CACHE_TTL_SECONDS))
        self.fingerprint_tolerance = float(self.config.get("fingerprint_tolerance", 0.5))
        self._clock = clock or self.config.get("clock")
        self._adapter = _SqlBackendAdapter(self.config, db=db, db_path=db_path)
        self._tables_ensured = False

    def on_load(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().on_load(config)
        if config:
            self._adapter.reconfigure(config)
            if "fallback_threshold" in config:
                self.fallback_threshold = float(config["fallback_threshold"])
            if "min_samples" in config:
                self.min_samples = int(config["min_samples"])
            if "ttl_seconds" in config:
                self.default_ttl_seconds = int(config["ttl_seconds"])
            self._tables_ensured = False

    def initialize(self, context: Optional[PluginContext] = None) -> None:
        super().initialize(context)
        if context and context.config:
            self._adapter.reconfigure(context.config)
            self._tables_ensured = False
        self.ensure_tables()

    def shutdown(self) -> None:
        self._adapter.close()
        self._tables_ensured = False
        super().shutdown()

    def ensure_tables(self) -> None:
        if self._tables_ensured:
            return
        self._adapter.execute(
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
            """
        )
        self._adapter.execute(
            "CREATE INDEX IF NOT EXISTS idx_udcp_cache_lookup "
            "ON udcp_script_cache(project_id, intent_id, platform, build_version)"
        )
        self._adapter.execute(
            "CREATE INDEX IF NOT EXISTS idx_udcp_cache_screen "
            "ON udcp_script_cache(project_id, intent_id, screen_hash)"
        )
        self._tables_ensured = True

    def _row_to_entry(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "cache_id": row["cache_id"],
            "project_id": row["project_id"],
            "intent_id": row["intent_id"],
            "platform": row["platform"],
            "build_version": row["build_version"],
            "screen_hash": row.get("screen_hash") or "",
            "ui_fingerprint": _json_loads_safe(row.get("ui_fingerprint_json"), {}),
            "udcp_commands": _json_loads_safe(row.get("udcp_commands_json"), []),
            "hit_count": int(row.get("hit_count", 0)),
            "fallback_count": int(row.get("fallback_count", 0)),
            "fallback_rate": float(row.get("fallback_rate", 0.0)),
            "invalidated": bool(row.get("invalidated", 0)),
            "invalidation_reason": row.get("invalidation_reason"),
            "self_heal_history": _json_loads_safe(row.get("self_heal_history_json"), []),
            "ttl_seconds": int(row.get("ttl_seconds", self.default_ttl_seconds)),
            "created_at": int(row.get("created_at", 0)),
            "updated_at": int(row.get("updated_at", 0)),
        }

    def _is_expired(self, entry: Dict[str, Any], now_ms: int) -> bool:
        ttl = int(entry.get("ttl_seconds", 0))
        if ttl <= 0:
            return False
        updated_at = int(entry.get("updated_at", 0))
        return now_ms >= updated_at + ttl * 1000

    def store_script(self, entry: Union[Dict[str, Any], UDCPScriptCacheEntry]) -> Dict[str, Any]:
        self.ensure_tables()
        data = _to_dict(entry)
        now_ms = _now_ms(self._clock)

        project_id = str(data["project_id"])
        intent_id = str(data["intent_id"])
        platform = str(data["platform"])
        build_version = str(data["build_version"])
        screen_hash = str(data.get("screen_hash") or "")
        cache_id = str(data.get("cache_id") or "").strip() or _compute_cache_id(
            project_id, intent_id, platform, build_version, screen_hash
        )

        existing = self._adapter.fetchone(
            "SELECT project_id FROM udcp_script_cache WHERE cache_id = ?", (cache_id,)
        )
        if existing and existing.get("project_id") != project_id:
            raise PermissionError(
                f"Cross-project cache_id collision: '{cache_id}' belongs to another project"
            )

        ui_fingerprint = dict(data.get("ui_fingerprint") or {})
        udcp_commands = list(data.get("udcp_commands") or [])
        hit_count = int(data.get("hit_count", 0))
        fallback_count = int(data.get("fallback_count", 0))
        fallback_rate = float(data.get("fallback_rate", 0.0))
        invalidated = bool(data.get("invalidated", False))
        invalidation_reason = data.get("invalidation_reason")
        self_heal_history = list(data.get("self_heal_history") or [])
        ttl_seconds = int(data.get("ttl_seconds", self.default_ttl_seconds))
        created_at = int(data.get("created_at", now_ms))
        updated_at = int(data.get("updated_at", now_ms))

        self._adapter.execute(
            """
            INSERT INTO udcp_script_cache (
                cache_id, project_id, intent_id, platform, build_version,
                screen_hash, ui_fingerprint_json, udcp_commands_json,
                hit_count, fallback_count, fallback_rate,
                invalidated, invalidation_reason, self_heal_history_json,
                ttl_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_id) DO UPDATE SET
                intent_id=EXCLUDED.intent_id,
                platform=EXCLUDED.platform,
                build_version=EXCLUDED.build_version,
                screen_hash=EXCLUDED.screen_hash,
                ui_fingerprint_json=EXCLUDED.ui_fingerprint_json,
                udcp_commands_json=EXCLUDED.udcp_commands_json,
                hit_count=EXCLUDED.hit_count,
                fallback_count=EXCLUDED.fallback_count,
                fallback_rate=EXCLUDED.fallback_rate,
                invalidated=EXCLUDED.invalidated,
                invalidation_reason=EXCLUDED.invalidation_reason,
                self_heal_history_json=EXCLUDED.self_heal_history_json,
                ttl_seconds=EXCLUDED.ttl_seconds,
                updated_at=EXCLUDED.updated_at
            WHERE udcp_script_cache.project_id = EXCLUDED.project_id
            """,
            (
                cache_id,
                project_id,
                intent_id,
                platform,
                build_version,
                screen_hash,
                json.dumps(ui_fingerprint),
                json.dumps(udcp_commands),
                hit_count,
                fallback_count,
                fallback_rate,
                1 if invalidated else 0,
                invalidation_reason,
                json.dumps(self_heal_history),
                ttl_seconds,
                created_at,
                updated_at,
            ),
        )
        stored = self._adapter.fetchone(
            "SELECT * FROM udcp_script_cache WHERE cache_id = ? AND project_id = ?",
            (cache_id, project_id),
        )
        if not stored:
            raise PermissionError(
                f"Cross-project cache_id collision: '{cache_id}' belongs to another project"
            )
        return self._row_to_entry(stored)

    def lookup_script(
        self,
        project_id: str,
        intent_id: str,
        platform: str,
        build_version: str,
        screen_hash: Optional[str] = None,
        include_invalidated: bool = False,
        ui_fingerprint: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        self.ensure_tables()
        sql = (
            "SELECT * FROM udcp_script_cache "
            "WHERE project_id = ? AND intent_id = ? AND platform = ? AND build_version = ?"
        )
        params: List[Any] = [project_id, intent_id, platform, build_version]
        if screen_hash is not None:
            sql += " AND screen_hash = ?"
            params.append(screen_hash)
        if not include_invalidated:
            sql += " AND invalidated = 0"
        sql += " ORDER BY updated_at DESC"

        rows = self._adapter.fetchall(sql, tuple(params))
        now_ms = _now_ms(self._clock)
        entries = [self._row_to_entry(r) for r in rows]
        entries = [e for e in entries if not self._is_expired(e, now_ms)]
        if not entries:
            return None

        if screen_hash is None and ui_fingerprint:
            best_score = -1.0
            best_entry: Optional[Dict[str, Any]] = None
            for cand in entries:
                score = _fingerprint_similarity(ui_fingerprint, cand.get("ui_fingerprint") or {})
                if score >= self.fingerprint_tolerance and score > best_score:
                    best_score = score
                    best_entry = cand
            return best_entry

        return entries[0]

    def record_execution(
        self,
        cache_id: str,
        success: bool,
        fallback_reason: Optional[str] = None,
        self_heal_patch: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_tables()
        if project_id is not None:
            row = self._adapter.fetchone(
                "SELECT * FROM udcp_script_cache WHERE cache_id = ? AND project_id = ?",
                (cache_id, project_id),
            )
        else:
            row = self._adapter.fetchone(
                "SELECT * FROM udcp_script_cache WHERE cache_id = ?",
                (cache_id,),
            )
        if not row:
            return {"cache_id": cache_id, "found": False, "error": "Cache entry not found"}

        current = self._row_to_entry(row)
        threshold = float(self.config.get("fallback_threshold", self.fallback_threshold))
        min_samples = int(self.config.get("min_samples", self.min_samples))
        updated = _apply_execution_update(
            current,
            success=success,
            fallback_reason=fallback_reason,
            self_heal_patch=self_heal_patch,
            fallback_threshold=threshold,
            min_samples=min_samples,
            now_ms=_now_ms(self._clock),
        )

        self._adapter.execute(
            """
            INSERT INTO udcp_script_cache (
                cache_id, project_id, intent_id, platform, build_version,
                screen_hash, ui_fingerprint_json, udcp_commands_json,
                hit_count, fallback_count, fallback_rate,
                invalidated, invalidation_reason, self_heal_history_json,
                ttl_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_id) DO UPDATE SET
                screen_hash=EXCLUDED.screen_hash,
                ui_fingerprint_json=EXCLUDED.ui_fingerprint_json,
                udcp_commands_json=EXCLUDED.udcp_commands_json,
                hit_count=EXCLUDED.hit_count,
                fallback_count=EXCLUDED.fallback_count,
                fallback_rate=EXCLUDED.fallback_rate,
                invalidated=EXCLUDED.invalidated,
                invalidation_reason=EXCLUDED.invalidation_reason,
                self_heal_history_json=EXCLUDED.self_heal_history_json,
                updated_at=EXCLUDED.updated_at
            WHERE udcp_script_cache.project_id = EXCLUDED.project_id
            """,
            (
                updated["cache_id"],
                updated["project_id"],
                updated["intent_id"],
                updated["platform"],
                updated["build_version"],
                updated["screen_hash"],
                json.dumps(updated["ui_fingerprint"]),
                json.dumps(updated["udcp_commands"]),
                updated["hit_count"],
                updated["fallback_count"],
                updated["fallback_rate"],
                1 if updated["invalidated"] else 0,
                updated["invalidation_reason"],
                json.dumps(updated["self_heal_history"]),
                updated["ttl_seconds"],
                updated["created_at"],
                updated["updated_at"],
            ),
        )
        return updated

    def invalidate_script(
        self,
        cache_id: str,
        reason: str,
        project_id: Optional[str] = None,
    ) -> bool:
        self.ensure_tables()
        if project_id is not None:
            existing = self._adapter.fetchone(
                "SELECT cache_id FROM udcp_script_cache WHERE cache_id = ? AND project_id = ?",
                (cache_id, project_id),
            )
        else:
            existing = self._adapter.fetchone(
                "SELECT cache_id FROM udcp_script_cache WHERE cache_id = ?",
                (cache_id,),
            )
        if not existing:
            return False
        if project_id is not None:
            self._adapter.execute(
                "UPDATE udcp_script_cache SET invalidated = 1, invalidation_reason = ?, updated_at = ? "
                "WHERE cache_id = ? AND project_id = ?",
                (reason, _now_ms(self._clock), cache_id, project_id),
            )
        else:
            self._adapter.execute(
                "UPDATE udcp_script_cache SET invalidated = 1, invalidation_reason = ?, updated_at = ? "
                "WHERE cache_id = ?",
                (reason, _now_ms(self._clock), cache_id),
            )
        return True

    def list_scripts(
        self,
        project_id: str,
        platform: Optional[str] = None,
        build_version: Optional[str] = None,
        include_invalidated: bool = True,
    ) -> List[Dict[str, Any]]:
        self.ensure_tables()
        sql = "SELECT * FROM udcp_script_cache WHERE project_id = ?"
        params: List[Any] = [project_id]
        if platform is not None:
            sql += " AND platform = ?"
            params.append(platform)
        if build_version is not None:
            sql += " AND build_version = ?"
            params.append(build_version)
        if not include_invalidated:
            sql += " AND invalidated = 0"
        sql += " ORDER BY updated_at DESC"
        rows = self._adapter.fetchall(sql, tuple(params))
        return [self._row_to_entry(r) for r in rows]

    def health_check(self) -> Dict[str, Any]:
        base = super().health_check()
        base.update({"backend": self._adapter.backend_name, "table": "udcp_script_cache"})
        return base


VerifiedUDCPScriptCachePlugin = SqlitePgScriptCachePlugin


# ---------------------------------------------------------------------------
# 2. RedisScriptCachePlugin
# ---------------------------------------------------------------------------
class _InMemoryRedisTTLStore:
    """Deterministic offline Redis-compatible key/TTL store."""

    def __init__(self, clock: Optional[Callable[[], int]] = None):
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}
        self._clock = clock

    def _now_sec(self) -> float:
        if self._clock is not None:
            return self._clock() / 1000.0
        return time.time()

    def _purge_if_expired(self, key: str) -> None:
        item = self._data.get(key)
        if item is None:
            return
        _, expires_at = item
        if expires_at is not None and self._now_sec() >= expires_at:
            del self._data[key]

    def get(self, key: str) -> Optional[str]:
        self._purge_if_expired(key)
        item = self._data.get(key)
        return item[0] if item is not None else None

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        expires_at = (self._now_sec() + ex) if (ex is not None and ex > 0) else None
        self._data[key] = (value, expires_at)
        return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        return self.set(key, value, ex=ttl)

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                removed += 1
        return removed

    def keys(self, pattern: str = "*") -> List[str]:
        now = self._now_sec()
        expired = [k for k, (_, exp) in self._data.items() if exp is not None and now >= exp]
        for k in expired:
            del self._data[k]
        return [k for k in self._data if fnmatch.fnmatch(k, pattern)]

    def scan_iter(self, match: str = "*") -> List[str]:
        return self.keys(pattern=match)


class RedisScriptCachePlugin(ScriptCachePlugin):
    """Redis / Valkey UDCP Script Cache adapter with offline TTL memory fallback."""

    metadata = PluginMetadata(
        plugin_id="cache.redis",
        name="Redis/Valkey UDCP Script Cache",
        version="1.0.0",
        category=PluginCategory.SCRIPT_CACHE.value,
        description="High-speed Redis/Valkey UDCP script cache with TTL expiry and offline memory fallback.",
        priority=60,
        capabilities=[
            "script_lookup",
            "script_store",
            "execution_telemetry",
            "auto_invalidation",
            "self_heal",
            "ttl_expiry",
        ],
    )

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        redis_client: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
    ):
        super().__init__(config=config)
        self.key_prefix = str(self.config.get("key_prefix", "pb:scache"))
        self.use_memory_fallback = bool(self.config.get("use_memory_fallback", True))
        self.fallback_threshold = float(self.config.get("fallback_threshold", DEFAULT_FALLBACK_THRESHOLD))
        self.min_samples = int(self.config.get("min_samples", DEFAULT_MIN_SAMPLES))
        self.default_ttl_seconds = int(self.config.get("ttl_seconds", DEFAULT_CACHE_TTL_SECONDS))
        self.fingerprint_tolerance = float(self.config.get("fingerprint_tolerance", 0.5))
        self._clock = clock or self.config.get("clock")
        self._memory_store = _InMemoryRedisTTLStore(clock=self._clock)
        self._client = redis_client or self.config.get("redis_client")

        if self._client is None and self.config.get("redis_url"):
            self._client = self._try_connect_redis(str(self.config["redis_url"]))

    def _try_connect_redis(self, redis_url: str) -> Optional[Any]:
        try:
            import redis  # type: ignore[import-not-found]
            client = redis.from_url(redis_url, decode_responses=True)
            client.ping()
            return client
        except Exception:
            if not self.use_memory_fallback:
                raise
            return None

    @property
    def _backend(self) -> Any:
        if self._client is not None:
            return self._client
        if self.use_memory_fallback:
            return self._memory_store
        raise RuntimeError("RedisScriptCachePlugin has no redis_client and use_memory_fallback=False")

    def _base_key(self, project_id: str, intent_id: str, platform: str, build_version: str) -> str:
        return f"{self.key_prefix}:{project_id}:{intent_id}:{platform}:{build_version}"

    def _screen_key(
        self, project_id: str, intent_id: str, platform: str, build_version: str, screen_hash: str
    ) -> str:
        base = self._base_key(project_id, intent_id, platform, build_version)
        return f"{base}:{screen_hash}" if screen_hash else base

    def _id_index_key(self, cache_id: str) -> str:
        return f"{self.key_prefix}:id:{cache_id}"

    def _backend_get(self, key: str) -> Optional[str]:
        val = self._backend.get(key)
        if isinstance(val, bytes):
            return val.decode("utf-8")
        return val

    def _backend_set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if (ttl_seconds is not None and ttl_seconds > 0) else None
        backend = self._backend
        if ttl is not None:
            try:
                backend.set(key, value, ex=ttl)
                return
            except TypeError:
                if hasattr(backend, "setex"):
                    backend.setex(key, ttl, value)
                    return
        backend.set(key, value)

    def _backend_keys(self, pattern: str) -> List[str]:
        backend = self._backend
        if hasattr(backend, "scan_iter"):
            raw = list(backend.scan_iter(match=pattern))
        elif hasattr(backend, "keys"):
            raw = backend.keys(pattern)
        else:
            raw = []
        return [k.decode("utf-8") if isinstance(k, bytes) else str(k) for k in raw]

    def _backend_delete(self, *keys: str) -> None:
        clean = [k for k in keys if k]
        if not clean:
            return
        backend = self._backend
        if hasattr(backend, "delete"):
            backend.delete(*clean)

    def _persist_entry(
        self,
        entry: Dict[str, Any],
        previous_screen_hash: Optional[str] = None,
    ) -> None:
        payload = json.dumps(entry)
        ttl = int(entry.get("ttl_seconds", self.default_ttl_seconds))
        base_key = self._base_key(
            entry["project_id"], entry["intent_id"], entry["platform"], entry["build_version"]
        )
        new_screen_hash = entry.get("screen_hash") or ""
        primary_key = self._screen_key(
            entry["project_id"],
            entry["intent_id"],
            entry["platform"],
            entry["build_version"],
            new_screen_hash,
        )
        if previous_screen_hash and previous_screen_hash != new_screen_hash:
            old_key = self._screen_key(
                entry["project_id"],
                entry["intent_id"],
                entry["platform"],
                entry["build_version"],
                previous_screen_hash,
            )
            if old_key != primary_key and old_key != base_key:
                self._backend_delete(old_key)
        self._backend_set(primary_key, payload, ttl_seconds=ttl)
        if primary_key != base_key:
            self._backend_set(base_key, payload, ttl_seconds=ttl)
        self._backend_set(self._id_index_key(entry["cache_id"]), primary_key, ttl_seconds=ttl)

    def _is_expired(self, entry: Dict[str, Any], now_ms: int) -> bool:
        ttl = int(entry.get("ttl_seconds", 0))
        if ttl <= 0:
            return False
        return now_ms >= int(entry.get("updated_at", 0)) + ttl * 1000

    def store_script(self, entry: Union[Dict[str, Any], UDCPScriptCacheEntry]) -> Dict[str, Any]:
        data = _to_dict(entry)
        now_ms = _now_ms(self._clock)
        project_id = str(data["project_id"])
        intent_id = str(data["intent_id"])
        platform = str(data["platform"])
        build_version = str(data["build_version"])
        screen_hash = str(data.get("screen_hash") or "")
        cache_id = str(data.get("cache_id") or "").strip() or _compute_cache_id(
            project_id, intent_id, platform, build_version, screen_hash
        )

        existing = self._get_by_cache_id(cache_id)
        if existing and existing.get("project_id") != project_id:
            raise PermissionError(
                f"Cross-project cache_id collision: '{cache_id}' belongs to another project"
            )
        old_screen_hash = str(existing.get("screen_hash") or "") if existing else None

        normalized = {
            "cache_id": cache_id,
            "project_id": project_id,
            "intent_id": intent_id,
            "platform": platform,
            "build_version": build_version,
            "screen_hash": screen_hash,
            "ui_fingerprint": dict(data.get("ui_fingerprint") or {}),
            "udcp_commands": list(data.get("udcp_commands") or []),
            "hit_count": int(data.get("hit_count", 0)),
            "fallback_count": int(data.get("fallback_count", 0)),
            "fallback_rate": float(data.get("fallback_rate", 0.0)),
            "invalidated": bool(data.get("invalidated", False)),
            "invalidation_reason": data.get("invalidation_reason"),
            "self_heal_history": list(data.get("self_heal_history") or []),
            "ttl_seconds": int(data.get("ttl_seconds", self.default_ttl_seconds)),
            "created_at": int(data.get("created_at", now_ms)),
            "updated_at": int(data.get("updated_at", now_ms)),
        }
        self._persist_entry(normalized, previous_screen_hash=old_screen_hash)
        return normalized

    def lookup_script(
        self,
        project_id: str,
        intent_id: str,
        platform: str,
        build_version: str,
        screen_hash: Optional[str] = None,
        include_invalidated: bool = False,
        ui_fingerprint: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        now_ms = _now_ms(self._clock)
        base_key = self._base_key(project_id, intent_id, platform, build_version)
        candidate_keys: List[str] = []
        if screen_hash is not None:
            candidate_keys.append(self._screen_key(project_id, intent_id, platform, build_version, screen_hash))
        candidate_keys.append(base_key)
        if screen_hash is None:
            escaped_base = _escape_redis_glob(base_key)
            candidate_keys.extend(k for k in self._backend_keys(f"{escaped_base}:*") if k not in candidate_keys)

        seen_ids: set[str] = set()
        matches: List[Dict[str, Any]] = []
        for key in candidate_keys:
            raw = self._backend_get(key)
            if not raw:
                continue
            entry = _json_loads_safe(raw, None)
            if not isinstance(entry, dict) or not entry.get("cache_id"):
                continue
            if entry.get("project_id") != project_id:
                continue
            cid = entry["cache_id"]
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            if self._is_expired(entry, now_ms):
                continue
            if screen_hash is not None and entry.get("screen_hash", "") != screen_hash:
                continue
            if not include_invalidated and entry.get("invalidated"):
                continue
            matches.append(entry)

        if not matches:
            return None

        matches.sort(key=lambda e: int(e.get("updated_at", 0)), reverse=True)
        if screen_hash is None and ui_fingerprint:
            best_score = -1.0
            best_entry: Optional[Dict[str, Any]] = None
            for cand in matches:
                score = _fingerprint_similarity(ui_fingerprint, cand.get("ui_fingerprint") or {})
                if score >= self.fingerprint_tolerance and score > best_score:
                    best_score = score
                    best_entry = cand
            return best_entry

        return matches[0]

    def _get_by_cache_id(
        self, cache_id: str, project_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        target_key = self._backend_get(self._id_index_key(cache_id))
        if target_key:
            raw = self._backend_get(target_key)
            if raw:
                parsed = _json_loads_safe(raw, None)
                if isinstance(parsed, dict):
                    if project_id is not None and parsed.get("project_id") != project_id:
                        return None
                    return parsed
        escaped_prefix = _escape_redis_glob(self.key_prefix)
        for key in self._backend_keys(f"{escaped_prefix}:*"):
            if key.startswith(f"{self.key_prefix}:id:"):
                continue
            raw = self._backend_get(key)
            parsed = _json_loads_safe(raw, None)
            if isinstance(parsed, dict) and parsed.get("cache_id") == cache_id:
                if project_id is not None and parsed.get("project_id") != project_id:
                    return None
                return parsed
        return None

    def record_execution(
        self,
        cache_id: str,
        success: bool,
        fallback_reason: Optional[str] = None,
        self_heal_patch: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self._get_by_cache_id(cache_id, project_id=project_id)
        if not current:
            return {"cache_id": cache_id, "found": False, "error": "Cache entry not found"}

        old_screen_hash = str(current.get("screen_hash") or "")
        threshold = float(self.config.get("fallback_threshold", self.fallback_threshold))
        min_samples = int(self.config.get("min_samples", self.min_samples))
        updated = _apply_execution_update(
            current,
            success=success,
            fallback_reason=fallback_reason,
            self_heal_patch=self_heal_patch,
            fallback_threshold=threshold,
            min_samples=min_samples,
            now_ms=_now_ms(self._clock),
        )
        persist_payload = {k: v for k, v in updated.items() if k != "found"}
        self._persist_entry(persist_payload, previous_screen_hash=old_screen_hash)
        return updated

    def invalidate_script(
        self,
        cache_id: str,
        reason: str,
        project_id: Optional[str] = None,
    ) -> bool:
        current = self._get_by_cache_id(cache_id, project_id=project_id)
        if not current:
            return False
        current["invalidated"] = True
        current["invalidation_reason"] = reason
        current["updated_at"] = _now_ms(self._clock)
        self._persist_entry(current)
        return True

    def list_scripts(
        self,
        project_id: str,
        platform: Optional[str] = None,
        build_version: Optional[str] = None,
        include_invalidated: bool = True,
    ) -> List[Dict[str, Any]]:
        now_ms = _now_ms(self._clock)
        keys = self._backend_keys(
            f"{_escape_redis_glob(self.key_prefix)}:{_escape_redis_glob(project_id)}:*"
        )
        seen_ids: set[str] = set()
        results: List[Dict[str, Any]] = []
        for key in keys:
            raw = self._backend_get(key)
            if not raw:
                continue
            entry = _json_loads_safe(raw, None)
            if not isinstance(entry, dict) or not entry.get("cache_id"):
                continue
            if entry.get("project_id") != project_id:
                continue
            cid = entry["cache_id"]
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            if self._is_expired(entry, now_ms):
                continue
            if platform is not None and entry.get("platform") != platform:
                continue
            if build_version is not None and entry.get("build_version") != build_version:
                continue
            if not include_invalidated and entry.get("invalidated"):
                continue
            results.append(entry)

        results.sort(key=lambda e: int(e.get("updated_at", 0)), reverse=True)
        return results

    def health_check(self) -> Dict[str, Any]:
        base = super().health_check()
        base.update(
            {
                "backend": "redis" if self._client is not None else "memory_ttl",
                "key_prefix": self.key_prefix,
            }
        )
        return base


# ---------------------------------------------------------------------------
# 3. LocalSqliteArtifactStorePlugin (TelemetryArtifactStorePlugin)
# ---------------------------------------------------------------------------
class LocalSqliteArtifactStorePlugin(ArtifactStorePlugin):
    """Local SQLite (or core.db) Synchronized Telemetry & Evidence Artifact Store."""

    metadata = PluginMetadata(
        plugin_id="artifacts.local_sqlite",
        name="Local SQLite Telemetry Artifact Store",
        version="1.0.0",
        category=PluginCategory.ARTIFACT_STORE.value,
        description="NTP-aligned multimodal telemetry artifact store supporting 30-90d retention and evidence window queries.",
        priority=100,
        capabilities=[
            "index_artifact",
            "query_artifacts",
            "evidence_window",
            "ttl_purge",
            "ntp_alignment",
        ],
    )

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        db_path: Optional[Union[str, Path]] = None,
        db: Optional[Any] = None,
        clock: Optional[Callable[[], int]] = None,
    ):
        super().__init__(config=config)
        self.default_retention_days = _clamp_retention_days(
            self.config.get("default_retention_days", self.config.get("retention_days")),
            default_days=MIN_RETENTION_DAYS,
        )
        self._artifact_root = self.config.get("artifact_root") or self.config.get("storage_dir")
        self._clock = clock or self.config.get("clock")
        self._adapter = _SqlBackendAdapter(self.config, db=db, db_path=db_path)
        self._tables_ensured = False

    def on_load(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().on_load(config)
        if config:
            self._adapter.reconfigure(config)
            if "default_retention_days" in config or "retention_days" in config:
                self.default_retention_days = _clamp_retention_days(
                    config.get("default_retention_days", config.get("retention_days")),
                    default_days=self.default_retention_days,
                )
            if "artifact_root" in config or "storage_dir" in config:
                self._artifact_root = config.get("artifact_root") or config.get("storage_dir")
            self._tables_ensured = False

    def initialize(self, context: Optional[PluginContext] = None) -> None:
        super().initialize(context)
        if context and context.config:
            self._adapter.reconfigure(context.config)
            self._tables_ensured = False
        self.ensure_tables()

    def shutdown(self) -> None:
        self._adapter.close()
        self._tables_ensured = False
        super().shutdown()

    def ensure_tables(self) -> None:
        if self._tables_ensured:
            return
        self._adapter.execute(
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
            """
        )
        self._adapter.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_run_step_ts "
            "ON telemetry_artifacts(run_id, step_id, ntp_timestamp_ms)"
        )
        self._adapter.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_run_ts "
            "ON telemetry_artifacts(run_id, ntp_timestamp_ms)"
        )
        self._adapter.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_expires "
            "ON telemetry_artifacts(expires_at)"
        )
        self._tables_ensured = True

    def _row_to_record(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "artifact_id": row["artifact_id"],
            "run_id": row["run_id"],
            "step_id": row["step_id"],
            "project_id": row["project_id"],
            "device_id": row.get("device_id") or "",
            "build_version": row.get("build_version") or "",
            "artifact_type": row["artifact_type"],
            "ntp_timestamp_ms": int(row["ntp_timestamp_ms"]),
            "duration_ms": int(row.get("duration_ms", 0)),
            "uri": row["uri"],
            "sha256": row.get("sha256") or "",
            "size_bytes": int(row.get("size_bytes", 0)),
            "summary_metrics": _json_loads_safe(row.get("summary_metrics_json"), {}),
            "retention_days": int(row.get("retention_days", self.default_retention_days)),
            "expires_at": int(row["expires_at"]),
            "created_at": int(row["created_at"]),
        }

    def index_artifact(self, record: Union[Dict[str, Any], TelemetryArtifactRecord]) -> Dict[str, Any]:
        self.ensure_tables()
        data = _to_dict(record)
        now_ms = _now_ms(self._clock)

        run_id = str(data["run_id"])
        step_id = str(data["step_id"])
        project_id = str(data.get("project_id", "default"))
        device_id = str(data.get("device_id") or "")
        build_version = str(data.get("build_version") or "")
        artifact_type = str(data["artifact_type"])
        ntp_timestamp_ms = int(data["ntp_timestamp_ms"])
        duration_ms = int(data.get("duration_ms", 0))
        uri = str(data["uri"])
        sha256 = str(data.get("sha256") or "")
        size_bytes = int(data.get("size_bytes", 0))
        summary_metrics = dict(data.get("summary_metrics") or {})

        retention_days = _clamp_retention_days(
            data.get("retention_days"), default_days=self.default_retention_days
        )
        created_at = int(data.get("created_at", now_ms))
        expires_at = (
            int(data["expires_at"])
            if data.get("expires_at") is not None
            else created_at + retention_days * MS_PER_DAY
        )
        artifact_id = str(data.get("artifact_id") or "").strip() or _compute_artifact_id(
            run_id, step_id, artifact_type, ntp_timestamp_ms, uri, project_id=project_id
        )

        existing = self._adapter.fetchone(
            "SELECT project_id FROM telemetry_artifacts WHERE artifact_id = ?", (artifact_id,)
        )
        if existing and existing.get("project_id") != project_id:
            raise PermissionError(
                f"Cross-project artifact_id collision: '{artifact_id}' belongs to another project"
            )

        self._adapter.execute(
            """
            INSERT INTO telemetry_artifacts (
                artifact_id, run_id, step_id, project_id, device_id, build_version,
                artifact_type, ntp_timestamp_ms, duration_ms, uri, sha256,
                size_bytes, summary_metrics_json, retention_days, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                run_id=EXCLUDED.run_id,
                step_id=EXCLUDED.step_id,
                device_id=EXCLUDED.device_id,
                build_version=EXCLUDED.build_version,
                artifact_type=EXCLUDED.artifact_type,
                ntp_timestamp_ms=EXCLUDED.ntp_timestamp_ms,
                duration_ms=EXCLUDED.duration_ms,
                uri=EXCLUDED.uri,
                sha256=EXCLUDED.sha256,
                size_bytes=EXCLUDED.size_bytes,
                summary_metrics_json=EXCLUDED.summary_metrics_json,
                retention_days=EXCLUDED.retention_days,
                expires_at=EXCLUDED.expires_at
            WHERE telemetry_artifacts.project_id = EXCLUDED.project_id
            """,
            (
                artifact_id,
                run_id,
                step_id,
                project_id,
                device_id,
                build_version,
                artifact_type,
                ntp_timestamp_ms,
                duration_ms,
                uri,
                sha256,
                size_bytes,
                json.dumps(summary_metrics),
                retention_days,
                expires_at,
                created_at,
            ),
        )
        stored = self._adapter.fetchone(
            "SELECT * FROM telemetry_artifacts WHERE artifact_id = ? AND project_id = ?",
            (artifact_id, project_id),
        )
        if not stored:
            raise PermissionError(
                f"Cross-project artifact_id collision: '{artifact_id}' belongs to another project"
            )
        return self._row_to_record(stored)

    def query_artifacts(
        self,
        run_id: Optional[str] = None,
        step_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        build_version: Optional[str] = None,
        limit: int = 100,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.ensure_tables()
        clauses: List[str] = []
        params: List[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if step_id is not None:
            clauses.append("step_id = ?")
            params.append(step_id)
        if artifact_type is not None:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        if build_version is not None:
            clauses.append("build_version = ?")
            params.append(build_version)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)

        sql = "SELECT * FROM telemetry_artifacts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ntp_timestamp_ms ASC LIMIT ?"
        params.append(int(limit))

        rows = self._adapter.fetchall(sql, tuple(params))
        return [self._row_to_record(r) for r in rows]

    def query_evidence_window(
        self,
        run_id: str,
        center_ts_ms: int,
        window_ms: int = 5000,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_tables()
        start_ts = int(center_ts_ms) - int(window_ms)
        end_ts = int(center_ts_ms) + int(window_ms)

        if project_id is not None:
            rows = self._adapter.fetchall(
                "SELECT * FROM telemetry_artifacts "
                "WHERE run_id = ? AND project_id = ? AND ntp_timestamp_ms BETWEEN ? AND ? "
                "ORDER BY ntp_timestamp_ms ASC",
                (run_id, project_id, start_ts, end_ts),
            )
            video_row = self._adapter.fetchone(
                "SELECT MIN(ntp_timestamp_ms) AS min_ts FROM telemetry_artifacts "
                "WHERE run_id = ? AND project_id = ? AND artifact_type = ?",
                (run_id, project_id, "video_h264"),
            )
        else:
            rows = self._adapter.fetchall(
                "SELECT * FROM telemetry_artifacts "
                "WHERE run_id = ? AND ntp_timestamp_ms BETWEEN ? AND ? "
                "ORDER BY ntp_timestamp_ms ASC",
                (run_id, start_ts, end_ts),
            )
            video_row = self._adapter.fetchone(
                "SELECT MIN(ntp_timestamp_ms) AS min_ts FROM telemetry_artifacts "
                "WHERE run_id = ? AND artifact_type = ?",
                (run_id, "video_h264"),
            )
        matched = [self._row_to_record(r) for r in rows]
        video_start_ms: Optional[int] = (
            int(video_row["min_ts"]) if video_row and video_row.get("min_ts") is not None else None
        )

        return _build_evidence_window(
            run_id=run_id,
            center_ts_ms=int(center_ts_ms),
            window_ms=int(window_ms),
            matched_items=matched,
            video_start_ms=video_start_ms,
        )

    def _safe_remove_managed_file(self, uri: str) -> Optional[str]:
        if not self._artifact_root:
            return None
        raw_path = uri[7:] if uri.startswith("file://") else uri
        if "://" in raw_path:
            return None
        try:
            root = Path(self._artifact_root).resolve()
            candidate = Path(raw_path).resolve()
            candidate.relative_to(root)
        except Exception:
            return None
        if candidate.is_file():
            candidate.unlink(missing_ok=True)
            return str(candidate)
        return None

    def purge_expired(
        self,
        now_ms: Optional[int] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_tables()
        cutoff = int(now_ms if now_ms is not None else _now_ms(self._clock))
        if project_id is not None:
            expired_rows = self._adapter.fetchall(
                "SELECT artifact_id, uri FROM telemetry_artifacts WHERE expires_at <= ? AND project_id = ?",
                (cutoff, project_id),
            )
        else:
            expired_rows = self._adapter.fetchall(
                "SELECT artifact_id, uri FROM telemetry_artifacts WHERE expires_at <= ?",
                (cutoff,),
            )
        deleted_ids = [r["artifact_id"] for r in expired_rows]
        deleted_files: List[str] = []
        for row in expired_rows:
            removed = self._safe_remove_managed_file(str(row.get("uri") or ""))
            if removed:
                deleted_files.append(removed)

        if deleted_ids:
            if project_id is not None:
                self._adapter.execute(
                    "DELETE FROM telemetry_artifacts WHERE expires_at <= ? AND project_id = ?",
                    (cutoff, project_id),
                )
            else:
                self._adapter.execute(
                    "DELETE FROM telemetry_artifacts WHERE expires_at <= ?",
                    (cutoff,),
                )

        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "deleted_files": deleted_files,
            "cutoff_ms": cutoff,
        }

    def health_check(self) -> Dict[str, Any]:
        base = super().health_check()
        base.update(
            {
                "backend": self._adapter.backend_name,
                "table": "telemetry_artifacts",
                "default_retention_days": self.default_retention_days,
            }
        )
        return base


TelemetryArtifactStorePlugin = LocalSqliteArtifactStorePlugin


# ---------------------------------------------------------------------------
# 4. MinioClickHouseArtifactStorePlugin
# ---------------------------------------------------------------------------
class MinioClickHouseArtifactStorePlugin(ArtifactStorePlugin):
    """MinIO/S3 object URI + ClickHouse HTTP (JSONEachRow / FORMAT JSON) Artifact Store."""

    metadata = PluginMetadata(
        plugin_id="artifacts.minio_clickhouse",
        name="MinIO + ClickHouse Telemetry Artifact Store",
        version="1.0.0",
        category=PluginCategory.ARTIFACT_STORE.value,
        description="Distributed MinIO S3 URI and ClickHouse HTTP telemetry artifact store with injectable transport and offline fallback.",
        priority=60,
        capabilities=[
            "index_artifact",
            "query_artifacts",
            "evidence_window",
            "ttl_purge",
            "s3_uri",
            "clickhouse_http",
        ],
    )

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        transport: Optional[httpx.BaseTransport] = None,
        http_client: Optional[httpx.Client] = None,
        clock: Optional[Callable[[], int]] = None,
    ):
        super().__init__(config=config)
        self.minio_endpoint = str(
            self.config.get("minio_endpoint") or self.config.get("s3_endpoint") or "http://localhost:9000"
        ).rstrip("/")
        self.bucket = self._validate_bucket(
            str(self.config.get("bucket") or "projectbrain-telemetry")
        )
        self.clickhouse_url = str(self.config.get("clickhouse_url") or "http://localhost:8123").rstrip("/")
        self.clickhouse_database = _validate_ch_identifier(
            str(self.config.get("clickhouse_database") or "default"),
            "clickhouse_database",
        )
        self.clickhouse_table = _validate_ch_identifier(
            str(self.config.get("clickhouse_table") or "telemetry_artifacts"),
            "clickhouse_table",
        )
        self.offline_fallback = bool(
            self.config.get("offline_fallback", self.config.get("use_offline_fallback", True))
        )
        self.default_retention_days = _clamp_retention_days(
            self.config.get("default_retention_days", self.config.get("retention_days")),
            default_days=MIN_RETENTION_DAYS,
        )
        self._clock = clock or self.config.get("clock")
        self._transport = transport or self.config.get("transport")
        self._external_client = http_client or self.config.get("http_client")
        self._client: Optional[httpx.Client] = self._external_client
        self._offline_records: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _validate_bucket(bucket: str) -> str:
        clean = str(bucket or "").strip()
        if not clean or "/" in clean or "\\" in clean or ".." in clean:
            raise ValueError(f"Invalid S3 bucket name: {bucket!r}")
        return clean

    def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        validated = dict(config or {})
        if "clickhouse_database" in validated and validated["clickhouse_database"] is not None:
            validated["clickhouse_database"] = _validate_ch_identifier(
                str(validated["clickhouse_database"]), "clickhouse_database"
            )
        if "clickhouse_table" in validated and validated["clickhouse_table"] is not None:
            validated["clickhouse_table"] = _validate_ch_identifier(
                str(validated["clickhouse_table"]), "clickhouse_table"
            )
        if "bucket" in validated and validated["bucket"] is not None:
            validated["bucket"] = self._validate_bucket(str(validated["bucket"]))
        return validated

    def on_load(self, config: Optional[Dict[str, Any]] = None) -> None:
        if config:
            validated = self.validate_config(config)
            super().on_load(validated)
            if "bucket" in validated and validated["bucket"] is not None:
                self.bucket = str(validated["bucket"])
            if "clickhouse_database" in validated and validated["clickhouse_database"] is not None:
                self.clickhouse_database = str(validated["clickhouse_database"])
            if "clickhouse_table" in validated and validated["clickhouse_table"] is not None:
                self.clickhouse_table = str(validated["clickhouse_table"])
            if "clickhouse_url" in validated and validated["clickhouse_url"] is not None:
                self.clickhouse_url = str(validated["clickhouse_url"]).rstrip("/")
            if "minio_endpoint" in validated or "s3_endpoint" in validated:
                self.minio_endpoint = str(
                    validated.get("minio_endpoint") or validated.get("s3_endpoint") or self.minio_endpoint
                ).rstrip("/")
        else:
            super().on_load(config)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            kwargs: Dict[str, Any] = {"timeout": float(self.config.get("timeout_seconds", 10.0))}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.Client(**kwargs)
        return self._client

    def shutdown(self) -> None:
        if self._client is not None and self._client is not self._external_client:
            self._client.close()
            self._client = None
        super().shutdown()

    def _build_s3_uri(
        self,
        run_id: str,
        step_id: str,
        artifact_id: str,
        artifact_type: str,
        raw_uri: Optional[str],
    ) -> str:
        if raw_uri and str(raw_uri).startswith("s3://"):
            raw_str = str(raw_uri)
            rest = raw_str[5:]
            target_bucket, _, key_path = rest.partition("/")
            if target_bucket != self.bucket or not key_path or "\\" in key_path:
                raise ValueError(f"Invalid S3 URI bucket or object key: {raw_uri!r}")
            raw_segments = [seg for seg in key_path.split("/") if seg != ""]
            if not raw_segments or any(seg in (".", "..") or ".." in seg for seg in raw_segments):
                raise ValueError(f"Unsafe S3 object key traversal in URI: {raw_uri!r}")
            safe_segments = [_sanitize_s3_segment(seg) for seg in raw_segments]
            return f"s3://{self.bucket}/{'/'.join(safe_segments)}"
        safe_run = _sanitize_s3_segment(run_id, "run")
        safe_step = _sanitize_s3_segment(step_id, "step")
        raw_leaf = Path(str(raw_uri)).name if raw_uri else f"{artifact_id}_{artifact_type}"
        safe_leaf = _sanitize_s3_segment(raw_leaf, f"{artifact_id}_{artifact_type}")
        return f"s3://{self.bucket}/{safe_run}/{safe_step}/{artifact_id}_{safe_leaf}"

    def _clickhouse_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        user = self.config.get("clickhouse_user")
        password = self.config.get("clickhouse_password")
        if user is not None:
            headers["X-ClickHouse-User"] = str(user)
        if password is not None:
            headers["X-ClickHouse-Key"] = str(password)
        return headers

    def _decode_ch_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        summary = row.get("summary_metrics")
        if summary is None:
            summary = _json_loads_safe(row.get("summary_metrics_json"), {})
        return {
            "artifact_id": str(row["artifact_id"]),
            "run_id": str(row["run_id"]),
            "step_id": str(row["step_id"]),
            "project_id": str(row.get("project_id", "default")),
            "device_id": str(row.get("device_id") or ""),
            "build_version": str(row.get("build_version") or ""),
            "artifact_type": str(row["artifact_type"]),
            "ntp_timestamp_ms": int(row["ntp_timestamp_ms"]),
            "duration_ms": int(row.get("duration_ms", 0)),
            "uri": str(row["uri"]),
            "sha256": str(row.get("sha256") or ""),
            "size_bytes": int(row.get("size_bytes", 0)),
            "summary_metrics": dict(summary) if isinstance(summary, dict) else {},
            "retention_days": int(row.get("retention_days", self.default_retention_days)),
            "expires_at": int(row["expires_at"]),
            "created_at": int(row["created_at"]),
        }

    def index_artifact(self, record: Union[Dict[str, Any], TelemetryArtifactRecord]) -> Dict[str, Any]:
        data = _to_dict(record)
        now_ms = _now_ms(self._clock)
        run_id = str(data["run_id"])
        step_id = str(data["step_id"])
        project_id = str(data.get("project_id", "default"))
        device_id = str(data.get("device_id") or "")
        build_version = str(data.get("build_version") or "")
        artifact_type = str(data["artifact_type"])
        ntp_timestamp_ms = int(data["ntp_timestamp_ms"])
        duration_ms = int(data.get("duration_ms", 0))
        raw_uri = data.get("uri")
        artifact_id = str(data.get("artifact_id") or "").strip() or _compute_artifact_id(
            run_id, step_id, artifact_type, ntp_timestamp_ms, str(raw_uri or ""), project_id=project_id
        )
        existing_offline = self._offline_records.get(artifact_id)
        if existing_offline and existing_offline.get("project_id") != project_id:
            raise PermissionError(
                f"Cross-project artifact_id collision: '{artifact_id}' belongs to another project"
            )
        s3_uri = self._build_s3_uri(run_id, step_id, artifact_id, artifact_type, raw_uri)
        sha256 = str(data.get("sha256") or "")
        size_bytes = int(data.get("size_bytes", 0))
        summary_metrics = dict(data.get("summary_metrics") or {})
        retention_days = _clamp_retention_days(
            data.get("retention_days"), default_days=self.default_retention_days
        )
        created_at = int(data.get("created_at", now_ms))
        expires_at = (
            int(data["expires_at"])
            if data.get("expires_at") is not None
            else created_at + retention_days * MS_PER_DAY
        )

        normalized = {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "step_id": step_id,
            "project_id": project_id,
            "device_id": device_id,
            "build_version": build_version,
            "artifact_type": artifact_type,
            "ntp_timestamp_ms": ntp_timestamp_ms,
            "duration_ms": duration_ms,
            "uri": s3_uri,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "summary_metrics": summary_metrics,
            "retention_days": retention_days,
            "expires_at": expires_at,
            "created_at": created_at,
        }

        ch_row = dict(normalized)
        ch_row["summary_metrics_json"] = json.dumps(summary_metrics)
        ch_row.pop("summary_metrics", None)

        payload_bytes = data.get("payload_bytes")
        try:
            client = self._get_client()
            if payload_bytes is not None:
                prefix = f"s3://{self.bucket}/"
                if not s3_uri.startswith(prefix):
                    raise ValueError(f"S3 URI outside configured bucket: {s3_uri!r}")
                object_key = s3_uri[len(prefix):]
                if not object_key or ".." in object_key.split("/"):
                    raise ValueError(f"Unsafe S3 object key: {object_key!r}")
                put_resp = client.put(
                    f"{self.minio_endpoint}/{self.bucket}/{object_key}",
                    content=payload_bytes if isinstance(payload_bytes, bytes) else str(payload_bytes).encode("utf-8"),
                )
                put_resp.raise_for_status()

            insert_sql = f"INSERT INTO {self.clickhouse_database}.{self.clickhouse_table} FORMAT JSONEachRow"
            resp = client.post(
                f"{self.clickhouse_url}/",
                params={"query": insert_sql},
                content=(json.dumps(ch_row) + "\n").encode("utf-8"),
                headers=self._clickhouse_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            if not self.offline_fallback:
                raise
            self._offline_records[artifact_id] = normalized
        else:
            if self.offline_fallback:
                self._offline_records[artifact_id] = normalized

        return normalized

    def _query_offline(
        self,
        run_id: Optional[str] = None,
        step_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        build_version: Optional[str] = None,
        limit: int = 100,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in self._offline_records.values():
            if run_id is not None and item["run_id"] != run_id:
                continue
            if step_id is not None and item["step_id"] != step_id:
                continue
            if artifact_type is not None and item["artifact_type"] != artifact_type:
                continue
            if build_version is not None and item["build_version"] != build_version:
                continue
            if project_id is not None and item["project_id"] != project_id:
                continue
            results.append(dict(item))
        results.sort(key=lambda r: (int(r["ntp_timestamp_ms"]), str(r["artifact_id"])))
        return results[: int(limit)]

    def _execute_ch_select(self, sql: str, query_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        client = self._get_client()
        params = {"query": sql}
        for k, v in query_params.items():
            params[f"param_{k}"] = str(v)
        resp = client.post(
            f"{self.clickhouse_url}/",
            params=params,
            headers=self._clickhouse_headers(),
        )
        resp.raise_for_status()
        body = resp.json()
        return [self._decode_ch_row(r) for r in body.get("data", [])]

    def query_artifacts(
        self,
        run_id: Optional[str] = None,
        step_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        build_version: Optional[str] = None,
        limit: int = 100,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        qparams: Dict[str, Any] = {"limit": int(limit)}
        if run_id is not None:
            clauses.append("run_id = {run_id:String}")
            qparams["run_id"] = run_id
        if step_id is not None:
            clauses.append("step_id = {step_id:String}")
            qparams["step_id"] = step_id
        if artifact_type is not None:
            clauses.append("artifact_type = {artifact_type:String}")
            qparams["artifact_type"] = artifact_type
        if build_version is not None:
            clauses.append("build_version = {build_version:String}")
            qparams["build_version"] = build_version
        if project_id is not None:
            clauses.append("project_id = {project_id:String}")
            qparams["project_id"] = project_id

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM {self.clickhouse_database}.{self.clickhouse_table}"
            f"{where_sql} ORDER BY ntp_timestamp_ms ASC LIMIT {{limit:UInt32}} FORMAT JSON"
        )
        try:
            return self._execute_ch_select(sql, qparams)
        except httpx.HTTPError:
            if not self.offline_fallback:
                raise
            return self._query_offline(
                run_id=run_id,
                step_id=step_id,
                artifact_type=artifact_type,
                build_version=build_version,
                limit=limit,
                project_id=project_id,
            )

    def query_evidence_window(
        self,
        run_id: str,
        center_ts_ms: int,
        window_ms: int = 5000,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        start_ts = int(center_ts_ms) - int(window_ms)
        end_ts = int(center_ts_ms) + int(window_ms)

        project_clause = " AND project_id = {project_id:String}" if project_id is not None else ""
        window_sql = (
            f"SELECT * FROM {self.clickhouse_database}.{self.clickhouse_table} "
            f"WHERE run_id = {{run_id:String}}{project_clause} "
            "AND ntp_timestamp_ms BETWEEN {start_ts:Int64} AND {end_ts:Int64} "
            "ORDER BY ntp_timestamp_ms ASC FORMAT JSON"
        )
        video_sql = (
            f"SELECT min(ntp_timestamp_ms) AS min_ts FROM {self.clickhouse_database}.{self.clickhouse_table} "
            f"WHERE run_id = {{run_id:String}}{project_clause} AND artifact_type = {{artifact_type:String}} FORMAT JSON"
        )

        try:
            qparams: Dict[str, Any] = {"run_id": run_id, "start_ts": start_ts, "end_ts": end_ts}
            vparams: Dict[str, Any] = {
                "query": video_sql,
                "param_run_id": run_id,
                "param_artifact_type": "video_h264",
            }
            if project_id is not None:
                qparams["project_id"] = project_id
                vparams["param_project_id"] = project_id

            matched = self._execute_ch_select(window_sql, qparams)
            client = self._get_client()
            vresp = client.post(
                f"{self.clickhouse_url}/",
                params=vparams,
                headers=self._clickhouse_headers(),
            )
            vresp.raise_for_status()
            vdata = vresp.json().get("data", [])
            raw_min = vdata[0].get("min_ts") if vdata else None
            video_start_ms = int(raw_min) if raw_min is not None else None
        except httpx.HTTPError:
            if not self.offline_fallback:
                raise
            run_items = [
                r
                for r in self._offline_records.values()
                if r["run_id"] == run_id and (project_id is None or r.get("project_id") == project_id)
            ]
            matched = [
                dict(r)
                for r in run_items
                if start_ts <= int(r["ntp_timestamp_ms"]) <= end_ts
            ]
            video_ts_list = [
                int(r["ntp_timestamp_ms"])
                for r in run_items
                if r["artifact_type"] == "video_h264"
            ]
            video_start_ms = min(video_ts_list) if video_ts_list else None

        return _build_evidence_window(
            run_id=run_id,
            center_ts_ms=int(center_ts_ms),
            window_ms=int(window_ms),
            matched_items=matched,
            video_start_ms=video_start_ms,
        )

    def purge_expired(
        self,
        now_ms: Optional[int] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cutoff = int(now_ms if now_ms is not None else _now_ms(self._clock))
        deleted_ids: List[str] = []

        for aid, item in list(self._offline_records.items()):
            if int(item["expires_at"]) <= cutoff and (
                project_id is None or item.get("project_id") == project_id
            ):
                deleted_ids.append(aid)
                del self._offline_records[aid]

        project_clause = " AND project_id = {project_id:String}" if project_id is not None else ""
        delete_sql = (
            f"ALTER TABLE {self.clickhouse_database}.{self.clickhouse_table} "
            f"DELETE WHERE expires_at <= {{cutoff:Int64}}{project_clause} SETTINGS mutations_sync = 1"
        )
        try:
            client = self._get_client()
            params: Dict[str, Any] = {"query": delete_sql, "param_cutoff": str(cutoff)}
            if project_id is not None:
                params["param_project_id"] = project_id
            resp = client.post(
                f"{self.clickhouse_url}/",
                params=params,
                headers=self._clickhouse_headers(),
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            if not self.offline_fallback:
                raise

        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "cutoff_ms": cutoff,
        }

    def health_check(self) -> Dict[str, Any]:
        base = super().health_check()
        base.update(
            {
                "backend": "minio_clickhouse",
                "bucket": self.bucket,
                "clickhouse_url": self.clickhouse_url,
                "offline_fallback": self.offline_fallback,
            }
        )
        return base


# ---------------------------------------------------------------------------
# Plugin Discovery & Registration Exports
# ---------------------------------------------------------------------------
PLUGIN_CLASSES = (
    SqlitePgScriptCachePlugin,
    RedisScriptCachePlugin,
    LocalSqliteArtifactStorePlugin,
    MinioClickHouseArtifactStorePlugin,
)


def get_plugins(config: Optional[Dict[str, Any]] = None) -> List[BasePlugin]:
    """Instantiate and return all Layer 1 cache & artifact plugins."""
    cfg = dict(config or {})
    return [
        SqlitePgScriptCachePlugin(config=cfg.get("cache.sqlite_pg")),
        RedisScriptCachePlugin(config=cfg.get("cache.redis")),
        LocalSqliteArtifactStorePlugin(config=cfg.get("artifacts.local_sqlite")),
        MinioClickHouseArtifactStorePlugin(config=cfg.get("artifacts.minio_clickhouse")),
    ]


def register_plugins(registry: Any, config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Register all Layer 1 cache & artifact plugins into a PluginRegistry."""
    registered: List[str] = []
    for plugin in get_plugins(config=config):
        registry.register(plugin)
        registered.append(plugin.metadata.plugin_id)
    return registered


__all__ = [
    "LocalSqliteArtifactStorePlugin",
    "MinioClickHouseArtifactStorePlugin",
    "PLUGIN_CLASSES",
    "RedisScriptCachePlugin",
    "SUPPORTED_ARTIFACT_TYPES",
    "SqlitePgScriptCachePlugin",
    "TelemetryArtifactStorePlugin",
    "VerifiedUDCPScriptCachePlugin",
    "get_plugins",
    "register_plugins",
]
