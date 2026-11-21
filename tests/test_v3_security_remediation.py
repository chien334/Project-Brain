"""Security & RBAC Regression Suite for Project-Brain v3.0 Plugin Microkernel (TASK-19B).

Verifies remediations for:
- V3-SEC-01: Admin-only mutating /plugins/* REST routes and FastMCP projectbrain_toggle_plugin,
  /plugins/reload directory confinement, and manifest entrypoint traversal rejection.
- V3-SEC-02: Secret redaction ("***" and DSN credentials) in GET /plugins, PUT /plugins/{id}/config,
  projectbrain_list_plugins, and projectbrain_toggle_plugin.
- V3-SEC-03: Path traversal and sensitive system/credential path rejection in projectbrain_v3_ingest_etl
  while permitting legitimate workspace and pytest tmp_path files.
- V3-SEC-04 / HIGH-03: Fail-closed webhook verification and blocking unverified payload project_id
  from downgrading or clearing an active global webhook secret.
- V3-SEC-05: Cross-project tenant isolation across script cache and telemetry artifacts
  (purge_expired, query_evidence_window, feedback, invalidate, store_script, index_artifact).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from projectbrain.ai.mcp import (
    projectbrain_list_plugins,
    projectbrain_toggle_plugin,
    projectbrain_v3_ingest_etl,
)
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.plugins import (
    PluginCategory,
    PluginRegistry,
    get_default_registry,
    reset_default_registry,
)
from projectbrain.server.api import SSEFilter, create_app


@pytest.fixture
def isolated_sec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide strictly isolated workspace, SQLite DB, and fresh PluginRegistry."""
    orig_db_url = env.database_url
    orig_plugins_dir = getattr(env, "plugins_dir", "./plugins")

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(ws)

    db_file = tmp_path / "test_v3_security_remediation.db"
    monkeypatch.setenv("OM_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("OM_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_READ_ONLY", "false")
    monkeypatch.setenv("OM_READ_ONLY", "false")
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.setenv("PB_REQUIRE_WEBHOOK_SIGNATURE", "false")
    monkeypatch.setenv("PB_MODE", "local")
    monkeypatch.delenv("PB_DISABLED_PLUGINS", raising=False)
    monkeypatch.delenv("PB_ENABLED_PLUGINS", raising=False)
    monkeypatch.delenv("PB_PLUGINS_DIR", raising=False)
    monkeypatch.delenv("OM_PLUGINS_DIR", raising=False)
    monkeypatch.delenv("PB_GITLAB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PB_MCP_ROLE", raising=False)
    monkeypatch.delenv("PB_WORKSPACE_ROOT", raising=False)

    env.plugins_dir = "./plugins"
    env.database_url = f"sqlite:///{db_file}"
    if db.conn is not None:
        try:
            db.conn.close()
        except Exception:
            pass
    db.conn = None
    db.connect()

    reset_default_registry()

    yield {
        "workspace": ws,
        "db_file": db_file,
        "tmp_path": tmp_path,
    }

    reset_default_registry()
    if db.conn is not None:
        try:
            db.conn.close()
        except Exception:
            pass
        db.conn = None
    env.database_url = orig_db_url
    env.plugins_dir = orig_plugins_dir


@pytest.fixture
def client(isolated_sec_env: Dict[str, Any]) -> TestClient:
    return TestClient(create_app())


def _write_test_plugin(pkg_dir: Path, plugin_id: str, class_name: str, version: str = "1.0.0") -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": plugin_id,
                "name": "Security Test Plugin",
                "version": version,
                "category": "doc_exporter",
                "entrypoint": "plugin.py",
                "class_name": class_name,
                "priority": 120,
                "default_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    (pkg_dir / "plugin.py").write_text(
        "from projectbrain.plugins.base import DocExporterPlugin, PluginMetadata\n"
        f"class {class_name}(DocExporterPlugin):\n"
        "    metadata = PluginMetadata(\n"
        f"        plugin_id='{plugin_id}',\n"
        "        name='Security Test Plugin',\n"
        f"        version='{version}',\n"
        "        category='doc_exporter',\n"
        "        priority=120,\n"
        "    )\n"
        "    export_format = 'sec_json'\n"
        "    def export(self, graph_data, output_path, context=None):\n"
        "        return {'status': 'ok'}\n",
        encoding="utf-8",
    )


# ============================================================================
# V3-SEC-01: Admin-only /plugins/* & projectbrain_toggle_plugin, Reload Scope,
#            and Manifest Entrypoint Confinement
# ============================================================================


@pytest.mark.asyncio
async def test_v3_sec_01_admin_only_plugin_control_plane_rest_and_mcp(
    client: TestClient,
    isolated_sec_env: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws: Path = isolated_sec_env["workspace"]
    plugins_dir = ws / "plugins"
    _write_test_plugin(plugins_dir / "sec_valid", "exporter.sec_valid", "SecValidExporter")

    for uname, tok, role in (
        ("sec_v3_reader", "pb_tok_sec_v3_reader", "reader"),
        ("sec_v3_collab", "pb_tok_sec_v3_collab", "collaborator"),
        ("sec_v3_admin", "pb_tok_sec_v3_admin", "admin"),
    ):
        acc = client.post(
            "/auth/accounts",
            json={"username": uname, "token": tok, "role": role, "allowed_tools": "*"},
        )
        assert acc.status_code == 200, acc.text

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")

    reader_hdrs = {"Authorization": "Bearer pb_tok_sec_v3_reader"}
    collab_hdrs = {"Authorization": "Bearer pb_tok_sec_v3_collab"}
    admin_hdrs = {"Authorization": "Bearer pb_tok_sec_v3_admin"}

    # GET /plugins remains accessible to reader, collaborator, and admin (401 anon)
    assert client.get("/plugins").status_code == 401
    assert client.get("/plugins", headers=reader_hdrs).status_code == 200
    assert client.get("/plugins", headers=collab_hdrs).status_code == 200
    assert client.get("/plugins", headers=admin_hdrs).status_code == 200

    # Collaborator gets 403 on all mutating /plugins/* endpoints
    assert client.post("/plugins/reload", json={"directory": "plugins"}, headers=collab_hdrs).status_code == 403
    assert client.post("/plugins/cache.redis/disable", json={"project_id": "p1"}, headers=collab_hdrs).status_code == 403
    assert client.post("/plugins/cache.redis/enable", json={"project_id": "p1"}, headers=collab_hdrs).status_code == 403
    assert (
        client.put(
            "/plugins/cache.redis/config",
            json={"project_id": "p1", "config": {"ttl_seconds": 60}},
            headers=collab_hdrs,
        ).status_code
        == 403
    )

    # Admin gets 200 on all mutating /plugins/* endpoints
    assert client.post("/plugins/cache.redis/disable", json={"project_id": "p1"}, headers=admin_hdrs).status_code == 200
    assert client.post("/plugins/cache.redis/enable", json={"project_id": "p1"}, headers=admin_hdrs).status_code == 200
    assert (
        client.put(
            "/plugins/cache.redis/config",
            json={"project_id": "p1", "config": {"ttl_seconds": 60}},
            headers=admin_hdrs,
        ).status_code
        == 200
    )
    reload_admin = client.post("/plugins/reload", json={"directory": "plugins"}, headers=admin_hdrs)
    assert reload_admin.status_code == 200, reload_admin.text
    assert "exporter.sec_valid" in reload_admin.json()["reloaded_plugins"]

    # SSEFilter omits projectbrain_toggle_plugin for collaborator and reader, preserves for admin
    tools_payload = (
        "event: message\ndata: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {"name": "projectbrain_list_plugins", "description": "List"},
                        {"name": "projectbrain_toggle_plugin", "description": "Toggle"},
                    ]
                },
                "id": 1,
            }
        )
        + "\n\n"
    ).encode("utf-8")

    collab_filtered = json.loads(SSEFilter("collaborator", "*").process_chunk(tools_payload).decode("utf-8").split("data: ", 1)[1])
    assert [t["name"] for t in collab_filtered["result"]["tools"]] == ["projectbrain_list_plugins"]

    admin_filtered = json.loads(SSEFilter("admin", "*").process_chunk(tools_payload).decode("utf-8").split("data: ", 1)[1])
    assert {t["name"] for t in admin_filtered["result"]["tools"]} == {
        "projectbrain_list_plugins",
        "projectbrain_toggle_plugin",
    }

    # FastMCP /mcp/messages/ enforces admin-only access on projectbrain_toggle_plugin
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        call_payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "projectbrain_toggle_plugin",
                "arguments": {"plugin_id": "cache.redis", "enabled": False},
            },
            "id": 99,
        }
        res_reader = await ac.post("/mcp/messages/?session_id=sec_sess", json=call_payload, headers=reader_hdrs)
        assert res_reader.status_code == 403

        res_collab = await ac.post("/mcp/messages/?session_id=sec_sess", json=call_payload, headers=collab_hdrs)
        assert res_collab.status_code == 403
        assert "Admin role required" in res_collab.json()["error"]["message"]

        res_admin = await ac.post("/mcp/messages/?session_id=sec_sess", json=call_payload, headers=admin_hdrs)
        assert res_admin.status_code != 403


def test_v3_sec_01_reload_directory_restriction_and_manifest_entrypoint_rejected(
    client: TestClient,
    isolated_sec_env: Dict[str, Any],
) -> None:
    ws: Path = isolated_sec_env["workspace"]
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "arbitrary.py").write_text("raise RuntimeError('should not execute')\n", encoding="utf-8")

    # Arbitrary workspace subdir "src" and traversal "../" must be rejected with 400
    r_src = client.post("/plugins/reload", json={"directory": "src"})
    assert r_src.status_code == 400, r_src.text

    r_dotdot = client.post("/plugins/reload", json={"directory": "../"})
    assert r_dotdot.status_code == 400, r_dotdot.text

    r_escape = client.post("/plugins/reload", json={"directory": "../outside"})
    assert r_escape.status_code == 400, r_escape.text

    r_etc = client.post("/plugins/reload", json={"directory": "/etc"})
    assert r_etc.status_code == 400, r_etc.text

    # Configured plugins_dir inside workspace is allowed
    plugins_dir = ws / "plugins"
    _write_test_plugin(plugins_dir / "valid_pack", "exporter.valid_pack", "ValidPackExporter")
    r_ok = client.post("/plugins/reload", json={"directory": "plugins"})
    assert r_ok.status_code == 200, r_ok.text
    assert "exporter.valid_pack" in r_ok.json()["reloaded_plugins"]

    # Already-registered external directory in registry._plugin_dirs is also allowed
    reg = get_default_registry()
    pre_reg_dir = isolated_sec_env["tmp_path"] / "pre_registered_plugins"
    _write_test_plugin(pre_reg_dir / "pre_pack", "exporter.pre_pack", "PrePackExporter")
    reg.discover_directory(pre_reg_dir)
    r_reg = client.post("/plugins/reload", json={"directory": str(pre_reg_dir)})
    assert r_reg.status_code == 200, r_reg.text
    assert "exporter.pre_pack" in r_reg.json()["reloaded_plugins"]

    # Manifest entrypoint="../evil.py" must be rejected and never executed
    evil_parent = isolated_sec_env["tmp_path"] / "malicious_plugin_root"
    pkg_dir = evil_parent / "bad_bundle"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    marker_file = evil_parent / "pwned.txt"
    (evil_parent / "evil.py").write_text(
        f"from pathlib import Path\nPath({str(marker_file)!r}).write_text('pwned', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (pkg_dir / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "exporter.evil_escape",
                "name": "Evil Escape",
                "version": "1.0.0",
                "category": "doc_exporter",
                "entrypoint": "../evil.py",
                "class_name": "Missing",
            }
        ),
        encoding="utf-8",
    )

    isolated_reg = PluginRegistry()
    with pytest.raises(ValueError, match="entrypoint"):
        isolated_reg.discover_directory(evil_parent)
    assert not marker_file.exists()


# ============================================================================
# V3-SEC-02: Secret Redaction in REST & FastMCP Plugin Config Outputs
# ============================================================================


@pytest.mark.asyncio
async def test_v3_sec_02_secret_redaction_in_rest_and_mcp(
    client: TestClient,
    isolated_sec_env: Dict[str, Any],
) -> None:
    secret_payload = {
        "webhook_secret": "whsec_live_super_secret",
        "api_key": "qdrant_api_key_secret_999",
        "password": "pg_db_password_secret",
        "access_token": "tok_secret_value",
        "redis_url": "redis://pb_user:redis_pw_secret@localhost:6379/0",
        "fallback_threshold": 0.3,
        "key_prefix": "pb:scache",
    }

    put_res = client.put(
        "/plugins/webhook.event_ingest/config",
        json={"project_id": "sec_redact_proj", "config": secret_payload},
    )
    assert put_res.status_code == 200, put_res.text
    put_cfg = put_res.json()["config"]
    assert put_cfg["webhook_secret"] == "***"
    assert put_cfg["api_key"] == "***"
    assert put_cfg["password"] == "***"
    assert put_cfg["access_token"] == "***"
    assert "redis_pw_secret" not in put_cfg["redis_url"]
    assert "***@" in put_cfg["redis_url"]
    assert put_cfg["fallback_threshold"] == 0.3
    assert put_cfg["key_prefix"] == "pb:scache"

    # GET /plugins masks secrets
    list_res = client.get("/plugins", params={"project_id": "sec_redact_proj"})
    assert list_res.status_code == 200, list_res.text
    wh_entry = next(p for p in list_res.json()["plugins"] if p["plugin_id"] == "webhook.event_ingest")
    assert wh_entry["config"]["webhook_secret"] == "***"
    assert wh_entry["config"]["api_key"] == "***"
    assert wh_entry["config"]["password"] == "***"
    assert wh_entry["config"]["access_token"] == "***"
    assert "redis_pw_secret" not in wh_entry["config"]["redis_url"]
    assert wh_entry["config"]["key_prefix"] == "pb:scache"

    # FastMCP projectbrain_list_plugins masks secrets
    mcp_list = json.loads(await projectbrain_list_plugins(project_id="sec_redact_proj"))
    mcp_wh = next(p for p in mcp_list["plugins"] if p["plugin_id"] == "webhook.event_ingest")
    assert mcp_wh["config"]["webhook_secret"] == "***"
    assert mcp_wh["config"]["api_key"] == "***"
    assert mcp_wh["config"]["password"] == "***"
    assert "redis_pw_secret" not in mcp_wh["config"]["redis_url"]

    # FastMCP projectbrain_toggle_plugin also masks secrets
    mcp_toggle = json.loads(
        await projectbrain_toggle_plugin(
            plugin_id="webhook.event_ingest",
            enabled=True,
            project_id="sec_redact_proj",
        )
    )
    assert mcp_toggle["config"]["webhook_secret"] == "***"
    assert mcp_toggle["config"]["api_key"] == "***"
    assert mcp_toggle["config"]["password"] == "***"

    # Internal registry config remains intact for runtime verification
    raw_cfg = get_default_registry().get_effective_config("webhook.event_ingest", project_id="sec_redact_proj")
    assert raw_cfg["webhook_secret"] == "whsec_live_super_secret"


# ============================================================================
# V3-SEC-03: FastMCP projectbrain_v3_ingest_etl Path Traversal & System Paths
# ============================================================================


@pytest.mark.asyncio
async def test_v3_sec_03_mcp_ingest_etl_rejects_traversal_and_sensitive_paths(
    isolated_sec_env: Dict[str, Any],
) -> None:
    ws: Path = isolated_sec_env["workspace"]
    tmp_root: Path = isolated_sec_env["tmp_path"]

    forbidden_paths = [
        "../../etc/passwd",
        "../etc/passwd",
        "/etc/passwd",
        "/proc/self/environ",
        "/sys/kernel/notes",
        "/var/run/docker.sock",
        "~/.ssh/id_rsa",
        "~/.aws/credentials",
        "~/.gnupg/secring.gpg",
    ]
    for bad_path in forbidden_paths:
        raw_out = await projectbrain_v3_ingest_etl(file_path=bad_path, project_id="sec_etl_p")
        parsed = json.loads(raw_out)
        assert parsed["status"] == "error", f"Expected error for forbidden path {bad_path}: {raw_out}"
        assert "error" in parsed

    # Legitimate workspace path succeeds
    ws_csv = ws / "valid_ws_cases.csv"
    ws_csv.write_text(
        "TC_ID,Title,Preconditions,Steps,Expected Result,Priority,Platform,SRS_Ref\n"
        "TC-WS-1,Workspace Boot,Power on,1. Boot,UART BOOT_OK,P0,firmware,REQ-1\n",
        encoding="utf-8",
    )
    ws_res = json.loads(await projectbrain_v3_ingest_etl(file_path=str(ws_csv), project_id="sec_etl_p"))
    assert ws_res["status"] == "ok"
    assert ws_res["canonical_intents_count"] == 1

    # Legitimate pytest tmp_path file outside workspace cwd also succeeds
    tmp_csv = tmp_root / "outside_cwd_tmp_cases.csv"
    tmp_csv.write_text(
        "TC_ID,Title,Preconditions,Steps,Expected Result,Priority,Platform,SRS_Ref\n"
        "TC-TMP-1,Tmp Boot,Power on,1. Boot,UART BOOT_OK,P0,firmware,REQ-2\n",
        encoding="utf-8",
    )
    tmp_res = json.loads(await projectbrain_v3_ingest_etl(file_path=str(tmp_csv), project_id="sec_etl_p"))
    assert tmp_res["status"] == "ok"
    assert tmp_res["canonical_intents_count"] == 1


# ============================================================================
# V3-SEC-04 & HIGH-03: Webhook Fail-Closed Default & Downgrade Resistance
# ============================================================================


def test_v3_sec_04_webhook_blocks_unverified_payload_project_downgrade(
    client: TestClient,
    isolated_sec_env: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Fail-closed by default when no secret or allow_unsigned=True is configured
    unconfigured = client.post("/v3/webhooks/gitlab", json={"project_id": "unconf", "object_kind": "push"})
    assert unconfigured.status_code == 401

    # 2. Configure global webhook_secret; attacker sends payload project_id="other"
    #    (even with per-project allow_unsigned=True / empty secret on "other") -> must still return 401
    assert (
        client.put(
            "/plugins/webhook.event_ingest/config",
            json={"config": {"webhook_secret": "global-top-secret"}},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/plugins/webhook.event_ingest/config",
            json={"project_id": "other", "config": {"allow_unsigned": True, "webhook_secret": ""}},
        ).status_code
        == 200
    )

    unsigned_bypass = client.post(
        "/v3/webhooks/gitlab",
        json={"project_id": "other", "object_kind": "push"},
    )
    assert unsigned_bypass.status_code == 401

    wrong_sig = client.post(
        "/v3/webhooks/gitlab",
        json={"project_id": "other", "object_kind": "push"},
        headers={"X-Gitlab-Token": "wrong-secret"},
    )
    assert wrong_sig.status_code == 401

    # Valid signature against active global secret succeeds
    payload_bytes = json.dumps({"project_id": "other", "object_kind": "push", "ref": "refs/heads/main"}).encode("utf-8")
    valid_hmac = "sha256=" + hmac.new(b"global-top-secret", payload_bytes, hashlib.sha256).hexdigest()
    valid_res = client.post(
        "/v3/webhooks/gitlab",
        content=payload_bytes,
        headers={"Content-Type": "application/json", "X-PB-Signature": valid_hmac},
    )
    assert valid_res.status_code == 200, valid_res.text

    # 3. PB_WEBHOOK_SECRET env var + PB_REQUIRE_WEBHOOK_SIGNATURE=true cannot be bypassed
    reset_default_registry()
    monkeypatch.setenv("PB_WEBHOOK_SECRET", "env-global-secret")
    monkeypatch.setenv("PB_REQUIRE_WEBHOOK_SIGNATURE", "true")
    client.put(
        "/plugins/webhook.event_ingest/config",
        json={"project_id": "other", "config": {"allow_unsigned": True}},
    )
    env_bypass = client.post("/v3/webhooks/gitlab", json={"project_id": "other", "object_kind": "push"})
    assert env_bypass.status_code == 401

    # 4. Explicit allow_unsigned=False without secret fails closed (401)
    monkeypatch.delenv("PB_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("PB_REQUIRE_WEBHOOK_SIGNATURE", "false")
    reset_default_registry()
    client.put("/plugins/webhook.event_ingest/config", json={"config": {"allow_unsigned": False}})
    explicit_false = client.post("/v3/webhooks/gitlab", json={"project_id": "p", "object_kind": "push"})
    assert explicit_false.status_code == 401

    # 5. Explicit allow_unsigned=True globally without secret and PB_REQUIRE_WEBHOOK_SIGNATURE=false is honored
    client.put("/plugins/webhook.event_ingest/config", json={"config": {"allow_unsigned": True}})
    explicit_true = client.post("/v3/webhooks/gitlab", json={"project_id": "p", "object_kind": "push"})
    assert explicit_true.status_code == 200


# ============================================================================
# V3-SEC-05: Cross-Project Script Cache & Telemetry Artifact Tenant Isolation
# ============================================================================


def test_v3_sec_05_cross_project_script_cache_and_artifact_isolation(
    client: TestClient,
    isolated_sec_env: Dict[str, Any],
) -> None:
    # 1. Script cache cross-tenant isolation via REST & plugin store_script ownership
    s_a = client.post(
        "/v3/script-cache/store",
        json={
            "project_id": "proj_a",
            "intent_id": "TC-ISO-1",
            "platform": "android",
            "build_version": "v1",
            "screen_hash": "hash_a",
            "udcp_commands": [{"action": "tap", "target": "btn_a"}],
        },
    )
    assert s_a.status_code == 200, s_a.text
    cache_id_a = s_a.json()["entry"]["cache_id"]

    # proj_b cannot record feedback or invalidate proj_a's cache entry
    fb_b = client.post(
        "/v3/script-cache/feedback",
        json={"project_id": "proj_b", "cache_id": cache_id_a, "success": False},
    )
    assert fb_b.status_code == 404

    inv_b = client.post(
        "/v3/script-cache/invalidate",
        json={"project_id": "proj_b", "cache_id": cache_id_a, "reason": "cross_tenant_attack"},
    )
    assert inv_b.status_code == 200
    assert inv_b.json()["invalidated"] is False

    # proj_a entry remains valid and untouched
    lookup_a = client.post(
        "/v3/script-cache/lookup",
        json={
            "project_id": "proj_a",
            "intent_id": "TC-ISO-1",
            "platform": "android",
            "build_version": "v1",
            "screen_hash": "hash_a",
        },
    )
    assert lookup_a.status_code == 200
    assert lookup_a.json()["cache_hit"] is True
    assert lookup_a.json()["entry"]["project_id"] == "proj_a"

    # store_script with proj_b reusing cache_id_a must preserve proj_a ownership
    reg = get_default_registry()
    cache_plugin = reg.resolve(PluginCategory.SCRIPT_CACHE, project_id="proj_b")
    try:
        hijack_res = cache_plugin.store_script(
            {
                "cache_id": cache_id_a,
                "project_id": "proj_b",
                "intent_id": "TC-HIJACK",
                "platform": "android",
                "build_version": "v2",
                "screen_hash": "hash_b",
                "udcp_commands": [{"action": "tap", "target": "evil"}],
            }
        )
        assert hijack_res["project_id"] == "proj_a"
    except (ValueError, PermissionError):
        pass

    after_hijack = cache_plugin.lookup_script(
        project_id="proj_a",
        intent_id="TC-ISO-1",
        platform="android",
        build_version="v1",
        screen_hash="hash_a",
    )
    assert after_hijack is not None
    assert after_hijack["project_id"] == "proj_a"

    # 2. Telemetry artifacts cross-tenant isolation: index_artifact, window, and purge_expired
    shared_run = "run-shared-999"
    art_a = client.post(
        "/v3/artifacts/index",
        json={
            "project_id": "proj_a",
            "run_id": shared_run,
            "step_id": "step_1",
            "artifact_type": "uart_log",
            "ntp_timestamp_ms": 1_700_000_000_000,
            "uri": "s3://pb/proj_a/uart.log",
            "expires_at": 1_000,
        },
    )
    assert art_a.status_code == 200, art_a.text
    art_id_a = art_a.json()["artifact"]["artifact_id"]

    art_b = client.post(
        "/v3/artifacts/index",
        json={
            "project_id": "proj_b",
            "run_id": shared_run,
            "step_id": "step_1",
            "artifact_type": "uart_log",
            "ntp_timestamp_ms": 1_700_000_000_100,
            "uri": "s3://pb/proj_b/uart.log",
            "expires_at": 1_000,
        },
    )
    assert art_b.status_code == 200, art_b.text
    art_id_b = art_b.json()["artifact"]["artifact_id"]
    assert art_id_a != art_id_b

    # GET /v3/artifacts/window scoped to proj_b must NOT return proj_a's artifact
    win_b = client.get(
        "/v3/artifacts/window",
        params={
            "run_id": shared_run,
            "center_ts_ms": 1_700_000_000_050,
            "window_ms": 500,
            "project_id": "proj_b",
        },
    )
    assert win_b.status_code == 200, win_b.text
    win_b_ids = {item["artifact_id"] for item in win_b.json()["timeline"]}
    assert art_id_b in win_b_ids
    assert art_id_a not in win_b_ids

    # POST /v3/artifacts/purge scoped to proj_a must NOT delete proj_b's expired artifact
    purge_a = client.post(
        "/v3/artifacts/purge",
        json={"project_id": "proj_a", "now_ms": 9_999_999},
    )
    assert purge_a.status_code == 200, purge_a.text
    assert art_id_a in purge_a.json()["deleted_ids"]
    assert art_id_b not in purge_a.json()["deleted_ids"]

    art_plugin = reg.resolve(PluginCategory.ARTIFACT_STORE, project_id="proj_b")
    remaining_b = art_plugin.query_artifacts(run_id=shared_run, project_id="proj_b")
    assert [r["artifact_id"] for r in remaining_b] == [art_id_b]

    # index_artifact in proj_a cannot hijack proj_b's artifact_id
    try:
        art_hijack = art_plugin.index_artifact(
            {
                "artifact_id": art_id_b,
                "project_id": "proj_a",
                "run_id": shared_run,
                "step_id": "step_1",
                "artifact_type": "uart_log",
                "ntp_timestamp_ms": 1_700_000_000_100,
                "uri": "s3://pb/proj_a/evil.log",
            }
        )
        assert art_hijack["project_id"] == "proj_b"
    except (ValueError, PermissionError):
        pass
    still_b = art_plugin.query_artifacts(run_id=shared_run, project_id="proj_b")
    assert len(still_b) == 1
    assert still_b[0]["project_id"] == "proj_b"
    assert still_b[0]["uri"] == "s3://pb/proj_b/uart.log"


# ============================================================================
# V3-SEC-06, V3-SEC-07 & HIGH-01: ReDoS Guard, ClickHouse IDs, Edge Metadata
# ============================================================================


def test_v3_sec_06_07_and_high_01_edge_metadata_roundtrip(
    client: TestClient,
    isolated_sec_env: Dict[str, Any],
) -> None:
    import time
    from projectbrain.plugins.builtins.cache_and_artifact_plugins import MinioClickHouseArtifactStorePlugin

    # V3-SEC-07: ClickHouse identifier SQLi rejection
    with pytest.raises(ValueError):
        MinioClickHouseArtifactStorePlugin(
            config={"clickhouse_database": "default; DROP TABLE telemetry_artifacts"}
        )

    # HIGH-01: GraphEdge.metadata expected_signals round-trip + canonical state name normalization
    etl_res = client.post(
        "/v3/etl/ingest",
        json={
            "project_id": "edge_meta_proj",
            "branch": "main",
            "version_tag": "fw-meta-1",
            "source_name": "flow_spec.md",
            "format_hint": "datasheet",
            "content": (
                "# Flow Spec\n\n"
                "```mermaid\n"
                "stateDiagram-v2\n"
                "    Booting --> Ready : BOOT_OK / READY_SIGNAL\n"
                "```\n"
            ),
        },
    )
    assert etl_res.status_code == 200, etl_res.text

    # Missing expected_signals -> FAIL (verifies GraphEdge.metadata persisted & hydrated)
    oracle_missing = client.post(
        "/v3/oracle/context",
        json={
            "project_id": "edge_meta_proj",
            "branch": "main",
            "version_tag": "fw-meta-1",
            "screen_id": "Ready",
            "telemetry_summary": {
                "uart_lines": ["BOOT_OK"],
                "observed_states": ["Ready"],
                "observed_signals": ["BOOT_OK"],
            },
        },
    )
    assert oracle_missing.status_code == 200, oracle_missing.text
    assert oracle_missing.json()["verdict"] == "FAIL"

    # Present expected_signals + V3-SEC-06 ReDoS pattern -> PASS and finishes quickly
    t_start = time.monotonic()
    oracle_ok = client.post(
        "/v3/oracle/context",
        json={
            "project_id": "edge_meta_proj",
            "branch": "main",
            "version_tag": "fw-meta-1",
            "screen_id": "Ready",
            "telemetry_summary": {
                "uart_lines": ["BOOT_OK", "READY_SIGNAL", "a" * 40 + "!"],
                "observed_states": ["Ready"],
                "observed_signals": ["BOOT_OK", "READY_SIGNAL"],
                "error_rules": [{"code": "REDOS_RULE", "pattern": "(a+)+$"}],
            },
        },
    )
    elapsed = time.monotonic() - t_start
    assert oracle_ok.status_code == 200, oracle_ok.text
    assert oracle_ok.json()["verdict"] == "PASS"
    assert elapsed < 2.0

