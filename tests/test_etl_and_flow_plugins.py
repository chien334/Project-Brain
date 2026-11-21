"""Tests for built-in ETL, Knowledge Linker, and Document Exporter plugins (TASK-14B)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pytest

from projectbrain.plugins.builtins.etl_and_flow_plugins import (
    CodeDocLinkerPlugin,
    CppEmbeddedETLPlugin,
    DocxExporterPlugin,
    GraphifyASTETLPlugin,
    HardwareDatasheetETLPlugin,
    LegacyTestcaseETLPlugin,
    MarkdownWikiExporterPlugin,
    MultimodalDocETLPlugin,
    OpenAPI31ETLPlugin,
    PptxExporterPlugin,
    StateFlowGraphPlugin,
    XlsxExporterPlugin,
    get_plugins,
    register_plugins,
)


class _DummyRegistry:
    def __init__(self) -> None:
        self.plugins: Dict[str, Any] = {}

    def register(self, plugin: Any) -> None:
        self.plugins[plugin.metadata.plugin_id] = plugin


# ---------------------------------------------------------------------------
# 1. Discovery & Registration Tests
# ---------------------------------------------------------------------------
def test_get_and_register_all_12_plugins() -> None:
    plugins = get_plugins()
    assert len(plugins) == 12
    ids = {p.metadata.plugin_id for p in plugins}
    assert ids == {
        "etl.graphify_ast",
        "etl.multimodal_doc",
        "linker.code_doc",
        "exporter.wiki",
        "exporter.docx",
        "exporter.xlsx",
        "exporter.pptx",
        "etl.legacy_testcase",
        "etl.hardware_datasheet",
        "etl.openapi31",
        "etl.cpp_embedded",
        "linker.state_flow",
    }

    reg = _DummyRegistry()
    registered_ids = register_plugins(reg)
    assert len(registered_ids) == 12
    assert set(reg.plugins.keys()) == ids


# ---------------------------------------------------------------------------
# 2. Wrapper Plugins (7 built-in wrappers)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_graphify_ast_and_multimodal_doc_wrappers(tmp_path: Path) -> None:
    py_file = tmp_path / "sample_mod.py"
    py_file.write_text(
        "def authenticate_device(device_id: str) -> bool:\n"
        "    return bool(device_id)\n",
        encoding="utf-8",
    )
    ast_plugin = GraphifyASTETLPlugin(config={"cache_dir": str(tmp_path / ".cache")})
    assert ast_plugin.can_parse(str(py_file))
    ast_res = await ast_plugin.parse(str(py_file), project_id="proj-1")
    node_labels = [n["label"] for n in ast_res["graph_delta"]["nodes"]]
    assert any("authenticate_device" in lbl for lbl in node_labels)

    md_file = tmp_path / "architecture.md"
    md_file.write_text(
        "# Auth Flow\n\nCalls `authenticate_device` before pairing.\n",
        encoding="utf-8",
    )
    doc_plugin = MultimodalDocETLPlugin(config={"cache_dir": str(tmp_path / ".cache")})
    assert doc_plugin.can_parse(str(md_file))
    doc_res = await doc_plugin.parse(str(md_file), project_id="proj-1")
    assert doc_res["plugin_id"] == "etl.multimodal_doc"
    assert len(doc_res["chunks"]) >= 1
    assert len(doc_res["graph_delta"]["nodes"]) >= 1


def test_code_doc_linker_and_exporters_wrappers(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "sample_mod.py").write_text(
        "def authenticate_device(device_id: str) -> bool:\n"
        "    return bool(device_id)\n",
        encoding="utf-8",
    )
    graph_data = {
        "project_id": "proj-1",
        "branch": "main",
        "nodes": [
            {
                "id": "code:pkg/sample_mod.py:authenticate_device",
                "project_id": "proj-1",
                "label": "authenticate_device",
                "file_type": "code",
                "source_file": "pkg/sample_mod.py",
                "metadata": {"symbol_type": "function"},
            }
        ],
        "edges": [],
    }

    linker = CodeDocLinkerPlugin(config={"workspace_path": str(tmp_path)})
    link_res = linker.link(
        project_id="proj-1",
        graph_data=graph_data,
        chunks=[{"doc_path": "docs/auth.md", "content": "# Auth\n\nUse `authenticate_device()` to login."}],
        context={"workspace_path": str(tmp_path)},
    )
    assert link_res["symbol_count"] >= 1
    assert len(link_res["edges"]) >= 1

    wiki_dir = tmp_path / "wiki"
    wiki_res = MarkdownWikiExporterPlugin().export(graph_data, str(wiki_dir), context={"workspace_path": str(tmp_path)})
    assert wiki_res["page_count"] >= 4
    assert (wiki_dir / "index.md").is_file()

    docx_path = tmp_path / "exports" / "spec.docx"
    docx_res = DocxExporterPlugin().export(graph_data, str(docx_path), context={"workspace_path": str(tmp_path)})
    assert docx_res["format"] == "docx"
    assert docx_path.is_file()

    xlsx_path = tmp_path / "exports" / "trace.xlsx"
    xlsx_res = XlsxExporterPlugin().export(graph_data, str(xlsx_path), context={"workspace_path": str(tmp_path)})
    assert xlsx_res["format"] == "xlsx"
    assert xlsx_path.is_file()

    pptx_path = tmp_path / "exports" / "deck.pptx"
    pptx_res = PptxExporterPlugin().export(graph_data, str(pptx_path), context={"workspace_path": str(tmp_path)})
    assert pptx_res["format"] == "pptx"
    assert pptx_path.is_file()


# ---------------------------------------------------------------------------
# 3. LegacyTestcaseETLPlugin (CSV, .xlsx, TestRail JSON, TestRail XML)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legacy_testcase_csv_and_xlsx(tmp_path: Path) -> None:
    plugin = LegacyTestcaseETLPlugin()
    csv_path = tmp_path / "legacy_cases.csv"
    csv_path.write_text(
        "TC_ID,Title,Preconditions,Steps,Expected Result,Priority,Platform,SRS_Ref\n"
        "TC-101,BLE Boot & Pairing,Flash firmware v1.2 and power cycle device; pair BLE,1. Send AT+PAIR; 2. Wait event,UART BOOT_OK and PAIRED_EVENT within 5000ms; max current <= 45mA,Critical,firmware;android,REQ-BLE-01\n"
        "TC-102,API Login Check,Seed db and login session,1. POST /auth/login,HTTP 200 status code,Medium,backend,REQ-AUTH-02\n",
        encoding="utf-8",
    )
    assert plugin.can_parse(str(csv_path))
    res = await plugin.parse(str(csv_path), project_id="proj-iot", version_tag="fw-v1.2.0")
    intents = res["canonical_intents"]
    assert len(intents) == 2

    tc1 = intents[0]
    assert tc1["intent_id"] == "TC-101"
    assert tc1["priority"] == "P0"
    assert tc1["target_platforms"] == ["firmware", "android"]
    assert tc1["linked_requirements"] == ["REQ-BLE-01"]
    pre_types = {p["type"] for p in tc1["preconditions"]}
    assert {"flash_firmware", "power_cycle", "pair_ble"} <= pre_types
    assert len(tc1["steps"]) == 2
    assert any(a["assertion_type"] == "uart_pattern" for a in tc1["expected_oracle_assertions"])

    tc2 = intents[1]
    assert {p["type"] for p in tc2["preconditions"]} >= {"seed_db", "login_session"}
    assert any(a["assertion_type"] == "api_status" for a in tc2["expected_oracle_assertions"])

    edges = res["graph_delta"]["edges"]
    assert any(e["relation"] == "verifies_requirement" and e["target"] == "req:REQ-BLE-01" for e in edges)
    assert any(e["relation"] == "requires_precondition" and e["target"] == "precondition:flash_firmware" for e in edges)

    # Excel .xlsx
    xlsx_path = tmp_path / "suite.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PowerTests"
    ws.append(["case_id", "name", "setup", "actions", "oracle", "priority", "targets", "req_id"])
    ws.append(["TC-201", "Sleep Power Budget", "Power cycle MCU", "1. Measure idle current", "Power <= 12 mA", "P1", "firmware", "REQ-PWR-01"])
    wb.save(xlsx_path)

    xlsx_res = await plugin.parse(str(xlsx_path), project_id="proj-iot")
    assert len(xlsx_res["canonical_intents"]) == 1
    assert xlsx_res["canonical_intents"][0]["intent_id"] == "TC-201"
    assert xlsx_res["canonical_intents"][0]["expected_oracle_assertions"][0]["assertion_type"] == "power_budget_ma"


@pytest.mark.asyncio
async def test_legacy_testcase_testrail_json_and_xml(tmp_path: Path) -> None:
    plugin = LegacyTestcaseETLPlugin()

    tr_json = tmp_path / "testrail_suite.json"
    tr_json.write_text(
        json.dumps(
            {
                "suite": {
                    "sections": [
                        {
                            "name": "BLE Provisioning",
                            "cases": [
                                {
                                    "id": "C401",
                                    "title": "Pair peripheral over GATT",
                                    "custom_preconds": "Flash firmware and pair BLE",
                                    "custom_steps_separated": [
                                        {"content": "Send AT+BLE_ADV", "expected": "UART OK"},
                                    ],
                                    "priority_id": 1,
                                    "refs": "SRS-10",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert plugin.can_parse(str(tr_json), content_preview=tr_json.read_text(encoding="utf-8")[:200])
    j_res = await plugin.parse(str(tr_json))
    assert j_res["canonical_intents"][0]["intent_id"] == "C401"
    assert j_res["canonical_intents"][0]["linked_requirements"] == ["SRS-10"]
    assert j_res["canonical_intents"][0]["expected_oracle_assertions"][0]["assertion_type"] == "uart_pattern"

    tr_xml = tmp_path / "testrail_export.xml"
    tr_xml.write_text(
        """<suite>
          <sections>
            <section>
              <name>UI Sanity</name>
              <cases>
                <case>
                  <id>C502</id>
                  <title>Verify Pairing Dialog</title>
                  <priority>High</priority>
                  <custom>
                    <preconds>Login session active</preconds>
                    <steps_separated>
                      <step>
                        <content>Tap Pair Button</content>
                        <expected>Pairing screen visible</expected>
                      </step>
                    </steps_separated>
                  </custom>
                  <refs>SRS-UI-02</refs>
                </case>
              </cases>
            </section>
          </sections>
        </suite>""",
        encoding="utf-8",
    )
    assert plugin.can_parse(str(tr_xml))
    x_res = await plugin.parse(str(tr_xml))
    assert x_res["canonical_intents"][0]["intent_id"] == "C502"
    assert x_res["canonical_intents"][0]["priority"] == "P0"
    assert x_res["canonical_intents"][0]["expected_oracle_assertions"][0]["assertion_type"] == "ui_state"


# ---------------------------------------------------------------------------
# 4. HardwareDatasheetETLPlugin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hardware_datasheet_etl(tmp_path: Path) -> None:
    ds_path = tmp_path / "nrf52_datasheet.md"
    ds_path.write_text(
        "# Hardware Interface & Error Codes\n\n"
        "| Code | Hex | Description | Remediation |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| AT+ERR=0x04 | 0x04 | Invalid AT parameter length | Check payload bytes |\n"
        "| UART_ERR_CRC | 0x12 | Frame CRC mismatch on UART0 | Verify baudrate 115200 |\n"
        "| E_BLE_TIMEOUT | 0x20 | GATT connection supervision timeout | Increase timeout |\n\n"
        "## Cortex-M HardFault & CFSR Registers\n"
        "- HFSR bit `FORCED`: Escalated fault into HardFault.\n"
        "- CFSR BFSR bit `PRECISERR`: Precise data bus error at BFAR.\n"
        "- CFSR UFSR bit `DIVBYZERO`: Integer divide by zero.\n\n"
        "## Electrical & Timing Limits\n"
        "| Parameter | Value | Unit | Notes |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| max_current_ma | 120 mA | mA | Peak TX current |\n"
        "| boot_timeout_ms | 2500 ms | ms | Max cold boot |\n"
        "voltage_mv: 3300 mV\n",
        encoding="utf-8",
    )
    plugin = HardwareDatasheetETLPlugin()
    assert plugin.can_parse(str(ds_path))
    res = await plugin.parse(str(ds_path), project_id="proj-hw", version_tag="hw-rev-b")

    codes = {r["code"]: r for r in res["error_rules"]}
    assert "AT+ERR=0x04" in codes
    assert codes["AT+ERR=0x04"]["protocol"] == "at"
    assert "UART_ERR_CRC" in codes
    assert codes["UART_ERR_CRC"]["protocol"] == "uart"
    assert "E_BLE_TIMEOUT" in codes
    assert {"FORCED", "PRECISERR", "DIVBYZERO"} <= set(codes.keys())
    assert codes["PRECISERR"]["protocol"] == "hardfault"
    assert codes["PRECISERR"]["severity"] == "critical"

    limits = {lim["name"]: lim for lim in res["electrical_limits"]}
    assert limits["max_current_ma"]["value"] == 120
    assert limits["boot_timeout_ms"]["value"] == 2500
    assert limits["voltage_mv"]["value"] == 3300

    assert any(e["relation"] == "defines_error_rule" for e in res["graph_delta"]["edges"])


# ---------------------------------------------------------------------------
# 5. OpenAPI31ETLPlugin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi31_etl_yaml_and_validation(tmp_path: Path) -> None:
    spec_path = tmp_path / "device_api.yaml"
    spec_path.write_text(
        """openapi: "3.1.0"
info:
  title: Device Telemetry API
  version: "3.1.2"
paths:
  /v1/devices/{id}/telemetry:
    post:
      operationId: ingestTelemetry
      summary: Ingest device telemetry
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/TelemetryPayload"
      responses:
        "202":
          description: Accepted
        "400":
          description: Malformed telemetry payload
        "503":
          description: Downstream ClickHouse unavailable
components:
  schemas:
    TelemetryPayload:
      type: object
      required: [device_id, timestamp_ms]
      properties:
        device_id:
          type: string
        timestamp_ms:
          type: integer
""",
        encoding="utf-8",
    )
    plugin = OpenAPI31ETLPlugin()
    assert plugin.can_parse(str(spec_path))
    res = await plugin.parse(str(spec_path), project_id="proj-api")

    assert res["openapi_version"] == "3.1.0"
    assert len(res["endpoints"]) == 1
    assert res["endpoints"][0]["operation_id"] == "ingestTelemetry"
    assert res["schemas"][0]["name"] == "TelemetryPayload"

    rule_codes = {r["code"]: r for r in res["error_rules"]}
    assert "HTTP_400" in rule_codes
    assert rule_codes["HTTP_400"]["severity"] == "error"
    assert "HTTP_503" in rule_codes
    assert rule_codes["HTTP_503"]["severity"] == "critical"

    edges = res["graph_delta"]["edges"]
    assert any(e["relation"] == "specifies_endpoint" for e in edges)
    assert any(e["relation"] == "uses_schema" and e["target"] == "schema:TelemetryPayload" for e in edges)
    assert any(e["relation"] == "defines_error_rule" for e in edges)

    with pytest.raises(ValueError):
        await plugin.parse('{"swagger": "2.0"}', source_name="old.json")


# ---------------------------------------------------------------------------
# 6. CppEmbeddedETLPlugin (C/C++, Qt .ui, GNU .ld / .map)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cpp_embedded_source_qt_ui_and_linker(tmp_path: Path) -> None:
    plugin = CppEmbeddedETLPlugin()

    # C/C++ + Qt signals/slots + AT table + ISR
    cpp_file = tmp_path / "ble_controller.hpp"
    cpp_file.write_text(
        """#define ERR_BLE_DISCONNECTED -101
#define AT_CMD_PAIR "AT+PAIR"

enum class LinkState { IDLE, ADVERTISING, CONNECTED };

class BleController : public QObject {
    Q_OBJECT
public:
    void startPairing();
signals:
    void pairedSuccess();
public slots:
    void onTimeout();
};

void USART1_IRQHandler(void) { }
ISR(TIMER1_COMPA_vect) { }

static const at_entry_t g_at_table[] = {
    {"AT+PAIR", handle_at_pair}
};
void handle_at_pair(void) {}
""",
        encoding="utf-8",
    )
    cpp_res = await plugin.parse(str(cpp_file), project_id="proj-fw", version_tag="fw-v1.0")
    sym_names = {s["name"] for s in cpp_res["symbols"]}
    assert {"ERR_BLE_DISCONNECTED", "LinkState", "BleController", "BleController.pairedSuccess", "BleController.onTimeout", "USART1_IRQHandler", "TIMER1_COMPA_vect", "AT+PAIR"} <= sym_names
    assert len(cpp_res["error_rules"]) == 1
    assert cpp_res["error_rules"][0]["code"] == "ERR_BLE_DISCONNECTED"
    rels = {e["relation"] for e in cpp_res["graph_delta"]["edges"]}
    assert {"emits_signal", "connects_slot", "handles_at_command"} <= rels

    # Qt .ui XML
    ui_file = tmp_path / "main_window.ui"
    ui_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>MainWindow</class>
 <widget class="QMainWindow" name="MainWindow">
  <widget class="QWidget" name="centralWidget">
   <widget class="QPushButton" name="btnPair">
    <property name="text"><string>Pair Device</string></property>
   </widget>
  </widget>
 </widget>
 <connections>
  <connection>
   <sender>btnPair</sender>
   <signal>clicked()</signal>
   <receiver>MainWindow</receiver>
   <slot>startPairing()</slot>
  </connection>
 </connections>
</ui>""",
        encoding="utf-8",
    )
    ui_res = await plugin.parse(str(ui_file), project_id="proj-app")
    widget_names = {w["name"] for w in ui_res["qt_widgets"]}
    assert {"MainWindow", "centralWidget", "btnPair"} <= widget_names
    ui_rels = {e["relation"] for e in ui_res["graph_delta"]["edges"]}
    assert {"contains_widget", "emits_signal", "connects_slot"} <= ui_rels

    # GNU .ld linker script
    ld_file = tmp_path / "stm32f4.ld"
    ld_file.write_text(
        """MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 512K
  RAM   (xrw) : ORIGIN = 0x20000000, LENGTH = 128K
}
""",
        encoding="utf-8",
    )
    ld_res = await plugin.parse(str(ld_file), project_id="proj-fw")
    regions = {r["name"]: r for r in ld_res["memory_regions"]}
    assert regions["FLASH"]["size_bytes"] == 512 * 1024
    assert regions["RAM"]["origin"] == "0x20000000"

    # GNU .map linker map
    map_file = tmp_path / "firmware.map"
    map_file.write_text(
        "Memory Configuration\n"
        "FLASH 0x08000000 0x00080000 xr\n"
        "RAM 0x20000000 0x00020000 xrw\n"
        ".text 0x08000000 0x12400\n"
        ".bss 0x20000100 0x01800\n",
        encoding="utf-8",
    )
    map_res = await plugin.parse(str(map_file), project_id="proj-fw")
    assert any(r.get("name") == "FLASH" for r in map_res["memory_regions"])
    assert any(r.get("section") == ".text" and r.get("size_bytes") == 0x12400 for r in map_res["memory_regions"])


# ---------------------------------------------------------------------------
# 7. StateFlowGraphPlugin (STATE_NODE / SCREEN_NODE / TRANSITION_EDGE + Diff)
# ---------------------------------------------------------------------------
def test_state_flow_graph_linking_and_version_diff() -> None:
    plugin = StateFlowGraphPlugin()

    v1_doc = """
```mermaid
stateDiagram-v2
    state "Pairing Screen" as PairingScreen
    Booting --> BleAdvertising : BOOT_OK -> ADV_STARTED [battery_ok]
    BleAdvertising --> PairingScreen : USER_PAIR / PAIRED_EVENT timeout=3000ms
```

| Source | Target | Trigger | Guard | Expected Signals | Timeout |
| :--- | :--- | :--- | :--- | :--- | :--- |
| PairingScreen | DashboardScreen | CONFIRM_PIN | pin_valid | PIN_ACK | 2000 |
"""

    v2_doc = """
```mermaid
stateDiagram-v2
    state "Pairing Screen" as PairingScreen
    Booting --> BleAdvertising : BOOT_OK -> ADV_STARTED [battery_ok]
    BleAdvertising --> OtaCheckScreen : USER_PAIR / PAIRED_EVENT timeout=4000ms
    OtaCheckScreen --> PairingScreen : OTA_SKIP / READY
```
"""

    g1 = plugin.link(
        project_id="proj-iot",
        version_tag="fw-v1.2.0",
        context={"flow_sources": [v1_doc], "domain": "firmware"},
    )
    g2 = plugin.link(
        project_id="proj-iot",
        version_tag="fw-v1.3.0",
        context={"flow_sources": [v2_doc], "domain": "firmware"},
    )

    n1 = {n["node_id"]: n for n in g1["flow_nodes"]}
    assert n1["Booting"]["node_kind"] == "STATE_NODE"
    assert n1["PairingScreen"]["node_kind"] == "SCREEN_NODE"
    assert n1["DashboardScreen"]["node_kind"] == "SCREEN_NODE"
    assert all(n["version_tag"] == "fw-v1.2.0" for n in g1["flow_nodes"])
    assert all(e["version_tag"] == "fw-v1.2.0" for e in g1["flow_edges"])

    diff = plugin.diff_flow_versions(g1, g2)
    assert diff["version_a"] == "fw-v1.2.0"
    assert diff["version_b"] == "fw-v1.3.0"
    assert any(n["node_id"] == "OtaCheckScreen" for n in diff["added_nodes"])
    assert any(n["node_id"] == "DashboardScreen" for n in diff["removed_nodes"])
    assert diff["summary"]["has_breaking_changes"] is True
    assert diff["summary"]["added_transitions"] >= 2
    assert diff["summary"]["removed_transitions"] >= 2
