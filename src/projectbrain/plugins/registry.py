"""Project-Brain v3.0 Plugin Microkernel Registry.

Provides 3-tier plugin discovery (built-ins, local/user directories with hot-reload,
and Python packaging entrypoints), layered configuration overrides (TOML/YAML/JSON,
environment variables, and per-project SQLite/PostgreSQL overrides), priority-ordered
resolution, and aggregated health reporting.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import yaml

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from .base import BasePlugin, PluginCategory, PluginContext, PluginManifest, PluginMetadata

logger = logging.getLogger("projectbrain.plugins.registry")

BUILTIN_SUBMODULES: Tuple[str, ...] = (
    "storage_plugins",
    "etl_and_flow_plugins",
    "cache_and_artifact_plugins",
    "brain_consumer_plugins",
)


def _normalize_category(category: Union[str, PluginCategory]) -> str:
    if isinstance(category, PluginCategory):
        return category.value
    return str(category)


def _parse_csv_env(var_name: str) -> Set[str]:
    raw = os.getenv(var_name, "")
    if not raw.strip():
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


_SENSITIVE_KEY_SUBSTRINGS: Tuple[str, ...] = (
    "secret",
    "password",
    "passwd",
    "passphrase",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "access_key",
    "credential",
)
_SENSITIVE_EXACT_OR_SUFFIX_KEYS: Tuple[str, ...] = ("auth", "authorization", "key")
_URL_CRED_RE = re.compile(r"(://)([^/@\s]+)(@)")
_DSN_PASS_RE = re.compile(r"(?i)(\bpassword\s*=\s*)([^\s;]+)")


def _is_sensitive_config_key(key: str) -> bool:
    low = key.lower()
    if any(sub in low for sub in _SENSITIVE_KEY_SUBSTRINGS):
        return True
    return low in _SENSITIVE_EXACT_OR_SUFFIX_KEYS or low.endswith(("_auth", "_key"))


def _redact_config_value(val: Any, key: Optional[str] = None) -> Any:
    if key is not None and _is_sensitive_config_key(str(key)):
        return None if val is None else "[REDACTED]"
    if isinstance(val, dict):
        return {k: _redact_config_value(v, key=str(k)) for k, v in val.items()}
    if isinstance(val, list):
        return [_redact_config_value(item) for item in val]
    if isinstance(val, tuple):
        return tuple(_redact_config_value(item) for item in val)
    if isinstance(val, str):
        key_low = str(key or "").lower()
        val_low = val.lower()
        if (
            key_low == "dsn"
            or key_low.endswith(("_url", "_dsn"))
            or key_low in ("url", "uri", "endpoint", "minio_endpoint", "s3_endpoint", "neo4j_uri")
            or _URL_CRED_RE.search(val)
            or "password=" in val_low
        ):
            masked = _URL_CRED_RE.sub(r"\1[REDACTED]\3", val)
            if key_low == "dsn" or key_low.endswith("_dsn") or "password=" in masked.lower():
                masked = _DSN_PASS_RE.sub(r"\1[REDACTED]", masked)
            return masked
    return val


def redact_plugin_config(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a deep copy of plugin configuration with secrets and URL/DSN credentials redacted."""
    if not isinstance(cfg, dict):
        return {}
    return {k: _redact_config_value(v, key=str(k)) for k, v in cfg.items()}


class PluginRegistry:
    """Central microkernel registry managing lifecycle, discovery, config, and resolution."""

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        use_core_db: bool = False,
    ) -> None:
        self._plugins: Dict[str, BasePlugin] = {}
        self._enabled: Dict[str, bool] = {}
        self._project_overrides: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._plugin_dirs: List[Path] = []
        self._dir_plugins: Dict[str, List[str]] = {}
        self._dir_modules: Dict[str, List[str]] = {}
        self._global_config_overrides: Dict[str, Dict[str, Any]] = {}
        self._global_priority_overrides: Dict[str, int] = {}
        self._db_path: Optional[str] = str(db_path) if db_path is not None else None
        self._use_core_db: bool = use_core_db
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._override_context: bool = False
        self._registration_log: Optional[List[str]] = None

        if self._db_path or self._use_core_db:
            self.ensure_tables()
            self.load_project_configs_from_db()

    # ------------------------------------------------------------------
    # Database persistence for plugin_project_configs (SQLite / PG ANSI)
    # ------------------------------------------------------------------
    def _get_sqlite_conn(self) -> Optional[sqlite3.Connection]:
        if not self._db_path:
            return None
        if self._sqlite_conn is None:
            if self._db_path != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._sqlite_conn.row_factory = sqlite3.Row
        return self._sqlite_conn

    def ensure_tables(self) -> None:
        ddl = (
            "CREATE TABLE IF NOT EXISTS plugin_project_configs ("
            "project_id TEXT NOT NULL, "
            "plugin_id TEXT NOT NULL, "
            "enabled INTEGER DEFAULT 1, "
            "priority INTEGER, "
            "config_json TEXT DEFAULT '{}', "
            "updated_at BIGINT, "
            "PRIMARY KEY (project_id, plugin_id))"
        )
        if self._db_path:
            conn = self._get_sqlite_conn()
            if conn is not None:
                with conn:
                    conn.execute(ddl)
        elif self._use_core_db:
            from ..core.db import db

            if db.conn is None:
                db.connect()
            db.execute(ddl)
            db.commit()

    def _persist_project_override(self, project_id: str, plugin_id: str) -> None:
        if not self._db_path and not self._use_core_db:
            return
        entry = self._project_overrides.get(project_id, {}).get(plugin_id, {})
        enabled_val = 1 if entry.get("enabled", True) else 0
        priority_val = entry.get("priority")
        config_json = json.dumps(entry.get("config", {}))
        now_ms = int(time.time() * 1000)

        sql = (
            "INSERT INTO plugin_project_configs "
            "(project_id, plugin_id, enabled, priority, config_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (project_id, plugin_id) DO UPDATE SET "
            "enabled = excluded.enabled, "
            "priority = excluded.priority, "
            "config_json = excluded.config_json, "
            "updated_at = excluded.updated_at"
        )
        params = (project_id, plugin_id, enabled_val, priority_val, config_json, now_ms)

        if self._db_path:
            conn = self._get_sqlite_conn()
            if conn is not None:
                with conn:
                    conn.execute(sql, params)
        elif self._use_core_db:
            from ..core.db import db

            db.execute(sql, params)
            db.commit()

    def load_project_configs_from_db(self, project_id: Optional[str] = None) -> int:
        if not self._db_path and not self._use_core_db:
            return 0
        rows: List[Dict[str, Any]] = []
        if self._db_path:
            conn = self._get_sqlite_conn()
            if conn is not None:
                if project_id:
                    cur = conn.execute(
                        "SELECT project_id, plugin_id, enabled, priority, config_json "
                        "FROM plugin_project_configs WHERE project_id = ?",
                        (project_id,),
                    )
                else:
                    cur = conn.execute(
                        "SELECT project_id, plugin_id, enabled, priority, config_json "
                        "FROM plugin_project_configs"
                    )
                rows = [dict(r) for r in cur.fetchall()]
        elif self._use_core_db:
            from ..core.db import db

            if project_id:
                rows = db.fetchall(
                    "SELECT project_id, plugin_id, enabled, priority, config_json "
                    "FROM plugin_project_configs WHERE project_id = ?",
                    (project_id,),
                )
            else:
                rows = db.fetchall(
                    "SELECT project_id, plugin_id, enabled, priority, config_json "
                    "FROM plugin_project_configs"
                )

        loaded = 0
        for row in rows:
            pid = row["project_id"]
            plg_id = row["plugin_id"]
            try:
                cfg = json.loads(row.get("config_json") or "{}")
            except Exception:
                cfg = {}
            proj_bucket = self._project_overrides.setdefault(pid, {})
            proj_bucket[plg_id] = {
                "enabled": bool(row.get("enabled", 1)),
                "priority": row.get("priority"),
                "config": cfg if isinstance(cfg, dict) else {},
            }
            loaded += 1
        return loaded

    def close(self) -> None:
        if self._sqlite_conn is not None:
            try:
                self._sqlite_conn.close()
            finally:
                self._sqlite_conn = None

    # ------------------------------------------------------------------
    # Registration & Lifecycle
    # ------------------------------------------------------------------
    def _env_requirements_met(self, requires_env: List[str]) -> bool:
        for var in requires_env or []:
            if not os.getenv(var):
                return False
        return True

    def register(self, plugin: BasePlugin, override: Optional[bool] = None) -> None:
        if not isinstance(plugin, BasePlugin):
            raise TypeError(f"Expected BasePlugin instance, got {type(plugin).__name__}")
        meta = getattr(plugin, "metadata", None)
        if not isinstance(meta, PluginMetadata):
            raise ValueError(f"Plugin {type(plugin).__name__} must define a valid PluginMetadata")
        if not meta.plugin_id or not meta.category:
            raise ValueError("PluginMetadata requires non-empty plugin_id and category")

        effective_override = self._override_context if override is None else override
        pid = meta.plugin_id
        if pid in self._plugins:
            if not effective_override:
                raise ValueError(f"Plugin '{pid}' is already registered")
            try:
                self._plugins[pid].shutdown()
            except Exception:
                logger.debug("Error shutting down replaced plugin %s", pid, exc_info=True)

        # Apply any pre-loaded global config overrides
        merged_cfg: Dict[str, Any] = dict(plugin.config or {})
        if pid in self._global_config_overrides:
            merged_cfg.update(self._global_config_overrides[pid])

        validated_cfg = plugin.validate_config(merged_cfg)
        plugin.on_load(validated_cfg)

        self._plugins[pid] = plugin

        # Determine initial enabled state if not already explicitly toggled via config
        if pid not in self._enabled:
            env_ok = self._env_requirements_met(meta.requires_env)
            self._enabled[pid] = bool(meta.default_enabled and env_ok)

        if self._registration_log is not None and pid not in self._registration_log:
            self._registration_log.append(pid)

    def unregister(self, plugin_id: str) -> bool:
        plugin = self._plugins.pop(plugin_id, None)
        if plugin is None:
            return False
        try:
            plugin.shutdown()
        except Exception:
            logger.debug("Error shutting down plugin %s", plugin_id, exc_info=True)
        self._enabled.pop(plugin_id, None)
        return True

    def get(self, plugin_id: str) -> Optional[BasePlugin]:
        return self._plugins.get(plugin_id)

    def list_plugins(
        self,
        category: Optional[Union[str, PluginCategory]] = None,
        project_id: Optional[str] = None,
        include_disabled: bool = True,
    ) -> List[Dict[str, Any]]:
        cat_filter = _normalize_category(category) if category is not None else None
        items: List[Dict[str, Any]] = []
        for pid, plugin in self._plugins.items():
            meta = plugin.metadata
            if cat_filter and _normalize_category(meta.category) != cat_filter:
                continue
            enabled = self.is_enabled(pid, project_id=project_id)
            if not include_disabled and not enabled:
                continue
            items.append(
                {
                    "plugin_id": pid,
                    "name": meta.name,
                    "version": meta.version,
                    "category": _normalize_category(meta.category),
                    "description": meta.description,
                    "author": meta.author,
                    "priority": self.get_effective_priority(pid, project_id=project_id),
                    "enabled": enabled,
                    "capabilities": list(meta.capabilities),
                    "requires_env": list(meta.requires_env),
                    "config": redact_plugin_config(
                        self.get_effective_config(pid, project_id=project_id)
                    ),
                }
            )
        items.sort(key=lambda x: (-int(x["priority"]), x["plugin_id"]))
        return items

    # ------------------------------------------------------------------
    # 3-Tier Discovery
    # ------------------------------------------------------------------
    def _register_from_module(
        self,
        module: Any,
        manifest: Optional[PluginManifest] = None,
        override: bool = False,
    ) -> List[str]:
        registered: List[str] = []

        # 1. Explicit register_plugins(registry) hook
        reg_fn = getattr(module, "register_plugins", None)
        if callable(reg_fn):
            prev_override = self._override_context
            prev_log = self._registration_log
            log: List[str] = []
            self._override_context = override
            self._registration_log = log
            try:
                reg_fn(self)
            finally:
                self._override_context = prev_override
                self._registration_log = prev_log
            return log

        # 2. Explicit manifest.class_name
        if manifest and manifest.class_name:
            cls = getattr(module, manifest.class_name, None)
            if cls is None or not (isinstance(cls, type) and issubclass(cls, BasePlugin)):
                raise ValueError(
                    f"Manifest class_name '{manifest.class_name}' not found or not a BasePlugin in {module.__name__}"
                )
            instance = cls(config=dict(manifest.default_config))
            self._apply_manifest_metadata(instance, manifest)
            self.register(instance, override=override)
            registered.append(instance.metadata.plugin_id)
            return registered

        # 3. Module-level get_builtin_plugins() factory
        factory = getattr(module, "get_builtin_plugins", None)
        if callable(factory):
            for inst in factory():
                if isinstance(inst, BasePlugin):
                    pid = inst.metadata.plugin_id
                    if pid not in self._plugins or override:
                        self.register(inst, override=override)
                        registered.append(pid)
            return registered

        # 4. Scan concrete BasePlugin subclasses defined in this module
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if not issubclass(obj, BasePlugin) or inspect.isabstract(obj):
                continue
            meta = getattr(obj, "metadata", None)
            if meta is None and manifest is None:
                continue
            try:
                cfg = dict(manifest.default_config) if manifest else {}
                instance = obj(config=cfg)
            except TypeError:
                instance = obj()
            if manifest:
                self._apply_manifest_metadata(instance, manifest)
            pid = instance.metadata.plugin_id
            if pid not in self._plugins or override:
                self.register(instance, override=override)
                registered.append(pid)

        return registered

    @staticmethod
    def _apply_manifest_metadata(instance: BasePlugin, manifest: PluginManifest) -> None:
        if getattr(instance, "metadata", None) is None:
            instance.metadata = PluginMetadata(
                plugin_id=manifest.plugin_id,
                name=manifest.name,
                version=manifest.version,
                category=manifest.category,
                description=manifest.description,
                priority=manifest.priority,
                requires_env=list(manifest.requires_env),
                default_enabled=manifest.default_enabled,
                entrypoint=manifest.entrypoint,
            )
        else:
            meta = instance.metadata
            if manifest.priority is not None:
                meta.priority = manifest.priority
            if manifest.requires_env:
                meta.requires_env = list(manifest.requires_env)
            meta.default_enabled = manifest.default_enabled

    def discover_builtins(self, override: bool = False) -> int:
        count = 0
        for submod in BUILTIN_SUBMODULES:
            full_name = f"projectbrain.plugins.builtins.{submod}"
            try:
                mod = importlib.import_module(full_name)
            except ImportError as exc:
                logger.debug("Skipping unavailable builtin module %s: %s", full_name, exc)
                continue
            except Exception as exc:
                logger.warning("Skipping broken builtin module %s: %s", full_name, exc)
                continue
            loaded = self._register_from_module(mod, override=override)
            count += len(loaded)
        return count

    def _load_module_from_file(self, file_path: Path, module_name: str) -> Any:
        importlib.invalidate_caches()
        unique_name = f"{module_name}_{time.time_ns()}"
        spec = importlib.util.spec_from_file_location(unique_name, str(file_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load module spec from {file_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        try:
            source = file_path.read_text(encoding="utf-8")
            exec(compile(source, str(file_path), "exec"), module.__dict__)
        except Exception:
            sys.modules.pop(unique_name, None)
            raise
        return module

    def discover_directory(
        self,
        directory: Union[str, Path],
        override: bool = True,
    ) -> List[str]:
        base_dir = Path(directory).expanduser().resolve()
        if not base_dir.exists() or not base_dir.is_dir():
            return []

        if base_dir not in self._plugin_dirs:
            self._plugin_dirs.append(base_dir)

        dir_key = str(base_dir)
        loaded_ids: List[str] = []
        loaded_mods: List[str] = self._dir_modules.setdefault(dir_key, [])

        for entry in sorted(base_dir.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue

            if entry.is_dir():
                resolved_entry = entry.resolve()
                try:
                    resolved_entry.relative_to(base_dir)
                except ValueError as exc:
                    raise ValueError(
                        f"Unsafe plugin directory symlink outside plugins directory: {entry}"
                    ) from exc

                manifest_path = None
                for candidate in ("plugin.json", "plugin.yaml", "plugin.yml"):
                    cand = resolved_entry / candidate
                    if cand.is_file():
                        manifest_path = cand
                        break

                manifest: Optional[PluginManifest] = None
                if manifest_path is not None:
                    resolved_manifest = manifest_path.resolve()
                    try:
                        resolved_manifest.relative_to(resolved_entry)
                    except ValueError as exc:
                        raise ValueError(
                            f"Unsafe plugin manifest outside plugin directory: {manifest_path}"
                        ) from exc
                    raw_text = resolved_manifest.read_text(encoding="utf-8")
                    if manifest_path.suffix == ".json":
                        raw_data = json.loads(raw_text)
                    else:
                        raw_data = yaml.safe_load(raw_text) or {}
                    manifest = PluginManifest(**raw_data)
                    entrypoint_rel = manifest.entrypoint
                else:
                    entrypoint_rel = "plugin.py"

                py_path = (resolved_entry / entrypoint_rel).resolve()
                try:
                    py_path.relative_to(resolved_entry)
                except ValueError as exc:
                    raise ValueError(
                        f"Unsafe plugin manifest entrypoint outside plugin directory: {entrypoint_rel}"
                    ) from exc

                if py_path == resolved_entry or not py_path.is_file():
                    continue

                mod_name = f"pb_dir_plugin_{abs(hash(str(py_path)))}_{entry.name}"
                module = self._load_module_from_file(py_path, mod_name)
                loaded_mods.append(module.__name__)
                ids = self._register_from_module(module, manifest=manifest, override=override)
                loaded_ids.extend(ids)

            elif entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                py_path = entry.resolve()
                try:
                    py_path.relative_to(base_dir)
                except ValueError as exc:
                    raise ValueError(
                        f"Unsafe plugin file symlink outside plugin directory: {entry}"
                    ) from exc

                mod_name = f"pb_dir_plugin_{abs(hash(str(py_path)))}_{entry.stem}"
                module = self._load_module_from_file(py_path, mod_name)
                loaded_mods.append(module.__name__)
                ids = self._register_from_module(module, manifest=None, override=override)
                loaded_ids.extend(ids)

        existing_ids = self._dir_plugins.setdefault(dir_key, [])
        for pid in loaded_ids:
            if pid not in existing_ids:
                existing_ids.append(pid)

        return loaded_ids

    def reload_directory(self, directory: Union[str, Path]) -> List[str]:
        base_dir = Path(directory).expanduser().resolve()
        dir_key = str(base_dir)

        for pid in list(self._dir_plugins.get(dir_key, [])):
            self.unregister(pid)
        self._dir_plugins[dir_key] = []

        for mod_name in list(self._dir_modules.get(dir_key, [])):
            sys.modules.pop(mod_name, None)
        self._dir_modules[dir_key] = []

        return self.discover_directory(base_dir, override=True)

    def discover_entrypoints(
        self,
        group: str = "projectbrain.plugins",
        override: bool = False,
    ) -> List[str]:
        eps_all = importlib.metadata.entry_points()
        if hasattr(eps_all, "select"):
            selected = list(eps_all.select(group=group))
        elif isinstance(eps_all, dict):
            selected = list(eps_all.get(group, []))
        else:
            selected = [ep for ep in eps_all if getattr(ep, "group", None) == group]

        loaded_ids: List[str] = []
        for ep in selected:
            try:
                obj = ep.load()
            except Exception as exc:
                logger.warning("Failed to load entrypoint '%s': %s", getattr(ep, "name", ep), exc)
                continue

            if isinstance(obj, BasePlugin):
                self.register(obj, override=override)
                loaded_ids.append(obj.metadata.plugin_id)
            elif isinstance(obj, type) and issubclass(obj, BasePlugin):
                inst = obj()
                self.register(inst, override=override)
                loaded_ids.append(inst.metadata.plugin_id)
            elif callable(obj):
                before = set(self._plugins.keys())
                result = obj(self)
                if isinstance(result, BasePlugin):
                    self.register(result, override=override)
                    loaded_ids.append(result.metadata.plugin_id)
                elif isinstance(result, (list, tuple)):
                    for item in result:
                        if isinstance(item, BasePlugin):
                            self.register(item, override=override)
                            loaded_ids.append(item.metadata.plugin_id)
                else:
                    after = set(self._plugins.keys())
                    loaded_ids.extend(sorted(after - before))

        return loaded_ids

    # ------------------------------------------------------------------
    # Enable / Disable / Configure / Overrides
    # ------------------------------------------------------------------
    def enable(self, plugin_id: str, project_id: Optional[str] = None) -> None:
        if project_id:
            proj = self._project_overrides.setdefault(project_id, {})
            entry = proj.setdefault(plugin_id, {"enabled": True, "priority": None, "config": {}})
            entry["enabled"] = True
            self._persist_project_override(project_id, plugin_id)
        else:
            self._enabled[plugin_id] = True

    def disable(self, plugin_id: str, project_id: Optional[str] = None) -> None:
        if project_id:
            proj = self._project_overrides.setdefault(project_id, {})
            entry = proj.setdefault(plugin_id, {"enabled": False, "priority": None, "config": {}})
            entry["enabled"] = False
            self._persist_project_override(project_id, plugin_id)
        else:
            self._enabled[plugin_id] = False

    def configure(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        project_id: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> Dict[str, Any]:
        plugin = self._plugins.get(plugin_id)
        validated = plugin.validate_config(config) if plugin is not None else dict(config)

        if project_id:
            proj = self._project_overrides.setdefault(project_id, {})
            entry = proj.setdefault(
                plugin_id,
                {
                    "enabled": self._enabled.get(plugin_id, True),
                    "priority": None,
                    "config": {},
                },
            )
            entry["config"] = {**entry.get("config", {}), **validated}
            if priority is not None:
                entry["priority"] = int(priority)
            self._persist_project_override(project_id, plugin_id)
            return dict(entry["config"])

        merged = {**self._global_config_overrides.get(plugin_id, {}), **validated}
        self._global_config_overrides[plugin_id] = merged
        if priority is not None:
            self._global_priority_overrides[plugin_id] = int(priority)
        if plugin is not None:
            plugin.on_load(merged)
        return dict(merged)

    def get_effective_priority(self, plugin_id: str, project_id: Optional[str] = None) -> int:
        if project_id:
            proj_entry = self._project_overrides.get(project_id, {}).get(plugin_id)
            if proj_entry and proj_entry.get("priority") is not None:
                return int(proj_entry["priority"])
        if plugin_id in self._global_priority_overrides:
            return int(self._global_priority_overrides[plugin_id])
        plugin = self._plugins.get(plugin_id)
        return int(plugin.metadata.priority) if plugin is not None else 0

    def get_effective_config(
        self, plugin_id: str, project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        plugin = self._plugins.get(plugin_id)
        cfg: Dict[str, Any] = dict(plugin.config) if plugin is not None else {}
        if plugin_id in self._global_config_overrides:
            cfg.update(self._global_config_overrides[plugin_id])
        if project_id:
            proj_entry = self._project_overrides.get(project_id, {}).get(plugin_id)
            if proj_entry and isinstance(proj_entry.get("config"), dict):
                cfg.update(proj_entry["config"])
        return cfg

    def is_enabled(self, plugin_id: str, project_id: Optional[str] = None) -> bool:
        if plugin_id not in self._plugins:
            return False

        # 1. Per-project override
        if project_id:
            proj_entry = self._project_overrides.get(project_id, {}).get(plugin_id)
            if proj_entry is not None and "enabled" in proj_entry:
                return bool(proj_entry["enabled"])

        # 2. Environment variable overrides
        disabled_env = _parse_csv_env("PB_DISABLED_PLUGINS") | _parse_csv_env("PB_PLUGINS_DISABLED")
        if plugin_id in disabled_env:
            return False
        enabled_env = _parse_csv_env("PB_ENABLED_PLUGINS") | _parse_csv_env("PB_PLUGINS_ENABLED")
        if plugin_id in enabled_env:
            return True

        # 3. Global toggle
        if plugin_id in self._enabled:
            return bool(self._enabled[plugin_id])

        # 4. Metadata default + requires_env
        meta = self._plugins[plugin_id].metadata
        return bool(meta.default_enabled and self._env_requirements_met(meta.requires_env))

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def resolve_all(
        self,
        category: Union[str, PluginCategory],
        project_id: Optional[str] = None,
    ) -> List[BasePlugin]:
        target_cat = _normalize_category(category)
        candidates: List[Tuple[int, str, BasePlugin]] = []
        for pid, plugin in self._plugins.items():
            if _normalize_category(plugin.metadata.category) != target_cat:
                continue
            if not self.is_enabled(pid, project_id=project_id):
                continue
            prio = self.get_effective_priority(pid, project_id=project_id)
            candidates.append((prio, pid, plugin))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in candidates]

    def resolve(
        self,
        category: Union[str, PluginCategory],
        preferred_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> BasePlugin:
        target_cat = _normalize_category(category)
        if preferred_id:
            plugin = self._plugins.get(preferred_id)
            if (
                plugin is not None
                and _normalize_category(plugin.metadata.category) == target_cat
                and self.is_enabled(preferred_id, project_id=project_id)
            ):
                return plugin

        candidates = self.resolve_all(target_cat, project_id=project_id)
        if not candidates:
            raise LookupError(
                f"No enabled plugin found for category '{target_cat}'"
                + (f" (project='{project_id}')" if project_id else "")
            )
        return candidates[0]

    # ------------------------------------------------------------------
    # Config File Loading (TOML / YAML / JSON)
    # ------------------------------------------------------------------
    def load_config_file(self, path: Union[str, Path]) -> None:
        cfg_path = Path(path).expanduser()
        if not cfg_path.is_file():
            raise FileNotFoundError(f"Plugin config file not found: {cfg_path}")

        suffix = cfg_path.suffix.lower()
        if suffix == ".toml":
            if tomllib is None:
                raise RuntimeError("TOML parsing requires Python 3.11+ (tomllib) or tomli")
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
            section = raw.get("plugins", raw)
        elif suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            section = raw.get("plugins", raw) if isinstance(raw, dict) else {}
        elif suffix == ".json":
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            section = raw.get("plugins", raw) if isinstance(raw, dict) else {}
        else:
            raise ValueError(f"Unsupported plugin config format: {suffix}")

        if not isinstance(section, dict):
            return

        for d in section.get("directories", []) or []:
            self.discover_directory(d)

        for pid in section.get("enabled", []) or []:
            self.enable(str(pid))

        for pid in section.get("disabled", []) or []:
            self.disable(str(pid))

        # Per-project overrides block: projects: {proj_a: {plugin_id: {...}}}
        projects_block = section.get("projects", {})
        if isinstance(projects_block, dict):
            for proj_id, proj_plugins in projects_block.items():
                if not isinstance(proj_plugins, dict):
                    continue
                for pid, spec in proj_plugins.items():
                    if not isinstance(spec, dict):
                        continue
                    if "enabled" in spec:
                        if spec["enabled"]:
                            self.enable(pid, project_id=proj_id)
                        else:
                            self.disable(pid, project_id=proj_id)
                    cfg_dict = spec.get("config")
                    prio = spec.get("priority")
                    if cfg_dict is not None or prio is not None:
                        self.configure(
                            pid,
                            cfg_dict if isinstance(cfg_dict, dict) else {},
                            project_id=proj_id,
                            priority=prio,
                        )

        # Direct per-plugin config tables inside [plugins]
        reserved = {"directories", "enabled", "disabled", "projects"}
        for key, val in section.items():
            if key in reserved or not isinstance(val, dict):
                continue
            pid = str(key)
            if "enabled" in val:
                if val["enabled"]:
                    self.enable(pid)
                else:
                    self.disable(pid)
            prio = val.get("priority")
            cfg_dict = val.get("config")
            if cfg_dict is None:
                cfg_dict = {k: v for k, v in val.items() if k not in ("enabled", "priority")}
            if cfg_dict or prio is not None:
                self.configure(pid, cfg_dict, priority=prio)

    # ------------------------------------------------------------------
    # Health Reporting
    # ------------------------------------------------------------------
    def health_report(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        plugins_health: Dict[str, Any] = {}
        by_category: Dict[str, int] = {}
        enabled_count = 0
        overall = "healthy"

        for pid, plugin in sorted(self._plugins.items()):
            enabled = self.is_enabled(pid, project_id=project_id)
            cat = _normalize_category(plugin.metadata.category)
            by_category[cat] = by_category.get(cat, 0) + 1
            if enabled:
                enabled_count += 1
            try:
                hc = plugin.health_check()
            except Exception as exc:
                hc = {
                    "plugin_id": pid,
                    "category": cat,
                    "status": "unhealthy",
                    "error": str(exc),
                }
            hc = redact_plugin_config(dict(hc))
            hc["enabled"] = enabled
            hc["priority"] = self.get_effective_priority(pid, project_id=project_id)
            if enabled and hc.get("status") != "healthy":
                overall = "degraded"
            plugins_health[pid] = hc

        return {
            "status": overall,
            "project_id": project_id,
            "total_plugins": len(self._plugins),
            "enabled_plugins": enabled_count,
            "by_category": by_category,
            "plugins": plugins_health,
        }


# ---------------------------------------------------------------------------
# Lazy Singleton Default Registry
# ---------------------------------------------------------------------------
_DEFAULT_REGISTRY: Optional[PluginRegistry] = None


def _get_runtime_env() -> Optional[Any]:
    try:
        from ..core import config as core_config

        getter = getattr(core_config, "get_env", None)
        if callable(getter):
            return getter()
        return getattr(core_config, "env", None)
    except Exception:
        return None


def get_default_registry(auto_discover: bool = True) -> PluginRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        cfg_env = _get_runtime_env()
        env_db_path = (os.getenv("PB_DB_PATH") or os.getenv("OM_DB_PATH") or "").strip() or None
        reg = PluginRegistry(db_path=env_db_path)
        if auto_discover:
            reg.discover_builtins()

            discovered_dirs: List[Path] = []
            env_dir = (
                (os.getenv("PB_PLUGINS_DIR") or os.getenv("OM_PLUGINS_DIR") or "").strip()
                or (getattr(cfg_env, "plugins_dir", None) if cfg_env is not None else None)
            )
            if env_dir:
                p_dir = Path(str(env_dir)).expanduser()
                if p_dir.is_dir():
                    reg.discover_directory(p_dir)
                    discovered_dirs.append(p_dir.resolve())

            user_plugins_dir = Path("~/.projectbrain/plugins").expanduser()
            if user_plugins_dir.is_dir() and user_plugins_dir.resolve() not in discovered_dirs:
                reg.discover_directory(user_plugins_dir)

            try:
                reg.discover_entrypoints()
            except Exception:
                logger.debug("Entrypoint discovery skipped", exc_info=True)

            cfg_path = (
                (
                    os.getenv("PB_PLUGINS_CONFIG_PATH")
                    or os.getenv("PB_PLUGINS_CONFIG")
                    or os.getenv("OM_PLUGINS_CONFIG_PATH")
                    or ""
                ).strip()
                or (getattr(cfg_env, "plugins_config_path", None) if cfg_env is not None else None)
            )
            if cfg_path:
                p_cfg = Path(str(cfg_path)).expanduser()
                if p_cfg.is_file():
                    reg.load_config_file(p_cfg)
                else:
                    logger.warning("Configured plugins_config_path not found: %s", p_cfg)

            if cfg_env is not None:
                for pid in getattr(cfg_env, "enabled_plugins", None) or []:
                    if str(pid).strip():
                        reg.enable(str(pid).strip())
                for pid in getattr(cfg_env, "disabled_plugins", None) or []:
                    if str(pid).strip():
                        reg.disable(str(pid).strip())

            if env_db_path:
                reg.load_project_configs_from_db()

        _DEFAULT_REGISTRY = reg
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is not None:
        for pid in list(_DEFAULT_REGISTRY._plugins.keys()):
            _DEFAULT_REGISTRY.unregister(pid)
        _DEFAULT_REGISTRY.close()
        _DEFAULT_REGISTRY = None


__all__ = [
    "BUILTIN_SUBMODULES",
    "PluginRegistry",
    "get_default_registry",
    "redact_plugin_config",
    "reset_default_registry",
]
