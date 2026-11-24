"""Tests for Client Kit Distribution API, installers, CLI client-install, and sub/ preservation."""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
import uvicorn
from fastapi.testclient import TestClient

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.main import cli_main, run_client_install
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes
from projectbrain.server.routes.client_kit import (
    _iter_kit_files,
    _sanitize_base_url,
    get_kit_root,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def isolated_client_kit_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a FastAPI TestClient with an isolated SQLite DB and clean auth env."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres
    orig_require_rest = getattr(env, "require_rest_auth", False)

    temp_db = tmp_path / "client_kit_test.db"
    monkeypatch.setenv("PB_DB_PATH", str(temp_db))
    monkeypatch.setenv("OM_DB_PATH", str(temp_db))
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.setattr(env, "database_url", f"sqlite:///{temp_db}")
    monkeypatch.setattr(env, "db_path", str(temp_db))
    monkeypatch.setattr(env, "require_rest_auth", False)

    db.conn = None
    db.is_postgres = False
    auth_routes._ensured_conn = None
    auth_routes._ensured_db_url = None
    db.connect()
    auth_routes.init_auth_system()

    app = create_app()
    client = TestClient(app)

    try:
        yield app, client
    finally:
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        env.require_rest_auth = orig_require_rest
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None


# ============================================================================
# 1. _iter_kit_files() & /client-kit/* API endpoints + REST Auth Exemption
# ============================================================================


def test_iter_kit_files_filters_junk_and_working_sub_files(tmp_path: Path):
    kit = tmp_path / "kit"
    skill_dir = kit / ".agents" / "skills" / "spec-driven-development"
    (skill_dir / "sub").mkdir(parents=True)
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "__pycache__").mkdir(parents=True)
    (kit / ".agents" / "workflows").mkdir(parents=True)
    (kit / ".claude" / "commands").mkdir(parents=True)
    (kit / ".cursor" / "commands").mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")
    (skill_dir / "sub" / "README.md").write_text("# Sub README", encoding="utf-8")
    (skill_dir / "sub" / "spec.md").write_text("# Working spec should NOT be bundled", encoding="utf-8")
    (skill_dir / "references" / "guide.md").write_text("# Guide", encoding="utf-8")
    (skill_dir / "__pycache__" / "mod.pyc").write_bytes(b"pyc")
    (skill_dir / ".DS_Store").write_bytes(b"ds")
    (kit / ".agents" / "workflows" / "pb-spec.md").write_text("# WF", encoding="utf-8")
    (kit / ".claude" / "commands" / "pb-spec.md").write_text("# Claude", encoding="utf-8")
    (kit / ".cursor" / "commands" / "pb-spec.md").write_text("# Cursor", encoding="utf-8")
    (kit / "AGENTS.md").write_text("# Agents", encoding="utf-8")

    arcnames = [arc for arc, _ in _iter_kit_files(kit)]
    assert ".agents/skills/spec-driven-development/SKILL.md" in arcnames
    assert ".agents/skills/spec-driven-development/sub/README.md" in arcnames
    assert ".agents/skills/spec-driven-development/references/guide.md" in arcnames
    assert ".agents/workflows/pb-spec.md" in arcnames
    assert ".claude/commands/pb-spec.md" in arcnames
    assert ".cursor/commands/pb-spec.md" in arcnames
    assert "AGENTS.md" in arcnames

    assert ".agents/skills/spec-driven-development/sub/spec.md" not in arcnames
    assert not any("__pycache__" in a or ".DS_Store" in a or a.endswith(".pyc") for a in arcnames)


def test_client_kit_endpoints_and_rest_auth_exemption(
    isolated_client_kit_app, monkeypatch: pytest.MonkeyPatch
):
    _, client = isolated_client_kit_app

    # Enable strict REST auth — /client-kit/* MUST remain public
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    monkeypatch.setattr(env, "require_rest_auth", True)

    # Confirm a protected route actually rejects unauthenticated calls
    protected_res = client.get("/memory/history")
    assert protected_res.status_code == 401

    # 1. GET /client-kit/manifest
    m_res = client.get("/client-kit/manifest")
    assert m_res.status_code == 200, m_res.text
    manifest = m_res.json()
    assert manifest["status"] == "ok"
    assert "spec-driven-development" in manifest["skills"]
    assert "pb-spec.md" in manifest["workflows"]
    assert "pb-spec.md" in manifest["claude_commands"]
    assert "pb-spec.md" in manifest["cursor_commands"]
    expected_files = list(_iter_kit_files(get_kit_root()))
    assert manifest["file_count"] == len(expected_files)
    assert manifest["file_count"] > 0

    # 2. GET /client-kit/bundle.tar.gz
    tar_res = client.get("/client-kit/bundle.tar.gz")
    assert tar_res.status_code == 200
    assert tar_res.headers["content-type"].startswith("application/gzip")
    assert "projectbrain-client-kit.tar.gz" in tar_res.headers.get("content-disposition", "")
    with tarfile.open(fileobj=io.BytesIO(tar_res.content), mode="r:gz") as tar:
        tar_names = [m.name for m in tar.getmembers() if m.isfile()]
    assert len(tar_names) == manifest["file_count"]
    assert ".agents/skills/spec-driven-development/SKILL.md" in tar_names
    assert ".agents/skills/spec-driven-development/sub/README.md" in tar_names
    assert ".agents/workflows/pb-spec.md" in tar_names
    assert ".claude/commands/pb-spec.md" in tar_names
    assert ".cursor/commands/pb-spec.md" in tar_names
    assert "AGENTS.md" in tar_names
    assert not any("__pycache__" in n or n.endswith(".DS_Store") for n in tar_names)

    # 3. GET /client-kit/bundle.zip
    zip_res = client.get("/client-kit/bundle.zip")
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"].startswith("application/zip")
    assert "projectbrain-client-kit.zip" in zip_res.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(zip_res.content), mode="r") as zf:
        zip_names = zf.namelist()
    assert sorted(zip_names) == sorted(tar_names)

    # 4. GET /client-kit/install.sh
    sh_res = client.get("/client-kit/install.sh")
    assert sh_res.status_code == 200
    assert sh_res.headers["content-type"].startswith("text/plain")
    sh_lines = sh_res.text.splitlines()
    assert sh_lines[0] == "#!/usr/bin/env bash"
    assert 'PB_DEFAULT_SERVER_URL="${PB_DEFAULT_SERVER_URL:-http://testserver}"' in sh_lines[:5]

    # 5. GET /client-kit/install.ps1
    ps_res = client.get("/client-kit/install.ps1")
    assert ps_res.status_code == 200
    assert ps_res.headers["content-type"].startswith("text/plain")
    assert "PB_DEFAULT_SERVER_URL" in ps_res.text
    assert "http://testserver" in ps_res.text
    # Ensure param(...) still precedes $PB_DEFAULT_SERVER_URL assignment for valid PowerShell
    param_idx = ps_res.text.index("param(")
    default_idx = ps_res.text.index("$PB_DEFAULT_SERVER_URL = 'http://testserver'")
    assert param_idx < default_idx

    # Host header injection check
    assert _sanitize_base_url('https://evil.com";rm -rf /') == "http://localhost:8080"


# ============================================================================
# 2. Shell Installer (install_client.sh) Local & Remote Modes + sub/ Preservation
# ============================================================================


def test_install_client_sh_local_kit_and_sub_preservation(tmp_path: Path):
    target_dir = tmp_path / "client_repo"
    sub_dir = target_dir / ".agents" / "skills" / "spec-driven-development" / "sub"
    sub_dir.mkdir(parents=True)

    custom_spec = "# Project-Specific Spec Override\nKeep this 100% intact!\n"
    spec_file = sub_dir / "spec.md"
    spec_file.write_text(custom_spec, encoding="utf-8")

    # Pre-existing .mcp.json with another server entry that must NOT be clobbered
    mcp_file = target_dir / ".mcp.json"
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "postgres": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]}
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    script_path = REPO_ROOT / "scripts" / "install_client.sh"
    assert os.access(script_path, os.X_OK), "scripts/install_client.sh must be executable"

    proc = subprocess.run(
        [
            "bash",
            str(script_path),
            "--local-kit",
            ".",
            "--target-dir",
            str(target_dir),
            "--server-url",
            "https://pb.example.com",
            "--token",
            "pb_pat_test",
            "--project-id",
            "acme/app",
            "--ide",
            "all",
            "--non-interactive",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    # Verify skills, sub/README.md, workflows, claude commands, cursor commands, AGENTS.md
    assert (target_dir / ".agents" / "skills" / "spec-driven-development" / "SKILL.md").is_file()
    assert (target_dir / ".agents" / "skills" / "spec-driven-development" / "sub" / "README.md").is_file()
    assert (target_dir / ".agents" / "workflows" / "pb-spec.md").is_file()
    assert (target_dir / ".claude" / "commands" / "pb-spec.md").is_file()
    assert (target_dir / ".cursor" / "commands" / "pb-spec.md").is_file()
    assert (target_dir / "AGENTS.md").is_file()

    # CRITICAL: sub/spec.md must be 100% untouched
    assert spec_file.is_file()
    assert spec_file.read_text(encoding="utf-8") == custom_spec

    # Verify .mcp.json merged cleanly
    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    servers = mcp_data["mcpServers"]
    assert "postgres" in servers
    assert servers["postgres"]["command"] == "npx"
    assert servers["projectbrain"]["type"] == "sse"
    assert (
        servers["projectbrain"]["url"]
        == "https://pb.example.com/mcp/sse?token=pb_pat_test&project_id=acme/app"
    )
    assert servers["projectbrain-http"]["type"] == "http"
    assert servers["projectbrain-http"]["url"] == "https://pb.example.com/mcp-http"
    assert servers["projectbrain-http"]["headers"]["Authorization"] == "Bearer pb_pat_test"


def test_install_client_sh_ide_filtering(tmp_path: Path):
    script_path = REPO_ROOT / "scripts" / "install_client.sh"

    claude_target = tmp_path / "only_claude"
    proc_c = subprocess.run(
        [
            "bash",
            str(script_path),
            "--local-kit",
            str(REPO_ROOT),
            "--target-dir",
            str(claude_target),
            "--ide",
            "claude",
            "--non-interactive",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_c.returncode == 0, proc_c.stderr
    assert (claude_target / ".claude" / "commands" / "pb-spec.md").is_file()
    assert not (claude_target / ".cursor").exists()

    cursor_target = tmp_path / "only_cursor"
    proc_cur = subprocess.run(
        [
            "bash",
            str(script_path),
            "--local-kit",
            str(REPO_ROOT),
            "--target-dir",
            str(cursor_target),
            "--ide",
            "cursor",
            "--non-interactive",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_cur.returncode == 0, proc_cur.stderr
    assert (cursor_target / ".cursor" / "commands" / "pb-spec.md").is_file()
    assert not (cursor_target / ".claude").exists()


def test_install_client_sh_remote_mode_end_to_end(
    isolated_client_kit_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app, _ = isolated_client_kit_app
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    monkeypatch.setattr(env, "require_rest_auth", True)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10.0
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "Uvicorn test server failed to start"

        base_url = f"http://127.0.0.1:{port}"
        target_dir = tmp_path / "remote_client"
        sub_dir = target_dir / ".agents" / "skills" / "spec-driven-development" / "sub"
        sub_dir.mkdir(parents=True)
        (sub_dir / "spec.md").write_text("remote-preserve-me", encoding="utf-8")

        if shutil.which("curl"):
            # Pipe the server-hosted /client-kit/install.sh directly into bash (relying on injected PB_DEFAULT_SERVER_URL)
            cmd = (
                f'curl -fsSL "{base_url}/client-kit/install.sh" | '
                f'bash -s -- --target-dir "{target_dir}" --token pb_pat_remote --project-id acme/remote --ide all --non-interactive'
            )
            proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False)
            assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        else:
            proc = subprocess.run(
                [
                    "bash",
                    str(REPO_ROOT / "scripts" / "install_client.sh"),
                    "--server-url",
                    base_url,
                    "--target-dir",
                    str(target_dir),
                    "--token",
                    "pb_pat_remote",
                    "--project-id",
                    "acme/remote",
                    "--ide",
                    "all",
                    "--non-interactive",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

        assert (target_dir / ".agents" / "skills" / "spec-driven-development" / "SKILL.md").is_file()
        assert (target_dir / ".agents" / "skills" / "spec-driven-development" / "sub" / "README.md").is_file()
        assert (target_dir / ".claude" / "commands" / "pb-spec.md").is_file()
        assert (target_dir / ".cursor" / "commands" / "pb-spec.md").is_file()
        assert (target_dir / "AGENTS.md").is_file()
        assert (sub_dir / "spec.md").read_text(encoding="utf-8") == "remote-preserve-me"

        mcp_data = json.loads((target_dir / ".mcp.json").read_text(encoding="utf-8"))
        assert (
            mcp_data["mcpServers"]["projectbrain"]["url"]
            == f"{base_url}/mcp/sse?token=pb_pat_remote&project_id=acme/remote"
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


# ============================================================================
# 3. CLI `projectbrain client-install` Subcommand + --bootstrap
# ============================================================================


def test_cli_client_install_preserves_sub_and_writes_mcp_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target_dir = tmp_path / "cli_target"
    sub_dir = target_dir / ".agents" / "skills" / "spec-driven-development" / "sub"
    sub_dir.mkdir(parents=True)
    (sub_dir / "spec.md").write_text("cli-preserved-spec", encoding="utf-8")

    # Pre-existing .agents/skills/spec-driven-development/sub/README.md should also NOT be overwritten if present
    existing_readme = "# Custom Local Sub README\n"
    (sub_dir / "README.md").write_text(existing_readme, encoding="utf-8")

    (target_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "projectbrain",
            "client-install",
            "--target-dir",
            str(target_dir),
            "--server-url",
            "https://brain.company.com",
            "--token",
            "pb_tok_cli",
            "--project-id",
            "acme/cli-proj",
            "--ide",
            "all",
        ],
    )
    cli_main()

    assert (target_dir / ".agents" / "skills" / "spec-driven-development" / "SKILL.md").is_file()
    assert (sub_dir / "spec.md").read_text(encoding="utf-8") == "cli-preserved-spec"
    assert (sub_dir / "README.md").read_text(encoding="utf-8") == existing_readme
    assert (target_dir / ".claude" / "commands" / "pb-spec.md").is_file()
    assert (target_dir / ".cursor" / "commands" / "pb-spec.md").is_file()
    assert (target_dir / ".agents" / "workflows" / "pb-spec.md").is_file()

    mcp_data = json.loads((target_dir / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp_data["mcpServers"]["github"] == {"command": "gh-mcp"}
    assert (
        mcp_data["mcpServers"]["projectbrain"]["url"]
        == "https://brain.company.com/mcp/sse?token=pb_tok_cli&project_id=acme/cli-proj"
    )
    assert mcp_data["mcpServers"]["projectbrain-http"]["url"] == "https://brain.company.com/mcp-http"


def test_run_client_install_bootstrap_posts_bearer_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import httpx

    captured: List[Dict[str, Any]] = []

    class _DummyResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Dict[str, Any]:
            return {"status": "ok", "project_id": "acme/boot"}

    def _fake_post(url: str, json: Dict[str, Any], headers: Dict[str, str], timeout: float):
        captured.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _DummyResp()

    monkeypatch.setattr(httpx, "post", _fake_post)

    target_dir = tmp_path / "boot_target"
    res = run_client_install(
        target_dir=str(target_dir),
        server_url="https://brain.company.com/",
        token="pb_pat_boot",
        project_id="acme/boot",
        ide="all",
        bootstrap=True,
    )
    assert res["status"] == "ok"
    assert res["bootstrap"] == {"status": "ok", "project_id": "acme/boot"}
    assert len(captured) == 1
    assert captured[0]["url"] == "https://brain.company.com/master/hub/bootstrap"
    assert captured[0]["headers"]["Authorization"] == "Bearer pb_pat_boot"
    assert captured[0]["json"]["project_id"] == "acme/boot"
    assert captured[0]["json"]["workspace_root"] == str(target_dir.resolve())
