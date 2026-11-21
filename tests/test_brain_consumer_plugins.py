"""Unit tests for Layer 2 Brain Consumer & Layer 0 Webhook built-in plugins (TASK-14D)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional

import pytest

from projectbrain.plugins.builtins.brain_consumer_plugins import (
    ImpactTestDAGPlannerPlugin,
    OracleKnowledgeJudgePlugin,
    RCATriageMemoryPlugin,
    WebhookEventIngestPlugin,
    get_plugins,
    register_plugins,
)


class _FakeScriptCache:
    """In-memory ScriptCachePlugin stub for deterministic unit testing."""

    def __init__(self) -> None:
        self.scripts: Dict[str, Dict[str, Any]] = {}
        self.executions: List[Dict[str, Any]] = []
        self.invalidated: Dict[str, str] = {}

    def seed(
        self,
        *,
        cache_id: str,
        project_id: str,
        intent_id: str,
        platform: str,
        build_version: str,
        udcp_commands: List[Dict[str, Any]],
    ) -> None:
        key = f"{project_id}:{intent_id}:{platform}:{build_version}"
        self.scripts[key] = {
            "cache_id": cache_id,
            "project_id": project_id,
            "intent_id": intent_id,
            "platform": platform,
            "build_version": build_version,
            "udcp_commands": udcp_commands,
            "invalidated": False,
        }

    def lookup_script(
        self,
        project_id: str,
        intent_id: str,
        platform: str,
        build_version: str,
        screen_hash: Optional[str] = None,
        include_invalidated: bool = False,
    ) -> Optional[Dict[str, Any]]:
        key = f"{project_id}:{intent_id}:{platform}:{build_version}"
        entry = self.scripts.get(key)
        if not entry:
            return None
        if entry.get("invalidated") and not include_invalidated:
            return None
        return dict(entry)

    def record_execution(
        self,
        cache_id: str,
        success: bool,
        fallback_reason: Optional[str] = None,
        self_heal_patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        rec = {
            "cache_id": cache_id,
            "success": success,
            "fallback_reason": fallback_reason,
            "self_heal_patch": self_heal_patch,
        }
        self.executions.append(rec)
        return rec

    def invalidate_script(self, cache_id: str, reason: str) -> bool:
        self.invalidated[cache_id] = reason
        for entry in self.scripts.values():
            if entry["cache_id"] == cache_id:
                entry["invalidated"] = True
                entry["invalidation_reason"] = reason
        return True


class _FakeGraphStore:
    def __init__(self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
        self._nodes = nodes
        self._edges = edges

    def get_resolved_subgraph(
        self, project_id: str, branch_name: str, limit: int = 1000
    ) -> Dict[str, Any]:
        return {"nodes": list(self._nodes), "edges": list(self._edges)}


class _FakeGraphBackend:
    def __init__(self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
        self._store = _FakeGraphStore(nodes, edges)

    def create_store(self, config: Optional[Dict[str, Any]] = None) -> Any:
        return self._store

    def query_flow_graph(
        self,
        project_id: str,
        branch: str = "main",
        version_tag: Optional[str] = None,
        store: Optional[Any] = None,
    ) -> Dict[str, Any]:
        flow_nodes = [
            n for n in self._store._nodes if n.get("file_type") in ("state", "screen")
        ]
        flow_edges = [
            e for e in self._store._edges if e.get("relation") == "transitions_to"
        ]
        return {"nodes": flow_nodes, "edges": flow_edges}


class _FakeRegistry:
    def __init__(self) -> None:
        self.plugins: Dict[str, Any] = {}
        self.by_category: Dict[str, Any] = {}

    def register(self, plugin: Any) -> None:
        self.plugins[plugin.metadata.plugin_id] = plugin
        self.by_category[plugin.metadata.category] = plugin

    def bind_category(self, category: str, plugin: Any) -> None:
        self.by_category[category] = plugin

    def resolve(
        self,
        category: str,
        preferred_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[Any]:
        if preferred_id and preferred_id in self.plugins:
            return self.plugins[preferred_id]
        return self.by_category.get(category)


# ---------------------------------------------------------------------------
# Registration & Discovery
# ---------------------------------------------------------------------------


def test_get_and_register_plugins():
    plugins = get_plugins()
    ids = [p.metadata.plugin_id for p in plugins]
    assert ids == [
        "brain.planner",
        "brain.oracle",
        "brain.rca",
        "webhook.event_ingest",
    ]

    reg = _FakeRegistry()
    registered = register_plugins(reg)
    assert registered == ids
    assert len(reg.plugins) == 4


# ---------------------------------------------------------------------------
# 1. ImpactTestDAGPlannerPlugin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_blast_radius_preconditions_cache_and_toposort():
    graph_nodes = [
        {
            "id": "src/fw/ble_pair.c",
            "label": "ble_pair_init",
            "source_file": "src/fw/ble_pair.c",
            "file_type": "code",
            "metadata": {"symbol": "ble_pair_init", "is_core_hub": True},
        },
        {
            "id": "state_ble_pairing",
            "label": "BLE_PAIRING",
            "file_type": "state",
            "metadata": {"state_id": "BLE_PAIRING"},
        },
        {
            "id": "REQ-BLE-01",
            "label": "Device pairs over BLE within 5s",
            "file_type": "srs",
            "metadata": {"srs_ref": "REQ-BLE-01"},
        },
        {
            "id": "TC-UNRELATED-UI",
            "label": "About page copyright text",
            "file_type": "test_intent",
            "metadata": {
                "intent_id": "TC-UNRELATED-UI",
                "priority": "P2",
                "target_platforms": ["android"],
            },
        },
    ]
    graph_edges = [
        {"source": "src/fw/ble_pair.c", "target": "state_ble_pairing", "relation": "implements"},
        {"source": "state_ble_pairing", "target": "REQ-BLE-01", "relation": "verifies"},
    ]

    intents = [
        {
            "intent_id": "TC-BOOT-P0",
            "title": "Verify core boot sequence",
            "priority": "P0",
            "target_platforms": ["firmware"],
            "preconditions": [{"action": "power_cycle"}],
        },
        {
            "intent_id": "TC-BLE-PAIR",
            "title": "Verify BLE pairing flow",
            "priority": "P1",
            "target_platforms": ["android"],
            "linked_requirements": ["REQ-BLE-01"],
            "preconditions": [{"action": "pair_ble"}, {"action": "seed_db"}],
            "depends_on": ["TC-BOOT-P0"],
        },
    ]

    cache = _FakeScriptCache()
    cache.seed(
        cache_id="sc_ble_01",
        project_id="iot_proj",
        intent_id="TC-BLE-PAIR",
        platform="android",
        build_version="fw-v2.0.0",
        udcp_commands=[{"action": "tap", "target": "btn_pair"}],
    )

    registry = _FakeRegistry()
    registry.bind_category("graph_backend", _FakeGraphBackend(graph_nodes, graph_edges))
    registry.bind_category("script_cache", cache)

    planner = ImpactTestDAGPlannerPlugin()
    res = await planner.execute(
        request={
            "project_id": "iot_proj",
            "branch": "feat/ble-fix",
            "version_tag": "fw-v2.0.0",
            "srs_diff": "Updated BLE pairing timeout",
            "code_diff_files": ["src/fw/ble_pair.c"],
            "artifacts": ["build/firmware.bin", "build/mobile.apk"],
            "intents": intents,
            "max_depth": 4,
        },
        registry=registry,
    )

    assert res["status"] == "ok"
    assert res["acyclic"] is True
    assert res["blast_radius"]["core_hub_touched"] is True

    dag_nodes = {n["id"]: n for n in res["dag"]["nodes"]}
    # Preconditions injected automatically
    assert "precond_flash_firmware" in dag_nodes
    assert "precond_install_app" in dag_nodes
    assert "precond_power_cycle" in dag_nodes
    assert "precond_pair_ble" in dag_nodes
    assert "precond_seed_db" in dag_nodes

    # Impacted P1 intent + core P0 regression retained; unrelated P2 excluded
    assert "TC-BOOT-P0" in dag_nodes
    assert "TC-BLE-PAIR" in dag_nodes
    assert "TC-UNRELATED-UI" not in dag_nodes

    # Script cache binding & ratio (1 hit out of 2 selected intents)
    assert dag_nodes["TC-BLE-PAIR"]["cache_binding"]["cache_hit"] is True
    assert dag_nodes["TC-BLE-PAIR"]["cache_binding"]["cache_id"] == "sc_ble_01"
    assert dag_nodes["TC-BOOT-P0"]["cache_binding"]["cache_hit"] is False
    assert res["stats"]["cache_hits"] == 1
    assert res["stats"]["cache_hit_ratio"] == 0.5

    # Topological waves: preconditions in wave 0, TC-BOOT-P0 before TC-BLE-PAIR
    waves = res["dag"]["execution_waves"]
    wave_index = {nid: idx for idx, w in enumerate(waves) for nid in w}
    assert wave_index["precond_flash_firmware"] == 0
    assert wave_index["precond_install_app"] == 0
    assert wave_index["precond_power_cycle"] < wave_index["TC-BOOT-P0"]
    assert wave_index["TC-BOOT-P0"] < wave_index["TC-BLE-PAIR"]


@pytest.mark.asyncio
async def test_planner_detects_cyclic_intent_dependencies():
    planner = ImpactTestDAGPlannerPlugin()
    res = await planner.execute(
        request={
            "project_id": "p1",
            "code_diff_files": ["main.c"],
            "intents": [
                {"intent_id": "TC-A", "priority": "P0", "depends_on": ["TC-B"]},
                {"intent_id": "TC-B", "priority": "P0", "depends_on": ["TC-A"]},
            ],
        }
    )
    assert res["acyclic"] is False
    assert res["status"] == "error"


# ---------------------------------------------------------------------------
# 2. OracleKnowledgeJudgePlugin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oracle_pass_when_criteria_transitions_and_power_ok():
    oracle = OracleKnowledgeJudgePlugin()
    res = await oracle.execute(
        request={
            "project_id": "iot_proj",
            "test_intent_id": "TC-BLE-PAIR",
            "srs_criteria": [
                {"criterion_id": "SRS-1", "expected_signal": "PAIRED_EVENT"},
                {"criterion_id": "SRS-2", "expected_state": "CONNECTED"},
            ],
            "expected_transitions": [
                {
                    "source_state": "PAIRING",
                    "target_state": "CONNECTED",
                    "expected_signals": ["BOOT_OK", "PAIRED_EVENT"],
                }
            ],
            "power_budget_ma": 150.0,
            "telemetry_summary": {
                "uart_lines": ["[00:01] BOOT_OK", "[00:02] PAIRED_EVENT"],
                "http_responses": [{"status": 200, "path": "/api/device/pair"}],
                "power_ma_peak": 118.5,
                "observed_state": "CONNECTED",
                "observed_signals": ["BOOT_OK", "PAIRED_EVENT"],
                "crash_detected": False,
                "hardfault_cfsr": "0x00000000",
            },
        }
    )
    assert res["verdict"] == "PASS"
    assert res["violated_rules"] == []
    assert len(res["matched_criteria"]) >= 3
    assert res["confidence"] >= 0.85


@pytest.mark.asyncio
async def test_oracle_fail_on_uart_error_hardfault_power_and_http_5xx():
    oracle = OracleKnowledgeJudgePlugin()
    res = await oracle.execute(
        request={
            "project_id": "iot_proj",
            "error_rules": [
                {
                    "rule_id": "R-AT-04",
                    "code": "AT+ERR=0x04",
                    "protocol": "at",
                    "severity": "error",
                    "pattern": r"AT\+ERR=0x04",
                }
            ],
            "power_budget_ma": 100.0,
            "telemetry_summary": {
                "uart_lines": [
                    "AT+CONN=1",
                    "AT+ERR=0x04 BLE_TIMEOUT",
                    "HardFault_Handler triggered",
                ],
                "http_responses": [
                    {"status": 500, "path": "/api/v1/telemetry"},
                    {"status": 404, "path": "/api/v1/missing"},
                ],
                "power_ma_peak": 142.0,
                "crash_detected": True,
                "hardfault_cfsr": "0x00008200",
            },
        }
    )
    assert res["verdict"] == "FAIL"
    codes = {v["code"] for v in res["violated_rules"]}
    assert "AT+ERR=0x04" in codes
    assert "CRASH_DETECTED" in codes
    assert "ARM_HARDFAULT_CFSR" in codes
    assert "POWER_BUDGET_EXCEEDED" in codes
    assert "HTTP_500" in codes
    assert "HTTP_404" in codes


@pytest.mark.asyncio
async def test_oracle_inconclusive_on_empty_telemetry():
    oracle = OracleKnowledgeJudgePlugin()
    res = await oracle.execute(
        request={
            "project_id": "iot_proj",
            "expected_transitions": [
                {"source_state": "IDLE", "target_state": "ACTIVE", "expected_signals": ["ACK"]}
            ],
            "telemetry_summary": {},
        }
    )
    assert res["verdict"] == "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# 3. RCATriageMemoryPlugin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rca_deterministic_hashing_dedup_ticket_linking(tmp_path):
    db_path = str(tmp_path / "rca.db")
    rca = RCATriageMemoryPlugin(config={"db_path": db_path})

    log_run_1 = (
        "2026-11-21T10:00:01.123Z [pid:1042] HardFault_Handler CFSR=0x00008200 "
        "R0=0x20001100 PC=0x08004422 at ble_write_char+0x14 (0x2000a000) ref FW-402"
    )
    log_run_2 = (
        "2026-11-21T14:55:09.999Z [pid:9981] HardFault_Handler CFSR=0x00008200 "
        "R0=0x2000ff80 PC=0x080099aa at ble_write_char+0x28 (0x2000bbcc) ref FW-402"
    )

    r1 = await rca.execute(
        {
            "project_id": "iot_proj",
            "run_id": "run-001",
            "stacktrace_or_log": log_run_1,
            "failure_artifacts": ["gdb_hardfault"],
        }
    )
    r2 = await rca.execute(
        {
            "project_id": "iot_proj",
            "run_id": "run-002",
            "stacktrace_or_log": log_run_2,
            "failure_artifacts": ["gdb_hardfault"],
            "ticket_refs": ["LIN-77"],
        }
    )

    assert r1["signature_hash"] == r2["signature_hash"]
    assert "CFSR=0x00008200" in r1["normalized_signature"]
    assert "0x2000a000" not in r1["normalized_signature"]
    assert r1["fault_domain"] == "Firmware"
    assert r1["is_duplicate"] is False
    assert r1["occurrence_count"] == 1

    assert r2["incident_id"] == r1["incident_id"]
    assert r2["is_duplicate"] is True
    assert r2["occurrence_count"] == 2
    assert r2["ticket_refs"] == ["FW-402", "LIN-77"]


@pytest.mark.asyncio
async def test_rca_fault_domain_classification_and_flaky_cache_invalidation(tmp_path):
    db_path = str(tmp_path / "rca_domains.db")
    rca = RCATriageMemoryPlugin(config={"db_path": db_path})

    cache = _FakeScriptCache()
    cache.seed(
        cache_id="sc_login_01",
        project_id="app_proj",
        intent_id="TC-LOGIN",
        platform="android",
        build_version="v3.1.0",
        udcp_commands=[{"action": "tap", "target": "old_btn_id"}],
    )
    reg = _FakeRegistry()
    reg.bind_category("script_cache", cache)

    # Hardware/Power
    hw = await rca.execute(
        {
            "project_id": "app_proj",
            "run_id": "r-hw",
            "stacktrace_or_log": "PMIC brownout undervoltage BOD_RESET at 210mA",
        }
    )
    assert hw["fault_domain"] == "Hardware/Power"

    # App
    app = await rca.execute(
        {
            "project_id": "app_proj",
            "run_id": "r-app",
            "stacktrace_or_log": "java.lang.NullPointerException in MainActivity.onCreate",
            "failure_artifacts": ["crashpad_dmp"],
        }
    )
    assert app["fault_domain"] == "App"

    # Backend
    be = await rca.execute(
        {
            "project_id": "app_proj",
            "run_id": "r-be",
            "stacktrace_or_log": "HTTP 500 Internal Server Error in FastAPI /v1/checkout",
        }
    )
    assert be["fault_domain"] == "Backend"

    # FlakyScript -> triggers ScriptCachePlugin invalidation
    flaky = await rca.execute(
        {
            "project_id": "app_proj",
            "run_id": "r-flaky",
            "stacktrace_or_log": "UDCP locator timeout: Element not found for button#submit",
            "screen_hash": "hash_v2_changed",
            "cache_id": "sc_login_01",
        },
        registry=reg,
    )
    assert flaky["fault_domain"] == "FlakyScript"
    assert flaky["cache_feedback"]["applied"] is True
    assert flaky["cache_feedback"]["invalidated"] is True
    assert "sc_login_01" in cache.invalidated
    assert cache.lookup_script("app_proj", "TC-LOGIN", "android", "v3.1.0") is None


# ---------------------------------------------------------------------------
# 4. WebhookEventIngestPlugin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_signature_verification_and_multi_source_normalization():
    wh = WebhookEventIngestPlugin(config={"webhook_secret": "super-secret"})
    body = b'{"ref":"refs/heads/main"}'
    sig = "sha256=" + hmac.new(b"super-secret", body, hashlib.sha256).hexdigest()

    # Valid & invalid HMAC-SHA256
    assert wh.verify_signature({"X-Hub-Signature-256": sig}, body) is True
    assert wh.verify_signature({"X-PB-Signature": sig}, body) is True
    assert wh.verify_signature({"X-Hub-Signature-256": "sha256=deadbeef"}, body) is False

    # GitLab token
    assert wh.verify_signature({"X-Gitlab-Token": "super-secret"}, body) is True
    assert wh.verify_signature({"X-Gitlab-Token": "wrong"}, body) is False

    # Unsigned fail-closed
    assert wh.verify_signature({}, body) is False

    # GitLab MR normalization
    gl_event = await wh.parse_event(
        {"X-Gitlab-Event": "Merge Request Hook"},
        {
            "object_kind": "merge_request",
            "project": {"path_with_namespace": "acme/iot-fw"},
            "object_attributes": {
                "iid": 42,
                "source_branch": "feat/ota-v2",
                "target_branch": "main",
                "title": "PROJ-101 Enable delta OTA",
                "description": "Adds flash verification",
            },
            "version_tag": "fw-v2.1.0",
            "changes": [{"new_path": "src/ota.c"}, {"new_path": "dist/firmware.bin"}],
            "builds": [{"artifacts_file": {"filename": "dist/firmware.elf"}}],
        },
    )
    assert gl_event["source"] == "gitlab"
    assert gl_event["branch"] == "feat/ota-v2"
    assert gl_event["base_branch"] == "main"
    assert gl_event["version_tag"] == "fw-v2.1.0"
    assert "src/ota.c" in gl_event["changed_files"]
    assert {a["name"] for a in gl_event["artifacts"]} == {
        "dist/firmware.elf",
        "dist/firmware.bin",
    }
    assert gl_event["ticket_refs"] == ["PROJ-101"]

    # GitHub PR/Release normalization
    gh_event = await wh.parse_event(
        {"X-GitHub-Event": "pull_request"},
        {
            "repository": {"full_name": "acme/mobile-app"},
            "pull_request": {
                "number": 9,
                "title": "Release app-v3.0.1 LIN-88",
                "head": {"ref": "release/3.0.1"},
                "base": {"ref": "main"},
                "files": [{"filename": "app/Main.kt"}],
            },
            "release": {
                "tag_name": "app-v3.0.1",
                "assets": [{"name": "app-release.apk"}, {"name": "app-release.ipa"}],
            },
        },
    )
    assert gh_event["source"] == "github"
    assert gh_event["version_tag"] == "app-v3.0.1"
    assert {a["extension"] for a in gh_event["artifacts"]} == {".apk", ".ipa"}
    assert gh_event["ticket_refs"] == ["LIN-88"]

    # TestRail normalization
    tr_event = await wh.parse_event(
        {"X-TestRail-Event": "case.updated"},
        {
            "project_id": "qa_proj",
            "suite_id": 7,
            "version_tag": "v3.0",
            "cases": [{"case_id": 1001, "title": "Verify BLE reconnect QA-12"}],
        },
    )
    assert tr_event["source"] == "testrail"
    assert "QA-12" in tr_event["ticket_refs"]

    # Jira normalization
    jira_event = await wh.parse_event(
        {},
        {
            "webhookEvent": "jira:issue_updated",
            "issue": {
                "key": "IOT-55",
                "fields": {
                    "project": {"key": "IOT"},
                    "summary": "Update power budget to 120mA",
                    "description": "See spec section 4.2",
                    "fixVersions": [{"name": "fw-v2.2.0"}],
                },
            },
        },
    )
    assert jira_event["source"] == "jira"
    assert jira_event["project_id"] == "IOT"
    assert jira_event["version_tag"] == "fw-v2.2.0"
    assert "IOT-55" in jira_event["ticket_refs"]

    # Notion normalization
    notion_event = await wh.parse_event(
        {"User-Agent": "NotionWebhook/1.0"},
        {
            "type": "page.content_updated",
            "workspace_id": "ws-1",
            "page": {"id": "pg-99", "title": "SRS v3 Acceptance Criteria"},
            "content_diff": "Added REQ-99 for OTA rollback",
        },
    )
    assert notion_event["source"] == "notion"
    assert notion_event["event_type"] == "page.content_updated"
    assert "REQ-99" in notion_event["srs_diff"]
