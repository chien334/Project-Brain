"""Audit Remediation Verification Suite (Waves 1, 2, and 3).

Covers all remediations across:
- Wave 1: Temporal graph factual queries, sources.py connector/webhook imports &
  truthful graphify_synced reporting, reflection/decay/audio extraction fixes,
  CrewAIMemory & ProjectBrainRetriever sync/async loop bridging, en2ja comment
  translation prompt, HTTP 404 memory routes, CLI _get_positional_args, and
  SQLiteVectorStore.getVectorsBySector.
- Wave 2: Gemini error key redaction, PB_REQUIRE_REST_AUTH RBAC enforcement,
  _is_write_tool & FastMCP reader role blocking, PB_READ_ONLY enforcement,
  MCP config secret masking & shell metacharacter rejection, sibling-prefix path
  traversal protection in projectbrain_sync_codegraph, and SSRF URL validation.
- Wave 3: Multi-tenant SimHash deduplication isolation, 64-bit SimHash & bounded
  OrderedDict query cache invalidation, ValkeyVectorStore composite sector keys,
  Memory.delete / delete_all vector cleanup + cache invalidation, and
  SqliteOverlayGraphStore connection lifecycle management.
"""

from __future__ import annotations

import fnmatch
import io
import json
import shutil
import sqlite3
import sys
import time
import warnings
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import projectbrain.main as main_mod
from extensions_mcp.codebase_migration_helper import server as migration_server
from projectbrain.ai import gemini
from projectbrain.ai.mcp import projectbrain_query, projectbrain_sync_codegraph
from projectbrain.connectors import web_crawler
from projectbrain.connectors.agents import CrewAIMemory as ConnectorCrewAIMemory
from projectbrain.core.config import env
from projectbrain.core.db import db, log_maint_op, q
from projectbrain.core.vector.valkey import ValkeyVectorStore
from projectbrain.core.vector_store import SQLiteVectorStore, VectorRow
from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.graphify_engine.store import SqliteOverlayGraphStore
from projectbrain.integrations.agents import CrewAIMemory
from projectbrain.integrations.langchain import ProjectBrainRetriever
from projectbrain.main import Memory, _get_positional_args
from projectbrain.memory import decay, hsg, reflect
from projectbrain.memory.hsg import (
    add_hsg_memory,
    compute_simhash,
    invalidate_query_cache,
)
from projectbrain.ops import extract as extract_mod
from projectbrain.server.api import _is_write_tool, create_app
from projectbrain.server.routes import mcp_registry
from projectbrain.temporal_graph import insert_fact, query_facts_at_time


# =====================================================================
# Strictly Isolated Per-Test Database & Environment Fixture
# =====================================================================


@pytest.fixture(autouse=True)
def isolated_audit_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test to a fresh SQLite DB in tmp_path and restore global state."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    temp_db_path = tmp_path / "audit_remediation.db"
    temp_graph_db_path = tmp_path / "audit_graph.db"
    claude_cfg_path = tmp_path / "claude_desktop_config.json"

    monkeypatch.setenv("PB_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("PB_GRAPH_DB_PATH", str(temp_graph_db_path))
    monkeypatch.setenv("OM_GRAPH_DB_PATH", str(temp_graph_db_path))
    monkeypatch.delenv("PB_REQUIRE_REST_AUTH", raising=False)
    monkeypatch.delenv("PB_READ_ONLY", raising=False)
    monkeypatch.delenv("OM_READ_ONLY", raising=False)
    monkeypatch.delenv("PB_ALLOW_PRIVATE_CRAWL", raising=False)

    monkeypatch.setattr(env, "database_url", f"sqlite:///{temp_db_path}")
    monkeypatch.setattr(env, "db_path", str(temp_db_path))
    monkeypatch.setattr(env, "emb_kind", "synthetic")
    monkeypatch.setattr(mcp_registry, "get_claude_config_path", lambda: claude_cfg_path)

    # Detach existing connection without closing it so other test modules remain unaffected
    db.conn = None
    db.is_postgres = False
    db.connect()
    invalidate_query_cache()

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
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        invalidate_query_cache()


# =====================================================================
# 1. Wave 1: Temporal Graph & projectbrain_query(type="factual")
# =====================================================================


@pytest.mark.asyncio
async def test_temporal_graph_insert_and_factual_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Prevent git branch auto-suffixing in resolve_mcp_user_id so "u_fact" matches literally
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    fact_id = await insert_fact(
        "ServiceA", "uses", "Postgres", valid_from=1_000_000, user_id="u_fact"
    )
    assert isinstance(fact_id, str) and fact_id

    queried = await query_facts_at_time(
        subject="ServiceA",
        predicate="uses",
        subject_object="Postgres",
        at=2_000_000,
        user_id="u_fact",
    )
    assert len(queried) == 1
    assert queried[0]["id"] == fact_id
    assert queried[0]["subject"] == "ServiceA"
    assert queried[0]["predicate"] == "uses"
    assert queried[0]["object"] == "Postgres"

    raw_response = await projectbrain_query(
        query="Postgres",
        type="factual",
        fact_pattern={
            "subject": "ServiceA",
            "predicate": "uses",
            "object": "Postgres",
        },
        at=None,
        user_id="u_fact",
    )
    assert not raw_response.startswith("Error:"), raw_response
    _, json_blob = raw_response.split("\n\n", 1)
    parsed = json.loads(json_blob)

    assert parsed["type"] == "factual"
    factual_items = parsed["factual"]
    assert len(factual_items) >= 1
    assert any(
        item["subject"] == "ServiceA"
        and item["predicate"] == "uses"
        and item["object"] == "Postgres"
        for item in factual_items
    )


# =====================================================================
# 2. Wave 1: routes/sources.py Imports & Truthful graphify_synced
# =====================================================================


def test_sources_ingest_unknown_and_webhooks_clean_imports():
    app = create_app()
    client = TestClient(app)

    resp_unknown = client.post(
        "/sources/unknown_src/ingest",
        json={"creds": {}, "filters": {}, "user_id": "u_src"},
    )
    assert resp_unknown.status_code == 400
    assert "unknown source" in resp_unknown.json()["detail"].lower()

    resp_gh = client.post(
        "/sources/webhook/github",
        headers={"x-github-event": "ping"},
        json={"zen": "Responsive is better than fast."},
    )
    assert resp_gh.status_code == 200
    assert resp_gh.json()["ok"] is True

    resp_notion = client.post(
        "/sources/webhook/notion",
        json={"type": "page.content_updated", "entity": {"id": "page-123"}},
    )
    assert resp_notion.status_code == 200
    assert resp_notion.json()["ok"] is True


def test_sources_upload_reports_graphify_synced_false_on_extractor_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    from projectbrain.memory import hsg as hsg_mod

    monkeypatch.setattr(
        hsg_mod,
        "add_hsg_memory",
        AsyncMock(return_value={"id": "mem_upload_ok", "root_memory_id": "mem_upload_ok"}),
    )
    monkeypatch.setattr(
        GraphifyExtractor,
        "extract_document_graph",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    app = create_app()
    client = TestClient(app)

    files = {
        "file": (
            "design_spec.md",
            io.BytesIO(b"# Architecture\nSystem overview and boundaries."),
            "text/markdown",
        )
    }
    resp = client.post(
        "/sources/upload",
        data={"project_id": "proj_upload", "branch": "main"},
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["memory_id"] == "mem_upload_ok"
    assert body["graphify_synced"] is False


# =====================================================================
# 3. Wave 1: reflect.py, decay.py, extract.py, CrewAIMemory, Retriever
# =====================================================================


@pytest.mark.asyncio
async def test_reflect_log_maint_op_and_run_reflection(monkeypatch: pytest.MonkeyPatch):
    log_maint_op("reflect", 2)
    stat_rows = db.fetchall("SELECT * FROM stats")
    assert len(stat_rows) == 1
    assert json.loads(stat_rows[0]["metrics"]) == {"op": "reflect", "count": 2}

    now_ms = int(time.time() * 1000)
    # Insert two high-overlap semantic memories (sim_txt > 0.8) so cluster() groups them
    q.ins_mem(
        id="ref_m1",
        user_id="u_ref",
        segment=0,
        content="alpha beta gamma delta epsilon zeta eta theta iota cluster pattern",
        simhash="0000000000000001",
        primary_sector="semantic",
        tags=json.dumps(["test"]),
        meta=json.dumps({}),
        created_at=now_ms,
        updated_at=now_ms,
        last_seen_at=now_ms,
        salience=0.6,
        decay_lambda=0.02,
    )
    q.ins_mem(
        id="ref_m2",
        user_id="u_ref",
        segment=0,
        content="alpha beta gamma delta epsilon zeta eta theta iota cluster summary",
        simhash="0000000000000002",
        primary_sector="semantic",
        tags=json.dumps(["test"]),
        meta=json.dumps({}),
        created_at=now_ms,
        updated_at=now_ms,
        last_seen_at=now_ms,
        salience=0.6,
        decay_lambda=0.02,
    )

    monkeypatch.setattr(env, "reflect_min", 2)
    monkeypatch.setattr(env, "reflect_interval", 5)

    res = await reflect.run_reflection()
    assert res["created"] == 1
    assert res["clusters"] == 1


@pytest.mark.asyncio
async def test_apply_decay_executes_without_missing_summary_column(
    monkeypatch: pytest.MonkeyPatch,
):
    # Populate a cold stale memory with a stored vector so both vector compression and
    # fingerprint_mem execute without OperationalError on missing 'summary' column
    stale_ts = 1_000
    q.ins_mem(
        id="decay_m1",
        user_id="u_decay",
        segment=0,
        content="Legacy authentication subsystem specification for decay verification",
        simhash="abcdef0123456789",
        primary_sector="semantic",
        tags=json.dumps([]),
        meta=json.dumps({}),
        created_at=stale_ts,
        updated_at=stale_ts,
        last_seen_at=stale_ts,
        salience=0.2,
        decay_lambda=0.05,
        feedback_score=0,
    )
    vs = SQLiteVectorStore()
    await vs.storeVector("decay_m1", "semantic", [0.1] * 128, 128, user_id="u_decay")

    monkeypatch.setattr(env, "decay_ratio", 1.0)
    decay.active_q = 0
    decay.last_decay = 0

    await decay.apply_decay()

    updated = q.get_mem("decay_m1")
    assert updated is not None
    assert updated["salience"] <= 0.2


@pytest.mark.asyncio
async def test_extract_audio_openai_key_validation_and_env_pickup(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(env, "openai_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OpenAI API key required"):
        await extract_mod.extract_audio(b"audio", "audio/mp3")

    captured_keys: List[Optional[str]] = []

    class FakeAsyncOpenAI:
        def __init__(self, api_key: Optional[str] = None, **kwargs: Any):
            captured_keys.append(api_key)
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(
                    create=AsyncMock(
                        return_value=SimpleNamespace(
                            text="transcribed audio text",
                            duration=2.5,
                            language="en",
                        )
                    )
                )
            )

    monkeypatch.setattr(env, "openai_key", "sk-test-env-openai-key")
    monkeypatch.setattr(extract_mod, "AsyncOpenAI", FakeAsyncOpenAI)

    result = await extract_mod.extract_audio(b"audio", "audio/mp3")
    assert captured_keys == ["sk-test-env-openai-key"]
    assert result["text"] == "transcribed audio text"
    assert result["metadata"]["extraction_method"] == "whisper"


def _make_memory_mock() -> MagicMock:
    mem_mock = MagicMock(spec=Memory)
    mem_mock.add = AsyncMock(return_value={"id": "m_sync_1"})
    mem_mock.search = AsyncMock(
        return_value=[{"id": "m_sync_1", "content": "hello from memory"}]
    )
    return mem_mock


def test_crewai_memory_and_retriever_outside_event_loop():
    mem_mock = _make_memory_mock()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        crew_mem = CrewAIMemory(mem_mock, user_id="u1")
        crew_mem.save("hello")
        assert crew_mem.search("hello") == ["hello from memory"]

        conn_crew_mem = ConnectorCrewAIMemory(mem_mock, user_id="u1")
        conn_crew_mem.save("hello")
        assert conn_crew_mem.search("hello") == ["hello from memory"]

        retriever = ProjectBrainRetriever(memory=mem_mock, user_id="u1")
        docs = retriever._get_relevant_documents("hello")
        assert len(docs) == 1
        assert docs[0].page_content == "hello from memory"

    unawaited = [w for w in caught if "never awaited" in str(w.message)]
    assert not unawaited


@pytest.mark.asyncio
async def test_crewai_memory_and_retriever_inside_active_event_loop():
    mem_mock = _make_memory_mock()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        crew_mem = CrewAIMemory(mem_mock, user_id="u1")
        crew_mem.save("hello")
        assert crew_mem.search("hello") == ["hello from memory"]

        retriever = ProjectBrainRetriever(memory=mem_mock, user_id="u1")
        docs = retriever._get_relevant_documents("hello")
        assert len(docs) == 1
        assert docs[0].page_content == "hello from memory"

    unawaited = [w for w in caught if "never awaited" in str(w.message)]
    assert not unawaited


# =====================================================================
# 4. Wave 1: en2ja Translation, HTTP 404, CLI Args, SQLiteVectorStore
# =====================================================================


@pytest.mark.asyncio
async def test_translate_code_comments_en2ja_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sample_py = tmp_path / "sample.py"
    sample_py.write_text(
        "# Compute checksum for payload\ndef checksum(data: bytes) -> int:\n    return sum(data)\n",
        encoding="utf-8",
    )

    captured: Dict[str, Any] = {}

    async def fake_call_gemini(prompt: str, system_instruction: Optional[str] = None) -> str:
        captured["prompt"] = prompt
        captured["system_prompt"] = system_instruction
        return "# ペイロードのチェックサムを計算する\ndef checksum(data: bytes) -> int:\n    return sum(data)\n"

    monkeypatch.setattr(migration_server, "call_gemini", fake_call_gemini)

    res = await migration_server.translate_code_comments(
        file_path=str(sample_py),
        direction="en2ja",
    )
    assert res["direction"] == "en2ja"
    assert "translated_code" in res
    sys_prompt = captured.get("system_prompt") or ""
    assert "in English" in sys_prompt
    assert "to clear developer-friendly Japanese" in sys_prompt


def test_memory_get_and_reinforce_nonexistent_return_404():
    app = create_app()
    client = TestClient(app)

    res_get = client.get("/memory/nonexistent-id-404")
    assert res_get.status_code == 404

    res_reinforce = client.post("/memory/nonexistent-id-404/reinforce")
    assert res_reinforce.status_code == 404


def test_cli_get_positional_args_skips_value_and_boolean_flags():
    result = _get_positional_args(["my-proj", "--branch", "feat/x", "--verbose"])
    assert result == ["my-proj"]


@pytest.mark.asyncio
async def test_sqlite_vector_store_get_vectors_by_sector():
    vs = SQLiteVectorStore()
    await vs.storeVector("v_sem_1", "semantic", [0.25, 0.5, 0.75, 1.0], 4, user_id="u_vec")
    await vs.storeVector("v_epi_1", "episodic", [1.0, 0.0, 0.0, 0.0], 4, user_id="u_vec")

    rows = await vs.getVectorsBySector("semantic")
    assert len(rows) == 1
    assert isinstance(rows[0], VectorRow)
    assert rows[0].id == "v_sem_1"
    assert rows[0].sector == "semantic"
    assert rows[0].dim == 4
    assert rows[0].vector == pytest.approx([0.25, 0.5, 0.75, 1.0])


# =====================================================================
# 5. Wave 2: Security Hardening
# =====================================================================


def test_gemini_sanitize_error_message_redacts_query_key_and_token():
    raw = "https://api.example.com?key=sample-tok-123 failed with sample-tok-123"
    sanitized = gemini._sanitize_error_message(raw, "sample-tok-123")

    assert "sample-tok-123" not in sanitized
    assert "?key=[REDACTED]" in sanitized
    assert sanitized == "https://api.example.com?key=[REDACTED] failed with [REDACTED]"


def test_rest_auth_rbac_matrix(monkeypatch: pytest.MonkeyPatch):
    app = create_app()
    client = TestClient(app)

    admin_headers = {"Authorization": "Bearer pb_tok_admin"}
    reader_headers = {"Authorization": "Bearer pb_tok_audit_reader"}
    collab_headers = {"Authorization": "Bearer pb_tok_audit_collab"}

    # Seed reader and collaborator accounts while unauthenticated or via admin
    assert (
        client.post(
            "/auth/accounts",
            json={
                "username": "audit_reader",
                "token": "pb_tok_audit_reader",
                "role": "reader",
                "allowed_tools": "*",
            },
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/accounts",
            json={
                "username": "audit_collab",
                "token": "pb_tok_audit_collab",
                "role": "collaborator",
                "allowed_tools": "*",
            },
            headers=admin_headers,
        ).status_code
        == 200
    )

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")

    # Anonymous GET /memory/stats -> 401
    assert client.get("/memory/stats").status_code == 401

    # Reader POST /memory/add -> 403, GET /memory/stats -> 200
    assert (
        client.post(
            "/memory/add", json={"content": "should fail"}, headers=reader_headers
        ).status_code
        == 403
    )
    assert client.get("/memory/stats", headers=reader_headers).status_code == 200

    # Reader & Collaborator GET /auth/accounts and GET /memory/mcp/status -> 403
    for hdrs in (reader_headers, collab_headers):
        assert client.get("/auth/accounts", headers=hdrs).status_code == 403
        assert client.get("/memory/mcp/status", headers=hdrs).status_code == 403

    # Admin -> 200
    assert client.get("/auth/accounts", headers=admin_headers).status_code == 200
    assert client.get("/memory/mcp/status", headers=admin_headers).status_code == 200

    # Public /health -> 200 without auth
    assert client.get("/health").status_code == 200


def test_is_write_tool_classification_and_mcp_reader_403():
    expected_write_tools = [
        "docx_create_document",
        "markdown_convert_to_excel",
        "pptx_create_presentation",
        "pdf_extract_images",
        "pptx_extract_images",
        "ocr_image_to_markdown",
        "ocr_pdf_to_markdown",
        "img2md_extract_image",
        "img2md_extract_folder",
        "migration_batch_scan_logic",
        "migration_plan_execution_phases",
    ]
    for tool_name in expected_write_tools:
        assert _is_write_tool(tool_name) is True, f"Expected {tool_name} to be a write tool"

    assert _is_write_tool("projectbrain_query") is False
    assert _is_write_tool("docx_extract_text") is False

    app = create_app()
    client = TestClient(app)
    res_acc = client.post(
        "/auth/accounts",
        json={
            "username": "mcp_reader_docx",
            "token": "pb_tok_mcp_reader_docx",
            "role": "reader",
            "allowed_tools": "*",
        },
    )
    assert res_acc.status_code == 200

    res_mcp = client.post(
        "/mcp/messages/?session_id=s1",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "docx_create_document",
                "arguments": {"file_path": "out.docx"},
            },
            "id": 42,
        },
        headers={"Authorization": "Bearer pb_tok_mcp_reader_docx"},
    )
    assert res_mcp.status_code == 403
    assert "Read-only accounts cannot execute modifying tool" in res_mcp.json()["error"]["message"]


def test_pb_read_only_blocks_memory_and_source_mutations(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PB_READ_ONLY", "true")
    app = create_app()
    client = TestClient(app)

    res_mem = client.post("/memory/add", json={"content": "blocked write"})
    assert res_mem.status_code == 403

    res_src = client.post("/sources/web/ingest", json={"creds": {}, "filters": {}})
    assert res_src.status_code == 403


def test_mcp_registry_sanitization_and_command_validation():
    raw_cfg = {
        "command": "npx",
        "args": [
            "-y",
            "mcp-remote",
            "https://user:pass@mcp.internal.example/sse?token=super_secret_tok",
            "--api-key=sk_live_987654",
        ],
        "env": {
            "GEMINI_API_KEY": "AIzaSySecretKey999",
            "PB_DEFAULT_USER_ID": "proj-1",
        },
    }
    sanitized = mcp_registry._sanitize_mcp_config_for_response(raw_cfg)
    serialized = json.dumps(sanitized)

    assert sanitized["env"]["GEMINI_API_KEY"] == "********"
    assert sanitized["env"]["PB_DEFAULT_USER_ID"] == "proj-1"
    assert "AIzaSySecretKey999" not in serialized
    assert "super_secret_tok" not in serialized
    assert "?token=********" in serialized
    assert "user:pass@" not in serialized
    assert "https://****@mcp.internal.example" in serialized
    assert "sk_live_987654" not in serialized
    assert "--api-key=********" in serialized

    with pytest.raises(HTTPException) as exc_info:
        mcp_registry._validate_external_command("npx; echo hello")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_projectbrain_sync_codegraph_rejects_sibling_prefix_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root_dir = tmp_path / "projects"
    sibling_dir = tmp_path / "projects_evil"
    root_dir.mkdir()
    sibling_dir.mkdir()

    monkeypatch.setenv("PB_PROJECTS_ROOT_DIR", str(root_dir))

    res = await projectbrain_sync_codegraph(
        project_id="evil",
        project_path=str(sibling_dir),
    )
    assert "Error: Security violation" in res


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://127.0.0.1:8080/",
        "http://10.0.0.1/",
        "http://169.254.169.254/metadata",
        "http://localhost:8000/",
        "http://[::1]:8000/",
        "ftp://example.com/resource",
    ],
)
def test_web_crawler_validate_public_url_blocks_ssrf_targets(blocked_url: str):
    with pytest.raises(ValueError):
        web_crawler._validate_public_url(blocked_url)


# =====================================================================
# 6. Wave 3: Multi-Tenant Integrity, Vector Sync, SQLite Lifecycle
# =====================================================================


@pytest.mark.asyncio
async def test_multitenant_add_hsg_memory_dedup_isolation(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_embed_multi_sector(
        mid: str, txt: str, secs: List[str], chunks: Any = None
    ) -> List[Dict[str, Any]]:
        return [{"sector": s, "vector": [0.1, 0.2, 0.3, 0.4], "dim": 4} for s in secs]

    fake_store = SimpleNamespace(storeVector=AsyncMock())
    monkeypatch.setattr(hsg, "embed_multi_sector", fake_embed_multi_sector)
    monkeypatch.setattr(hsg, "store", fake_store)
    monkeypatch.setattr(hsg, "create_single_waypoint", AsyncMock())
    monkeypatch.setattr(hsg, "create_inter_mem_waypoints", AsyncMock())
    monkeypatch.setattr(hsg, "update_user_summary", AsyncMock())

    text = "Identical system design specification v1"
    res_a = await add_hsg_memory(text, user_id="projA")
    res_b = await add_hsg_memory(text, user_id="projB")

    assert res_b["id"] != res_a["id"]
    assert not res_b.get("deduplicated")

    res_a_repeat = await add_hsg_memory(text, user_id="projA")
    assert res_a_repeat.get("deduplicated") is True
    assert res_a_repeat["id"] == res_a["id"]


def test_simhash_64bit_distribution_and_query_cache_invalidation():
    hashes = [compute_simhash(f"uniqueword{i}") for i in range(64)]
    assert all(len(h) == 16 and all(c in "0123456789abcdef" for c in h) for h in hashes)
    assert len(set(hashes)) == 64

    ints = [int(h, 16) for h in hashes]
    bits_ever_one = 0
    bits_ever_zero = 0
    mask_64 = (1 << 64) - 1
    for val in ints:
        bits_ever_one |= val
        bits_ever_zero |= (~val) & mask_64
    assert bits_ever_one == mask_64
    assert bits_ever_zero == mask_64

    assert hsg.MAX_CACHE_SIZE == 512
    assert isinstance(hsg.cache, OrderedDict)

    hsg.cache["sample_query_key"] = {"r": [{"id": "m1"}], "t": time.time() * 1000}
    assert len(hsg.cache) == 1
    invalidate_query_cache()
    assert len(hsg.cache) == 0


class _FakeRedisPipeline:
    def __init__(self, store_dict: Dict[str, Dict[str, Any]]):
        self._store = store_dict
        self._ops: List[str] = []

    def hgetall(self, key: str) -> "_FakeRedisPipeline":
        self._ops.append(key)
        return self

    async def execute(self) -> List[Dict[str, Any]]:
        return [dict(self._store.get(k, {})) for k in self._ops]


class _FakeAsyncRedisClient:
    def __init__(self):
        self.hashes: Dict[str, Dict[str, Any]] = {}

    async def hset(self, key: str, mapping: Dict[str, Any]) -> int:
        self.hashes[key] = dict(mapping)
        return len(mapping)

    async def hgetall(self, key: str) -> Dict[str, Any]:
        return dict(self.hashes.get(key, {}))

    async def scan(
        self, cursor: int = 0, match: str = "*", count: int = 100
    ) -> Tuple[int, List[str]]:
        matched = [k for k in self.hashes if fnmatch.fnmatchcase(k, match)]
        return 0, matched

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self.hashes)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self.hashes:
                del self.hashes[k]
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_valkey_vector_store_composite_sector_hashes():
    vs = ValkeyVectorStore("redis://localhost:6379")
    fake_redis = _FakeAsyncRedisClient()
    vs.client = fake_redis

    await vs.storeVector("doc_42", "semantic", [0.1, 0.2, 0.3], 3, user_id="projV")
    await vs.storeVector("doc_42", "procedural", [0.4, 0.5, 0.6], 3, user_id="projV")

    sem_key = f"{vs.prefix}doc_42:semantic"
    proc_key = f"{vs.prefix}doc_42:procedural"
    assert sem_key in fake_redis.hashes
    assert proc_key in fake_redis.hashes

    fetched = await vs.getVectorsById("doc_42")
    by_sector = {r.sector: r for r in fetched}
    assert set(by_sector.keys()) == {"semantic", "procedural"}
    assert by_sector["semantic"].vector == pytest.approx([0.1, 0.2, 0.3])
    assert by_sector["procedural"].vector == pytest.approx([0.4, 0.5, 0.6])

    await vs.deleteVectors("doc_42")
    assert sem_key not in fake_redis.hashes
    assert proc_key not in fake_redis.hashes
    assert await vs.getVectorsById("doc_42") == []


@pytest.mark.asyncio
async def test_memory_delete_and_delete_all_sync_vectors_and_invalidate_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    now_ms = int(time.time() * 1000)
    for mid, uid in [("m_del_single", "u_single"), ("m_del_all_1", "u_batch"), ("m_del_all_2", "u_batch")]:
        q.ins_mem(
            id=mid,
            user_id=uid,
            segment=0,
            content=f"content for {mid}",
            simhash=compute_simhash(mid),
            primary_sector="semantic",
            tags=json.dumps([]),
            meta=json.dumps({}),
            created_at=now_ms,
            updated_at=now_ms,
            last_seen_at=now_ms,
            salience=0.5,
            decay_lambda=0.02,
        )

    delete_vectors_mock = AsyncMock()
    invalidate_calls: List[int] = []

    def tracked_invalidate() -> None:
        invalidate_calls.append(1)
        invalidate_query_cache()

    monkeypatch.setattr(main_mod.vector_store, "deleteVectors", delete_vectors_mock)
    monkeypatch.setattr(main_mod, "invalidate_query_cache", tracked_invalidate)

    mem_client = Memory()

    hsg.cache["cached_q1"] = {"r": [], "t": now_ms}
    await mem_client.delete("m_del_single")
    delete_vectors_mock.assert_awaited_once_with("m_del_single")
    assert len(invalidate_calls) == 1
    assert len(hsg.cache) == 0
    assert q.get_mem("m_del_single") is None

    delete_vectors_mock.reset_mock()
    hsg.cache["cached_q2"] = {"r": [], "t": now_ms}
    await mem_client.delete_all(user_id="u_batch")
    deleted_ids = {call.args[0] for call in delete_vectors_mock.await_args_list}
    assert deleted_ids == {"m_del_all_1", "m_del_all_2"}
    assert len(invalidate_calls) == 2
    assert len(hsg.cache) == 0
    assert q.all_mem_by_user("u_batch") == []


def test_sqlite_overlay_graph_store_connection_lifecycle(tmp_path: Path):
    # 1. Explicit .close() closes owned connection and clears _conn
    owned_store = SqliteOverlayGraphStore(db_path=str(tmp_path / "owned_close.db"))
    raw_owned_conn = owned_store._conn
    assert raw_owned_conn is not None
    owned_store.close()
    assert owned_store._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        raw_owned_conn.execute("SELECT 1")

    # 2. Context manager closes owned connection on exit
    with SqliteOverlayGraphStore(db_path=str(tmp_path / "owned_ctx.db")) as ctx_store:
        raw_ctx_conn = ctx_store._conn
        assert raw_ctx_conn is not None
    assert ctx_store._conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        raw_ctx_conn.execute("SELECT 1")

    # 3. Externally injected sqlite3.Connection remains open across with-block and .close()
    ext_conn = sqlite3.connect(str(tmp_path / "external_injected.db"))
    ext_conn.row_factory = sqlite3.Row
    try:
        with SqliteOverlayGraphStore(conn=ext_conn) as ext_store:
            assert ext_store._conn is ext_conn
        ext_store.close()
        assert ext_conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        ext_conn.close()
