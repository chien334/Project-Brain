"""Comprehensive Unit & Integration Test Suite for Runtime AI Settings & Multi-Provider RAG (`TASK-35A`).

Covers:
- Default `GET /pm/ai-settings` (`provider == "auto"`, `resolved_provider == "local_rag"`, zero secret leakage)
- Zero-key `POST /pm/search` & `POST /pm/generate-doc` succeeding with HTTP 200 via `local_rag`
- Graphify AST symbol enrichment (`lookup_project_graph_matches`) alongside or without HSG memories
- `PUT /pm/ai-settings` secret masking, empty-key preservation, `clear_gemini_key=True`, live `env`/`os.environ` sync,
  startup persistence (`sync_ai_settings_from_db`), audit logging, and strict Admin RBAC (401/403)
- `POST /pm/ai-settings/test` for `local_rag`, mocked `GeminiAdapter.chat`, unsaved overrides, and sanitized diagnostics
- Multi-provider `auto` priority chain (`gemini` -> `openai` -> `minimax` -> `local_ai` -> `ollama` -> `local_rag`)
  and `fallback_to_local_rag` True vs False behavior
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from projectbrain.ai.gemini import GeminiAdapter
from projectbrain.ai.minimax import MiniMaxAdapter
from projectbrain.ai.ollama import OllamaAdapter
from projectbrain.ai.openai import OpenAIAdapter
from projectbrain.ai.runtime_settings import (
    get_public_ai_settings,
    mask_secret,
    sanitize_secret,
    sync_ai_settings_from_db,
)
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.graphify_engine.models import GraphDelta, GraphNode
from projectbrain.graphify_engine.store import get_graph_store
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes


@pytest.fixture(autouse=True)
def isolated_ai_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test to a fresh SQLite DB, clean AI env vars, and deterministic offline Ollama probe."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    env_attrs_to_save = (
        "allow_registration",
        "default_signup_role",
        "session_ttl_hours",
        "require_rest_auth",
        "multi_db_enabled",
        "ai_provider",
        "emb_kind",
        "gemini_key",
        "gemini_model",
        "gemini_base_url",
        "openai_key",
        "openai_base_url",
        "openai_model",
        "ollama_url",
        "ollama_model",
        "ollama_vision_model",
        "minimax_key",
        "minimax_base_url",
        "minimax_model",
        "local_ai_base_url",
        "local_ai_model",
        "local_ai_api_key",
    )
    saved_attrs = {attr: getattr(env, attr, None) for attr in env_attrs_to_save}

    os_keys = (
        "PB_DB_PATH",
        "OM_DB_PATH",
        "PB_MULTI_DB_ENABLED",
        "OM_MULTI_DB_ENABLED",
        "PB_REQUIRE_REST_AUTH",
        "PB_ALLOW_REGISTRATION",
        "PB_AI_PROVIDER",
        "PB_AI_FALLBACK_LOCAL_RAG",
        "PB_EMBED_KIND",
        "OM_EMBED_KIND",
        "GEMINI_API_KEY",
        "PB_GEMINI_KEY",
        "OM_GEMINI_KEY",
        "PB_GEMINI_MODEL",
        "OM_GEMINI_MODEL",
        "PB_GEMINI_BASE_URL",
        "OM_GEMINI_BASE_URL",
        "OPENAI_API_KEY",
        "PB_OPENAI_API_KEY",
        "OM_OPENAI_API_KEY",
        "PB_OPENAI_BASE_URL",
        "OM_OPENAI_BASE_URL",
        "PB_OPENAI_MODEL",
        "OM_OPENAI_MODEL",
        "OLLAMA_URL",
        "PB_OLLAMA_URL",
        "PB_OLLAMA_MODEL",
        "OM_OLLAMA_MODEL",
        "OLLAMA_MODEL",
        "PB_OLLAMA_VISION_MODEL",
        "OLLAMA_VISION_MODEL",
        "MINIMAX_API_KEY",
        "PB_MINIMAX_API_KEY",
        "OM_MINIMAX_API_KEY",
        "PB_MINIMAX_BASE_URL",
        "OM_MINIMAX_BASE_URL",
        "PB_MINIMAX_MODEL",
        "OM_MINIMAX_MODEL",
        "PB_LOCAL_AI_URL",
        "PB_LOCAL_AI_MODEL",
        "PB_LOCAL_AI_API_KEY",
    )
    saved_os_env = {k: os.environ.get(k) for k in os_keys}

    for k in os_keys:
        monkeypatch.delenv(k, raising=False)

    temp_db_path = tmp_path / "ai_settings_test.db"
    monkeypatch.setenv("PB_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("PB_MULTI_DB_ENABLED", "false")
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")

    monkeypatch.setattr(env, "database_url", f"sqlite:///{temp_db_path}")
    monkeypatch.setattr(env, "db_path", str(temp_db_path))
    monkeypatch.setattr(env, "multi_db_enabled", False)
    monkeypatch.setattr(env, "require_rest_auth", False)
    monkeypatch.setattr(env, "emb_kind", "synthetic")
    monkeypatch.setattr(env, "gemini_key", "")
    monkeypatch.setattr(env, "gemini_model", "gemini-3.1-pro-preview")
    monkeypatch.setattr(env, "gemini_base_url", "")
    monkeypatch.setattr(env, "openai_key", "")
    monkeypatch.setattr(env, "openai_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(env, "openai_model", None)
    monkeypatch.setattr(env, "ollama_url", "http://localhost:11434")
    monkeypatch.setattr(env, "ollama_model", "llama3.2")
    monkeypatch.setattr(env, "ollama_vision_model", "qwen2.5vl:7b")
    monkeypatch.setattr(env, "minimax_key", "")
    monkeypatch.setattr(env, "minimax_base_url", "https://api.minimax.io/v1")
    monkeypatch.setattr(env, "minimax_model", None)
    monkeypatch.setattr(env, "local_ai_base_url", "")
    monkeypatch.setattr(env, "local_ai_model", "")
    monkeypatch.setattr(env, "local_ai_api_key", "local-no-key")
    monkeypatch.setattr(env, "ai_provider", None, raising=False)

    # Default offline Ollama probe so host daemons never affect test determinism
    async def _offline_ollama_probe(self, timeout: float = 2.0):
        return {
            "reachable": False,
            "base_url": self.base_url,
            "models": [],
            "vision_models": [],
            "error": "Connection refused",
        }

    monkeypatch.setattr(OllamaAdapter, "check_health_and_models", _offline_ollama_probe)

    db.conn = None
    db.is_postgres = False
    auth_routes._ensured_conn = None
    auth_routes._ensured_db_url = None
    db.connect()
    auth_routes.init_auth_system()

    try:
        yield temp_db_path
    finally:
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        for attr, prev in saved_attrs.items():
            setattr(env, attr, prev)

        for k, prev_val in saved_os_env.items():
            if prev_val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev_val

        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None


@pytest.fixture
def client(isolated_ai_env) -> TestClient:
    return TestClient(create_app())


# ============================================================================
# 1. Default AI Settings & Secret Masking
# ============================================================================


def test_mask_and_sanitize_secrets():
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("short123") == "••••••••"
    assert mask_secret("AIzaSySecretKey9876") == "AIza••••9876"

    leaked = "Error calling https://generativelanguage.googleapis.com/v1beta?key=AIzaSySecretKey9876 with sk-proj1234567890abcdef"
    redacted = sanitize_secret(leaked, "customSecretToken99")
    assert "AIzaSySecretKey9876" not in redacted
    assert "sk-proj1234567890abcdef" not in redacted
    assert "[REDACTED]" in redacted


def test_get_ai_settings_defaults_to_auto_and_local_rag_zero_key(client: TestClient):
    res = client.get("/pm/ai-settings")
    assert res.status_code == 200
    data = res.json()

    assert data["provider"] == "auto"
    assert data["resolved_provider"] == "local_rag"
    assert "Local RAG" in data["resolved_label"]
    assert data["fallback_to_local_rag"] is True
    assert data["emb_kind"] == "synthetic"
    assert data["has_gemini_key"] is False
    assert data["gemini_key_masked"] == ""
    assert data["gemini_model"] == "gemini-2.5-flash"
    assert data["has_openai_key"] is False
    assert data["openai_key_masked"] == ""
    assert data["openai_model"] == "gpt-4o-mini"
    assert data["ollama_reachable"] is False
    assert data["ollama_models"] == []
    assert data["has_minimax_key"] is False
    assert data["minimax_key_masked"] == ""
    assert data["has_local_ai_key"] is False

    # Synchronous helper without probing Ollama
    sync_view = get_public_ai_settings(probe_ollama=False)
    assert sync_view["ollama_reachable"] is None
    assert sync_view["resolved_provider"] == "local_rag"


# ============================================================================
# 2. Zero-Key Offline Local RAG Search, Graph Enrichment & PM Doc Generation
# ============================================================================


def test_pm_search_and_generate_doc_without_gemini_key_use_local_rag(
    client: TestClient,
    isolated_ai_env: Path,
):
    # Empty project search returns graceful message (no HTTP 500!)
    empty_res = client.post("/pm/search", json={"query": "nonexistent architecture", "user_id": "proj_alpha:main"})
    assert empty_res.status_code == 200
    empty_data = empty_res.json()
    assert empty_data["provider_used"] == "none"
    assert empty_data["source_memories"] == []
    assert empty_data["graph_matches"] == []

    # Add cognitive memories
    r1 = client.post(
        "/memory/add",
        json={
            "content": "Completed multi-tenant database isolation via ProjectDatabaseManager in tenant_manager.py.",
            "user_id": "proj_alpha:main",
            "tags": ["architecture", "db"],
        },
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/memory/add",
        json={
            "content": "Risk: PostgreSQL connection pool exhaustion under burst graph sync workloads; mitigation in progress.",
            "user_id": "proj_alpha:main",
            "tags": ["risk", "postgres"],
        },
    )
    assert r2.status_code == 200

    # Sync a Graphify AST symbol into the same project
    store = get_graph_store(db_path=str(isolated_ai_env))
    try:
        delta = GraphDelta(
            project_id="proj_alpha",
            branch="main",
            nodes=[
                GraphNode(
                    id="src/projectbrain/core/tenant_manager.py::ProjectDatabaseManager",
                    project_id="proj_alpha",
                    label="ProjectDatabaseManager",
                    file_type="code",
                    source_file="src/projectbrain/core/tenant_manager.py",
                    source_location="L120-L340",
                    metadata={"kind": "class", "docstring": "Manages 3-tier tenant DB isolation."},
                )
            ],
        )
        store.sync_branch_delta("proj_alpha", "main", delta)
    finally:
        store.close()

    # 1. Semantic Search WITHOUT GEMINI_API_KEY -> HTTP 200 via local_rag
    search_res = client.post(
        "/pm/search",
        json={"query": "ProjectDatabaseManager tenant isolation", "user_id": "proj_alpha:main"},
    )
    assert search_res.status_code == 200
    s_data = search_res.json()
    assert s_data["provider_used"] == "local_rag"
    assert s_data["fallback_reason"] is None
    assert len(s_data["source_memories"]) >= 1
    assert len(s_data["graph_matches"]) >= 1
    assert s_data["graph_matches"][0]["label"] == "ProjectDatabaseManager"
    assert "### Executive Summary" in s_data["results"]
    assert "### Relevant Cognitive Memories" in s_data["results"]
    assert "### Matching Code & Knowledge Graph Symbols" in s_data["results"]
    assert "ProjectDatabaseManager" in s_data["results"]

    # 2. Symbol-only search on a project with 0 memories still succeeds via Graphify AST lookup!
    store2 = get_graph_store(db_path=str(isolated_ai_env))
    try:
        store2.sync_branch_delta(
            "graph_only_proj",
            "main",
            GraphDelta(
                project_id="graph_only_proj",
                branch="main",
                nodes=[
                    GraphNode(
                        id="src/auth.py::TokenVerifier",
                        project_id="graph_only_proj",
                        label="TokenVerifier",
                        file_type="code",
                        source_file="src/auth.py",
                        source_location="L42",
                        metadata={"kind": "class"},
                    )
                ],
            ),
        )
    finally:
        store2.close()

    sym_only_res = client.post(
        "/pm/search",
        json={"query": "TokenVerifier", "user_id": "graph_only_proj:%"},
    )
    assert sym_only_res.status_code == 200
    sym_data = sym_only_res.json()
    assert sym_data["provider_used"] == "local_rag"
    assert sym_data["source_memories"] == []
    assert len(sym_data["graph_matches"]) == 1
    assert sym_data["graph_matches"][0]["label"] == "TokenVerifier"
    assert "TokenVerifier" in sym_data["results"]

    # 3. PM Copilot Document Generation WITHOUT GEMINI_API_KEY -> HTTP 200 across all doc types
    for doc_type, expected_heading in (
        ("status_report", "# Project Status Report"),
        ("prd", "# Product Requirements Document (PRD)"),
        ("user_story", "# User Stories & Acceptance Criteria"),
        ("roadmap", "# Product & Engineering Roadmap"),
    ):
        doc_res = client.post(
            "/pm/generate-doc",
            json={
                "prompt": "ProjectDatabaseManager tenant isolation and PostgreSQL scaling",
                "doc_type": doc_type,
                "user_id": "proj_alpha:main",
            },
        )
        assert doc_res.status_code == 200
        d_data = doc_res.json()
        assert d_data["provider_used"] == "local_rag"
        assert expected_heading in d_data["document"]
        assert len(d_data["source_memories"]) >= 1


# ============================================================================
# 3. Saving, Masking, Preserving, Clearing Keys + RBAC & Startup Sync
# ============================================================================


def test_put_ai_settings_masks_preserves_clears_and_persists(client: TestClient):
    raw_gemini_key = "AIzaSyTestSecretKey_99887766"

    # 1. Save Gemini key and provider="gemini"
    put_res = client.put(
        "/pm/ai-settings",
        json={
            "provider": "gemini",
            "gemini_key": raw_gemini_key,
            "gemini_model": "gemini-2.5-flash",
            "emb_kind": "synthetic",
        },
    )
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["has_gemini_key"] is True
    assert put_data["gemini_key_masked"] == "AIza••••7766"
    assert raw_gemini_key not in json.dumps(put_data)
    assert put_data["provider"] == "gemini"
    assert put_data["resolved_provider"] == "gemini"
    assert env.gemini_key == raw_gemini_key
    assert os.environ.get("GEMINI_API_KEY") == raw_gemini_key

    # Audit log recorded without leaking raw secret
    logs_res = client.get("/auth/audit-logs?event_type=ai_settings_updated")
    assert logs_res.status_code == 200
    logs = logs_res.json()["logs"]
    assert len(logs) >= 1
    assert raw_gemini_key not in json.dumps(logs)

    # 2. Saving other fields with gemini_key="" preserves existing key
    put_preserve = client.put(
        "/pm/ai-settings",
        json={
            "gemini_key": "",
            "gemini_model": "gemini-2.5-pro",
        },
    )
    assert put_preserve.status_code == 200
    pres_data = put_preserve.json()
    assert pres_data["has_gemini_key"] is True
    assert pres_data["gemini_key_masked"] == "AIza••••7766"
    assert pres_data["gemini_model"] == "gemini-2.5-pro"
    assert env.gemini_key == raw_gemini_key

    # 3. Simulate server restart: clear runtime env, then sync_ai_settings_from_db()
    env.gemini_key = ""
    os.environ.pop("GEMINI_API_KEY", None)
    sync_ai_settings_from_db()
    assert env.gemini_key == raw_gemini_key
    assert os.environ.get("GEMINI_API_KEY") == raw_gemini_key

    # 4. Clear Gemini key with clear_gemini_key=True
    put_clear = client.put(
        "/pm/ai-settings",
        json={
            "provider": "auto",
            "clear_gemini_key": True,
        },
    )
    assert put_clear.status_code == 200
    clr_data = put_clear.json()
    assert clr_data["has_gemini_key"] is False
    assert clr_data["gemini_key_masked"] == ""
    assert clr_data["resolved_provider"] == "local_rag"
    assert env.gemini_key == ""
    assert "GEMINI_API_KEY" not in os.environ


def test_ai_settings_rbac_enforcement_for_reader_collaborator_and_admin(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    # Create reader and collaborator accounts while admin auth is configured
    admin_headers = {"Authorization": "Bearer pb_tok_admin"}
    r_collab = client.post(
        "/auth/accounts",
        headers=admin_headers,
        json={"username": "collab_user", "token": "pb_tok_collab_test", "role": "collaborator"},
    )
    assert r_collab.status_code == 200

    r_reader = client.post(
        "/auth/accounts",
        headers=admin_headers,
        json={"username": "reader_user", "token": "pb_tok_reader_test", "role": "reader"},
    )
    assert r_reader.status_code == 200

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    monkeypatch.setattr(env, "require_rest_auth", True)

    # Unauthenticated -> 401
    assert client.get("/pm/ai-settings").status_code == 401
    assert client.put("/pm/ai-settings", json={"provider": "local_rag"}).status_code == 401
    assert client.post("/pm/ai-settings/test", json={"provider": "local_rag"}).status_code == 401

    collab_headers = {"Authorization": "Bearer pb_tok_collab_test"}
    reader_headers = {"Authorization": "Bearer pb_tok_reader_test"}

    # Reader & Collaborator can GET /pm/ai-settings (masked status)
    assert client.get("/pm/ai-settings", headers=collab_headers).status_code == 200
    assert client.get("/pm/ai-settings", headers=reader_headers).status_code == 200

    # Reader & Collaborator are blocked (403) from PUT /pm/ai-settings and POST /pm/ai-settings/test
    assert client.put("/pm/ai-settings", headers=collab_headers, json={"provider": "local_rag"}).status_code == 403
    assert client.put("/pm/ai-settings", headers=reader_headers, json={"provider": "local_rag"}).status_code == 403
    assert client.post("/pm/ai-settings/test", headers=collab_headers, json={"provider": "local_rag"}).status_code == 403
    assert client.post("/pm/ai-settings/test", headers=reader_headers, json={"provider": "local_rag"}).status_code == 403

    # Admin succeeds (200)
    ok_put = client.put("/pm/ai-settings", headers=admin_headers, json={"provider": "local_rag"})
    assert ok_put.status_code == 200
    ok_test = client.post("/pm/ai-settings/test", headers=admin_headers, json={"provider": "local_rag"})
    assert ok_test.status_code == 200
    assert ok_test.json()["ok"] is True


# ============================================================================
# 4. Connection Diagnostics (`POST /pm/ai-settings/test`) & Multi-Provider Dispatch
# ============================================================================


def test_ai_settings_test_endpoint_local_rag_and_mocked_providers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    # 1. Test local_rag directly
    res_local = client.post("/pm/ai-settings/test", json={"provider": "local_rag"})
    assert res_local.status_code == 200
    d_local = res_local.json()
    assert d_local["ok"] is True
    assert d_local["provider_used"] == "local_rag"
    assert d_local["error"] is None
    assert isinstance(d_local["latency_ms"], (int, float))

    # 2. Test explicit gemini without key -> HTTP 200 with ok=False (clean UI diagnostic)
    res_missing = client.post("/pm/ai-settings/test", json={"provider": "gemini"})
    assert res_missing.status_code == 200
    d_missing = res_missing.json()
    assert d_missing["ok"] is False
    assert d_missing["provider_used"] == "gemini"
    assert "missing" in (d_missing["error"] or "").lower()

    # 3. Test mocked GeminiAdapter.chat with unsaved form key
    async def _mock_gemini_chat(self, messages: List[Dict[str, str]], model: str = None, **kwargs) -> str:
        assert self.api_key == "AIzaUnsavedFormKey12345"
        return f"Gemini ACK from {model}"

    monkeypatch.setattr(GeminiAdapter, "chat", _mock_gemini_chat)

    res_gem = client.post(
        "/pm/ai-settings/test",
        json={
            "provider": "gemini",
            "gemini_key": "AIzaUnsavedFormKey12345",
            "gemini_model": "gemini-2.5-flash",
        },
    )
    assert res_gem.status_code == 200
    d_gem = res_gem.json()
    assert d_gem["ok"] is True
    assert d_gem["provider_used"] == "gemini"
    assert d_gem["model_used"] == "gemini-2.5-flash"
    assert "Gemini ACK" in d_gem["reply"]
    assert d_gem["error"] is None


def test_multi_provider_auto_chain_and_fallback_behavior(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    # Seed a memory so /pm/search runs RAG synthesis
    client.post(
        "/memory/add",
        json={"content": "Release v3.2 ships unified multi-provider AI routing.", "user_id": "p1:main"},
    )

    # A) Auto mode picks OpenAI when OPENAI_API_KEY is set and GEMINI_API_KEY is absent
    async def _mock_openai_chat(self, messages: List[Dict[str, str]], model: str = None, **kwargs) -> str:
        return f"OpenAI answer via {model}"

    monkeypatch.setattr(OpenAIAdapter, "chat", _mock_openai_chat)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openaiTestKey1234567890")

    s_openai = client.post("/pm/search", json={"query": "Release v3.2", "user_id": "p1:main"})
    assert s_openai.status_code == 200
    assert s_openai.json()["provider_used"] == "openai"
    assert "OpenAI answer" in s_openai.json()["results"]

    # B) Auto mode picks Ollama when no cloud keys exist and Ollama is reachable with models
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _online_ollama_probe(self, timeout: float = 2.0) -> Dict[str, Any]:
        return {
            "reachable": True,
            "base_url": self.base_url,
            "models": ["qwen2.5-coder:7b", "llama3.2:latest"],
            "vision_models": [],
            "error": None,
        }

    async def _mock_ollama_chat(self, messages: List[Dict[str, Any]], model: str = None, **kwargs) -> str:
        return f"Ollama offline-first answer ({model})"

    monkeypatch.setattr(OllamaAdapter, "check_health_and_models", _online_ollama_probe)
    monkeypatch.setattr(OllamaAdapter, "chat", _mock_ollama_chat)

    s_ollama = client.post("/pm/search", json={"query": "Release v3.2", "user_id": "p1:main"})
    assert s_ollama.status_code == 200
    assert s_ollama.json()["provider_used"] == "ollama"
    assert s_ollama.json()["model_used"] == "llama3.2:latest"
    assert "Ollama offline-first answer" in s_ollama.json()["results"]

    # C) Provider failure with fallback_to_local_rag=True falls back cleanly & redacts secret
    secret_key = "AIzaBrokenKeySecret999999"

    async def _failing_gemini_chat(self, messages: List[Dict[str, str]], model: str = None, **kwargs) -> str:
        raise RuntimeError(f"Upstream 403 invalid key={secret_key}")

    monkeypatch.setattr(GeminiAdapter, "chat", _failing_gemini_chat)
    client.put(
        "/pm/ai-settings",
        json={
            "provider": "gemini",
            "gemini_key": secret_key,
            "fallback_to_local_rag": True,
        },
    )

    s_fb = client.post("/pm/search", json={"query": "Release v3.2", "user_id": "p1:main"})
    assert s_fb.status_code == 200
    fb_data = s_fb.json()
    assert fb_data["provider_used"] == "local_rag"
    assert fb_data["fallback_reason"] is not None
    assert secret_key not in fb_data["fallback_reason"]
    assert "[REDACTED]" in fb_data["fallback_reason"]
    assert "### Executive Summary" in fb_data["results"]

    # D) Explicit provider failure with fallback_to_local_rag=False surfaces sanitized error
    client.put("/pm/ai-settings", json={"fallback_to_local_rag": False})
    s_strict = client.post("/pm/search", json={"query": "Release v3.2", "user_id": "p1:main"})
    assert s_strict.status_code == 500
    assert secret_key not in s_strict.text
    assert "[REDACTED]" in s_strict.json()["detail"]

    # E) MiniMax explicit provider works when configured
    async def _mock_minimax_chat(self, messages: List[Dict[str, str]], model: str = None, **kwargs) -> str:
        return f"MiniMax synthesis ({model})"

    monkeypatch.setattr(MiniMaxAdapter, "chat", _mock_minimax_chat)
    client.put(
        "/pm/ai-settings",
        json={
            "provider": "minimax",
            "minimax_key": "mmx_secret_key_123456789",
            "minimax_model": "MiniMax-Text-01",
            "fallback_to_local_rag": True,
        },
    )
    s_mmx = client.post("/pm/search", json={"query": "Release v3.2", "user_id": "p1:main"})
    assert s_mmx.status_code == 200
    assert s_mmx.json()["provider_used"] == "minimax"
    assert "MiniMax synthesis" in s_mmx.json()["results"]
