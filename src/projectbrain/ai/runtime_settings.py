"""Runtime AI & LLM Provider Settings, Multi-Provider Dispatcher, and Zero-Key Local RAG Engine.

Persists runtime AI configuration in Master DB `auth_settings`, synchronizes updates live to
`env.*` and `os.environ` without server restart, dispatches across Gemini / OpenAI / MiniMax /
Local OpenAI-compatible (vLLM, LM Studio, llama.cpp) / Ollama, and provides deterministic
zero-key offline extractive RAG synthesis over HSG cognitive memories + Graphify AST symbols.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.config import env
from ..core.db import db
from ..graphify_engine.store import get_graph_store
from .gemini import GeminiAdapter
from .minimax import MiniMaxAdapter
from .ollama import OllamaAdapter
from .openai import OpenAIAdapter

logger = logging.getLogger("ai.runtime_settings")

VALID_AI_PROVIDERS: Tuple[str, ...] = (
    "auto",
    "local_rag",
    "gemini",
    "openai",
    "ollama",
    "minimax",
    "local_ai",
)

VALID_EMB_KINDS: Tuple[str, ...] = (
    "synthetic",
    "gemini",
    "openai",
    "ollama",
    "minimax",
    "local_gpu",
    "sentence_transformers",
    "aws",
    "vllm",
    "lmstudio",
    "llamacpp",
)

_LEGACY_UNCONFIGURED_GEMINI_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OLLAMA_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-Text-01"
LOCAL_RAG_MODEL_ID = "extractive-rag-v1"
LOCAL_RAG_LABEL = "Built-in Local RAG (Offline · Zero Key)"

_QUERY_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "what",
        "where",
        "when",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "does",
        "did",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "can",
        "could",
        "should",
        "would",
        "will",
        "into",
        "about",
        "across",
        "between",
        "show",
        "tell",
        "find",
        "explain",
        "describe",
        "project",
    }
)


def _parse_bool(raw: Any, default: bool = True) -> bool:
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


def _clean_secret(val: Optional[Any]) -> str:
    if val is None:
        return ""
    s = str(val).strip().strip('"').strip("'").strip()
    return s


def mask_secret(val: Optional[str]) -> str:
    """Mask middle characters of an API secret; never expose raw secret values."""
    cleaned = _clean_secret(val)
    if not cleaned:
        return ""
    if len(cleaned) > 8:
        return f"{cleaned[:4]}••••{cleaned[-4:]}"
    return "••••••••"


def sanitize_secret(msg: Any, *extra_secrets: Optional[str]) -> str:
    """Redact API keys, bearer tokens, and configured secrets from error/diagnostic strings."""
    text = str(msg or "")
    if not text:
        return ""
    sanitized = re.sub(r"([?&]key=)[^&\s\"']+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    sanitized = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bAIza[0-9A-Za-z\-_]{12,}\b", "[REDACTED]", sanitized)
    sanitized = re.sub(r"\bsk-[0-9A-Za-z\-_]{12,}\b", "[REDACTED]", sanitized)

    candidates: List[str] = []
    for item in extra_secrets:
        c = _clean_secret(item)
        if c and c != "local-no-key" and len(c) >= 4:
            candidates.append(c)

    for env_attr in ("gemini_key", "openai_key", "minimax_key", "local_ai_api_key"):
        c = _clean_secret(getattr(env, env_attr, None))
        if c and c != "local-no-key" and len(c) >= 4:
            candidates.append(c)

    for env_name in (
        "GEMINI_API_KEY",
        "PB_GEMINI_KEY",
        "OM_GEMINI_KEY",
        "OPENAI_API_KEY",
        "PB_OPENAI_API_KEY",
        "OM_OPENAI_API_KEY",
        "MINIMAX_API_KEY",
        "PB_MINIMAX_API_KEY",
        "OM_MINIMAX_API_KEY",
        "PB_LOCAL_AI_API_KEY",
    ):
        c = _clean_secret(os.getenv(env_name))
        if c and c != "local-no-key" and len(c) >= 4:
            candidates.append(c)

    for secret_val in sorted(set(candidates), key=len, reverse=True):
        sanitized = sanitized.replace(secret_val, "[REDACTED]")
    return sanitized


def _read_db_ai_settings() -> Dict[str, str]:
    """Read persisted `ai.*` keys from Master DB `auth_settings`."""
    try:
        from ..server.routes import auth

        auth.ensure_auth_schema()
        rows = db.fetchall(
            "SELECT setting_key, setting_value FROM auth_settings WHERE setting_key LIKE 'ai.%'"
        )
        return {
            str(r["setting_key"]): str(r["setting_value"])
            for r in rows
            if r.get("setting_key") is not None and r.get("setting_value") is not None
        }
    except Exception as exc:
        logger.debug(f"[AI-SETTINGS] Failed reading auth_settings: {exc}")
        return {}


def _upsert_db_ai_settings(
    updates: Dict[str, str],
    keys_to_delete: Sequence[str] = (),
) -> None:
    """Persist `ai.*` settings into Master DB `auth_settings` (SQLite & PostgreSQL compatible)."""
    from ..server.routes import auth

    auth.ensure_auth_schema()
    now = int(time.time())
    for key in keys_to_delete:
        db.execute("DELETE FROM auth_settings WHERE setting_key = ?", (key,))
    for key, val in updates.items():
        db.execute(
            """
            INSERT INTO auth_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (key, val, now),
        )
    if updates or keys_to_delete:
        db.commit()


def _resolve_default_gemini_model(db_map: Dict[str, str]) -> str:
    if "ai.gemini_model" in db_map and db_map["ai.gemini_model"].strip():
        return db_map["ai.gemini_model"].strip()

    env_override = os.getenv("PB_GEMINI_MODEL") or os.getenv("OM_GEMINI_MODEL")
    if env_override and env_override.strip():
        return env_override.strip()

    toml_ai = (getattr(env, "_toml", None) or {}).get("ai", {})
    if isinstance(toml_ai, dict) and toml_ai.get("gemini_model"):
        return str(toml_ai["gemini_model"]).strip()

    runtime_val = str(getattr(env, "gemini_model", "") or "").strip()
    if runtime_val and runtime_val != _LEGACY_UNCONFIGURED_GEMINI_MODEL:
        return runtime_val

    return DEFAULT_GEMINI_MODEL


def get_effective_ai_settings(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve effective AI settings combining DB overrides, os.environ / env.*, and optional transient overrides."""
    db_map = _read_db_ai_settings()

    if "ai.provider" in db_map and db_map["ai.provider"].strip():
        raw_provider = db_map["ai.provider"].strip().lower()
    else:
        raw_provider = (
            os.getenv("PB_AI_PROVIDER")
            or getattr(env, "ai_provider", None)
            or "auto"
        )
        raw_provider = str(raw_provider).strip().lower() or "auto"
    provider = raw_provider if raw_provider in VALID_AI_PROVIDERS else "auto"

    if "ai.fallback_to_local_rag" in db_map:
        fallback_to_local_rag = _parse_bool(db_map["ai.fallback_to_local_rag"], True)
    elif os.getenv("PB_AI_FALLBACK_LOCAL_RAG") is not None:
        fallback_to_local_rag = _parse_bool(os.getenv("PB_AI_FALLBACK_LOCAL_RAG"), True)
    else:
        fallback_to_local_rag = True

    if "ai.emb_kind" in db_map and db_map["ai.emb_kind"].strip():
        emb_kind = db_map["ai.emb_kind"].strip().lower()
    else:
        emb_kind = (
            os.getenv("PB_EMBED_KIND")
            or os.getenv("OM_EMBED_KIND")
            or getattr(env, "emb_kind", None)
            or "synthetic"
        )
        emb_kind = str(emb_kind).strip().lower() or "synthetic"

    if "ai.gemini_key" in db_map:
        gemini_key = _clean_secret(db_map["ai.gemini_key"])
    else:
        gemini_key = _clean_secret(
            os.getenv("GEMINI_API_KEY")
            or os.getenv("PB_GEMINI_KEY")
            or os.getenv("OM_GEMINI_KEY")
            or getattr(env, "gemini_key", None)
        )

    gemini_model = _resolve_default_gemini_model(db_map)

    if "ai.gemini_base_url" in db_map:
        gemini_base_url = db_map["ai.gemini_base_url"].strip()
    else:
        gemini_base_url = str(
            os.getenv("PB_GEMINI_BASE_URL")
            or os.getenv("OM_GEMINI_BASE_URL")
            or getattr(env, "gemini_base_url", "")
            or ""
        ).strip()

    if "ai.openai_key" in db_map:
        openai_key = _clean_secret(db_map["ai.openai_key"])
    else:
        openai_key = _clean_secret(
            os.getenv("OPENAI_API_KEY")
            or os.getenv("PB_OPENAI_API_KEY")
            or os.getenv("OM_OPENAI_API_KEY")
            or getattr(env, "openai_key", None)
        )

    if "ai.openai_base_url" in db_map and db_map["ai.openai_base_url"].strip():
        openai_base_url = db_map["ai.openai_base_url"].strip()
    else:
        openai_base_url = (
            str(
                os.getenv("PB_OPENAI_BASE_URL")
                or os.getenv("OM_OPENAI_BASE_URL")
                or getattr(env, "openai_base_url", "")
                or DEFAULT_OPENAI_BASE_URL
            ).strip()
            or DEFAULT_OPENAI_BASE_URL
        )

    if "ai.openai_model" in db_map and db_map["ai.openai_model"].strip():
        openai_model = db_map["ai.openai_model"].strip()
    else:
        openai_model = (
            str(
                os.getenv("PB_OPENAI_MODEL")
                or os.getenv("OM_OPENAI_MODEL")
                or getattr(env, "openai_model", "")
                or DEFAULT_OPENAI_MODEL
            ).strip()
            or DEFAULT_OPENAI_MODEL
        )

    if "ai.ollama_url" in db_map and db_map["ai.ollama_url"].strip():
        ollama_url = db_map["ai.ollama_url"].strip()
    else:
        ollama_url = (
            str(
                os.getenv("OLLAMA_URL")
                or os.getenv("PB_OLLAMA_URL")
                or getattr(env, "ollama_url", "")
                or DEFAULT_OLLAMA_URL
            ).strip()
            or DEFAULT_OLLAMA_URL
        )

    if "ai.ollama_model" in db_map and db_map["ai.ollama_model"].strip():
        ollama_model = db_map["ai.ollama_model"].strip()
    else:
        ollama_model = (
            str(
                os.getenv("PB_OLLAMA_MODEL")
                or os.getenv("OM_OLLAMA_MODEL")
                or os.getenv("OLLAMA_MODEL")
                or getattr(env, "ollama_model", "")
                or DEFAULT_OLLAMA_MODEL
            ).strip()
            or DEFAULT_OLLAMA_MODEL
        )

    if "ai.ollama_vision_model" in db_map and db_map["ai.ollama_vision_model"].strip():
        ollama_vision_model = db_map["ai.ollama_vision_model"].strip()
    else:
        ollama_vision_model = (
            str(
                os.getenv("PB_OLLAMA_VISION_MODEL")
                or os.getenv("OLLAMA_VISION_MODEL")
                or getattr(env, "ollama_vision_model", "")
                or DEFAULT_OLLAMA_VISION_MODEL
            ).strip()
            or DEFAULT_OLLAMA_VISION_MODEL
        )

    if "ai.minimax_key" in db_map:
        minimax_key = _clean_secret(db_map["ai.minimax_key"])
    else:
        minimax_key = _clean_secret(
            os.getenv("MINIMAX_API_KEY")
            or os.getenv("PB_MINIMAX_API_KEY")
            or os.getenv("OM_MINIMAX_API_KEY")
            or getattr(env, "minimax_key", None)
        )

    if "ai.minimax_base_url" in db_map and db_map["ai.minimax_base_url"].strip():
        minimax_base_url = db_map["ai.minimax_base_url"].strip()
    else:
        minimax_base_url = (
            str(
                os.getenv("PB_MINIMAX_BASE_URL")
                or os.getenv("OM_MINIMAX_BASE_URL")
                or getattr(env, "minimax_base_url", "")
                or DEFAULT_MINIMAX_BASE_URL
            ).strip()
            or DEFAULT_MINIMAX_BASE_URL
        )

    if "ai.minimax_model" in db_map and db_map["ai.minimax_model"].strip():
        minimax_model = db_map["ai.minimax_model"].strip()
    else:
        minimax_model = (
            str(
                os.getenv("PB_MINIMAX_MODEL")
                or os.getenv("OM_MINIMAX_MODEL")
                or getattr(env, "minimax_model", "")
                or DEFAULT_MINIMAX_MODEL
            ).strip()
            or DEFAULT_MINIMAX_MODEL
        )

    if "ai.local_ai_base_url" in db_map:
        local_ai_base_url = db_map["ai.local_ai_base_url"].strip()
    else:
        local_ai_base_url = str(
            os.getenv("PB_LOCAL_AI_URL") or getattr(env, "local_ai_base_url", "") or ""
        ).strip()

    if "ai.local_ai_model" in db_map:
        local_ai_model = db_map["ai.local_ai_model"].strip()
    else:
        local_ai_model = str(
            os.getenv("PB_LOCAL_AI_MODEL") or getattr(env, "local_ai_model", "") or ""
        ).strip()

    if "ai.local_ai_api_key" in db_map:
        local_ai_api_key = _clean_secret(db_map["ai.local_ai_api_key"])
    else:
        raw_local_key = _clean_secret(
            os.getenv("PB_LOCAL_AI_API_KEY") or getattr(env, "local_ai_api_key", None)
        )
        local_ai_api_key = "" if raw_local_key == "local-no-key" else raw_local_key

    eff: Dict[str, Any] = {
        "provider": provider,
        "fallback_to_local_rag": fallback_to_local_rag,
        "emb_kind": emb_kind,
        "gemini_key": gemini_key,
        "gemini_model": gemini_model,
        "gemini_base_url": gemini_base_url,
        "openai_key": openai_key,
        "openai_base_url": openai_base_url,
        "openai_model": openai_model,
        "ollama_url": ollama_url,
        "ollama_model": ollama_model,
        "ollama_vision_model": ollama_vision_model,
        "minimax_key": minimax_key,
        "minimax_base_url": minimax_base_url,
        "minimax_model": minimax_model,
        "local_ai_base_url": local_ai_base_url,
        "local_ai_model": local_ai_model,
        "local_ai_api_key": local_ai_api_key,
    }

    if overrides:
        if overrides.get("provider") is not None:
            cand = str(overrides["provider"]).strip().lower()
            if cand not in VALID_AI_PROVIDERS:
                raise ValueError(
                    f"Unsupported AI provider '{overrides['provider']}'. "
                    f"Must be one of: {', '.join(VALID_AI_PROVIDERS)}"
                )
            eff["provider"] = cand

        if overrides.get("fallback_to_local_rag") is not None:
            eff["fallback_to_local_rag"] = _parse_bool(overrides["fallback_to_local_rag"], True)

        if overrides.get("emb_kind") is not None and str(overrides["emb_kind"]).strip():
            cand_emb = str(overrides["emb_kind"]).strip().lower()
            if cand_emb not in VALID_EMB_KINDS:
                raise ValueError(
                    f"Unsupported embedding provider '{overrides['emb_kind']}'. "
                    f"Must be one of: {', '.join(VALID_EMB_KINDS)}"
                )
            eff["emb_kind"] = cand_emb

        # Secret overrides: empty string preserves saved secret unless clear_* is True
        if _parse_bool(overrides.get("clear_gemini_key"), False):
            eff["gemini_key"] = ""
        elif overrides.get("gemini_key") is not None and _clean_secret(overrides.get("gemini_key")):
            eff["gemini_key"] = _clean_secret(overrides["gemini_key"])

        if _parse_bool(overrides.get("clear_openai_key"), False):
            eff["openai_key"] = ""
        elif overrides.get("openai_key") is not None and _clean_secret(overrides.get("openai_key")):
            eff["openai_key"] = _clean_secret(overrides["openai_key"])

        if _parse_bool(overrides.get("clear_minimax_key"), False):
            eff["minimax_key"] = ""
        elif overrides.get("minimax_key") is not None and _clean_secret(overrides.get("minimax_key")):
            eff["minimax_key"] = _clean_secret(overrides["minimax_key"])

        if _parse_bool(overrides.get("clear_local_ai_key"), False):
            eff["local_ai_api_key"] = ""
        elif overrides.get("local_ai_api_key") is not None and _clean_secret(overrides.get("local_ai_api_key")):
            cleaned_lk = _clean_secret(overrides["local_ai_api_key"])
            eff["local_ai_api_key"] = "" if cleaned_lk == "local-no-key" else cleaned_lk

        for str_field, allow_empty in (
            ("gemini_model", False),
            ("gemini_base_url", True),
            ("openai_base_url", False),
            ("openai_model", False),
            ("ollama_url", False),
            ("ollama_model", False),
            ("ollama_vision_model", False),
            ("minimax_base_url", False),
            ("minimax_model", False),
            ("local_ai_base_url", True),
            ("local_ai_model", True),
        ):
            if overrides.get(str_field) is not None:
                val_s = str(overrides[str_field]).strip()
                if val_s or allow_empty:
                    eff[str_field] = val_s

    return eff


def sync_ai_settings_to_env(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Synchronize effective AI settings into `env.*` and `os.environ` without server restart."""
    eff = settings if settings is not None else get_effective_ai_settings()

    env.ai_provider = eff["provider"]
    os.environ["PB_AI_PROVIDER"] = eff["provider"]
    os.environ["PB_AI_FALLBACK_LOCAL_RAG"] = "true" if eff["fallback_to_local_rag"] else "false"

    env.emb_kind = eff["emb_kind"]
    os.environ["PB_EMBED_KIND"] = eff["emb_kind"]
    os.environ["OM_EMBED_KIND"] = eff["emb_kind"]

    env.gemini_key = eff["gemini_key"]
    if eff["gemini_key"]:
        os.environ["GEMINI_API_KEY"] = eff["gemini_key"]
    else:
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("PB_GEMINI_KEY", None)
        os.environ.pop("OM_GEMINI_KEY", None)

    env.gemini_model = eff["gemini_model"]
    os.environ["PB_GEMINI_MODEL"] = eff["gemini_model"]
    os.environ["OM_GEMINI_MODEL"] = eff["gemini_model"]

    env.gemini_base_url = eff["gemini_base_url"]
    if eff["gemini_base_url"]:
        os.environ["PB_GEMINI_BASE_URL"] = eff["gemini_base_url"]
        os.environ["OM_GEMINI_BASE_URL"] = eff["gemini_base_url"]
    else:
        os.environ.pop("PB_GEMINI_BASE_URL", None)
        os.environ.pop("OM_GEMINI_BASE_URL", None)

    env.openai_key = eff["openai_key"]
    if eff["openai_key"]:
        os.environ["OPENAI_API_KEY"] = eff["openai_key"]
    else:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("PB_OPENAI_API_KEY", None)
        os.environ.pop("OM_OPENAI_API_KEY", None)

    env.openai_base_url = eff["openai_base_url"]
    os.environ["PB_OPENAI_BASE_URL"] = eff["openai_base_url"]
    os.environ["OM_OPENAI_BASE_URL"] = eff["openai_base_url"]

    env.openai_model = eff["openai_model"]
    os.environ["PB_OPENAI_MODEL"] = eff["openai_model"]
    os.environ["OM_OPENAI_MODEL"] = eff["openai_model"]

    env.ollama_url = eff["ollama_url"]
    os.environ["OLLAMA_URL"] = eff["ollama_url"]

    env.ollama_model = eff["ollama_model"]
    os.environ["PB_OLLAMA_MODEL"] = eff["ollama_model"]
    os.environ["OLLAMA_MODEL"] = eff["ollama_model"]

    env.ollama_vision_model = eff["ollama_vision_model"]
    os.environ["PB_OLLAMA_VISION_MODEL"] = eff["ollama_vision_model"]
    os.environ["OLLAMA_VISION_MODEL"] = eff["ollama_vision_model"]

    env.minimax_key = eff["minimax_key"]
    if eff["minimax_key"]:
        os.environ["MINIMAX_API_KEY"] = eff["minimax_key"]
    else:
        os.environ.pop("MINIMAX_API_KEY", None)
        os.environ.pop("PB_MINIMAX_API_KEY", None)
        os.environ.pop("OM_MINIMAX_API_KEY", None)

    env.minimax_base_url = eff["minimax_base_url"]
    os.environ["PB_MINIMAX_BASE_URL"] = eff["minimax_base_url"]
    os.environ["OM_MINIMAX_BASE_URL"] = eff["minimax_base_url"]

    env.minimax_model = eff["minimax_model"]
    os.environ["PB_MINIMAX_MODEL"] = eff["minimax_model"]
    os.environ["OM_MINIMAX_MODEL"] = eff["minimax_model"]

    env.local_ai_base_url = eff["local_ai_base_url"]
    if eff["local_ai_base_url"]:
        os.environ["PB_LOCAL_AI_URL"] = eff["local_ai_base_url"]
    else:
        os.environ.pop("PB_LOCAL_AI_URL", None)

    env.local_ai_model = eff["local_ai_model"]
    if eff["local_ai_model"]:
        os.environ["PB_LOCAL_AI_MODEL"] = eff["local_ai_model"]
    else:
        os.environ.pop("PB_LOCAL_AI_MODEL", None)

    env.local_ai_api_key = eff["local_ai_api_key"] or "local-no-key"
    if eff["local_ai_api_key"]:
        os.environ["PB_LOCAL_AI_API_KEY"] = eff["local_ai_api_key"]
    else:
        os.environ.pop("PB_LOCAL_AI_API_KEY", None)

    return eff


def sync_ai_settings_from_db() -> None:
    """Load persisted `ai.*` settings from `auth_settings` on server startup if any exist."""
    db_map = _read_db_ai_settings()
    if not db_map:
        return
    eff = get_effective_ai_settings()
    sync_ai_settings_to_env(eff)


def update_ai_settings(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Persist runtime AI settings updates to `auth_settings` and sync live to `env` / `os.environ`.

    Returns `(updated_effective_settings, safe_audit_summary)`.
    """
    updates: Dict[str, str] = {}
    audit_fields: List[str] = []

    if payload.get("provider") is not None:
        prov = str(payload["provider"]).strip().lower()
        if prov not in VALID_AI_PROVIDERS:
            raise ValueError(
                f"Invalid provider '{payload['provider']}'. Must be one of: {', '.join(VALID_AI_PROVIDERS)}"
            )
        updates["ai.provider"] = prov
        audit_fields.append("provider")

    if payload.get("fallback_to_local_rag") is not None:
        fb = _parse_bool(payload["fallback_to_local_rag"], True)
        updates["ai.fallback_to_local_rag"] = "true" if fb else "false"
        audit_fields.append("fallback_to_local_rag")

    if payload.get("emb_kind") is not None and str(payload["emb_kind"]).strip():
        ek = str(payload["emb_kind"]).strip().lower()
        if ek not in VALID_EMB_KINDS:
            raise ValueError(
                f"Invalid emb_kind '{payload['emb_kind']}'. Must be one of: {', '.join(VALID_EMB_KINDS)}"
            )
        updates["ai.emb_kind"] = ek
        audit_fields.append("emb_kind")

    # Secret fields: preserve existing secret when empty string is sent unless clear_* is True
    secret_specs = (
        ("gemini_key", "clear_gemini_key", "ai.gemini_key"),
        ("openai_key", "clear_openai_key", "ai.openai_key"),
        ("minimax_key", "clear_minimax_key", "ai.minimax_key"),
        ("local_ai_api_key", "clear_local_ai_key", "ai.local_ai_api_key"),
    )
    for field_name, clear_flag, db_key in secret_specs:
        if _parse_bool(payload.get(clear_flag), False):
            updates[db_key] = ""
            audit_fields.append(clear_flag)
        elif payload.get(field_name) is not None:
            cleaned = _clean_secret(payload.get(field_name))
            if field_name == "local_ai_api_key" and cleaned == "local-no-key":
                cleaned = ""
            if cleaned:
                updates[db_key] = cleaned
                audit_fields.append(f"{field_name}_updated")

    non_secret_specs = (
        ("gemini_model", "ai.gemini_model", False),
        ("gemini_base_url", "ai.gemini_base_url", True),
        ("openai_base_url", "ai.openai_base_url", False),
        ("openai_model", "ai.openai_model", False),
        ("ollama_url", "ai.ollama_url", False),
        ("ollama_model", "ai.ollama_model", False),
        ("ollama_vision_model", "ai.ollama_vision_model", False),
        ("minimax_base_url", "ai.minimax_base_url", False),
        ("minimax_model", "ai.minimax_model", False),
        ("local_ai_base_url", "ai.local_ai_base_url", True),
        ("local_ai_model", "ai.local_ai_model", True),
    )
    for field_name, db_key, allow_empty in non_secret_specs:
        if payload.get(field_name) is not None:
            val_str = str(payload[field_name]).strip()
            if val_str or allow_empty:
                updates[db_key] = val_str
                audit_fields.append(field_name)

    _upsert_db_ai_settings(updates)
    eff = get_effective_ai_settings()
    sync_ai_settings_to_env(eff)

    audit_summary: Dict[str, Any] = {
        "provider": eff["provider"],
        "fallback_to_local_rag": eff["fallback_to_local_rag"],
        "emb_kind": eff["emb_kind"],
        "updated_fields": audit_fields,
    }
    return eff, audit_summary


def _pick_ollama_model(preferred: str, available_models: Sequence[str]) -> str:
    if not available_models:
        return preferred or DEFAULT_OLLAMA_MODEL
    pref = (preferred or "").strip()
    if pref:
        if pref in available_models:
            return pref
        pref_base = pref.split(":", 1)[0].lower()
        for m in available_models:
            if m.lower() == pref.lower() or m.lower().startswith(pref_base + ":") or m.lower() == pref_base:
                return m
    return str(available_models[0])


def _provider_display_label(provider_id: str, model_name: Optional[str] = None) -> str:
    if provider_id == "gemini":
        return f"Google Gemini ({model_name or DEFAULT_GEMINI_MODEL})"
    if provider_id == "openai":
        return f"OpenAI ({model_name or DEFAULT_OPENAI_MODEL})"
    if provider_id == "minimax":
        return f"MiniMax ({model_name or DEFAULT_MINIMAX_MODEL})"
    if provider_id == "local_ai":
        return f"Local OpenAI-Compatible ({model_name or 'default'})"
    if provider_id == "ollama":
        return f"Local Ollama ({model_name or DEFAULT_OLLAMA_MODEL})"
    return LOCAL_RAG_LABEL


def _resolve_active_provider_and_label(
    eff: Dict[str, Any],
    ollama_reachable: Optional[bool],
    ollama_models: Sequence[str],
) -> Tuple[str, str]:
    prov = eff["provider"]
    if prov == "local_rag":
        return "local_rag", LOCAL_RAG_LABEL
    if prov == "gemini":
        if not eff["gemini_key"] and eff["fallback_to_local_rag"]:
            return "local_rag", LOCAL_RAG_LABEL
        return "gemini", _provider_display_label("gemini", eff["gemini_model"])
    if prov == "openai":
        if not eff["openai_key"] and eff["fallback_to_local_rag"]:
            return "local_rag", LOCAL_RAG_LABEL
        return "openai", _provider_display_label("openai", eff["openai_model"])
    if prov == "minimax":
        if not eff["minimax_key"] and eff["fallback_to_local_rag"]:
            return "local_rag", LOCAL_RAG_LABEL
        return "minimax", _provider_display_label("minimax", eff["minimax_model"])
    if prov == "local_ai":
        if not eff["local_ai_base_url"] and eff["fallback_to_local_rag"]:
            return "local_rag", LOCAL_RAG_LABEL
        return "local_ai", _provider_display_label("local_ai", eff["local_ai_model"] or "default")
    if prov == "ollama":
        if ollama_reachable is False and eff["fallback_to_local_rag"]:
            return "local_rag", LOCAL_RAG_LABEL
        chosen = _pick_ollama_model(eff["ollama_model"], ollama_models) if ollama_models else eff["ollama_model"]
        return "ollama", _provider_display_label("ollama", chosen)

    # Auto mode priority chain
    if eff["gemini_key"]:
        return "gemini", _provider_display_label("gemini", eff["gemini_model"])
    if eff["openai_key"]:
        return "openai", _provider_display_label("openai", eff["openai_model"])
    if eff["minimax_key"]:
        return "minimax", _provider_display_label("minimax", eff["minimax_model"])
    if eff["local_ai_base_url"]:
        return "local_ai", _provider_display_label("local_ai", eff["local_ai_model"] or "default")
    if ollama_reachable and ollama_models:
        chosen = _pick_ollama_model(eff["ollama_model"], ollama_models)
        return "ollama", _provider_display_label("ollama", chosen)
    return "local_rag", LOCAL_RAG_LABEL


async def _probe_ollama_async(ollama_url: str, timeout: float = 0.75) -> Tuple[bool, List[str]]:
    try:
        health = await OllamaAdapter(base_url=ollama_url).check_health_and_models(timeout=timeout)
        reachable = bool(health.get("reachable"))
        models = [str(m) for m in (health.get("models") or []) if m]
        return reachable, models
    except Exception:
        return False, []


def _format_public_ai_settings(
    eff: Dict[str, Any],
    ollama_reachable: Optional[bool],
    ollama_models: List[str],
) -> Dict[str, Any]:
    resolved_provider, resolved_label = _resolve_active_provider_and_label(
        eff, ollama_reachable, ollama_models
    )
    return {
        "provider": eff["provider"],
        "resolved_provider": resolved_provider,
        "resolved_label": resolved_label,
        "fallback_to_local_rag": bool(eff["fallback_to_local_rag"]),
        "emb_kind": eff["emb_kind"],
        "has_gemini_key": bool(eff["gemini_key"]),
        "gemini_key_masked": mask_secret(eff["gemini_key"]),
        "gemini_model": eff["gemini_model"],
        "gemini_base_url": eff["gemini_base_url"],
        "has_openai_key": bool(eff["openai_key"]),
        "openai_key_masked": mask_secret(eff["openai_key"]),
        "openai_base_url": eff["openai_base_url"],
        "openai_model": eff["openai_model"],
        "ollama_url": eff["ollama_url"],
        "ollama_model": eff["ollama_model"],
        "ollama_vision_model": eff["ollama_vision_model"],
        "ollama_reachable": ollama_reachable,
        "ollama_models": ollama_models,
        "has_minimax_key": bool(eff["minimax_key"]),
        "minimax_key_masked": mask_secret(eff["minimax_key"]),
        "minimax_base_url": eff["minimax_base_url"],
        "minimax_model": eff["minimax_model"],
        "local_ai_base_url": eff["local_ai_base_url"],
        "local_ai_model": eff["local_ai_model"],
        "has_local_ai_key": bool(eff["local_ai_api_key"]),
        "local_ai_key_masked": mask_secret(eff["local_ai_api_key"]),
    }


async def get_public_ai_settings_async(probe_ollama: bool = False) -> Dict[str, Any]:
    """Async variant of `get_public_ai_settings` for non-blocking FastAPI route handlers."""
    eff = get_effective_ai_settings()
    ollama_reachable: Optional[bool] = None
    ollama_models: List[str] = []
    if probe_ollama:
        ollama_reachable, ollama_models = await _probe_ollama_async(eff["ollama_url"], timeout=0.75)
    return _format_public_ai_settings(eff, ollama_reachable, ollama_models)


def get_public_ai_settings(probe_ollama: bool = False) -> Dict[str, Any]:
    """Return masked, UI-safe AI settings payload (never exposes raw API keys)."""
    eff = get_effective_ai_settings()
    ollama_reachable: Optional[bool] = None
    ollama_models: List[str] = []
    if probe_ollama:
        try:
            asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=1) as pool:
                ollama_reachable, ollama_models = pool.submit(
                    lambda: asyncio.run(_probe_ollama_async(eff["ollama_url"], timeout=0.75))
                ).result(timeout=2.0)
        except RuntimeError:
            ollama_reachable, ollama_models = asyncio.run(
                _probe_ollama_async(eff["ollama_url"], timeout=0.75)
            )
        except Exception:
            ollama_reachable, ollama_models = False, []
    return _format_public_ai_settings(eff, ollama_reachable, ollama_models)


# ============================================================================
# Graphify Symbol Enrichment & Offline Local Extractive RAG Synthesis
# ============================================================================


def _tokenize_query_terms(query: str) -> List[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_./:-]{3,}", query or "")
    seen = set()
    terms: List[str] = []
    for tok in raw_tokens:
        low = tok.lower()
        if low in _QUERY_STOPWORDS or low in seen:
            continue
        seen.add(low)
        terms.append(low)
    return terms


def _normalize_graph_node(
    node: Dict[str, Any],
    default_source_id: Optional[str] = None,
    default_branch: Optional[str] = None,
    default_scope: Optional[str] = None,
    via_relation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    label = str(node.get("label") or node.get("name") or node.get("id") or "symbol")
    kind = str(
        node.get("kind")
        or meta.get("kind")
        or meta.get("symbol_type")
        or node.get("group")
        or node.get("file_type")
        or "symbol"
    )
    source_file = str(node.get("source_file") or node.get("file_path") or "")
    source_location = node.get("source_location")
    if not source_location and (node.get("start_line") or meta.get("start_line")):
        s_line = node.get("start_line") or meta.get("start_line")
        e_line = node.get("end_line") or meta.get("end_line")
        source_location = f"L{s_line}-L{e_line}" if e_line and e_line != s_line else f"L{s_line}"

    eff_src = (
        str(default_source_id).strip()
        if default_source_id
        else (
            str(node.get("source_id") or meta.get("source_id") or "__default__").strip()
            or "__default__"
        )
    )
    eff_scope = default_scope or (
        "project_shared"
        if eff_src == "__shared__"
        else str(node.get("scope") or meta.get("scope") or "source").strip() or "source"
    )
    eff_branch = default_branch or str(node.get("branch") or meta.get("branch") or "main").strip() or "main"

    return {
        "id": str(node.get("id") or label),
        "label": label,
        "kind": kind,
        "source_file": source_file,
        "source_location": str(source_location) if source_location else None,
        "signature": node.get("signature") or meta.get("signature"),
        "docstring": node.get("docstring") or meta.get("docstring") or meta.get("summary"),
        "source_id": eff_src,
        "branch": eff_branch,
        "scope": eff_scope,
        "via_relation": dict(via_relation) if via_relation is not None else None,
    }


def _call_visjs_safe(
    store: Any,
    proj: str,
    branch_name: str,
    query: Optional[str] = None,
    limit: int = 500,
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    import inspect

    fn = store.to_legacy_visjs
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs: Dict[str, Any] = {"branch_name": branch_name, "limit": limit}
    if query is not None:
        kwargs["query"] = query
    if source_id is not None and (
        "source_id" in params
        or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    ):
        kwargs["source_id"] = source_id
    return fn(proj, **kwargs)


def _node_matches_shared_filter(node: Dict[str, Any]) -> bool:
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    nsrc = str(node.get("source_id") or meta.get("source_id") or "").strip()
    nscope = str(node.get("scope") or meta.get("scope") or "").strip()
    return nsrc == "__shared__" or nscope == "project_shared"


def _node_matches_target_source(
    node: Dict[str, Any], target_src: Optional[str], multi_db_active: bool
) -> bool:
    if target_src is None:
        return True
    if target_src == "__shared__":
        return _node_matches_shared_filter(node)
    if multi_db_active and target_src != "__default__":
        return True
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    nsrc = str(node.get("source_id") or meta.get("source_id") or "__default__").strip() or "__default__"
    nscope = str(node.get("scope") or meta.get("scope") or "source").strip() or "source"
    if nscope == "project_shared" or nsrc == "__shared__":
        return False
    return nsrc == target_src


def _lookup_shared_doc_unit_matches(
    proj: str,
    shared_br: str,
    q_clean: str,
    terms: List[str],
    limit: int,
    target_source_id: Optional[str],
    binding_map: Dict[str, Dict[str, Dict[str, Any]]],
    existing_doc_keys: set,
) -> List[Tuple[float, Dict[str, Any]]]:
    """Collect matching sections from `pb_root` `document_versions` / `document_units`."""
    from ..core.tenant_manager import tenant_context, tenant_manager
    from ..memory.hsg import _is_shared_doc_allowed_for_source

    if not tenant_manager.has_branch(proj, shared_br, source_id="__shared__"):
        return []

    matches: List[Tuple[float, Dict[str, Any]]] = []
    q_low = q_clean.lower()
    try:
        with tenant_context(proj, shared_br, source_id="__shared__"):
            ver_rows = db.fetchall(
                "SELECT doc_key, version, filename, branch FROM document_versions "
                "WHERE project_id=? AND status='active' "
                "AND (scope='project_shared' OR source_id='__shared__') "
                "ORDER BY created_at DESC",
                (proj,),
            )
            for dv in ver_rows:
                dkey = str(dv.get("doc_key") or "").strip()
                dver = str(dv.get("version") or "").strip()
                fname = str(dv.get("filename") or f"{dkey}.md")
                dbr = str(dv.get("branch") or shared_br)
                if not dkey or dkey in existing_doc_keys:
                    continue
                if not _is_shared_doc_allowed_for_source(
                    dkey, dver, target_source_id, binding_map
                ):
                    continue
                units = db.fetchall(
                    "SELECT unit_index, unit_title, markdown_text FROM document_units "
                    "WHERE project_id=? AND branch=? AND doc_key=? AND version=? "
                    "ORDER BY unit_index ASC",
                    (proj, dbr, dkey, dver),
                )
                for u in units:
                    utitle = str(u.get("unit_title") or fname)
                    umarkdown = str(u.get("markdown_text") or "")
                    haystack = f"{fname} {dkey} {utitle} {umarkdown}".lower()
                    score = 0.0
                    if q_low in haystack:
                        score = 45.0
                    else:
                        for t in terms:
                            if t in utitle.lower() or t in dkey.lower():
                                score += 5.0
                            elif t in haystack:
                                score += 2.0
                    if score > 0:
                        matches.append(
                            (
                                score * 0.92,
                                {
                                    "id": f"shared_doc::{dkey}::u{u.get('unit_index', 0)}",
                                    "label": utitle or fname,
                                    "kind": "shared_doc",
                                    "source_file": fname,
                                    "source_location": f"{dver}#unit-{u.get('unit_index', 0)}",
                                    "signature": None,
                                    "docstring": _snippet(umarkdown, 220),
                                    "source_id": "__shared__",
                                    "branch": dbr,
                                    "scope": "project_shared",
                                    "via_relation": None,
                                },
                            )
                        )
                        if len(matches) >= limit:
                            break
    except Exception:
        return []
    return matches


def lookup_project_graph_matches(
    query: str,
    user_id: Optional[str],
    limit: int = 8,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Query Graphify store for matching code/doc symbols safely without provisioning phantom DBs."""
    from ..core.tenant_manager import is_multi_db_enabled, parse_tenant_spec, tenant_manager
    from ..memory.hsg import (
        _contract_key_overlaps_query,
        _ctx_source_arg,
        _is_shared_doc_allowed_for_source,
        _load_shared_binding_map,
        _resolve_project_root_branch,
        collect_cross_source_relations_bfs,
    )

    q_clean = (query or "").strip()
    if not q_clean or limit <= 0:
        return []

    clean_arg_src = (
        str(source_id).strip()
        if source_id is not None and str(source_id).strip()
        else None
    )

    raw_uid = (user_id or "").strip()
    if not raw_uid or raw_uid in ("%", "*", "all"):
        target_project: Optional[str] = None
        branch = "main"
        eff_src: Optional[str] = clean_arg_src
    else:
        parsed_proj, parsed_src, parsed_br = parse_tenant_spec(
            raw_uid, default_branch="main"
        )
        target_project = None if parsed_proj in ("%", "*", "all") else parsed_proj
        branch = "main" if parsed_br in ("", "%", "*") else parsed_br
        eff_src = clean_arg_src or (
            str(parsed_src).strip() if parsed_src else None
        )

    store = None
    try:
        try:
            store = get_graph_store(project_id=target_project)
        except TypeError:
            store = get_graph_store(target_project)  # type: ignore[arg-type]

        if store is None:
            return []

        # Direct plugin / mock `query_nodes(project_id, query, branch=..., limit=...)` support
        if hasattr(store, "query_nodes"):
            proj_arg = target_project or "default"
            raw_res = store.query_nodes(proj_arg, q_clean, branch=branch, limit=limit)
            if isinstance(raw_res, dict):
                raw_list = raw_res.get("nodes") or []
            elif isinstance(raw_res, list):
                raw_list = raw_res
            else:
                raw_list = []
            return [
                _normalize_graph_node(n, default_source_id=eff_src, default_branch=branch)
                for n in raw_list[:limit]
                if isinstance(n, dict)
            ]

        known_projects: List[str] = []
        if hasattr(store, "list_projects"):
            try:
                known_projects = list(store.list_projects() or [])
            except Exception:
                known_projects = []

        multi_db_active = is_multi_db_enabled()
        if target_project:
            if known_projects:
                if target_project not in known_projects:
                    return []
                candidate_projects = [target_project]
            else:
                if multi_db_active:
                    return []
                candidate_projects = [target_project]
        else:
            candidate_projects = known_projects[:5]

        if not candidate_projects:
            return []

        terms = _tokenize_query_terms(q_clean)
        q_low = q_clean.lower()
        collected: List[Tuple[float, Dict[str, Any]]] = []
        seen_ids = set()

        def _collect_from_visjs_target(
            proj: str,
            target_src: Optional[str],
            target_br: str,
            scope_label: Optional[str],
            via_rel: Optional[Dict[str, Any]],
            weight_mult: float,
            contract_node_ids: Optional[set] = None,
            contract_tokens: Optional[set] = None,
            contract_boost: float = 0.0,
            shared_only: bool = False,
            shared_bindings: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
            binding_target_src: Optional[str] = None,
            out_shared_doc_keys: Optional[set] = None,
        ) -> None:
            if not hasattr(store, "to_legacy_visjs"):
                return
            if multi_db_active and target_src is not None:
                ctx_src = _ctx_source_arg(target_src)
                if not tenant_manager.has_branch(proj, target_br, source_id=ctx_src):
                    return

            def _accept_node(n: Dict[str, Any]) -> bool:
                if shared_only:
                    if not _node_matches_shared_filter(n):
                        return False
                    if shared_bindings is not None:
                        nmeta = n.get("metadata") if isinstance(n.get("metadata"), dict) else {}
                        dkey = nmeta.get("doc_key")
                        dver = nmeta.get("doc_version")
                        if not _is_shared_doc_allowed_for_source(
                            dkey, dver, binding_target_src, shared_bindings
                        ):
                            return False
                        if out_shared_doc_keys is not None and dkey:
                            out_shared_doc_keys.add(str(dkey))
                    return True
                return _node_matches_target_source(n, target_src, multi_db_active)

            direct_vis = _call_visjs_safe(
                store, proj, target_br, query=q_clean, limit=limit, source_id=target_src
            )
            local_count = 0
            for idx, n in enumerate(direct_vis.get("nodes") or []):
                if not isinstance(n, dict) or not _accept_node(n):
                    continue
                nid = str(n.get("id") or "")
                haystack = " ".join(
                    str(n.get(k) or "")
                    for k in ("id", "label", "qualified_name", "source_file", "docstring", "signature")
                ).lower()
                is_contract_node = bool(contract_node_ids and nid in contract_node_ids)
                if q_low in haystack:
                    score = 100.0 - idx
                elif terms and any(t in haystack for t in terms):
                    score = 50.0 - idx
                elif is_contract_node or (
                    contract_tokens and any(ct in haystack for ct in contract_tokens)
                ):
                    score = 40.0 - idx
                else:
                    continue

                adj_score = score * weight_mult + (
                    contract_boost if (is_contract_node or (contract_tokens and any(ct in haystack for ct in contract_tokens))) else 0.0
                )
                dedup_key = f"{proj}:{target_src or '__default__'}:{nid}"
                if dedup_key not in seen_ids:
                    seen_ids.add(dedup_key)
                    local_count += 1
                    collected.append(
                        (
                            adj_score,
                            _normalize_graph_node(
                                n,
                                default_source_id=target_src,
                                default_branch=target_br,
                                default_scope=scope_label,
                                via_relation=via_rel,
                            ),
                        )
                    )

            if local_count < limit and (terms or contract_node_ids or contract_tokens):
                broad_vis = _call_visjs_safe(
                    store, proj, target_br, limit=500, source_id=target_src
                )
                for n in broad_vis.get("nodes") or []:
                    if not isinstance(n, dict) or not _accept_node(n):
                        continue
                    nid = str(n.get("id") or "")
                    dedup_key = f"{proj}:{target_src or '__default__'}:{nid}"
                    if dedup_key in seen_ids:
                        continue
                    label_low = str(n.get("label") or "").lower()
                    file_low = str(n.get("source_file") or "").lower()
                    doc_low = str(n.get("docstring") or n.get("signature") or "").lower()
                    id_low = nid.lower()

                    token_score = 0.0
                    for t in terms:
                        if t == label_low:
                            token_score += 8.0
                        elif t in label_low:
                            token_score += 5.0
                        elif t in id_low or t in file_low:
                            token_score += 3.0
                        elif t in doc_low:
                            token_score += 1.5

                    is_contract_hit = bool(contract_node_ids and nid in contract_node_ids)
                    if is_contract_hit:
                        token_score += 6.0
                    elif contract_tokens:
                        hay_low = f"{label_low} {id_low} {file_low} {doc_low}"
                        if any(ct in hay_low for ct in contract_tokens):
                            token_score += 4.0
                            is_contract_hit = True

                    if token_score > 0:
                        seen_ids.add(dedup_key)
                        adj_score = token_score * weight_mult + (
                            contract_boost if is_contract_hit else 0.0
                        )
                        collected.append(
                            (
                                adj_score,
                                _normalize_graph_node(
                                    n,
                                    default_source_id=target_src,
                                    default_branch=target_br,
                                    default_scope=scope_label,
                                    via_relation=via_rel,
                                ),
                            )
                        )

        for proj in candidate_projects:
            # 1. Target sub-source (or legacy project root)
            primary_scope = "project_shared" if eff_src == "__shared__" else "source"
            _collect_from_visjs_target(
                proj=proj,
                target_src=eff_src,
                target_br=branch,
                scope_label=primary_scope,
                via_rel=None,
                weight_mult=1.0,
            )

            # 2. Shared Docs in pb_root when source_id / proj@src is queried
            if include_shared_docs and eff_src not in (None, "__shared__"):
                shared_br = _resolve_project_root_branch(proj)
                shared_bindings = _load_shared_binding_map(proj)
                found_shared_keys: set = set()
                _collect_from_visjs_target(
                    proj=proj,
                    target_src="__shared__",
                    target_br=shared_br,
                    scope_label="project_shared",
                    via_rel=None,
                    weight_mult=0.92,
                    shared_only=True,
                    shared_bindings=shared_bindings,
                    binding_target_src=eff_src,
                    out_shared_doc_keys=found_shared_keys,
                )
                doc_unit_matches = _lookup_shared_doc_unit_matches(
                    proj=proj,
                    shared_br=shared_br,
                    q_clean=q_clean,
                    terms=terms,
                    limit=limit,
                    target_source_id=eff_src,
                    binding_map=shared_bindings,
                    existing_doc_keys=found_shared_keys,
                )
                for u_score, u_node in doc_unit_matches:
                    dkey = f"{proj}:__shared__:{u_node['id']}"
                    if dkey not in seen_ids:
                        seen_ids.add(dkey)
                        collected.append((u_score, u_node))

            # 3. Connected sibling sub-source contract symbols (if relation_depth > 0)
            if (
                relation_depth > 0
                and eff_src not in (None, "__default__", "__shared__")
            ):
                bfs_res = collect_cross_source_relations_bfs(
                    project_id=proj,
                    source_id=eff_src,
                    branch=branch,
                    relation_depth=relation_depth,
                    relation_types=relation_types,
                    expand_relations=True,
                    include_shared_docs=include_shared_docs,
                )
                for (nbr_src, nbr_br), edge_cands in bfs_res["sibling_edges"].items():
                    best_rank: Optional[Tuple[int, int, float]] = None
                    best_choice: Optional[Tuple[int, str, Dict[str, Any], bool, float]] = None
                    c_node_ids: set = set()
                    c_tokens: set = set()
                    for hop, from_src, rel_d in edge_cands:
                        ck = str(rel_d.get("contract_key") or "")
                        ov = _contract_key_overlaps_query(ck, q_clean)
                        c_val = rel_d.get("confidence_score")
                        c_sc = max(0.0, min(1.0, float(c_val if c_val is not None else 1.0)))
                        if rel_d.get("src_source_id") == nbr_src and rel_d.get("src_node_id"):
                            c_node_ids.add(str(rel_d["src_node_id"]))
                        if rel_d.get("dst_source_id") == nbr_src and rel_d.get("dst_node_id"):
                            c_node_ids.add(str(rel_d["dst_node_id"]))
                        for tok in re.findall(r"[A-Za-z0-9_.-]{3,}", ck):
                            low_t = tok.lower()
                            if low_t not in _QUERY_STOPWORDS:
                                c_tokens.add(low_t)
                        rk = (1 if ov else 0, -hop, c_sc)
                        if best_rank is None or rk > best_rank:
                            best_rank = rk
                            best_choice = (hop, from_src, rel_d, ov, c_sc)

                    if best_choice is None:
                        continue
                    b_hop, b_from, b_rel, b_ov, b_conf = best_choice
                    via_rel = {
                        "relation_type": b_rel.get("relation_type"),
                        "contract_key": b_rel.get("contract_key") or "",
                        "from_source": b_from,
                        "to_source": nbr_src,
                        "hop": b_hop,
                        "status": b_rel.get("status") or "active",
                    }
                    _collect_from_visjs_target(
                        proj=proj,
                        target_src=nbr_src,
                        target_br=nbr_br,
                        scope_label="related_source",
                        via_rel=via_rel,
                        weight_mult=(0.72 ** b_hop) * b_conf,
                        contract_node_ids=c_node_ids,
                        contract_tokens=c_tokens if b_ov else None,
                        contract_boost=10.0 if b_ov else 0.0,
                    )

        collected.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in collected[:limit]]
    except Exception as exc:
        logger.debug(f"[AI-RAG] Graph symbol lookup skipped for '{user_id}': {exc}")
        return []
    finally:
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception:
                pass


def _format_tags(raw_tags: Any) -> str:
    if not raw_tags:
        return ""
    if isinstance(raw_tags, list):
        cleaned = [str(t).strip() for t in raw_tags if str(t).strip()]
        return ", ".join(cleaned)
    s = str(raw_tags).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].replace('"', "").replace("'", "").strip()
    return s


def _snippet(text: Any, max_len: int = 320) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _format_provenance_badge(item: Dict[str, Any]) -> str:
    """Build a concise provenance label for multi-source memories and graph symbols."""
    src = str(item.get("source_id") or "").strip()
    scope = str(item.get("scope") or "").strip()
    branch = str(item.get("branch") or "").strip()
    via = item.get("via_relation")
    if scope == "project_shared" or src == "__shared__":
        return "shared:__shared__"
    if isinstance(via, dict) and via:
        rel_t = via.get("relation_type") or "related"
        ck = via.get("contract_key") or ""
        ck_s = f" {ck}" if ck else ""
        hop = via.get("hop")
        hop_s = f" hop {hop}" if hop else ""
        return f"source:{src} | via {rel_t}{ck_s}{hop_s}"
    if src and src != "__default__":
        br_s = f":{branch}" if branch and branch != "main" else ""
        return f"source:{src}{br_s}"
    return ""


def _collect_provenance_relations_from_items(
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Sequence[Dict[str, Any]],
    explicit_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if explicit_relations:
        return list(explicit_relations)
    derived: List[Dict[str, Any]] = []
    seen = set()
    for item in list(memories) + list(graph_nodes):
        via = item.get("via_relation")
        if isinstance(via, dict) and via:
            key = (
                via.get("from_source"),
                via.get("to_source"),
                via.get("relation_type"),
                via.get("contract_key"),
            )
            if key not in seen:
                seen.add(key)
                derived.append(via)
    return derived


def synthesize_local_rag_answer(
    query: str,
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Optional[Sequence[Dict[str, Any]]] = None,
    cross_source_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    """Synthesize a clean, structured Markdown answer offline from HSG memories & Graphify symbols."""
    mems = list(memories or [])
    nodes = list(graph_nodes or [])
    rels = _collect_provenance_relations_from_items(mems, nodes, cross_source_relations)

    lines: List[str] = ["### Executive Summary"]
    mem_count = len(mems)
    node_count = len(nodes)

    if mem_count == 0 and node_count == 0:
        lines.append(
            f"No stored cognitive memories or knowledge graph symbols matched **`{query}`**."
        )
    else:
        parts_desc = []
        if mem_count:
            parts_desc.append(
                f"**{mem_count}** cognitive {'memory' if mem_count == 1 else 'memories'}"
            )
        if node_count:
            parts_desc.append(
                f"**{node_count}** code/knowledge graph {'symbol' if node_count == 1 else 'symbols'}"
            )
        joined_counts = " and ".join(parts_desc)
        lines.append(f"Found {joined_counts} relevant to **`{query}`**.")

        highlights: List[str] = []
        for m in mems[:2]:
            sec = str(m.get("primary_sector") or "semantic").upper()
            prov = _format_provenance_badge(m)
            prov_badge = f" (`{prov}`)" if prov else ""
            highlights.append(f"- **[{sec}]**{prov_badge} {_snippet(m.get('content'), 180)}")
        for n in nodes[:2]:
            loc = f" (`{n['source_file']}`)" if n.get("source_file") else ""
            prov = _format_provenance_badge(n)
            prov_badge = f" [`{prov}`]" if prov else ""
            highlights.append(f"- **Symbol `{n.get('label')}`** ({n.get('kind', 'symbol')}){loc}{prov_badge}")
        if highlights:
            lines.append("")
            lines.extend(highlights[:3])

    lines.append("")
    lines.append("### Relevant Cognitive Memories")
    if mems:
        for m in mems[:8]:
            sector = str(m.get("primary_sector") or "semantic").lower()
            score = m.get("score")
            score_str = f" · score `{float(score):.2f}`" if isinstance(score, (int, float)) else ""
            tags_str = _format_tags(m.get("tags"))
            tags_badge = f" · tags: `{tags_str}`" if tags_str else ""
            prov = _format_provenance_badge(m)
            prov_badge = f" · `{prov}`" if prov else ""
            lines.append(
                f"- **`[{sector}]`**{score_str}{prov_badge}{tags_badge}: {_snippet(m.get('content'), 340)}"
            )
    else:
        lines.append(
            "_No cognitive memories matched directly; see matching Code & Knowledge Graph symbols below._"
        )

    if nodes:
        lines.append("")
        lines.append("### Matching Code & Knowledge Graph Symbols")
        for n in nodes[:8]:
            label = n.get("label") or n.get("id") or "symbol"
            kind = n.get("kind") or "symbol"
            src_file = n.get("source_file") or ""
            src_loc = n.get("source_location") or ""
            loc_suffix = ""
            if src_file and src_loc:
                loc_suffix = f" — `{src_file}` ({src_loc})"
            elif src_file:
                loc_suffix = f" — `{src_file}`"
            prov = _format_provenance_badge(n)
            prov_badge = f" · `{prov}`" if prov else ""
            doc_snip = _snippet(n.get("docstring") or n.get("signature") or "", 160)
            extra = f": {doc_snip}" if doc_snip else ""
            lines.append(f"- **`{label}`** (`{kind}`){loc_suffix}{prov_badge}{extra}")

    if rels:
        lines.append("")
        lines.append("### Cross-Source Contract Context")
        for r in rels[:8]:
            f_src = r.get("from_source") or r.get("src_source_id") or "?"
            t_src = r.get("to_source") or r.get("dst_source_id") or "?"
            r_type = r.get("relation_type") or "relates_to"
            ck = r.get("contract_key") or ""
            st = r.get("status") or "active"
            ck_str = f" `{ck}`" if ck else ""
            lines.append(f"- `{f_src}` --[{r_type}{ck_str}]--> `{t_src}` (status: `{st}`)")

    lines.append("")
    lines.append(
        "> 💡 *Synthesized offline by ProjectBrain Local RAG Engine (zero API key required). "
        "Configure Google Gemini, OpenAI/vLLM, or Local Ollama in **AI Settings** for generative LLM reasoning.*"
    )
    return "\n".join(lines)


def synthesize_local_pm_doc(
    prompt: str,
    doc_type: str,
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Optional[Sequence[Dict[str, Any]]] = None,
    cross_source_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    """Draft a structured PM document (`status_report`, `prd`, `user_story`, `roadmap`) offline."""
    mems = list(memories or [])
    nodes = list(graph_nodes or [])
    rels = _collect_provenance_relations_from_items(mems, nodes, cross_source_relations)
    dtype = (doc_type or "status_report").strip().lower()

    completed_items: List[str] = []
    active_items: List[str] = []
    risk_items: List[str] = []
    general_items: List[str] = []

    for m in mems:
        text = _snippet(m.get("content"), 280)
        if not text:
            continue
        sec = str(m.get("primary_sector") or "semantic").lower()
        prov = _format_provenance_badge(m)
        prefix = f"[{sec} | {prov}]" if prov else f"[{sec}]"
        low = text.lower()
        if any(k in low for k in ("risk", "bug", "error", "fail", "issue", "blocker", "warn", "vulnerability")):
            risk_items.append(f"{prefix} {text}")
        elif any(k in low for k in ("done", "completed", "shipped", "implemented", "merged", "fixed", "released")):
            completed_items.append(f"{prefix} {text}")
        elif any(k in low for k in ("wip", "in progress", "todo", "next", "plan", "pending", "working on")):
            active_items.append(f"{prefix} {text}")
        else:
            general_items.append(f"{prefix} {text}")

    all_facts = completed_items + active_items + general_items + risk_items

    symbol_bullets: List[str] = []
    for n in nodes[:10]:
        prov = _format_provenance_badge(n)
        prov_part = f" · `{prov}`" if prov else ""
        loc_part = f" in `{n['source_file']}`" if n.get("source_file") else ""
        symbol_bullets.append(
            f"- **`{n.get('label')}`** (`{n.get('kind', 'symbol')}`){loc_part}{prov_part}"
        )

    lines: List[str] = []

    if dtype == "prd":
        lines.extend(
            [
                f"# Product Requirements Document (PRD)",
                f"**Initiative / Scope:** {prompt}",
                "",
                "## 1. Overview & Context",
                (
                    f"This PRD consolidates **{len(mems)}** project memories and **{len(nodes)}** "
                    f"code/knowledge graph symbols related to **{prompt}**."
                    if (mems or nodes)
                    else f"Initial PRD scaffold for **{prompt}**. No existing project memories matched this topic yet."
                ),
                "",
                "## 2. Objectives & Functional Requirements",
            ]
        )
        req_source = general_items + active_items + completed_items
        if req_source:
            for idx, item in enumerate(req_source[:10], start=1):
                lines.append(f"- **FR-{idx:02d}:** {item}")
        else:
            lines.append(f"- **FR-01:** Define core workflows and user-facing behavior for: {prompt}.")

        lines.extend(["", "## 3. Risks & Constraints"])
        if risk_items:
            lines.extend(f"- {r}" for r in risk_items[:6])
        else:
            lines.append("- Validate authentication, data isolation, and backward compatibility requirements.")

        if symbol_bullets:
            lines.extend(["", "## 4. Affected Technical Components & Graph Symbols", *symbol_bullets])

    elif dtype == "user_story":
        lines.extend(
            [
                "# User Stories & Acceptance Criteria",
                f"**Feature Scope:** {prompt}",
                "",
            ]
        )
        story_seeds = all_facts[:6]
        if story_seeds:
            for idx, fact in enumerate(story_seeds, start=1):
                lines.extend(
                    [
                        f"## Story {idx}: Core Capability #{idx}",
                        f"- **Context Fact:** {fact}",
                        f"- **User Story:** As a project stakeholder, I want the system to support `{_snippet(prompt, 80)}` grounded in this requirement so that delivery stays aligned with verified project context.",
                        "- **Acceptance Criteria:**",
                        "  - [ ] Implementation satisfies the documented context above.",
                        "  - [ ] Automated unit and integration tests cover happy-path and edge-case flows.",
                        "",
                    ]
                )
        else:
            lines.extend(
                [
                    "## Story 1: Baseline Delivery",
                    f"- **User Story:** As a user, I want `{prompt}` implemented cleanly with clear validation and observability.",
                    "- **Acceptance Criteria:**",
                    "  - [ ] Core functional flow works end-to-end.",
                    "  - [ ] Error states return actionable feedback.",
                    "",
                ]
            )
        if symbol_bullets:
            lines.extend(["## Linked Codebase Symbols", *symbol_bullets, ""])

    elif dtype == "roadmap":
        lines.extend(
            [
                "# Product & Engineering Roadmap",
                f"**Strategic Focus:** {prompt}",
                "",
                "## Milestone 1 — Established Foundation",
            ]
        )
        m1 = completed_items or general_items[:4]
        lines.extend([f"- {x}" for x in m1[:6]] or ["- Baseline architecture and project setup."])

        lines.extend(["", "## Milestone 2 — Active Execution & Core Delivery"])
        m2 = active_items or general_items[4:8]
        lines.extend([f"- {x}" for x in m2[:6]] or [f"- Deliver primary scope for: {prompt}."])

        lines.extend(["", "## Milestone 3 — Hardening & Risk Mitigation"])
        lines.extend(
            [f"- {x}" for x in risk_items[:6]]
            or ["- End-to-end regression testing, performance tuning, and rollout readiness."]
        )
        if symbol_bullets:
            lines.extend(["", "## Key Codebase Modules", *symbol_bullets])

    else:
        # Default: status_report
        lines.extend(
            [
                "# Project Status Report",
                f"**Topic / Scope:** {prompt}",
                "",
                "## Executive Summary",
                (
                    f"Synthesized from **{len(mems)}** cognitive project memories and "
                    f"**{len(nodes)}** graph symbols matching **{prompt}**."
                    if (mems or nodes)
                    else f"No recorded project memories matched **{prompt}** yet."
                ),
                "",
                "## Completed & Established Facts",
            ]
        )
        done_src = completed_items or general_items[:5]
        lines.extend([f"- {x}" for x in done_src[:8]] or ["- No completed items explicitly tagged in matching memories."])

        lines.extend(["", "## In-Progress Work & Context"])
        wip_src = active_items or general_items[5:10]
        lines.extend([f"- {x}" for x in wip_src[:8]] or [f"- Ongoing evaluation for: {prompt}."])

        lines.extend(["", "## Identified Risks & Attention Areas"])
        lines.extend([f"- {x}" for x in risk_items[:6]] or ["- No critical blockers recorded in matching memories."])

        if symbol_bullets:
            lines.extend(["", "## Referenced Code & Knowledge Graph Symbols", *symbol_bullets])

    if rels:
        lines.extend(["", "## Cross-Source Dependencies & Contracts"])
        for r in rels[:10]:
            f_src = r.get("from_source") or r.get("src_source_id") or "?"
            t_src = r.get("to_source") or r.get("dst_source_id") or "?"
            r_type = r.get("relation_type") or "relates_to"
            ck = r.get("contract_key") or ""
            st = r.get("status") or "active"
            ck_str = f" `{ck}`" if ck else ""
            lines.append(f"- `{f_src}` --[{r_type}{ck_str}]--> `{t_src}` (status: `{st}`)")

    lines.append("")
    lines.append(
        "> 💡 *Generated offline by ProjectBrain Local RAG Engine (zero API key required). "
        "Configure Google Gemini, OpenAI/vLLM, or Local Ollama in **AI Settings** for generative LLM drafting.*"
    )
    return "\n".join(lines)


# ============================================================================
# Multi-Provider Execution & Connection Diagnostics
# ============================================================================


async def _call_explicit_provider(
    provider: str,
    messages: List[Dict[str, str]],
    eff: Dict[str, Any],
) -> Tuple[str, str, str, str]:
    """Execute chat completion against a specific LLM provider (`gemini`, `openai`, `minimax`, `local_ai`, `ollama`)."""
    if provider == "gemini":
        if not eff["gemini_key"]:
            raise ValueError("Google Gemini API key is missing. Configure it in AI Settings or switch to Local RAG / Auto.")
        adapter = GeminiAdapter(api_key=eff["gemini_key"])
        if eff["gemini_base_url"]:
            adapter.base_url = eff["gemini_base_url"]
        model_used = eff["gemini_model"] or DEFAULT_GEMINI_MODEL
        prev_base = getattr(env, "gemini_base_url", "")
        try:
            if eff["gemini_base_url"] != prev_base:
                env.gemini_base_url = eff["gemini_base_url"]
            reply = await adapter.chat(messages, model=model_used)
        finally:
            if getattr(env, "gemini_base_url", "") != prev_base:
                env.gemini_base_url = prev_base
        return reply, "gemini", model_used, _provider_display_label("gemini", model_used)

    if provider == "openai":
        if not eff["openai_key"]:
            raise ValueError("OpenAI API key is missing. Configure it in AI Settings or switch to Local RAG / Auto.")
        model_used = eff["openai_model"] or DEFAULT_OPENAI_MODEL
        adapter = OpenAIAdapter(api_key=eff["openai_key"], base_url=eff["openai_base_url"])
        reply = await adapter.chat(messages, model=model_used)
        return reply, "openai", model_used, _provider_display_label("openai", model_used)

    if provider == "minimax":
        if not eff["minimax_key"]:
            raise ValueError("MiniMax API key is missing. Configure it in AI Settings or switch to Local RAG / Auto.")
        model_used = eff["minimax_model"] or DEFAULT_MINIMAX_MODEL
        adapter = MiniMaxAdapter(api_key=eff["minimax_key"], base_url=eff["minimax_base_url"])
        reply = await adapter.chat(messages, model=model_used)
        return reply, "minimax", model_used, _provider_display_label("minimax", model_used)

    if provider == "local_ai":
        if not eff["local_ai_base_url"]:
            raise ValueError("Local OpenAI-compatible base URL is missing. Configure local_ai_base_url in AI Settings.")
        model_used = eff["local_ai_model"] or "default"
        api_key = eff["local_ai_api_key"] or "local-no-key"
        adapter = OpenAIAdapter(api_key=api_key, base_url=eff["local_ai_base_url"])
        reply = await adapter.chat(messages, model=model_used)
        return reply, "local_ai", model_used, _provider_display_label("local_ai", model_used)

    if provider == "ollama":
        model_used = eff["ollama_model"] or DEFAULT_OLLAMA_MODEL
        adapter = OllamaAdapter(base_url=eff["ollama_url"])
        reply = await adapter.chat(messages, model=model_used)
        return reply, "ollama", model_used, _provider_display_label("ollama", model_used)

    raise ValueError(f"Unsupported LLM provider '{provider}'.")


async def _select_auto_llm_candidate(eff: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return `(provider_id, effective_settings)` for the highest-priority configured LLM in `auto` mode, or `None` for Local RAG."""
    if eff["gemini_key"]:
        return "gemini", eff
    if eff["openai_key"]:
        return "openai", eff
    if eff["minimax_key"]:
        return "minimax", eff
    if eff["local_ai_base_url"]:
        return "local_ai", eff

    reachable, models = await _probe_ollama_async(eff["ollama_url"], timeout=0.75)
    if reachable and models:
        chosen = _pick_ollama_model(eff["ollama_model"], models)
        patched = dict(eff)
        patched["ollama_model"] = chosen
        return "ollama", patched

    return None


def _build_rag_prompt(
    query: str,
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Sequence[Dict[str, Any]],
    cross_source_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    mem_lines: List[str] = []
    for m in memories:
        prov = _format_provenance_badge(m)
        prov_part = f" | {prov}" if prov else ""
        mem_lines.append(
            f"- [{m.get('primary_sector')} | Tag: {m.get('tags')}{prov_part}] {m.get('content')}"
        )

    graph_lines: List[str] = []
    for n in graph_nodes:
        prov = _format_provenance_badge(n)
        prov_part = f" | {prov}" if prov else ""
        loc_part = f" {n.get('source_location')}" if n.get("source_location") else ""
        graph_lines.append(
            (
                f"- [Symbol: {n.get('label')} ({n.get('kind')}) | File: {n.get('source_file') or 'n/a'}"
                f"{loc_part}{prov_part}] {n.get('docstring') or n.get('signature') or ''}"
            ).rstrip()
        )

    rels = _collect_provenance_relations_from_items(
        memories, graph_nodes, cross_source_relations
    )
    rel_lines = [
        f"- {r.get('from_source') or r.get('src_source_id')} --[{r.get('relation_type')} {r.get('contract_key') or ''}]--> "
        f"{r.get('to_source') or r.get('dst_source_id')} (status={r.get('status') or 'active'})"
        for r in rels
    ]

    sections: List[str] = []
    if mem_lines:
        sections.append("Project cognitive memories:\n" + "\n".join(mem_lines))
    if graph_lines:
        sections.append("Matching code & knowledge graph symbols:\n" + "\n".join(graph_lines))
    if rel_lines:
        sections.append("Cross-source contract relations:\n" + "\n".join(rel_lines))
    context_block = "\n\n".join(sections) if sections else "(No matching context)"

    return f"""You are a smart AI assistant supporting a PM and engineering team.
Please answer the question accurately based on the project context found below.
If the information in the context is insufficient, answer based on what is available and clearly state any gaps.

Question: {query}

{context_block}
"""


def _build_pm_doc_prompt(
    prompt: str,
    doc_type: str,
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Sequence[Dict[str, Any]],
    cross_source_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    doc_instructions = {
        "status_report": "Project Status Report including completed tasks, in-progress work, and identified risks.",
        "prd": "Product Requirement Document (PRD) including overview, objectives, user stories, functional and technical requirements.",
        "user_story": "Detailed list of User Stories accompanied by acceptance criteria (Acceptance Criteria).",
        "roadmap": "Product Roadmap broken down by milestones and major features.",
    }
    instruction = doc_instructions.get(doc_type, doc_instructions["status_report"])

    mem_lines: List[str] = []
    for m in memories:
        prov = _format_provenance_badge(m)
        prov_part = f" | {prov}" if prov else ""
        mem_lines.append(f"- [{m.get('primary_sector')}{prov_part}] {m.get('content')}")
    mem_context = "\n".join(mem_lines) or "(No cognitive memories matched)"

    graph_lines: List[str] = []
    for n in graph_nodes:
        prov = _format_provenance_badge(n)
        prov_part = f" [{prov}]" if prov else ""
        graph_lines.append(
            f"- {n.get('label')} ({n.get('kind')}) in {n.get('source_file') or 'n/a'}{prov_part}"
        )
    graph_context = "\n".join(graph_lines)
    extra_graph = f"\n\nMatching code & architecture symbols:\n{graph_context}" if graph_context else ""

    rels = _collect_provenance_relations_from_items(
        memories, graph_nodes, cross_source_relations
    )
    rel_context = "\n".join(
        f"- {r.get('from_source') or r.get('src_source_id')} --[{r.get('relation_type')} {r.get('contract_key') or ''}]--> "
        f"{r.get('to_source') or r.get('dst_source_id')} (status={r.get('status') or 'active'})"
        for r in rels
    )
    extra_rels = f"\n\nCross-source dependencies & contracts:\n{rel_context}" if rel_context else ""

    return f"""You are an outstanding Product Manager expert.
Your task is to draft a document: {instruction}
Please base your writing on the actual facts collected from the project memories below, and write the document professionally with a clear Markdown structure.

PM Request: {prompt}

Actual project memories collected:
{mem_context}{extra_graph}{extra_rels}
"""


async def run_rag_chat(
    query: str,
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Optional[Sequence[Dict[str, Any]]] = None,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
    cross_source_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Execute multi-provider RAG answer generation with automatic zero-key Local RAG fallback."""
    eff = get_effective_ai_settings()
    mems = list(memories or [])
    nodes = list(graph_nodes or [])
    rels = list(cross_source_relations or [])
    provider = eff["provider"]

    if provider == "local_rag":
        return {
            "answer": synthesize_local_rag_answer(query, mems, nodes, rels),
            "provider_used": "local_rag",
            "model_used": LOCAL_RAG_MODEL_ID,
            "provider_label": LOCAL_RAG_LABEL,
            "fallback_reason": None,
        }

    messages = [{"role": "user", "content": _build_rag_prompt(query, mems, nodes, rels)}]

    if provider == "auto":
        candidate = await _select_auto_llm_candidate(eff)
        if candidate is None:
            return {
                "answer": synthesize_local_rag_answer(query, mems, nodes, rels),
                "provider_used": "local_rag",
                "model_used": LOCAL_RAG_MODEL_ID,
                "provider_label": LOCAL_RAG_LABEL,
                "fallback_reason": None,
            }
        cand_provider, cand_eff = candidate
        try:
            reply, used_prov, used_model, used_label = await _call_explicit_provider(
                cand_provider, messages, cand_eff
            )
            return {
                "answer": reply,
                "provider_used": used_prov,
                "model_used": used_model,
                "provider_label": used_label,
                "fallback_reason": None,
            }
        except Exception as exc:
            clean_err = sanitize_secret(
                exc,
                eff["gemini_key"],
                eff["openai_key"],
                eff["minimax_key"],
                eff["local_ai_api_key"],
            )
            if eff["fallback_to_local_rag"]:
                logger.warning(
                    f"[AI-RAG] Auto provider '{cand_provider}' failed ({clean_err}); falling back to Local RAG."
                )
                return {
                    "answer": synthesize_local_rag_answer(query, mems, nodes, rels),
                    "provider_used": "local_rag",
                    "model_used": LOCAL_RAG_MODEL_ID,
                    "provider_label": LOCAL_RAG_LABEL,
                    "fallback_reason": clean_err,
                }
            raise RuntimeError(clean_err) from None

    # Explicit provider ("gemini", "openai", "ollama", "minimax", "local_ai")
    try:
        reply, used_prov, used_model, used_label = await _call_explicit_provider(
            provider, messages, eff
        )
        return {
            "answer": reply,
            "provider_used": used_prov,
            "model_used": used_model,
            "provider_label": used_label,
            "fallback_reason": None,
        }
    except Exception as exc:
        clean_err = sanitize_secret(
            exc,
            eff["gemini_key"],
            eff["openai_key"],
            eff["minimax_key"],
            eff["local_ai_api_key"],
        )
        if eff["fallback_to_local_rag"]:
            logger.warning(
                f"[AI-RAG] Provider '{provider}' failed ({clean_err}); falling back to Local RAG."
            )
            return {
                "answer": synthesize_local_rag_answer(query, mems, nodes, rels),
                "provider_used": "local_rag",
                "model_used": LOCAL_RAG_MODEL_ID,
                "provider_label": LOCAL_RAG_LABEL,
                "fallback_reason": clean_err,
            }
        raise ValueError(clean_err) from None


async def run_pm_doc_generation(
    prompt: str,
    doc_type: str,
    memories: Sequence[Dict[str, Any]],
    graph_nodes: Optional[Sequence[Dict[str, Any]]] = None,
    source_id: Optional[str] = None,
    include_shared_docs: bool = True,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
    cross_source_relations: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Execute multi-provider PM Copilot document generation with automatic Local RAG fallback."""
    eff = get_effective_ai_settings()
    mems = list(memories or [])
    nodes = list(graph_nodes or [])
    rels = list(cross_source_relations or [])
    provider = eff["provider"]

    if provider == "local_rag":
        return {
            "document": synthesize_local_pm_doc(prompt, doc_type, mems, nodes, rels),
            "provider_used": "local_rag",
            "model_used": LOCAL_RAG_MODEL_ID,
            "provider_label": LOCAL_RAG_LABEL,
            "fallback_reason": None,
        }

    messages = [{"role": "user", "content": _build_pm_doc_prompt(prompt, doc_type, mems, nodes, rels)}]

    if provider == "auto":
        candidate = await _select_auto_llm_candidate(eff)
        if candidate is None:
            return {
                "document": synthesize_local_pm_doc(prompt, doc_type, mems, nodes, rels),
                "provider_used": "local_rag",
                "model_used": LOCAL_RAG_MODEL_ID,
                "provider_label": LOCAL_RAG_LABEL,
                "fallback_reason": None,
            }
        cand_provider, cand_eff = candidate
        try:
            reply, used_prov, used_model, used_label = await _call_explicit_provider(
                cand_provider, messages, cand_eff
            )
            return {
                "document": reply,
                "provider_used": used_prov,
                "model_used": used_model,
                "provider_label": used_label,
                "fallback_reason": None,
            }
        except Exception as exc:
            clean_err = sanitize_secret(
                exc,
                eff["gemini_key"],
                eff["openai_key"],
                eff["minimax_key"],
                eff["local_ai_api_key"],
            )
            if eff["fallback_to_local_rag"]:
                logger.warning(
                    f"[AI-DOC] Auto provider '{cand_provider}' failed ({clean_err}); falling back to Local RAG."
                )
                return {
                    "document": synthesize_local_pm_doc(prompt, doc_type, mems, nodes, rels),
                    "provider_used": "local_rag",
                    "model_used": LOCAL_RAG_MODEL_ID,
                    "provider_label": LOCAL_RAG_LABEL,
                    "fallback_reason": clean_err,
                }
            raise RuntimeError(clean_err) from None

    # Explicit provider
    try:
        reply, used_prov, used_model, used_label = await _call_explicit_provider(
            provider, messages, eff
        )
        return {
            "document": reply,
            "provider_used": used_prov,
            "model_used": used_model,
            "provider_label": used_label,
            "fallback_reason": None,
        }
    except Exception as exc:
        clean_err = sanitize_secret(
            exc,
            eff["gemini_key"],
            eff["openai_key"],
            eff["minimax_key"],
            eff["local_ai_api_key"],
        )
        if eff["fallback_to_local_rag"]:
            logger.warning(
                f"[AI-DOC] Provider '{provider}' failed ({clean_err}); falling back to Local RAG."
            )
            return {
                "document": synthesize_local_pm_doc(prompt, doc_type, mems, nodes, rels),
                "provider_used": "local_rag",
                "model_used": LOCAL_RAG_MODEL_ID,
                "provider_label": LOCAL_RAG_LABEL,
                "fallback_reason": clean_err,
            }
        raise ValueError(clean_err) from None


def _expected_model_for_provider(provider: str, eff: Dict[str, Any]) -> str:
    if provider == "gemini":
        return eff["gemini_model"]
    if provider == "openai":
        return eff["openai_model"]
    if provider == "minimax":
        return eff["minimax_model"]
    if provider == "local_ai":
        return eff["local_ai_model"] or "default"
    if provider == "ollama":
        return eff["ollama_model"]
    return LOCAL_RAG_MODEL_ID


async def test_ai_provider(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test connectivity and response latency for saved or unsaved AI provider settings."""
    t0 = time.perf_counter()
    try:
        eff = get_effective_ai_settings(overrides=overrides)
    except ValueError as exc:
        return {
            "ok": False,
            "provider_used": str((overrides or {}).get("provider") or "unknown"),
            "model_used": "",
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "reply": "",
            "error": sanitize_secret(exc),
        }

    provider = eff["provider"]
    probe_messages = [
        {
            "role": "user",
            "content": "Reply with a brief confirmation: ProjectBrain AI connection OK.",
        }
    ]

    if provider == "local_rag":
        sample = synthesize_local_rag_answer(
            "connection_check",
            [{"primary_sector": "semantic", "score": 1.0, "tags": "health", "content": "Local RAG engine active."}],
            [],
        )
        return {
            "ok": True,
            "provider_used": "local_rag",
            "model_used": LOCAL_RAG_MODEL_ID,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "reply": "Built-in Local RAG Engine is active (offline · zero API key required)." if sample else "OK",
            "error": None,
        }

    if provider == "auto":
        candidate = await _select_auto_llm_candidate(eff)
        if candidate is None:
            return {
                "ok": True,
                "provider_used": "local_rag",
                "model_used": LOCAL_RAG_MODEL_ID,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "reply": "Auto mode resolved to Built-in Local RAG (offline · zero API key required).",
                "error": None,
            }
        cand_provider, cand_eff = candidate
        try:
            reply, used_prov, used_model, _ = await _call_explicit_provider(
                cand_provider, probe_messages, cand_eff
            )
            return {
                "ok": True,
                "provider_used": used_prov,
                "model_used": used_model,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "reply": (reply or "OK").strip()[:500],
                "error": None,
            }
        except Exception as exc:
            clean_err = sanitize_secret(
                exc,
                eff["gemini_key"],
                eff["openai_key"],
                eff["minimax_key"],
                eff["local_ai_api_key"],
            )
            return {
                "ok": False,
                "provider_used": cand_provider,
                "model_used": _expected_model_for_provider(cand_provider, cand_eff),
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "reply": "",
                "error": clean_err,
            }

    # Explicit provider test
    try:
        reply, used_prov, used_model, _ = await _call_explicit_provider(
            provider, probe_messages, eff
        )
        return {
            "ok": True,
            "provider_used": used_prov,
            "model_used": used_model,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "reply": (reply or "OK").strip()[:500],
            "error": None,
        }
    except Exception as exc:
        clean_err = sanitize_secret(
            exc,
            eff["gemini_key"],
            eff["openai_key"],
            eff["minimax_key"],
            eff["local_ai_api_key"],
        )
        return {
            "ok": False,
            "provider_used": provider,
            "model_used": _expected_model_for_provider(provider, eff),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "reply": "",
            "error": clean_err,
        }
