"""End-to-End Integration Test Suite for Project-Brain v3.0 Plugin Microkernel & Testing KB.

Covers AC-PLG-01 through AC-PLG-12:
1. /plugins/* REST routes: list 25 built-ins, per-project enable/disable/config,
   hot-reload external plugin directory, path traversal HTTP 400 rejection,
   PB_REQUIRE_REST_AUTH=true RBAC (401 anon, 403 reader, 200 collaborator/admin),
   and PB_READ_ONLY=true enforcement.
2. Closed-loop /v3/* Layer 0 -> Layer 1 -> Layer 2 lifecycle:
   - Layer 0: POST /v3/webhooks/gitlab (valid/invalid token & HMAC + .bin/.apk MR payload)
   - Layer 1 ETL & Flow: POST /v3/etl/ingest (CSV/TestRail, Hardware Datasheet,
     OpenAPI 3.1, C++ firmware) + GET /v3/flow-graph?version_tag=fw-v1.2
   - Layer 2 Planner: POST /v3/planner/test-dag (flash_firmware/install_app injection,
     blast radius, acyclic topological waves, script cache binding)
   - Layer 1 Script Cache: POST /v3/script-cache/{store,lookup,feedback,invalidate}
     + fallback-rate auto-invalidation
   - Layer 1 Telemetry Artifacts: POST /v3/artifacts/index, GET /v3/artifacts/window
     (video_offset_ms alignment), POST /v3/artifacts/purge (TTL cleanup)
   - Layer 2 AI Test Oracle: POST /v3/oracle/context (PASS & FAIL on UART/HardFault/power)
   - Layer 2 RCA Triage: POST /v3/rca/triage (deduplication occurrence_count==2,
     Jira ticket merge, FlakyScript cache invalidation feedback)
3. All 10 new FastMCP tools in ai/mcp.py + _is_write_tool RBAC & SSEFilter verification.
4. CLI subcommands (plugins, v3-etl, v3-cache, v3-artifacts, v3-plan, v3-oracle, v3-rca)
   executed via subprocess + main.py runner.
5. get_graph_store & UnifiedKBBuilder delegation to PluginRegistry (including custom
   graph.neo4j memory-mode store and custom spy ETL/Linker/Exporter plugins).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from projectbrain.ai.mcp import (
    mcp_server,
    projectbrain_list_plugins,
    projectbrain_toggle_plugin,
    projectbrain_v3_index_artifact,
    projectbrain_v3_ingest_etl,
    projectbrain_v3_oracle_context,
    projectbrain_v3_plan_test_dag,
    projectbrain_v3_query_evidence_window,
    projectbrain_v3_rca_triage,
    projectbrain_v3_script_cache_lookup,
    projectbrain_v3_script_cache_store,
)
from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.graphify_engine.kb_linker import UnifiedKBBuilder
from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from projectbrain.graphify_engine.store import get_graph_store
from projectbrain.plugins import (
    DocExporterPlugin,
    ETLParserPlugin,
    KnowledgeLinkerPlugin,
    PluginCategory,
    PluginMetadata,
    PluginRegistry,
    get_default_registry,
    reset_default_registry,
)
from projectbrain.plugins.builtins.storage_plugins import Neo4jGraphStore
from projectbrain.server.api import SSEFilter, _is_write_tool, create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


@pytest.fixture
def isolated_v3_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provide strictly isolated workspace, SQLite DB, and fresh PluginRegistry."""
    orig_db_url = env.database_url
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(ws)

    db_file = tmp_path / "test_v3_integration.db"
    monkeypatch.setenv("OM_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("OM_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_READ_ONLY", "false")
    monkeypatch.setenv("OM_READ_ONLY", "false")
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.setenv("PB_MODE", "local")
    monkeypatch.delenv("PB_DISABLED_PLUGINS", raising=False)
    monkeypatch.delenv("PB_ENABLED_PLUGINS", raising=False)
    monkeypatch.delenv("PB_PLUGINS_DIR", raising=False)
    monkeypatch.delenv("PB_GITLAB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PB_WEBHOOK_SECRET", raising=False)

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


@pytest.fixture
def client(isolated_v3_env) -> TestClient:
    """FastAPI TestClient bound to isolated v3 environment."""
    return TestClient(create_app())


# ============================================================================
# 1. /plugins/* REST Routes, Hot-Reload, Path Traversal & RBAC
# ============================================================================


def test_plugins_rest_list_toggle_config_and_hot_reload(
    client: TestClient, isolated_v3_env: Dict[str, Any]
) -> None:
    ws: Path = isolated_v3_env["workspace"]

    # 1. List all 25 built-in plugins
    res = client.get("/plugins")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "healthy"
    assert body["total_plugins"] == 25
    assert body["enabled_plugins"] == 25
    assert len(body["plugins"]) == 25
    assert len(body["by_category"]) == 9

    # Filter by category
    cat_res = client.get("/plugins", params={"category": "brain_consumer"})
    assert cat_res.status_code == 200
    assert {p["plugin_id"] for p in cat_res.json()["plugins"]} == {
        "brain.planner",
        "brain.oracle",
        "brain.rca",
    }

    # 2. Per-project enable / disable / configure
    dis_res = client.post(
        "/plugins/cache.redis/disable",
        json={"project_id": "proj_alpha"},
    )
    assert dis_res.status_code == 200
    assert dis_res.json()["enabled"] is False

    # proj_beta remains unaffected
    alpha_list = client.get(
        "/plugins",
        params={"category": "script_cache", "project_id": "proj_alpha", "include_disabled": False},
    )
    beta_list = client.get(
        "/plugins",
        params={"category": "script_cache", "project_id": "proj_beta", "include_disabled": False},
    )
    assert {p["plugin_id"] for p in alpha_list.json()["plugins"]} == {"cache.sqlite_pg"}
    assert {p["plugin_id"] for p in beta_list.json()["plugins"]} == {"cache.sqlite_pg", "cache.redis"}

    en_res = client.post(
        "/plugins/cache.redis/enable",
        json={"project_id": "proj_alpha"},
    )
    assert en_res.status_code == 200
    assert en_res.json()["enabled"] is True

    cfg_res = client.put(
        "/plugins/cache.sqlite_pg/config",
        json={
            "project_id": "proj_alpha",
            "priority": 150,
            "config": {"fallback_threshold": 0.25, "min_samples": 4},
        },
    )
    assert cfg_res.status_code == 200
    cfg_data = cfg_res.json()
    assert cfg_data["priority"] == 150
    assert cfg_data["config"]["fallback_threshold"] == 0.25
    assert cfg_data["config"]["min_samples"] == 4

    # Unknown plugin returns 404
    assert client.post("/plugins/nonexistent.plugin/enable").status_code == 404

    # 3. Hot-reload custom external plugin directory inside configured workspace plugins dir
    ext_dir = ws / "plugins"
    pkg_dir = ext_dir / "custom_json_exporter"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "exporter.custom_e2e",
                "name": "Custom E2E Exporter",
                "version": "1.0.0",
                "category": "doc_exporter",
                "entrypoint": "plugin.py",
                "class_name": "CustomE2EExporterPlugin",
                "priority": 120,
                "default_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    (pkg_dir / "plugin.py").write_text(
        "from projectbrain.plugins.base import DocExporterPlugin, PluginMetadata\n"
        "class CustomE2EExporterPlugin(DocExporterPlugin):\n"
        "    metadata = PluginMetadata(\n"
        "        plugin_id='exporter.custom_e2e',\n"
        "        name='Custom E2E Exporter',\n"
        "        version='1.0.0',\n"
        "        category='doc_exporter',\n"
        "        priority=120,\n"
        "    )\n"
        "    export_format = 'custom_json'\n"
        "    def export(self, graph_data, output_path, context=None):\n"
        "        return {'status': 'ok', 'version': 'v1'}\n",
        encoding="utf-8",
    )

    rel_res = client.post("/plugins/reload", json={"directory": "plugins"})
    assert rel_res.status_code == 200, rel_res.text
    assert "exporter.custom_e2e" in rel_res.json()["reloaded_plugins"]

    after_list = client.get("/plugins")
    assert after_list.json()["total_plugins"] == 26
    assert any(p["plugin_id"] == "exporter.custom_e2e" for p in after_list.json()["plugins"])

    # Update plugin version and hot-reload again without server restart
    (pkg_dir / "plugin.py").write_text(
        "from projectbrain.plugins.base import DocExporterPlugin, PluginMetadata\n"
        "class CustomE2EExporterPlugin(DocExporterPlugin):\n"
        "    metadata = PluginMetadata(\n"
        "        plugin_id='exporter.custom_e2e',\n"
        "        name='Custom E2E Exporter',\n"
        "        version='1.1.0',\n"
        "        category='doc_exporter',\n"
        "        priority=120,\n"
        "    )\n"
        "    export_format = 'custom_json'\n"
        "    def export(self, graph_data, output_path, context=None):\n"
        "        return {'status': 'ok', 'version': 'v2'}\n",
        encoding="utf-8",
    )
    rel_res2 = client.post("/plugins/reload", json={"directory": "plugins"})
    assert rel_res2.status_code == 200
    custom_meta = next(
        p for p in client.get("/plugins").json()["plugins"] if p["plugin_id"] == "exporter.custom_e2e"
    )
    assert custom_meta["version"] == "1.1.0"


def test_plugins_and_v3_path_traversal_and_read_only_rejected(
    client: TestClient, isolated_v3_env: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Path traversal on /plugins/reload
    r1 = client.post("/plugins/reload", json={"directory": "../outside_dir"})
    assert r1.status_code == 400
    assert "path traversal detected" in r1.json()["detail"]

    r2 = client.post("/plugins/reload", json={"directory": "/etc"})
    assert r2.status_code == 400
    assert "path traversal detected" in r2.json()["detail"]

    # Path traversal on /v3/etl/ingest
    r3 = client.post(
        "/v3/etl/ingest",
        json={"project_id": "sec_p", "file_path": "../../etc/passwd"},
    )
    assert r3.status_code == 400
    assert "path traversal detected" in r3.json()["detail"]

    r4 = client.post(
        "/v3/etl/ingest",
        json={"project_id": "sec_p", "file_path": "/etc/passwd"},
    )
    assert r4.status_code == 400

    # Read-only mode blocks mutations with 403 while reads remain 200
    monkeypatch.setenv("PB_READ_ONLY", "true")
    assert client.post("/plugins/reload", json={}).status_code == 403
    assert client.post("/plugins/cache.redis/disable", json={}).status_code == 403
    assert client.put("/plugins/cache.redis/config", json={"config": {}}).status_code == 403
    assert client.post("/v3/etl/ingest", json={"content": "x"}).status_code == 403
    assert client.post(
        "/v3/script-cache/store",
        json={"intent_id": "TC-1", "platform": "android", "build_version": "v1"},
    ).status_code == 403
    assert client.post(
        "/v3/artifacts/index",
        json={
            "run_id": "r1",
            "step_id": "s1",
            "artifact_type": "uart_log",
            "ntp_timestamp_ms": 1000,
            "uri": "file://u.log",
        },
    ).status_code == 403
    assert client.post("/v3/rca/triage", json={"stacktrace_or_log": "err"}).status_code == 403

    # Read routes remain 200
    assert client.get("/plugins").status_code == 200
    assert client.get("/v3/flow-graph", params={"project_id": "sec_p"}).status_code == 200
    assert client.post(
        "/v3/script-cache/lookup",
        json={"intent_id": "TC-1", "platform": "android", "build_version": "v1"},
    ).status_code == 200


def test_plugins_and_v3_rest_auth_rbac_matrix(
    client: TestClient, isolated_v3_env: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Create reader, collaborator, and admin accounts before enabling strict REST auth
    for uname, tok, role in (
        ("v3_reader", "pb_tok_v3_reader", "reader"),
        ("v3_collab", "pb_tok_v3_collab", "collaborator"),
        ("v3_admin", "pb_tok_v3_admin", "admin"),
    ):
        acc_res = client.post(
            "/auth/accounts",
            json={"username": uname, "token": tok, "role": role, "allowed_tools": "*"},
        )
        assert acc_res.status_code == 200, acc_res.text

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")

    # 1. Anonymous requests -> 401
    assert client.get("/plugins").status_code == 401
    assert client.get("/v3/flow-graph").status_code == 401
    assert client.post(
        "/v3/planner/test-dag",
        json={"project_id": "rbac_p"},
    ).status_code == 401

    reader_hdrs = {"Authorization": "Bearer pb_tok_v3_reader"}
    collab_hdrs = {"Authorization": "Bearer pb_tok_v3_collab"}
    admin_hdrs = {"Authorization": "Bearer pb_tok_v3_admin"}

    # 2. Reader: read-only GET & _READ_ONLY_POST_ROUTES succeed (200)
    assert client.get("/plugins", headers=reader_hdrs).status_code == 200
    assert client.get("/v3/flow-graph", params={"project_id": "rbac_p"}, headers=reader_hdrs).status_code == 200
    assert client.get(
        "/v3/artifacts/window",
        params={"run_id": "r-none", "center_ts_ms": 1000},
        headers=reader_hdrs,
    ).status_code == 200
    assert client.post(
        "/v3/script-cache/lookup",
        json={"project_id": "rbac_p", "intent_id": "TC-1", "platform": "android", "build_version": "v1"},
        headers=reader_hdrs,
    ).status_code == 200
    assert client.post(
        "/v3/planner/test-dag",
        json={"project_id": "rbac_p"},
        headers=reader_hdrs,
    ).status_code == 200
    assert client.post(
        "/v3/oracle/context",
        json={"project_id": "rbac_p", "telemetry_summary": {}},
        headers=reader_hdrs,
    ).status_code == 200

    # 3. Reader: mutating /plugins/* and /v3/* routes blocked with 403
    mutating_calls = [
        ("POST", "/plugins/reload", {}),
        ("POST", "/plugins/cache.redis/disable", {"project_id": "rbac_p"}),
        ("POST", "/plugins/cache.redis/enable", {"project_id": "rbac_p"}),
        ("PUT", "/plugins/cache.redis/config", {"project_id": "rbac_p", "config": {"ttl": 60}}),
        ("POST", "/v3/etl/ingest", {"project_id": "rbac_p", "content": "openapi: '3.1.0'\npaths: {}", "format_hint": "openapi31"}),
        ("POST", "/v3/script-cache/store", {"project_id": "rbac_p", "intent_id": "TC-1", "platform": "android", "build_version": "v1"}),
        ("POST", "/v3/script-cache/feedback", {"project_id": "rbac_p", "cache_id": "c1", "success": True}),
        ("POST", "/v3/script-cache/invalidate", {"project_id": "rbac_p", "cache_id": "c1"}),
        ("POST", "/v3/artifacts/index", {"project_id": "rbac_p", "run_id": "r1", "step_id": "s1", "artifact_type": "uart_log", "ntp_timestamp_ms": 1000, "uri": "file://x.log"}),
        ("POST", "/v3/artifacts/purge", {"project_id": "rbac_p"}),
        ("POST", "/v3/rca/triage", {"project_id": "rbac_p", "stacktrace_or_log": "HTTP 500"}),
        ("POST", "/v3/webhooks/gitlab", {"project_id": "rbac_p"}),
    ]
    for method, url, payload in mutating_calls:
        resp = client.request(method, url, json=payload, headers=reader_hdrs)
        assert resp.status_code == 403, f"Expected 403 for reader on {method} {url}, got {resp.status_code}"

    # 4. Collaborator & Admin: mutating routes succeed (200)
    collab_store = client.post(
        "/v3/script-cache/store",
        json={
            "project_id": "rbac_p",
            "intent_id": "TC-RBAC",
            "platform": "android",
            "build_version": "v1.0",
            "udcp_commands": [{"action": "tap", "target": "ok"}],
        },
        headers=collab_hdrs,
    )
    assert collab_store.status_code == 200, collab_store.text

    admin_toggle = client.post(
        "/plugins/cache.redis/disable",
        json={"project_id": "rbac_p"},
        headers=admin_hdrs,
    )
    assert admin_toggle.status_code == 200, admin_toggle.text


# ============================================================================
# 2. Closed-Loop /v3/* Layer 0 -> Layer 1 -> Layer 2 Lifecycle
# ============================================================================


def test_closed_loop_v3_layer0_to_layer2_lifecycle(
    client: TestClient, isolated_v3_env: Dict[str, Any]
) -> None:
    ws: Path = isolated_v3_env["workspace"]
    proj = "iot_ble_v3"
    branch = "feat/ble-fw-v1.2"
    ver = "fw-v1.2"

    # ------------------------------------------------------------------
    # Step 1: Layer 0 Webhook Ingest (GitLab MR with .bin & .apk + HMAC/Token)
    # ------------------------------------------------------------------
    secret = "gitlab-e2e-secret"
    cfg_wh = client.put(
        "/plugins/webhook.event_ingest/config",
        json={"project_id": proj, "config": {"webhook_secret": secret}},
    )
    assert cfg_wh.status_code == 200

    mr_payload = {
        "project_id": proj,
        "object_kind": "merge_request",
        "object_attributes": {
            "iid": 108,
            "source_branch": branch,
            "target_branch": "main",
            "title": "IOT-402 Update BLE pairing & OTA firmware",
            "description": "Adds UART pairing diagnostics and Android BLE v3 flow",
        },
        "version_tag": ver,
        "changes": [
            {"new_path": "src/fw/ble_pair.cpp"},
            {"new_path": "dist/firmware.bin"},
            {"new_path": "dist/app-release.apk"},
        ],
    }
    raw_mr = json.dumps(mr_payload).encode("utf-8")

    # Invalid token & invalid HMAC rejected with 401
    bad_tok = client.post(
        "/v3/webhooks/gitlab",
        content=raw_mr,
        headers={"Content-Type": "application/json", "X-Gitlab-Token": "wrong-token"},
    )
    assert bad_tok.status_code == 401

    bad_hmac = client.post(
        "/v3/webhooks/gitlab",
        content=raw_mr,
        headers={"Content-Type": "application/json", "X-PB-Signature": "sha256=deadbeef"},
    )
    assert bad_hmac.status_code == 401

    # Valid GitLab token accepted
    wh_res = client.post(
        "/v3/webhooks/gitlab",
        content=raw_mr,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": secret,
            "X-Gitlab-Event": "Merge Request Hook",
        },
    )
    assert wh_res.status_code == 200, wh_res.text
    wh_data = wh_res.json()
    assert wh_data["source"] == "gitlab"
    assert wh_data["branch"] == branch
    assert wh_data["version_tag"] == ver
    assert "src/fw/ble_pair.cpp" in wh_data["changed_files"]
    artifact_names = [a["name"] for a in wh_data["artifacts"]]
    assert "dist/firmware.bin" in artifact_names
    assert "dist/app-release.apk" in artifact_names

    # Also verify valid HMAC-SHA256 passes
    valid_sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw_mr, hashlib.sha256).hexdigest()
    wh_hmac_res = client.post(
        "/v3/webhooks/gitlab",
        content=raw_mr,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
    )
    assert wh_hmac_res.status_code == 200

    # ------------------------------------------------------------------
    # Step 2: Layer 1 ETL Ingest (Testcases, Datasheet+Flow, OpenAPI, C++)
    # ------------------------------------------------------------------
    # 2a. Legacy Testcases (CSV)
    tc_csv = ws / "qa_ble_cases.csv"
    tc_csv.write_text(
        "TC_ID,Title,Preconditions,Steps,Expected Result,Priority,Platform,SRS_Ref\n"
        "TC-BOOT-P0,MCU Cold Boot,Power cycle device,1. Wait boot,UART BOOT_OK within 2500ms,P0,firmware,REQ-BOOT-01\n"
        "TC-BLE-PAIR,BLE Pairing Flow,Pair BLE and flash firmware,1. Tap Pair; 2. Wait event,UART PAIRED_EVENT and UI PairingScreen visible,P1,android,REQ-BLE-01\n",
        encoding="utf-8",
    )
    etl_tc = client.post(
        "/v3/etl/ingest",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "file_path": "qa_ble_cases.csv",
        },
    )
    assert etl_tc.status_code == 200, etl_tc.text
    assert etl_tc.json()["plugin_id"] == "etl.legacy_testcase"
    assert len(etl_tc.json()["canonical_intents"]) == 2

    # 2a-bis. TestRail JSON testcase export
    etl_tr = client.post(
        "/v3/etl/ingest",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "source_name": "testrail_ble.json",
            "format_hint": "testrail_json",
            "content": json.dumps(
                {
                    "cases": [
                        {
                            "id": "TR-301",
                            "title": "API Token Refresh Check",
                            "custom_preconds": "Seed database",
                            "custom_steps_separated": [
                                {"content": "POST /v1/auth/refresh", "expected": "HTTP 200 OK"}
                            ],
                            "priority": "P1",
                            "refs": "REQ-AUTH-02",
                        }
                    ]
                }
            ),
        },
    )
    assert etl_tr.status_code == 200, etl_tr.text
    assert etl_tr.json()["plugin_id"] == "etl.legacy_testcase"
    assert etl_tr.json()["canonical_intents"][0]["intent_id"] == "TR-301"

    # 2b. Hardware Datasheet + Mermaid StateFlow transitions
    ds_md = ws / "ble_datasheet.md"
    ds_md.write_text(
        "# BLE Hardware Datasheet & Flow\n\n"
        "| Code | Hex | Description | Remediation |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| AT+ERR=0x04 | 0x04 | BLE connection timeout | Retry pairing |\n"
        "| UART_ERR_CRC | 0x12 | UART frame CRC mismatch | Check 115200 baud |\n\n"
        "max_current_ma: 120 mA\n\n"
        "```mermaid\n"
        "stateDiagram-v2\n"
        "    state \"Pairing Screen\" as PairingScreen\n"
        "    Booting --> BleAdvertising : BOOT_OK -> ADV_STARTED\n"
        "    BleAdvertising --> PairingScreen : USER_PAIR / PAIRED_EVENT timeout=3000ms\n"
        "```\n",
        encoding="utf-8",
    )
    etl_ds = client.post(
        "/v3/etl/ingest",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "file_path": "ble_datasheet.md",
        },
    )
    assert etl_ds.status_code == 200, etl_ds.text
    assert etl_ds.json()["plugin_id"] == "etl.hardware_datasheet"
    assert len(etl_ds.json()["error_rules"]) >= 2
    assert len(etl_ds.json()["flow_nodes"]) >= 3
    assert len(etl_ds.json()["flow_edges"]) >= 2

    # 2c. OpenAPI 3.1 spec
    etl_api = client.post(
        "/v3/etl/ingest",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "source_name": "device_api.yaml",
            "format_hint": "openapi31",
            "content": (
                'openapi: "3.1.0"\n'
                'info:\n  title: Device API\n  version: "1.2.0"\n'
                "paths:\n"
                "  /v1/ble/pair:\n"
                "    post:\n"
                "      operationId: pairDevice\n"
                "      responses:\n"
                '        "200": {description: OK}\n'
                '        "503": {description: BLE Bridge Unavailable}\n'
            ),
        },
    )
    assert etl_api.status_code == 200, etl_api.text
    assert etl_api.json()["plugin_id"] == "etl.openapi31"
    assert any(r["code"] == "HTTP_503" for r in etl_api.json()["error_rules"])

    # 2d. C++ Firmware source
    fw_dir = ws / "src" / "fw"
    fw_dir.mkdir(parents=True, exist_ok=True)
    (fw_dir / "ble_pair.cpp").write_text(
        "#define ERR_BLE_DISCONNECTED -101\n"
        "class BleController {\n"
        "public:\n"
        "    void ble_pair_start();\n"
        "};\n"
        "void USART1_IRQHandler(void) {}\n",
        encoding="utf-8",
    )
    etl_cpp = client.post(
        "/v3/etl/ingest",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "file_path": "src/fw/ble_pair.cpp",
        },
    )
    assert etl_cpp.status_code == 200, etl_cpp.text
    assert etl_cpp.json()["plugin_id"] == "etl.cpp_embedded"

    # Query GET /v3/flow-graph scoped by version_tag="fw-v1.2"
    fg_res = client.get(
        "/v3/flow-graph",
        params={"project_id": proj, "branch": branch, "version_tag": ver},
    )
    assert fg_res.status_code == 200, fg_res.text
    fg = fg_res.json()
    assert fg["version_tag"] == ver
    assert len(fg["nodes"]) >= 3
    assert len(fg["edges"]) >= 2
    assert all((n.get("metadata") or {}).get("version_tag") == ver for n in fg["nodes"])
    fg_node_ids = {n["id"] for n in fg["nodes"]}
    assert all(e["source"] in fg_node_ids and e["target"] in fg_node_ids for e in fg["edges"])

    # ------------------------------------------------------------------
    # Step 3 & 4: Script Cache Store/Lookup + Impact Test DAG Planner
    # ------------------------------------------------------------------
    store_res = client.post(
        "/v3/script-cache/store",
        json={
            "project_id": proj,
            "intent_id": "TC-BLE-PAIR",
            "platform": "android",
            "build_version": ver,
            "screen_hash": "scr_pair_hash_01",
            "ui_fingerprint": {"button_id": "btn_pair", "text": "Pair"},
            "udcp_commands": [{"action": "tap", "target": "btn_pair"}],
        },
    )
    assert store_res.status_code == 200, store_res.text
    cache_id = store_res.json()["entry"]["cache_id"]

    lookup_res = client.post(
        "/v3/script-cache/lookup",
        json={
            "project_id": proj,
            "intent_id": "TC-BLE-PAIR",
            "platform": "android",
            "build_version": ver,
            "screen_hash": "scr_pair_hash_01",
        },
    )
    assert lookup_res.status_code == 200
    assert lookup_res.json()["cache_hit"] is True
    assert lookup_res.json()["entry"]["cache_id"] == cache_id

    # Plan Test DAG using webhook-normalized changed_files & .bin/.apk artifacts
    dag_res = client.post(
        "/v3/planner/test-dag",
        json={
            "project_id": proj,
            "branch": branch,
            "base_branch": "main",
            "version_tag": ver,
            "srs_diff": wh_data["srs_diff"],
            "code_diff_files": wh_data["changed_files"],
            "artifacts": artifact_names,
            "target_devices": ["android"],
        },
    )
    assert dag_res.status_code == 200, dag_res.text
    dag_body = dag_res.json()
    assert dag_body["status"] == "ok"
    assert dag_body["acyclic"] is True
    dag_nodes = {n["id"]: n for n in dag_body["dag"]["nodes"]}
    assert "precond_flash_firmware" in dag_nodes
    assert "precond_install_app" in dag_nodes
    assert "TC-BOOT-P0" in dag_nodes
    assert "TC-BLE-PAIR" in dag_nodes
    assert dag_nodes["TC-BLE-PAIR"]["cache_binding"]["cache_hit"] is True
    assert dag_nodes["TC-BLE-PAIR"]["cache_binding"]["cache_id"] == cache_id
    assert len(dag_body["dag"]["execution_waves"]) >= 2

    # Fallback-rate auto-invalidation on script cache (> 0.25 after 4 samples)
    fb1 = client.post(
        "/v3/script-cache/feedback",
        json={"project_id": proj, "cache_id": cache_id, "success": True},
    )
    assert fb1.status_code == 200
    assert fb1.json()["invalidated"] is False

    for _ in range(3):
        fb_fail = client.post(
            "/v3/script-cache/feedback",
            json={
                "project_id": proj,
                "cache_id": cache_id,
                "success": False,
                "fallback_reason": "ui_element_moved",
            },
        )
        assert fb_fail.status_code == 200

    assert fb_fail.json()["invalidated"] is True
    assert fb_fail.json()["fallback_rate"] == 0.75

    lookup_after = client.post(
        "/v3/script-cache/lookup",
        json={
            "project_id": proj,
            "intent_id": "TC-BLE-PAIR",
            "platform": "android",
            "build_version": ver,
            "screen_hash": "scr_pair_hash_01",
        },
    )
    assert lookup_after.json()["cache_hit"] is False

    # Manual invalidate on a fresh cache entry
    s2 = client.post(
        "/v3/script-cache/store",
        json={
            "project_id": proj,
            "intent_id": "TC-BOOT-P0",
            "platform": "firmware",
            "build_version": ver,
            "screen_hash": "scr_boot",
            "udcp_commands": [{"action": "wait_uart", "pattern": "BOOT_OK"}],
        },
    ).json()["entry"]
    inv_res = client.post(
        "/v3/script-cache/invalidate",
        json={"project_id": proj, "cache_id": s2["cache_id"], "reason": "manual_test"},
    )
    assert inv_res.status_code == 200
    assert inv_res.json()["invalidated"] is True

    # ------------------------------------------------------------------
    # Step 5: Synchronized Telemetry & Evidence Window + TTL Purge
    # ------------------------------------------------------------------
    run_id = "run-e2e-001"
    t0 = 1_700_000_000_000
    artifacts_to_index = [
        {
            "project_id": proj,
            "run_id": run_id,
            "step_id": "step_0",
            "artifact_type": "video_h264",
            "ntp_timestamp_ms": t0,
            "duration_ms": 10000,
            "uri": "s3://pb/run-e2e-001/video.h264",
            "retention_days": 30,
        },
        {
            "project_id": proj,
            "run_id": run_id,
            "step_id": "step_1",
            "artifact_type": "uart_log",
            "ntp_timestamp_ms": t0 + 2500,
            "uri": "s3://pb/run-e2e-001/uart.log",
            "retention_days": 30,
        },
        {
            "project_id": proj,
            "run_id": run_id,
            "step_id": "step_1",
            "artifact_type": "power_trace_ma",
            "ntp_timestamp_ms": t0 + 2800,
            "uri": "s3://pb/run-e2e-001/power.csv",
            "summary_metrics": {"peak_ma": 95.0},
            "retention_days": 30,
        },
        {
            "project_id": proj,
            "run_id": run_id,
            "step_id": "step_2",
            "artifact_type": "gdb_hardfault",
            "ntp_timestamp_ms": t0 + 3000,
            "uri": "s3://pb/run-e2e-001/hardfault.txt",
            "retention_days": 90,
        },
    ]
    for art in artifacts_to_index:
        idx_res = client.post("/v3/artifacts/index", json=art)
        assert idx_res.status_code == 200, idx_res.text

    win_res = client.get(
        "/v3/artifacts/window",
        params={"run_id": run_id, "center_ts_ms": t0 + 2750, "window_ms": 500, "project_id": proj},
    )
    assert win_res.status_code == 200, win_res.text
    win_data = win_res.json()
    offsets = {item["artifact_type"]: item["video_offset_ms"] for item in win_data["timeline"]}
    assert offsets == {
        "uart_log": 2500,
        "power_trace_ma": 2800,
        "gdb_hardfault": 3000,
    }

    # Index an already-expired artifact and purge at now_ms
    client.post(
        "/v3/artifacts/index",
        json={
            "project_id": proj,
            "run_id": "run-old",
            "step_id": "s0",
            "artifact_type": "screenshot_png",
            "ntp_timestamp_ms": 1_000_000,
            "uri": "s3://pb/run-old/s.png",
            "expires_at": 1_500_000,
        },
    )
    purge_res = client.post("/v3/artifacts/purge", json={"project_id": proj, "now_ms": 2_000_000})
    assert purge_res.status_code == 200
    assert purge_res.json()["deleted_count"] >= 1

    # ------------------------------------------------------------------
    # Step 6: AI Test Oracle Context (PASS & FAIL)
    # ------------------------------------------------------------------
    oracle_pass = client.post(
        "/v3/oracle/context",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "test_intent_id": "TC-BLE-PAIR",
            "power_budget_ma": 120.0,
            "telemetry_summary": {
                "uart_lines": ["BOOT_OK", "ADV_STARTED", "PAIRED_EVENT"],
                "observed_states": ["flow:BleAdvertising", "flow:PairingScreen"],
                "observed_signals": ["BOOT_OK", "ADV_STARTED", "PAIRED_EVENT"],
                "http_responses": [{"status": 200, "path": "/v1/ble/pair"}],
                "power_ma_peak": 95.0,
                "crash_detected": False,
                "hardfault_cfsr": "0x00000000",
            },
        },
    )
    assert oracle_pass.status_code == 200, oracle_pass.text
    assert oracle_pass.json()["verdict"] == "PASS"
    assert oracle_pass.json()["violated_rules"] == []

    oracle_fail = client.post(
        "/v3/oracle/context",
        json={
            "project_id": proj,
            "branch": branch,
            "version_tag": ver,
            "test_intent_id": "TC-BLE-PAIR",
            "power_budget_ma": 120.0,
            "telemetry_summary": {
                "uart_lines": ["AT+ERR=0x04 BLE_TIMEOUT", "HardFault_Handler CFSR=0x00008200"],
                "http_responses": [{"status": 503, "path": "/v1/ble/pair"}],
                "power_ma_peak": 175.0,
                "crash_detected": True,
                "hardfault_cfsr": "0x00008200",
            },
        },
    )
    assert oracle_fail.status_code == 200, oracle_fail.text
    fail_body = oracle_fail.json()
    assert fail_body["verdict"] == "FAIL"
    fail_codes = {r["code"] for r in fail_body["violated_rules"]}
    assert {"AT+ERR=0x04", "CRASH_DETECTED", "ARM_HARDFAULT_CFSR", "POWER_BUDGET_EXCEEDED", "HTTP_503"} <= fail_codes

    # ------------------------------------------------------------------
    # Step 7: RCA Stacktrace Triage (Dedup, Ticket Merge & FlakyScript Feedback)
    # ------------------------------------------------------------------
    rca1 = client.post(
        "/v3/rca/triage",
        json={
            "project_id": proj,
            "run_id": "run-e2e-001",
            "stacktrace_or_log": (
                "2026-11-21T10:00:01Z [pid:111] HardFault_Handler CFSR=0x00008200 "
                "R0=0x20001000 PC=0x08001234 at ble_pair_start+0x10 ref IOT-402"
            ),
            "failure_artifacts": ["gdb_hardfault"],
        },
    )
    assert rca1.status_code == 200, rca1.text
    r1_data = rca1.json()
    assert r1_data["occurrence_count"] == 1
    assert r1_data["is_duplicate"] is False
    assert r1_data["fault_domain"] == "Firmware"

    rca2 = client.post(
        "/v3/rca/triage",
        json={
            "project_id": proj,
            "run_id": "run-e2e-002",
            "stacktrace_or_log": (
                "2026-11-21T11:22:33Z [pid:999] HardFault_Handler CFSR=0x00008200 "
                "R0=0x2000ff00 PC=0x0800abcd at ble_pair_start+0x24 ref IOT-402"
            ),
            "failure_artifacts": ["gdb_hardfault"],
            "ticket_refs": ["JIRA-901"],
        },
    )
    assert rca2.status_code == 200, rca2.text
    r2_data = rca2.json()
    assert r2_data["signature_hash"] == r1_data["signature_hash"]
    assert r2_data["incident_id"] == r1_data["incident_id"]
    assert r2_data["occurrence_count"] == 2
    assert r2_data["is_duplicate"] is True
    assert set(r2_data["ticket_refs"]) == {"IOT-402", "JIRA-901"}

    # FlakyScript cache invalidation feedback via RCA
    flaky_cache = client.post(
        "/v3/script-cache/store",
        json={
            "project_id": proj,
            "intent_id": "TC-FLAKY-UI",
            "platform": "android",
            "build_version": ver,
            "screen_hash": "scr_flaky_v1",
            "udcp_commands": [{"action": "tap", "target": "btn_old"}],
        },
    ).json()["entry"]
    flaky_cid = flaky_cache["cache_id"]

    rca_flaky = client.post(
        "/v3/rca/triage",
        json={
            "project_id": proj,
            "run_id": "run-e2e-003",
            "stacktrace_or_log": "UDCP locator timeout: Element not found for button#btn_old",
            "screen_hash": "scr_flaky_v2",
            "cache_id": flaky_cid,
            "test_intent_id": "TC-FLAKY-UI",
        },
    )
    assert rca_flaky.status_code == 200, rca_flaky.text
    flaky_data = rca_flaky.json()
    assert flaky_data["fault_domain"] == "FlakyScript"
    assert flaky_data["cache_feedback"]["applied"] is True
    assert flaky_data["cache_feedback"]["invalidated"] is True

    flaky_lookup = client.post(
        "/v3/script-cache/lookup",
        json={
            "project_id": proj,
            "intent_id": "TC-FLAKY-UI",
            "platform": "android",
            "build_version": ver,
            "screen_hash": "scr_flaky_v1",
        },
    )
    assert flaky_lookup.json()["cache_hit"] is False


# ============================================================================
# 3. All 10 FastMCP v3 Tools + _is_write_tool & Reader RBAC Verification
# ============================================================================


@pytest.mark.asyncio
async def test_fastmcp_v3_tools_rbac_and_execution(isolated_v3_env: Dict[str, Any]) -> None:
    ws: Path = isolated_v3_env["workspace"]
    read_tools = [
        "projectbrain_list_plugins",
        "projectbrain_v3_script_cache_lookup",
        "projectbrain_v3_query_evidence_window",
        "projectbrain_v3_plan_test_dag",
        "projectbrain_v3_oracle_context",
    ]
    write_tools = [
        "projectbrain_toggle_plugin",
        "projectbrain_v3_ingest_etl",
        "projectbrain_v3_script_cache_store",
        "projectbrain_v3_index_artifact",
        "projectbrain_v3_rca_triage",
    ]

    # 1. Verify all 10 tools are registered on FastMCP server
    registered_tools = getattr(mcp_server._tool_manager, "_tools", {}) or {}
    for tname in read_tools + write_tools:
        assert tname in registered_tools, f"Missing FastMCP tool: {tname}"

    # 2. Verify _is_write_tool classification
    for tname in read_tools:
        assert _is_write_tool(tname) is False, f"{tname} should be read-only"
    for tname in write_tools:
        assert _is_write_tool(tname) is True, f"{tname} should be write tool"

    # 3. Verify SSEFilter strips write tools and preserves read tools for reader role
    sse_filter = SSEFilter("reader", "*")
    payload_chunk = (
        "event: message\ndata: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "result": {"tools": [{"name": n, "description": n} for n in read_tools + write_tools]},
                "id": 1,
            }
        )
        + "\n\n"
    ).encode("utf-8")
    filtered_text = sse_filter.process_chunk(payload_chunk).decode("utf-8")
    filtered_names = {
        t["name"]
        for t in json.loads(filtered_text.split("data: ", 1)[1])["result"]["tools"]
    }
    assert filtered_names == set(read_tools)

    # 4. Verify HTTP /mcp/messages/ middleware blocks write tools (403) and permits read tools for reader
    app = create_app()
    sync_client = TestClient(app)
    acc = sync_client.post(
        "/auth/accounts",
        json={
            "username": "mcp_v3_reader",
            "token": "pb_tok_mcp_v3_reader",
            "role": "reader",
            "allowed_tools": "*",
        },
    )
    assert acc.status_code == 200

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        hdrs = {"Authorization": "Bearer pb_tok_mcp_v3_reader"}
        for idx, tname in enumerate(write_tools, start=10):
            resp = await ac.post(
                "/mcp/messages/?session_id=v3_rbac_sess",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tname, "arguments": {}},
                    "id": idx,
                },
                headers=hdrs,
            )
            assert resp.status_code == 403, f"Reader should get 403 on {tname}"
            assert "Read-only accounts cannot execute modifying tool" in resp.json()["error"]["message"]

        for idx, tname in enumerate(read_tools, start=50):
            resp = await ac.post(
                "/mcp/messages/?session_id=v3_rbac_sess",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tname, "arguments": {}},
                    "id": idx,
                },
                headers=hdrs,
            )
            assert resp.status_code != 403, f"Reader should not get 403 on {tname}"

    # 5. Direct Python invocation of all 10 FastMCP tools
    list_res = json.loads(await projectbrain_list_plugins())
    assert list_res["status"] == "ok"
    assert list_res["count"] == 25

    toggle_res = json.loads(
        await projectbrain_toggle_plugin(
            plugin_id="cache.redis",
            enabled=False,
            project_id="mcp_v3_proj",
        )
    )
    assert toggle_res["status"] == "ok"
    assert toggle_res["enabled"] is False

    tc_file = ws / "mcp_cases.csv"
    tc_file.write_text(
        "TC_ID,Title,Preconditions,Steps,Expected Result,Priority,Platform,SRS_Ref\n"
        "TC-MCP-01,Verify BLE Connect,Flash firmware,1. Connect BLE,UART BOOT_OK,P0,android,REQ-MCP-1\n",
        encoding="utf-8",
    )
    etl_res = json.loads(
        await projectbrain_v3_ingest_etl(
            file_path=str(tc_file),
            project_id="mcp_v3_proj",
            branch="main",
            version_tag="v3.0",
        )
    )
    assert etl_res["status"] == "ok"
    assert etl_res["canonical_intents_count"] == 1

    store_res = json.loads(
        await projectbrain_v3_script_cache_store(
            intent_id="TC-MCP-01",
            platform="android",
            build_version="v3.0",
            udcp_commands=[{"action": "tap", "target": "connect"}],
            project_id="mcp_v3_proj",
            screen_hash="mcp_scr_1",
        )
    )
    assert store_res["status"] == "ok"
    mcp_cid = store_res["entry"]["cache_id"]

    lookup_res = json.loads(
        await projectbrain_v3_script_cache_lookup(
            intent_id="TC-MCP-01",
            platform="android",
            build_version="v3.0",
            project_id="mcp_v3_proj",
            screen_hash="mcp_scr_1",
        )
    )
    assert lookup_res["cache_hit"] is True
    assert lookup_res["entry"]["cache_id"] == mcp_cid

    t_base = 1_700_005_000_000
    await projectbrain_v3_index_artifact(
        run_id="mcp-run-1",
        step_id="s0",
        artifact_type="video_h264",
        uri="s3://pb/mcp/v.h264",
        ntp_timestamp_ms=t_base,
        project_id="mcp_v3_proj",
    )
    art_res = json.loads(
        await projectbrain_v3_index_artifact(
            run_id="mcp-run-1",
            step_id="s1",
            artifact_type="uart_log",
            uri="s3://pb/mcp/uart.log",
            ntp_timestamp_ms=t_base + 1200,
            project_id="mcp_v3_proj",
        )
    )
    assert art_res["status"] == "ok"

    win_res = json.loads(
        await projectbrain_v3_query_evidence_window(
            run_id="mcp-run-1",
            center_ts_ms=t_base + 1200,
            window_ms=500,
            project_id="mcp_v3_proj",
        )
    )
    assert len(win_res["timeline"]) == 1
    assert win_res["timeline"][0]["video_offset_ms"] == 1200

    plan_res = json.loads(
        await projectbrain_v3_plan_test_dag(
            project_id="mcp_v3_proj",
            branch="main",
            version_tag="v3.0",
            code_diff_files=["src/fw/ble.c"],
            artifacts=["build/fw.bin"],
        )
    )
    assert plan_res["status"] == "ok"
    assert plan_res["acyclic"] is True

    oracle_res = json.loads(
        await projectbrain_v3_oracle_context(
            project_id="mcp_v3_proj",
            branch="main",
            version_tag="v3.0",
            test_intent_id="TC-MCP-01",
            telemetry_summary={"uart_lines": ["BOOT_OK"], "crash_detected": False},
        )
    )
    assert oracle_res["verdict"] == "PASS"

    rca_res = json.loads(
        await projectbrain_v3_rca_triage(
            stacktrace_or_log="UDCP locator timeout: Element not found for button#connect",
            project_id="mcp_v3_proj",
            run_id="mcp-run-1",
            test_intent_id="TC-MCP-01",
            cache_id=mcp_cid,
        )
    )
    assert rca_res["fault_domain"] == "FlakyScript"
    assert rca_res["cache_feedback"]["invalidated"] is True


# ============================================================================
# 4. CLI Subcommands (plugins, v3-etl, v3-cache, v3-artifacts, v3-plan, v3-oracle, v3-rca)
# ============================================================================


def test_cli_v3_subcommands_via_subprocess(isolated_v3_env: Dict[str, Any]) -> None:
    ws: Path = isolated_v3_env["workspace"]
    db_file: Path = isolated_v3_env["db_file"]

    cli_env = os.environ.copy()
    existing_pp = cli_env.get("PYTHONPATH", "")
    cli_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else str(SRC_DIR)
    cli_env["OM_DB_PATH"] = str(db_file)
    cli_env["PB_DB_PATH"] = str(db_file)
    cli_env["PB_GRAPH_DB_PATH"] = str(db_file)

    def _run_cli(*args: str) -> Dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "projectbrain.main", *args],
            cwd=str(ws),
            env=cli_env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)

    # 1. plugins list & reload
    plg_list = _run_cli("plugins", "list", "--category", "brain_consumer")
    assert plg_list["status"] == "ok"
    assert plg_list["count"] == 3

    ext_dir = ws / "cli_plugins"
    ext_dir.mkdir(parents=True, exist_ok=True)
    plg_reload = _run_cli("plugins", "reload", str(ext_dir))
    assert plg_reload["status"] == "ok"
    assert plg_reload["action"] == "reload"

    # 2. v3-etl
    ds_file = ws / "cli_datasheet.md"
    ds_file.write_text(
        "| Code | Hex | Description |\n"
        "| :--- | :--- | :--- |\n"
        "| AT+ERR=0x04 | 0x04 | Timeout |\n",
        encoding="utf-8",
    )
    etl_out = _run_cli(
        "v3-etl",
        "cli_proj",
        str(ds_file),
        "--branch",
        "main",
        "--version-tag",
        "fw-cli-1",
        "--db-path",
        str(db_file),
    )
    assert etl_out["status"] == "ok"
    assert etl_out["error_rules_count"] >= 1

    # 3. v3-cache store & lookup
    cmds_json = json.dumps([{"action": "tap", "target": "btn_cli"}])
    cache_store = _run_cli(
        "v3-cache",
        "store",
        "cli_proj",
        "--intent-id",
        "TC-CLI-1",
        "--platform",
        "android",
        "--build-version",
        "fw-cli-1",
        "--screen-hash",
        "cli_hash",
        "--commands",
        cmds_json,
        "--db-path",
        str(db_file),
    )
    assert cache_store["status"] == "ok"

    cache_lookup = _run_cli(
        "v3-cache",
        "lookup",
        "cli_proj",
        "--intent-id",
        "TC-CLI-1",
        "--platform",
        "android",
        "--build-version",
        "fw-cli-1",
        "--screen-hash",
        "cli_hash",
        "--db-path",
        str(db_file),
    )
    assert cache_lookup["cache_hit"] is True

    # 4. v3-artifacts window & purge (seed via LocalSqliteArtifactStorePlugin on same db_file)
    from projectbrain.plugins.builtins.cache_and_artifact_plugins import LocalSqliteArtifactStorePlugin

    art_store = LocalSqliteArtifactStorePlugin(db_path=str(db_file))
    try:
        art_store.index_artifact(
            {
                "project_id": "cli_proj",
                "run_id": "cli-run-1",
                "step_id": "s0",
                "artifact_type": "video_h264",
                "ntp_timestamp_ms": 1_700_010_000_000,
                "uri": "file://v.h264",
            }
        )
        art_store.index_artifact(
            {
                "project_id": "cli_proj",
                "run_id": "cli-run-1",
                "step_id": "s1",
                "artifact_type": "uart_log",
                "ntp_timestamp_ms": 1_700_010_001_500,
                "uri": "file://u.log",
            }
        )
    finally:
        art_store.shutdown()

    win_out = _run_cli(
        "v3-artifacts",
        "window",
        "--run-id",
        "cli-run-1",
        "--center-ts",
        "1700010001500",
        "--window-ms",
        "500",
        "--db-path",
        str(db_file),
    )
    assert len(win_out["timeline"]) == 1
    assert win_out["timeline"][0]["video_offset_ms"] == 1500

    purge_out = _run_cli("v3-artifacts", "purge", "--db-path", str(db_file))
    assert "deleted_count" in purge_out

    # 5. v3-plan
    plan_out = _run_cli(
        "v3-plan",
        "cli_proj",
        "--branch",
        "main",
        "--version-tag",
        "fw-cli-1",
        "--files",
        "src/fw/ota.c",
        "--artifacts",
        "fw.bin",
        "--db-path",
        str(db_file),
    )
    assert plan_out["status"] == "ok"
    assert plan_out["acyclic"] is True
    assert any(n["id"] == "precond_flash_firmware" for n in plan_out["dag"]["nodes"])

    # 6. v3-oracle
    oracle_out = _run_cli(
        "v3-oracle",
        "cli_proj",
        "--version-tag",
        "fw-cli-1",
        "--uart-log",
        "AT+ERR=0x04 BLE_TIMEOUT",
        "--db-path",
        str(db_file),
    )
    assert oracle_out["verdict"] == "FAIL"
    assert any(r["code"] == "AT+ERR=0x04" for r in oracle_out["violated_rules"])

    # 7. v3-rca
    rca_out = _run_cli(
        "v3-rca",
        "cli_proj",
        "HardFault_Handler CFSR=0x00008200 PC=0x08001234 ref CLI-55",
        "--run-id",
        "cli-run-1",
        "--db-path",
        str(db_file),
    )
    assert rca_out["status"] == "ok"
    assert rca_out["fault_domain"] == "Firmware"
    assert rca_out["ticket_refs"] == ["CLI-55"]


# ============================================================================
# 5. get_graph_store & UnifiedKBBuilder Delegation to PluginRegistry
# ============================================================================


def test_get_graph_store_delegates_to_neo4j_memory_plugin() -> None:
    reg = PluginRegistry()
    reg.discover_builtins()
    reg.configure("graph.neo4j", {"mode": "memory"}, project_id="neo_proj")

    store = get_graph_store(backend="neo4j", project_id="neo_proj", registry=reg)
    try:
        assert isinstance(store, Neo4jGraphStore)
        assert store.mode == "memory"

        store.ensure_root_schema()
        delta = GraphDelta(
            project_id="neo_proj",
            branch="main",
            nodes=[
                GraphNode(
                    id="flow:Boot",
                    project_id="neo_proj",
                    label="Boot",
                    file_type="state",
                    source_file="fw.md",
                    metadata={"version_tag": "fw-v1.2"},
                ),
                GraphNode(
                    id="flow:Ready",
                    project_id="neo_proj",
                    label="Ready",
                    file_type="state",
                    source_file="fw.md",
                    metadata={"version_tag": "fw-v1.2"},
                ),
            ],
            edges=[
                GraphEdge(
                    project_id="neo_proj",
                    source="flow:Boot",
                    target="flow:Ready",
                    relation="transitions_to",
                    source_file="fw.md",
                    metadata={"version_tag": "fw-v1.2", "expected_signals": ["BOOT_OK"]},
                )
            ],
        )
        sync_res = store.sync_branch_delta("neo_proj", "main", delta)
        assert sync_res["nodes_upserted"] == 2
        assert sync_res["edges_upserted"] == 1
        flow = store.query_flow_graph("neo_proj", branch="main", version_tag="fw-v1.2")
        assert len(flow["nodes"]) == 2
        assert len(flow["edges"]) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unified_kb_builder_delegates_to_plugin_registry(
    isolated_v3_env: Dict[str, Any],
) -> None:
    ws: Path = isolated_v3_env["workspace"]
    db_file: Path = isolated_v3_env["db_file"]

    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "app.py").write_text(
        "def run_boot():\n    return 'BOOT_OK'\n",
        encoding="utf-8",
    )
    (ws / "docs").mkdir(parents=True, exist_ok=True)
    (ws / "docs" / "boot.md").write_text(
        "# Boot Guide\n\nCalls `run_boot` on startup.\n",
        encoding="utf-8",
    )

    calls: List[str] = []

    class _SpyASTParser(ETLParserPlugin):
        metadata = PluginMetadata(
            plugin_id="etl.spy_ast",
            name="Spy AST Parser",
            category=PluginCategory.ETL_PARSER.value,
            priority=500,
        )

        def can_parse(
            self, source_name: str, format_hint: Optional[str] = None, content_preview: Optional[str] = None
        ) -> bool:
            return format_hint == "workspace"

        async def parse(
            self,
            source: Any,
            source_name: str = "",
            project_id: str = "default",
            branch: str = "main",
            version_tag: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            calls.append("etl.spy_ast")
            return {
                "plugin_id": self.metadata.plugin_id,
                "graph_delta": {
                    "project_id": project_id,
                    "branch": branch,
                    "nodes": [
                        {
                            "id": "code:src/app.py:run_boot",
                            "project_id": project_id,
                            "label": "run_boot",
                            "file_type": "code",
                            "source_file": "src/app.py",
                            "metadata": {"symbol_type": "function"},
                        }
                    ],
                    "edges": [],
                },
            }

    class _SpyWikiExporter(DocExporterPlugin):
        metadata = PluginMetadata(
            plugin_id="exporter.spy_wiki",
            name="Spy Wiki Exporter",
            category=PluginCategory.DOC_EXPORTER.value,
            priority=500,
        )
        export_format = "wiki"

        def export(
            self, graph_data: Any, output_path: str, context: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            calls.append("exporter.spy_wiki")
            out = Path(output_path)
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.md").write_text("# Spy Wiki\n", encoding="utf-8")
            return {"status": "ok", "output_path": str(out), "files": [str(out / "index.md")]}

    class _SpyFlowLinker(KnowledgeLinkerPlugin):
        metadata = PluginMetadata(
            plugin_id="linker.spy_flow",
            name="Spy Linker",
            category=PluginCategory.KNOWLEDGE_LINKER.value,
            priority=500,
        )

        def link(
            self,
            project_id: str,
            branch: str = "main",
            graph_data: Any = None,
            chunks: Optional[List[Any]] = None,
            version_tag: Optional[str] = None,
            context: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            calls.append("linker.spy_flow")
            return {"graph_delta": {"nodes": [], "edges": []}}

    reg = PluginRegistry()
    reg.discover_builtins()
    reg.register(_SpyASTParser(), override=True)
    reg.register(_SpyWikiExporter(), override=True)
    reg.register(_SpyFlowLinker(), override=True)

    out_dir = ws / "kb_out"
    builder = UnifiedKBBuilder(workspace_path=ws, registry=reg)
    result = await builder.build_unified_kb(
        project_id="kb_delegate_proj",
        branch="main",
        persist_memory=False,
        output_dir=str(out_dir),
        generate_office_exports=False,
        db_path=str(db_file),
    )

    assert "etl.spy_ast" in calls
    assert "exporter.spy_wiki" in calls
    assert "linker.spy_flow" in calls
    assert (out_dir / "wiki" / "index.md").is_file()
    assert result["project_id"] == "kb_delegate_proj"
    assert result["total_chunks"] >= 1
