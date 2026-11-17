"""Comprehensive Integration Test Suite for Server /kb/* Routes, FastMCP Tools, CLI & Security.

Tests:
1. POST /kb/build: Full end-to-end KB synthesis, chunking, linking, and GraphStore synchronization
2. GET /kb/trace: Cross-layer traceability querying symbol AST, doc chunks, and tests
3. GET /kb/coverage: Repository-wide coverage metrics, gap statistics, and drift alerts
4. POST /kb/export: On-demand Wiki and multimodal Office suite generation
5. Security Guardrails: Path traversal rejection (HTTP 400) on workspace_path & output_dir (SEC-02)
6. Read-Only Enforcement: PB_READ_ONLY blocks mutating POST routes with HTTP 403
7. FastMCP RBAC: reader role blocks modifying tools (build_complete_kb, generate_project_docs)
   while permitting read tools (trace_code_and_docs, kb_coverage_report)
8. FastMCP Tools: Direct Python invocation of all 4 KB tools
9. CLI Subcommands: build-kb, kb-trace, kb-coverage execution and argument parsing
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from projectbrain.ai.mcp import (
    projectbrain_build_complete_kb,
    projectbrain_generate_project_docs,
    projectbrain_kb_coverage_report,
    projectbrain_trace_code_and_docs,
)
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.main import run_build_kb, run_kb_coverage, run_kb_trace
from projectbrain.server.api import create_app


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Set up an isolated workspace and fresh SQLite DB in tmp_path with monkeypatched chdir."""
    orig_db_url = env.database_url
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(ws)

    db_file = tmp_path / "test_kb_integration.db"
    monkeypatch.setenv("OM_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_READ_ONLY", "false")
    monkeypatch.setenv("OM_READ_ONLY", "false")

    env.database_url = f"sqlite:///{db_file}"
    db.conn = None
    db.connect()

    # Create sample codebase
    src_dir = ws / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "calculator.py").write_text(
        '"""Calculator core."""\n\n'
        'class Calculator:\n'
        '    """Arithmetic evaluator."""\n'
        '    def add(self, a: int, b: int) -> int:\n'
        '        return a + b\n\n'
        '    def subtract(self, a: int, b: int) -> int:\n'
        '        return a - b\n',
        encoding="utf-8",
    )

    tests_dir = ws / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_calculator.py").write_text(
        'from src.calculator import Calculator\n\n'
        'def test_calc():\n'
        '    c = Calculator()\n'
        '    assert c.add(1, 2) == 3\n',
        encoding="utf-8",
    )

    docs_dir = ws / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "spec.md").write_text(
        '# Technical Specification\n\n'
        '## Calculator Module\n\n'
        'The `Calculator` class provides arithmetic operations such as `Calculator.add`.\n'
        '| Method | Description |\n'
        '| --- | --- |\n'
        '| add | Adds two numbers |\n',
        encoding="utf-8",
    )

    yield {
        "workspace": ws,
        "db_file": db_file,
    }

    if db.conn:
        try:
            db.conn.close()
        except Exception:
            pass
        db.conn = None
    env.database_url = orig_db_url
    try:
        db.connect()
    except Exception:
        pass


@pytest.fixture
def app_client(isolated_workspace):
    """FastAPI TestClient fixture."""
    app = create_app()
    return TestClient(app)


# ============================================================================
# 1. Server Routes (/kb/*) Tests
# ============================================================================

def test_post_kb_build(app_client, isolated_workspace):
    """Verify POST /kb/build builds knowledge base, persists chunks, and writes manifest."""
    ws = isolated_workspace["workspace"]
    res = app_client.post(
        "/kb/build",
        json={
            "project_id": "route_test_proj",
            "branch": "main",
            "workspace_path": ".",
            "persist_memory": True,
            "output_dir": "docs/kb",
            "generate_office_exports": True,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert data["project_id"] == "route_test_proj"
    assert data["total_chunks"] >= 1
    assert data["total_cross_edges"] >= 1

    # Verify artifacts on filesystem
    manifest_file = ws / "docs" / "kb" / "kb_manifest.json"
    assert manifest_file.exists()
    wiki_index = ws / "docs" / "kb" / "wiki" / "index.md"
    assert wiki_index.exists()
    docx_export = ws / "docs" / "kb" / "exports" / "ProjectBrain_Architecture_Spec.docx"
    assert docx_export.exists()


def test_get_kb_trace(app_client, isolated_workspace):
    """Verify GET /kb/trace returns code nodes, doc chunks, and tests for target symbol."""
    # First build KB
    build_res = app_client.post(
        "/kb/build",
        json={"project_id": "trace_test_proj", "branch": "main", "workspace_path": "."},
    )
    assert build_res.status_code == 200

    # Trace symbol
    res = app_client.get(
        "/kb/trace",
        params={
            "project_id": "trace_test_proj",
            "target": "Calculator",
            "branch": "main",
            "workspace_path": ".",
        },
    )
    assert res.status_code == 200, res.text
    trace_data = res.json()
    assert trace_data["query"] == "Calculator"
    assert len(trace_data["code_nodes"]) >= 1
    assert any("Calculator" in n.get("label", "") for n in trace_data["code_nodes"])
    assert len(trace_data["doc_chunks"]) >= 1
    assert trace_data["coverage_status"] in ("documented", "inferred")


def test_get_kb_coverage(app_client, isolated_workspace):
    """Verify GET /kb/coverage computes analytics and metrics."""
    build_res = app_client.post(
        "/kb/build",
        json={"project_id": "cov_test_proj", "branch": "main", "workspace_path": "."},
    )
    assert build_res.status_code == 200

    res = app_client.get(
        "/kb/coverage",
        params={"project_id": "cov_test_proj", "branch": "main", "workspace_path": "."},
    )
    assert res.status_code == 200, res.text
    cov_data = res.json()
    assert cov_data["project_id"] == "cov_test_proj"
    assert cov_data["total_symbols"] > 0
    assert cov_data["total_doc_chunks"] > 0
    assert 0.0 <= cov_data["documented_pct"] <= 100.0


def test_post_kb_export(app_client, isolated_workspace):
    """Verify POST /kb/export generates requested multimodal artifacts."""
    ws = isolated_workspace["workspace"]
    # Seed graph via build
    app_client.post(
        "/kb/build",
        json={"project_id": "export_test_proj", "branch": "main", "workspace_path": "."},
    )

    out_dir = "docs/custom_export"
    res = app_client.post(
        "/kb/export",
        json={
            "project_id": "export_test_proj",
            "branch": "main",
            "workspace_path": ".",
            "output_dir": out_dir,
            "formats": ["wiki", "docx", "xlsx", "pptx"],
        },
    )
    assert res.status_code == 200, res.text
    export_data = res.json()
    assert export_data["status"] == "success"
    assert len(export_data["artifacts"]) == 4

    # Verify generated files
    custom_dir = ws / out_dir
    assert (custom_dir / "wiki" / "index.md").exists()
    assert (custom_dir / "exports" / "ProjectBrain_Architecture_Spec.docx").exists()
    assert (custom_dir / "exports" / "ProjectBrain_Traceability_Matrix.xlsx").exists()
    assert (custom_dir / "exports" / "ProjectBrain_Architecture_Deck.pptx").exists()


# ============================================================================
# 2. Security Guardrails & Read-Only Tests
# ============================================================================

def test_kb_routes_path_traversal_rejection(app_client, isolated_workspace):
    """Verify SEC-02 path traversal rejection on workspace_path and output_dir with HTTP 400."""
    # 1. Absolute external paths on build
    res_abs_ws = app_client.post(
        "/kb/build",
        json={"workspace_path": "/etc", "project_id": "p"},
    )
    assert res_abs_ws.status_code == 400
    assert "path traversal detected" in res_abs_ws.json()["detail"]

    res_abs_out = app_client.post(
        "/kb/build",
        json={"workspace_path": ".", "output_dir": "/var/tmp/evil", "project_id": "p"},
    )
    assert res_abs_out.status_code == 400
    assert "path traversal detected" in res_abs_out.json()["detail"]

    # 2. Relative traversal paths escaping workspace
    res_rel_ws = app_client.post(
        "/kb/build",
        json={"workspace_path": "../../secret", "project_id": "p"},
    )
    assert res_rel_ws.status_code == 400
    assert "path traversal detected" in res_rel_ws.json()["detail"]

    res_rel_out = app_client.post(
        "/kb/build",
        json={"workspace_path": ".", "output_dir": "../evil_out", "project_id": "p"},
    )
    assert res_rel_out.status_code == 400
    assert "path traversal detected" in res_rel_out.json()["detail"]

    # 3. GET /kb/trace and GET /kb/coverage
    res_trace = app_client.get(
        "/kb/trace",
        params={"target": "Calculator", "workspace_path": "/etc/passwd"},
    )
    assert res_trace.status_code == 400

    res_cov = app_client.get(
        "/kb/coverage",
        params={"workspace_path": "../../shadow"},
    )
    assert res_cov.status_code == 400

    # 4. POST /kb/export
    res_exp = app_client.post(
        "/kb/export",
        json={"output_dir": "/tmp/evil", "project_id": "p"},
    )
    assert res_exp.status_code == 400


def test_kb_routes_read_only_mode(app_client, isolated_workspace, monkeypatch):
    """Verify PB_READ_ONLY mode blocks mutating POST /kb/build and POST /kb/export with HTTP 403."""
    monkeypatch.setenv("PB_READ_ONLY", "true")

    res_build = app_client.post(
        "/kb/build",
        json={"project_id": "ro_test", "workspace_path": "."},
    )
    assert res_build.status_code == 403
    assert "Read-Only mode" in res_build.json()["detail"]

    res_export = app_client.post(
        "/kb/export",
        json={"project_id": "ro_test", "workspace_path": "."},
    )
    assert res_export.status_code == 403
    assert "Read-Only mode" in res_export.json()["detail"]

    # Trace and coverage should still be accessible
    res_trace = app_client.get(
        "/kb/trace",
        params={"target": "Calculator", "workspace_path": "."},
    )
    assert res_trace.status_code == 200

    res_cov = app_client.get(
        "/kb/coverage",
        params={"workspace_path": "."},
    )
    assert res_cov.status_code == 200


# ============================================================================
# 3. FastMCP Reader Role RBAC Authorization
# ============================================================================

@pytest.mark.asyncio
async def test_fastmcp_reader_role_kb_authorization(isolated_workspace):
    """Verify FastMCP reader role blocks build & generate tools while permitting trace & coverage."""
    app = create_app()
    client = TestClient(app)

    # Register reader account
    res_acc = client.post(
        "/auth/accounts",
        json={
            "username": "kb_reader_agent",
            "token": "pb_tok_kb_reader_secret",
            "role": "reader",
            "allowed_tools": "*",
        },
    )
    assert res_acc.status_code == 200

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        auth_headers = {"Authorization": "Bearer pb_tok_kb_reader_secret"}

        # 1. Modifying tool projectbrain_build_complete_kb should be BLOCKED (403)
        res_build = await ac.post(
            "/mcp/messages/?session_id=kb_rbac_sess",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "projectbrain_build_complete_kb",
                    "arguments": {"workspace_path": "."},
                },
                "id": 201,
            },
            headers=auth_headers,
        )
        assert res_build.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in res_build.json()["error"]["message"]

        # 2. Modifying tool projectbrain_generate_project_docs should be BLOCKED (403)
        res_gen = await ac.post(
            "/mcp/messages/?session_id=kb_rbac_sess",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "projectbrain_generate_project_docs",
                    "arguments": {"workspace_path": "."},
                },
                "id": 202,
            },
            headers=auth_headers,
        )
        assert res_gen.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in res_gen.json()["error"]["message"]

        # 3. Read tool projectbrain_trace_code_and_docs should be ALLOWED by middleware (not 403)
        res_trace = await ac.post(
            "/mcp/messages/?session_id=kb_rbac_sess",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "projectbrain_trace_code_and_docs",
                    "arguments": {"target": "Calculator", "workspace_path": "."},
                },
                "id": 203,
            },
            headers=auth_headers,
        )
        # Should not be rejected with 403 Forbidden by auth middleware
        assert res_trace.status_code != 403

        # 4. Read tool projectbrain_kb_coverage_report should be ALLOWED by middleware (not 403)
        res_cov = await ac.post(
            "/mcp/messages/?session_id=kb_rbac_sess",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "projectbrain_kb_coverage_report",
                    "arguments": {"workspace_path": "."},
                },
                "id": 204,
            },
            headers=auth_headers,
        )
        assert res_cov.status_code != 403

        # 5. Verify SSEFilter tool discovery filtering for reader
        from projectbrain.server.api import SSEFilter
        reader_filter = SSEFilter("reader", "*")
        mock_tools_chunk = (
            b'event: message\ndata: {"jsonrpc":"2.0","result":{"tools":['
            b'{"name":"projectbrain_build_complete_kb","description":"Build"},'
            b'{"name":"projectbrain_generate_project_docs","description":"Gen"},'
            b'{"name":"projectbrain_trace_code_and_docs","description":"Trace"},'
            b'{"name":"projectbrain_kb_coverage_report","description":"Cov"}'
            b']}}\n\n'
        )
        filtered = reader_filter.process_chunk(mock_tools_chunk)
        filtered_data = json.loads(filtered.decode("utf-8").split("data: ")[1])
        allowed_names = [t["name"] for t in filtered_data["result"]["tools"]]
        assert "projectbrain_trace_code_and_docs" in allowed_names
        assert "projectbrain_kb_coverage_report" in allowed_names
        assert "projectbrain_build_complete_kb" not in allowed_names
        assert "projectbrain_generate_project_docs" not in allowed_names


# ============================================================================
# 4. FastMCP Tools Python Invocation Tests
# ============================================================================

@pytest.mark.asyncio
async def test_fastmcp_tools_direct_invocation(isolated_workspace):
    """Verify all 4 KB FastMCP tools execute directly and return valid JSON payloads."""
    ws = isolated_workspace["workspace"]

    # 1. projectbrain_build_complete_kb
    build_raw = await projectbrain_build_complete_kb(
        workspace_path=".",
        project_id="mcp_direct_proj",
        branch="main",
        persist_memory=True,
    )
    build_json = json.loads(build_raw)
    assert build_json["status"] == "success"
    assert build_json["total_chunks"] >= 1

    # 2. projectbrain_trace_code_and_docs
    trace_raw = await projectbrain_trace_code_and_docs(
        target="Calculator",
        project_id="mcp_direct_proj",
        branch="main",
        workspace_path=".",
    )
    trace_json = json.loads(trace_raw)
    assert trace_json["query"] == "Calculator"
    assert len(trace_json["code_nodes"]) >= 1

    # 3. projectbrain_kb_coverage_report
    cov_raw = await projectbrain_kb_coverage_report(
        project_id="mcp_direct_proj",
        branch="main",
        workspace_path=".",
    )
    cov_json = json.loads(cov_raw)
    assert cov_json["project_id"] == "mcp_direct_proj"
    assert cov_json["total_symbols"] > 0

    # 4. projectbrain_generate_project_docs
    gen_raw = await projectbrain_generate_project_docs(
        workspace_path=".",
        project_id="mcp_direct_proj",
        branch="main",
        output_dir="docs/mcp_generated",
        formats=["wiki", "docx"],
    )
    gen_json = json.loads(gen_raw)
    assert gen_json["status"] == "success"
    assert len(gen_json["artifacts"]) == 2
    assert (ws / "docs" / "mcp_generated" / "wiki" / "index.md").exists()
    assert (ws / "docs" / "mcp_generated" / "exports" / "ProjectBrain_Architecture_Spec.docx").exists()


# ============================================================================
# 5. CLI Subcommands Tests
# ============================================================================

def test_cli_subcommands_execution(isolated_workspace, capsys):
    """Verify build-kb, kb-trace, and kb-coverage CLI functions execute cleanly."""
    ws = isolated_workspace["workspace"]
    db_file = isolated_workspace["db_file"]

    # 1. run_build_kb
    run_build_kb(
        project_id="cli_test_proj",
        workspace_path=str(ws),
        branch="main",
        persist_memory=True,
        generate_office_exports=True,
        output_dir=str(ws / "docs" / "kb"),
        db_path=str(db_file),
    )
    out, err = capsys.readouterr()
    assert "Successfully built Unified Knowledge Base!" in out
    assert (ws / "docs" / "kb" / "kb_manifest.json").exists()

    # 2. run_kb_trace
    run_kb_trace(
        project_id="cli_test_proj",
        target="Calculator",
        branch="main",
        workspace_path=str(ws),
        db_path=str(db_file),
    )
    out, err = capsys.readouterr()
    assert '"query": "Calculator"' in out

    # 3. run_kb_coverage
    run_kb_coverage(
        project_id="cli_test_proj",
        branch="main",
        workspace_path=str(ws),
        db_path=str(db_file),
    )
    out, err = capsys.readouterr()
    assert '"project_id": "cli_test_proj"' in out
    assert '"total_symbols"' in out
