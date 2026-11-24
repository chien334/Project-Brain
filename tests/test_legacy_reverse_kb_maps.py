"""Wave L3-A Verification Suite: Reverse Engineering KB Maps in ProjectDocSynthesizer and /kb/export."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import tenant_manager
from projectbrain.graphify_engine.doc_synthesizer import ProjectDocSynthesizer
from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes


def _build_legacy_extracted_workspace(base_dir: Path) -> Path:
    """Create legacy C++ Session 0 Service, DesktopHMI (.cpp + UTF-16 .rc), CoreDll, and PLC .st files."""
    ws = base_dir / "LegacySuite"
    svc_dir = ws / "WinSession0Service"
    hmi_dir = ws / "DesktopHMI"
    dll_dir = ws / "CoreDll"
    plc_dir = ws / "PLC_Control"
    for d in (svc_dir, hmi_dir, dll_dir, plc_dir):
        d.mkdir(parents=True, exist_ok=True)

    svc_cpp = """// セッション0常駐サービス (CP932)
#include <windows.h>

#define ERR_PIPE_CONNECT 0x8001 // パイプ接続エラー

// IPC電文ヘッダ
#pragma pack(push, 1)
typedef struct _IPC_PACKET {
    DWORD dwCmd;
    DWORD dwPayloadSize;
    char szTag[64];
} IPC_PACKET;
#pragma pack(pop)

void WINAPI ServiceMain(DWORD argc, LPWSTR* argv) {
    CreateNamedPipeW(L"\\\\.\\pipe\\MyLegacyPipe", 3, 0, 1, 4096, 4096, 0, NULL);
}
"""
    (svc_dir / "service.cpp").write_bytes(svc_cpp.encode("cp932"))

    hmi_cpp = """// デスクトップHMI画面制御 (CP932)
#include <windows.h>

void ConnectToService() {
    CreateFileW(L"\\\\.\\pipe\\MyLegacyPipe", 0, 0, NULL, 3, 0, NULL);
}
"""
    (hmi_dir / "hmi_main.cpp").write_bytes(hmi_cpp.encode("cp932"))

    rc_text = (
        'IDD_HMI_MAIN DIALOGEX 0, 0, 320, 200\r\n'
        'CAPTION "工場監視HMI"\r\n'
        'BEGIN\r\n'
        '    PUSHBUTTON "接続", IDC_BTN_CONNECT, 10, 10, 60, 14\r\n'
        'END\r\n'
    )
    (hmi_dir / "hmi.rc").write_bytes(b"\xff\xfe" + rc_text.encode("utf-16-le"))

    (dll_dir / "core.cpp").write_text(
        "// コア計算エンジン (UTF-8)\n"
        'extern "C" __declspec(dllexport) int ComputeChecksum(const char* buf, int len) { return len; }\n',
        encoding="utf-8",
    )

    st_code = """(* 軸状態構造体 *)
TYPE ST_AxisState :
STRUCT
    bReady : BOOL;
    rPos : REAL;
END_STRUCT
END_TYPE

VAR_GLOBAL
    g_AlarmCode : INT := 0; (* アラームコード *)
    bEmergencyStop AT %IX0.0 : BOOL; (* 非常停止入力 *)
END_VAR

(* モーター制御ブロック *)
FUNCTION_BLOCK FB_ServoAxis
VAR_INPUT
    bEnable : BOOL;
END_VAR
END_FUNCTION_BLOCK

CLASS MotorController
    SERVER
        MotorSpeed : REAL; (* モーター指令速度 *)
    END_SERVER
    CLIENT
        ServoLink : FB_ServoAxis;
    END_CLIENT
END_CLASS
"""
    (plc_dir / "motor_ctrl.st").write_bytes(st_code.encode("cp932"))
    return ws


def _sample_cross_source_relations() -> List[Dict[str, Any]]:
    return [
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "WinSession0Service",
            "relation_type": "calls_pipe",
            "contract_key": "pipe:MyLegacyPipe",
            "contract_metadata": {"pipe_name": "MyLegacyPipe"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "WinSession0Service",
            "relation_type": "shares_shm",
            "contract_key": "shm:MySharedMem",
            "contract_metadata": {"shm_name": "MySharedMem"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "WinSession0Service",
            "relation_type": "controls_service",
            "contract_key": "service:WinSession0Service",
            "contract_metadata": {"service_name": "WinSession0Service"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "WinSession0Service",
            "relation_type": "calls_com",
            "contract_key": "com:ISessionBridge",
            "contract_metadata": {
                "com_target": "ISessionBridge",
                "uuid": "a1b2c3d4-1111-2222-3333-445566778899",
            },
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "WinSession0Service",
            "relation_type": "sends_win32_msg",
            "contract_key": "win32_msg:MY_LEGACY_MSG",
            "contract_metadata": {"message": "MY_LEGACY_MSG"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "CoreDll",
            "relation_type": "loads_dll",
            "contract_key": "export:ComputeChecksum",
            "contract_metadata": {"export_or_dll": "ComputeChecksum"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "drifted",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "WinSession0Service",
            "relation_type": "calls_socket",
            "contract_key": "socket:19000",
            "contract_metadata": {"port": 19000},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "WinSession0Service",
            "dst_source_id": "CoreDll",
            "relation_type": "depends_on",
            "contract_key": "dep:CoreDll",
            "contract_metadata": {"target_symbol": "CoreDll"},
            "src_file_path": "WinSession0Service/WinSession0Service.vcxproj",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "PLC_Control",
            "relation_type": "links_plc_channel",
            "contract_key": "plc_channel:MotorSpeed",
            "contract_metadata": {"plc_kind": "lasal_server", "dst_file_path": "PLC_Control/motor_ctrl.st"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "PLC_Control",
            "relation_type": "shares_plc_tag",
            "contract_key": "plc_tag:g_AlarmCode",
            "contract_metadata": {"plc_kind": "var_global", "dst_file_path": "PLC_Control/motor_ctrl.st"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "active",
        },
        {
            "src_source_id": "DesktopHMI",
            "dst_source_id": "GhostSource",
            "relation_type": "calls_pipe",
            "contract_key": "pipe:RejectedPipe",
            "contract_metadata": {"pipe_name": "RejectedPipe"},
            "src_file_path": "DesktopHMI/hmi_main.cpp",
            "status": "rejected",
        },
    ]


def test_synthesize_all_three_legacy_reverse_kb_maps(tmp_path: Path) -> None:
    """Extracted legacy nodes + cross-source IPC/PLC relations produce all 3 reverse-engineering views."""
    ws = _build_legacy_extracted_workspace(tmp_path)
    extractor = GraphifyExtractor(cache_dir=tmp_path / ".cache")
    delta = extractor.extract_workspace(ws, project_id="legacy/factory", use_cache=False)

    synthesizer = ProjectDocSynthesizer(workspace_path=ws)
    wiki_dir = tmp_path / "docs" / "kb" / "wiki"
    pages = synthesizer.synthesize_wiki(
        delta,
        output_dir=wiki_dir,
        cross_source_relations=_sample_cross_source_relations(),
    )

    by_slug = {p.slug: p for p in pages}
    assert "legacy_reverse_engineering" in by_slug
    legacy_page = by_slug["legacy_reverse_engineering"]
    assert legacy_page.category == "reverse_engineering"
    assert (wiki_dir / "legacy_reverse_engineering.md").exists()

    # Index links to the reverse-engineering page
    assert "legacy_reverse_engineering.md" in by_slug["index"].content

    # 1. Session 0 <-> Desktop Architecture Map assertions (both in dedicated page & architecture_overview)
    for content in (legacy_page.content, by_slug["architecture_overview"].content):
        assert "## Session 0 <-> Desktop Architecture Map" in content
        assert "```mermaid" in content
        assert "flowchart LR" in content
        for rel_name in (
            "calls_pipe",
            "shares_shm",
            "controls_service",
            "calls_com",
            "sends_win32_msg",
            "loads_dll",
            "calls_socket",
            "depends_on",
        ):
            assert rel_name in content, f"Missing relation {rel_name} in Session 0 map"
        assert "WinSession0Service" in content
        assert "DesktopHMI" in content
        assert "CoreDll" in content
        assert "MyLegacyPipe" in content
        assert "MySharedMem" in content
        assert "ComputeChecksum" in content
        assert "ISessionBridge" in content
        assert "MY_LEGACY_MSG" in content
        # Rejected relations must be excluded
        assert "RejectedPipe" not in content
        assert "GhostSource" not in content

    # 2. PLC / LASAL Server-Client & Tag Map assertions
    for content in (legacy_page.content, by_slug["architecture_overview"].content):
        assert "## PLC / LASAL Server-Client & Tag Map" in content
        assert "links_plc_channel" in content
        assert "shares_plc_tag" in content
        assert "MotorSpeed" in content
        assert "ServoLink" in content
        assert "g_AlarmCode" in content
        assert "bEmergencyStop" in content
        assert "%IX0.0" in content
        assert "SERVER" in content
        assert "CLIENT" in content
        assert "VAR_GLOBAL" in content

    # 3. Japanese Term & Struct Glossary assertions (dedicated page & database_and_schemas)
    for content in (legacy_page.content, by_slug["database_and_schemas"].content):
        assert "## Japanese Term & Struct Glossary" in content
        assert "IPC_PACKET" in content
        assert "#pragma pack(1)" in content
        assert "IPC電文ヘッダ" in content
        assert "ST_AxisState" in content
        assert "軸状態構造体" in content
        assert "ERR_PIPE_CONNECT" in content
        assert "0x8001" in content
        assert "パイプ接続エラー" in content
        assert "IDD_HMI_MAIN" in content
        assert "工場監視HMI" in content
        assert "FB_ServoAxis" in content
        assert "モーター制御ブロック" in content
        assert "cp932" in content
        assert "utf-16" in content
        assert "\ufffd" not in content


def test_synthesize_legacy_edges_from_graph_edges_directly(tmp_path: Path) -> None:
    """Legacy IPC and PLC edges passed as GraphEdge objects also render Session 0 & PLC maps."""
    delta = GraphDelta(
        project_id="edge_legacy",
        branch="main",
        nodes=[
            GraphNode(
                id="n_hmi",
                project_id="edge_legacy",
                label="ConnectPipe()",
                source_file="DesktopHMI/hmi.cpp",
                source_location="L10",
                metadata={"kind": "function", "language": "cpp", "source_id": "DesktopHMI"},
            ),
            GraphNode(
                id="n_svc",
                project_id="edge_legacy",
                label="ServiceMain()",
                source_file="WinSession0Service/svc.cpp",
                source_location="L20",
                metadata={"kind": "function", "language": "cpp", "source_id": "WinSession0Service"},
            ),
            GraphNode(
                id="n_plc_srv",
                project_id="edge_legacy",
                label="MotorSpeed",
                source_file="PLC_Control/motor.st",
                source_location="L5",
                metadata={"kind": "server", "language": "structured_text", "data_type": "REAL", "source_id": "PLC_Control"},
            ),
        ],
        edges=[
            GraphEdge(
                project_id="edge_legacy",
                source="n_hmi",
                target="n_svc",
                relation="calls_pipe",
                source_file="DesktopHMI/hmi.cpp",
            ),
            GraphEdge(
                project_id="edge_legacy",
                source="n_hmi",
                target="n_plc_srv",
                relation="links_plc_channel",
                source_file="DesktopHMI/hmi.cpp",
            ),
        ],
    )

    synthesizer = ProjectDocSynthesizer(workspace_path=tmp_path)
    pages = synthesizer.synthesize_wiki(delta, output_dir=tmp_path / "wiki")
    by_slug = {p.slug: p for p in pages}

    assert "legacy_reverse_engineering" in by_slug
    content = by_slug["legacy_reverse_engineering"].content
    assert "## Session 0 <-> Desktop Architecture Map" in content
    assert "DesktopHMI" in content
    assert "WinSession0Service" in content
    assert "calls_pipe" in content
    assert "## PLC / LASAL Server-Client & Tag Map" in content
    assert "links_plc_channel" in content
    assert "MotorSpeed" in content
    # No Japanese text in graph -> glossary should not be emitted
    assert "## Japanese Term & Struct Glossary" not in content


def test_modern_and_empty_graphs_remain_completely_clean(tmp_path: Path) -> None:
    """Empty, modern Python/React/Docker, and English-only C++ graphs emit zero legacy reverse-engineering noise."""
    synthesizer = ProjectDocSynthesizer(workspace_path=tmp_path)

    # 1. Empty graph
    empty_pages = synthesizer.synthesize_wiki(
        GraphDelta(project_id="empty_modern", branch="main"),
        output_dir=tmp_path / "wiki_empty",
    )
    assert "legacy_reverse_engineering" not in {p.slug for p in empty_pages}
    assert not (tmp_path / "wiki_empty" / "legacy_reverse_engineering.md").exists()
    for p in empty_pages:
        assert "Session 0 <-> Desktop Architecture Map" not in p.content
        assert "PLC / LASAL Server-Client & Tag Map" not in p.content
        assert "Japanese Term & Struct Glossary" not in p.content

    # 2. Modern multi-source repo with REST + Docker depends_on
    modern_graph = {
        "project_id": "modern_app",
        "nodes": [
            {
                "id": "py_api",
                "label": "create_order",
                "file_type": "code",
                "source_file": "backend/orders.py",
                "source_location": "backend/orders.py:12",
                "community": 0,
                "metadata": {"kind": "function", "language": "python", "docstring": "Create a customer order."},
            },
            {
                "id": "ts_ui",
                "label": "OrderForm",
                "file_type": "code",
                "source_file": "frontend/OrderForm.tsx",
                "source_location": "frontend/OrderForm.tsx:5",
                "community": 1,
                "metadata": {"kind": "class", "language": "typescript"},
            },
        ],
        "edges": [
            {"source": "ts_ui", "target": "py_api", "relation": "calls_api", "confidence": "EXTRACTED"},
        ],
        "cross_source_relations": [
            {
                "src_source_id": "frontend",
                "dst_source_id": "backend",
                "relation_type": "calls_api",
                "contract_key": "POST /orders",
                "status": "active",
            },
            {
                "src_source_id": "backend",
                "dst_source_id": "postgres",
                "relation_type": "depends_on",
                "contract_key": "dep:postgres",
                "status": "active",
            },
        ],
    }
    modern_pages = synthesizer.synthesize_wiki(modern_graph, output_dir=tmp_path / "wiki_modern")
    assert "legacy_reverse_engineering" not in {p.slug for p in modern_pages}
    assert not (tmp_path / "wiki_modern" / "legacy_reverse_engineering.md").exists()
    for p in modern_pages:
        assert "Session 0 <-> Desktop Architecture Map" not in p.content
        assert "PLC / LASAL Server-Client & Tag Map" not in p.content
        assert "Japanese Term & Struct Glossary" not in p.content
        assert "legacy_reverse_engineering.md" not in p.content

    # 3. Modern English-only C++ library without IPC, PLC, or Japanese comments
    modern_cpp_graph = GraphDelta(
        project_id="modern_cpp",
        branch="main",
        nodes=[
            GraphNode(
                id="cpp_vec",
                project_id="modern_cpp",
                label="FAST_VEC_SIZE",
                source_file="include/vec.hpp",
                source_location="L4",
                metadata={"kind": "macro", "language": "cpp", "value": "64", "docstring": "Default vector width"},
            )
        ],
    )
    cpp_pages = synthesizer.synthesize_wiki(modern_cpp_graph, output_dir=tmp_path / "wiki_modern_cpp")
    assert "legacy_reverse_engineering" not in {p.slug for p in cpp_pages}


def test_kb_export_route_includes_legacy_reverse_engineering_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /kb/export pulls cross_source_relations from TenantManager and emits legacy_reverse_engineering.md."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_multi_db = getattr(env, "multi_db_enabled", True)
    orig_require_rest_auth = getattr(env, "require_rest_auth", False)
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(ws)
    db_file = tmp_path / "kb_legacy_export.db"
    monkeypatch.setenv("PB_DB_PATH", str(db_file))
    monkeypatch.setenv("OM_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("OM_GRAPH_DB_PATH", str(db_file))
    monkeypatch.setenv("PB_MULTI_DB_ENABLED", "false")
    monkeypatch.setenv("OM_MULTI_DB_ENABLED", "false")
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.setenv("PB_READ_ONLY", "false")
    monkeypatch.setenv("OM_READ_ONLY", "false")

    monkeypatch.setattr(env, "database_url", f"sqlite:///{db_file}")
    monkeypatch.setattr(env, "db_path", str(db_file))
    monkeypatch.setattr(env, "multi_db_enabled", False)
    monkeypatch.setattr(env, "require_rest_auth", False)

    tenant_manager.reset()
    db.conn = None
    db.is_postgres = False
    auth_routes._ensured_conn = None
    auth_routes._ensured_db_url = None
    db.connect()
    tenant_manager.bind_master_db(db)
    tenant_manager.ensure_master_catalog()
    invalidate_query_cache()

    try:
        proj_id = "legacy-export-proj"
        tenant_manager.ensure_project_database(proj_id)
        for rel in (
            {
                "src_source_id": "DesktopHMI",
                "dst_source_id": "WinSession0Service",
                "relation_type": "calls_pipe",
                "contract_key": "pipe:FactoryPipe",
                "contract_metadata": {"pipe_name": "FactoryPipe"},
                "src_file_path": "DesktopHMI/client.cpp",
                "origin": "auto",
                "confidence": "high",
                "confidence_score": 0.95,
                "status": "active",
            },
            {
                "src_source_id": "DesktopHMI",
                "dst_source_id": "PLC_Control",
                "relation_type": "links_plc_channel",
                "contract_key": "plc_channel:ConveyorSpeed",
                "contract_metadata": {"plc_kind": "lasal_server"},
                "src_file_path": "DesktopHMI/client.cpp",
                "origin": "auto",
                "confidence": "high",
                "confidence_score": 0.95,
                "status": "active",
            },
            {
                "src_source_id": "DesktopHMI",
                "dst_source_id": "IgnoredSvc",
                "relation_type": "calls_pipe",
                "contract_key": "pipe:ShouldNotAppear",
                "contract_metadata": {"pipe_name": "ShouldNotAppear"},
                "status": "rejected",
            },
        ):
            tenant_manager.upsert_cross_source_relation(proj_id, **rel)

        app = create_app()
        client = TestClient(app)
        res = client.post(
            "/kb/export",
            json={
                "project_id": proj_id,
                "branch": "main",
                "workspace_path": ".",
                "output_dir": "docs/kb",
                "formats": ["wiki"],
            },
        )
        assert res.status_code == 200, res.text
        legacy_md = ws / "docs" / "kb" / "wiki" / "legacy_reverse_engineering.md"
        assert legacy_md.exists()
        text = legacy_md.read_text(encoding="utf-8")
        assert "FactoryPipe" in text
        assert "ConveyorSpeed" in text
        assert "ShouldNotAppear" not in text
    finally:
        tenant_manager.reset()
        if db.conn is not None and db.conn is not orig_conn:
            try:
                db.conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        env.multi_db_enabled = orig_multi_db
        env.require_rest_auth = orig_require_rest_auth
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None
        invalidate_query_cache()
