"""Wave L2 Verification Suite: Windows Session 0 IPC, PLC/LASAL Linking, and .sln/.vcxproj Legacy Discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.core.tenant_manager import tenant_manager
from projectbrain.graphify_engine.cross_source_linker import CrossSourceLinker
from projectbrain.graphify_engine.hub_bootstrap import (
    bootstrap_project_hub,
    discover_workspace_sources,
)
from projectbrain.memory.hsg import invalidate_query_cache
from projectbrain.server.routes import auth as auth_routes


@pytest.fixture
def isolated_legacy_hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate workspace, Master SQLite DB, and per-project physical DBs in tmp_path."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_emb_kind = env.emb_kind
    orig_min_score = env.min_score
    orig_root_branch = env.root_branch
    orig_multi_db = getattr(env, "multi_db_enabled", True)
    orig_require_rest_auth = getattr(env, "require_rest_auth", False)
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    env_keys = (
        "PB_DB_PATH",
        "OM_DB_PATH",
        "PB_GRAPH_DB_PATH",
        "OM_GRAPH_DB_PATH",
        "OM_EMBED_PROVIDER",
        "PB_MULTI_DB_ENABLED",
        "OM_MULTI_DB_ENABLED",
        "PB_ROOT_BRANCH",
        "OM_ROOT_BRANCH",
        "PB_REQUIRE_REST_AUTH",
        "PB_READ_ONLY",
        "OM_READ_ONLY",
        "PB_MODE",
        "PB_WORKSPACE_ROOT",
    )
    orig_os_env = {k: os.environ.get(k) for k in env_keys}

    monkeypatch.chdir(tmp_path)
    master_db_path = tmp_path / "master_legacy_l2.db"

    monkeypatch.setenv("PB_DB_PATH", str(master_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(master_db_path))
    monkeypatch.delenv("PB_GRAPH_DB_PATH", raising=False)
    monkeypatch.delenv("OM_GRAPH_DB_PATH", raising=False)
    monkeypatch.setenv("OM_EMBED_PROVIDER", "synthetic")
    monkeypatch.setenv("PB_MULTI_DB_ENABLED", "true")
    monkeypatch.setenv("OM_MULTI_DB_ENABLED", "true")
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.delenv("PB_ROOT_BRANCH", raising=False)
    monkeypatch.delenv("OM_ROOT_BRANCH", raising=False)
    monkeypatch.setenv("PB_READ_ONLY", "false")
    monkeypatch.setenv("OM_READ_ONLY", "false")
    monkeypatch.delenv("PB_MODE", raising=False)
    monkeypatch.delenv("PB_WORKSPACE_ROOT", raising=False)

    monkeypatch.setattr(env, "database_url", f"sqlite:///{master_db_path}")
    monkeypatch.setattr(env, "db_path", str(master_db_path))
    monkeypatch.setattr(env, "emb_kind", "synthetic")
    monkeypatch.setattr(env, "min_score", 0.0)
    monkeypatch.setattr(env, "root_branch", "main")
    monkeypatch.setattr(env, "multi_db_enabled", True)
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
        yield {"tmp_path": tmp_path, "master_db_path": master_db_path}
    finally:
        tenant_manager.reset()
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        env.emb_kind = orig_emb_kind
        env.min_score = orig_min_score
        env.root_branch = orig_root_branch
        env.multi_db_enabled = orig_multi_db
        env.require_rest_auth = orig_require_rest_auth
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None

        for key, prev_val in orig_os_env.items():
            if prev_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev_val

        invalidate_query_cache()


def _create_legacy_sln_workspace(base_dir: Path) -> Path:
    """Create a realistic Shift-JIS multi-project Windows .sln + Session 0 + PLC/LASAL workspace."""
    ws = base_dir / "LegacyFactorySuite"
    svc_dir = ws / "WinSession0Service"
    hmi_dir = ws / "DesktopHMI"
    dll_dir = ws / "CoreDll"
    plc_dir = ws / "PLC_Control"
    docs_folder = ws / "SolutionDocs"  # referenced only as a Solution Folder in .sln

    for d in (svc_dir, hmi_dir, dll_dir, plc_dir, docs_folder):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Visual Studio .sln with Windows backslashes, Solution Folder (ignored), and ProjectDependencies
    sln_text = (
        "Microsoft Visual Studio Solution File, Format Version 12.00\r\n"
        "# Visual Studio Version 17\r\n"
        'Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "WinSession0Service", "WinSession0Service\\WinSession0Service.vcxproj", "{11111111-1111-1111-1111-111111111111}"\r\n'
        "\tProjectSection(ProjectDependencies) = postProject\r\n"
        "\t\t{33333333-3333-3333-3333-333333333333} = {33333333-3333-3333-3333-333333333333}\r\n"
        "\tEndProjectSection\r\n"
        "EndProject\r\n"
        'Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "DesktopHMI", "DesktopHMI\\DesktopHMI.vcxproj", "{22222222-2222-2222-2222-222222222222}"\r\n'
        "EndProject\r\n"
        'Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "CoreDll", "CoreDll\\CoreDll.vcxproj", "{33333333-3333-3333-3333-333333333333}"\r\n'
        "EndProject\r\n"
        'Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "SolutionDocs", "SolutionDocs", "{99999999-9999-9999-9999-999999999999}"\r\n'
        "EndProject\r\n"
    )
    (ws / "LegacyFactorySuite.sln").write_text(sln_text, encoding="utf-8")

    # 2. WinSession0Service (C++ Windows Service in Shift-JIS)
    (svc_dir / "WinSession0Service.vcxproj").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n'
        "  <PropertyGroup Label=\"Configuration\">\n"
        "    <ConfigurationType>Application</ConfigurationType>\n"
        "  </PropertyGroup>\n"
        "  <PropertyGroup>\n"
        "    <ProjectGuid>{11111111-1111-1111-1111-111111111111}</ProjectGuid>\n"
        "    <SubSystem>Console</SubSystem>\n"
        "  </PropertyGroup>\n"
        "</Project>\n",
        encoding="utf-8",
    )
    svc_cpp = """// セッション0 常駐サービス本体 (Shift-JIS)
#include <windows.h>
#include <winsock2.h>

#define IPC_TCP_PORT 19000

#pragma pack(push, 1)
typedef struct _IPC_PACKET {
    DWORD dwCmd;
    DWORD dwPayloadSize;
    char szTag[64];
} IPC_PACKET;
#pragma pack(pop)

[uuid("A1B2C3D4-1111-2222-3333-445566778899")]
interface ISessionBridge {
    HRESULT PingSession();
};

LRESULT CALLBACK ServiceWndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    static UINT wmLegacy = RegisterWindowMessageW(L"MY_LEGACY_MSG");
    switch (msg) {
    case WM_COPYDATA:
        return 1;
    case WM_USER + 10:
        return 1;
    default:
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
}

void WINAPI ServiceMain(DWORD argc, LPWSTR* argv) {
    RegisterServiceCtrlHandlerW(L"WinSession0Service", NULL);
    HANDLE hPipe = CreateNamedPipeW(
        L"\\\\.\\pipe\\MyLegacyPipe",
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_MESSAGE,
        1, 4096, 4096, 0, NULL
    );
    HANDLE hMap = CreateFileMappingW(
        INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, sizeof(IPC_PACKET), L"Global\\MySharedMem"
    );
    IPC_PACKET* pView = (IPC_PACKET*)MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(IPC_PACKET));
    void* pBridge = NULL;
    CoCreateInstance(CLSID_CoreBridge, NULL, 1, IID_ICoreBridge, &pBridge);

    WSADATA wsa;
    WSAStartup(0x0202, &wsa);
    SOCKET s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr;
    addr.sin_port = htons(IPC_TCP_PORT);
    bind(s, (struct sockaddr*)&addr, sizeof(addr));
    listen(s, 4);
}
"""
    (svc_dir / "service.cpp").write_bytes(svc_cpp.encode("cp932"))

    # 3. DesktopHMI (C++ Desktop UI + <ProjectReference> to CoreDll + UTF-16 .rc)
    (hmi_dir / "DesktopHMI.vcxproj").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n'
        "  <PropertyGroup Label=\"Configuration\">\n"
        "    <ConfigurationType>Application</ConfigurationType>\n"
        "    <UseOfMfc>Dynamic</UseOfMfc>\n"
        "  </PropertyGroup>\n"
        "  <PropertyGroup>\n"
        "    <ProjectGuid>{22222222-2222-2222-2222-222222222222}</ProjectGuid>\n"
        "    <SubSystem>Windows</SubSystem>\n"
        "  </PropertyGroup>\n"
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\CoreDll\\CoreDll.vcxproj">\n'
        "      <Project>{33333333-3333-3333-3333-333333333333}</Project>\n"
        "    </ProjectReference>\n"
        "  </ItemGroup>\n"
        "</Project>\n",
        encoding="utf-8",
    )
    hmi_cpp = """// デスクトップHMIクライアント (Shift-JIS)
#include <windows.h>
#include <winsock2.h>

#define IPC_TCP_PORT 19000

#pragma pack(push, 1)
typedef struct _IPC_PACKET {
    DWORD dwCmd;
    DWORD dwPayloadSize;
    char szTag[64];
} IPC_PACKET;
#pragma pack(pop)

void ConnectServiceAndPlc(HWND hTarget) {
    SC_HANDLE hScm = OpenSCManagerW(NULL, NULL, SC_MANAGER_CONNECT);
    SC_HANDLE hSvc = OpenServiceW(hScm, L"WinSession0Service", SERVICE_START | SERVICE_USER_DEFINED_CONTROL);
    StartServiceW(hSvc, 0, NULL);
    ControlService(hSvc, 128, NULL);

    HANDLE hPipe = CreateFileW(
        L"\\\\.\\pipe\\MyLegacyPipe",
        GENERIC_READ | GENERIC_WRITE,
        0, NULL, OPEN_EXISTING, 0, NULL
    );
    HANDLE hMap = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, L"Global\\MySharedMem");
    IPC_PACKET* pView = (IPC_PACKET*)MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(IPC_PACKET));

    UINT wmLegacy = RegisterWindowMessageW(L"MY_LEGACY_MSG");
    SendMessageW(hTarget, WM_COPYDATA, 0, 0);
    SendMessageW(hTarget, WM_USER + 10, 0, 0);

    HMODULE hDll = LoadLibraryW(L"CoreDll.dll");
    void* pFn = GetProcAddress(hDll, "ComputeChecksum");

    void* pSess = NULL;
    CoCreateInstance(CLSID_SessionBridge, NULL, 1, IID_ISessionBridge, &pSess);

    WSADATA wsa;
    WSAStartup(0x0202, &wsa);
    SOCKET s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr;
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    addr.sin_port = htons(IPC_TCP_PORT);
    connect(s, (struct sockaddr*)&addr, sizeof(addr));

    // PLC / LASAL channel & global tag access
    double speed = LasalRead("MotorSpeed");
    int alarm = ReadPlcTag("g_AlarmCode");
    (void)speed; (void)alarm; (void)pView; (void)pFn; (void)hPipe; (void)wmLegacy;
}
"""
    (hmi_dir / "hmi_main.cpp").write_bytes(hmi_cpp.encode("cp932"))
    rc_text = (
        'IDD_HMI_MAIN DIALOGEX 0, 0, 300, 200\r\n'
        'CAPTION "工場監視HMI"\r\n'
        'BEGIN\r\n'
        '    PUSHBUTTON "接続", IDC_BTN_CONNECT, 10, 10, 60, 14\r\n'
        'END\r\n'
    )
    (hmi_dir / "hmi.rc").write_bytes(b"\xff\xfe" + rc_text.encode("utf-16-le"))

    # 4. CoreDll (C++ DLL with .def EXPORTS + __declspec(dllexport) + COM IDL)
    (dll_dir / "CoreDll.vcxproj").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n'
        "  <PropertyGroup Label=\"Configuration\">\n"
        "    <ConfigurationType>DynamicLibrary</ConfigurationType>\n"
        "  </PropertyGroup>\n"
        "  <PropertyGroup>\n"
        "    <ProjectGuid>{33333333-3333-3333-3333-333333333333}</ProjectGuid>\n"
        "  </PropertyGroup>\n"
        "</Project>\n",
        encoding="utf-8",
    )
    (dll_dir / "CoreDll.def").write_text(
        'LIBRARY "CoreDll"\n'
        "EXPORTS\n"
        "    ComputeChecksum @1\n",
        encoding="utf-8",
    )
    (dll_dir / "core.cpp").write_text(
        "#include <stdint.h>\n"
        "#include <stddef.h>\n\n"
        'extern "C" __declspec(dllexport) uint32_t ComputeChecksum(const uint8_t* data, size_t len) {\n'
        "    return (uint32_t)len;\n"
        "}\n",
        encoding="utf-8",
    )
    (dll_dir / "CoreBridge.idl").write_text(
        'import "oaidl.idl";\n'
        "[\n"
        "    object,\n"
        "    uuid(C1D2E3F4-1111-2222-3333-445566778899)\n"
        "]\n"
        "interface ICoreBridge : IUnknown {\n"
        "    HRESULT Compute([in] long val);\n"
        "};\n"
        "[\n"
        "    uuid(D1E2F3A4-1111-2222-3333-445566778899)\n"
        "]\n"
        "coclass CoreBridge {\n"
        "    [default] interface ICoreBridge;\n"
        "};\n",
        encoding="utf-8",
    )

    # 5. PLC_Control (Sigmatek LASAL .lcp + Shift-JIS .st with SERVER MotorSpeed & VAR_GLOBAL g_AlarmCode)
    (plc_dir / "PLC_Control.lcp").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Project Name="PLC_Control">\n'
        '  <Network IpAddress="192.168.1.50" Port="1954" />\n'
        "</Project>\n",
        encoding="utf-8",
    )
    st_code = """(* PLC モーター制御プロジェクト - Shift-JIS *)
VAR_GLOBAL
    g_AlarmCode : INT := 0;
END_VAR

CLASS MotorController
    SERVER
        MotorSpeed : REAL;
    END_SERVER
    METHOD GLOBAL UpdateSpeed
        MotorSpeed := 1200.0;
    END_METHOD
END_CLASS
"""
    (plc_dir / "motor_ctrl.st").write_bytes(st_code.encode("cp932"))

    # 6. Shared Shift-JIS README specification
    readme_sjis = (
        "# レガシー制御システム仕様書\n\n"
        "## 通信仕様\n"
        "- Named Pipe: `\\\\.\\pipe\\MyLegacyPipe`\n"
        "- Shared Memory: `Global\\MySharedMem` (`IPC_PACKET`)\n"
        "- DLL Export: `ComputeChecksum`\n"
        "- PLC Channel/Tag: `MotorSpeed`, `g_AlarmCode`\n"
    )
    (ws / "README.md").write_bytes(readme_sjis.encode("cp932"))
    return ws


def test_discover_sln_vcxproj_plc_and_depends_on(isolated_legacy_hub_env: Dict[str, Any]) -> None:
    """discover_workspace_sources finds all 4 sub-sources, ignores Solution Folders, and extracts depends_on."""
    ws = _create_legacy_sln_workspace(isolated_legacy_hub_env["tmp_path"])

    res = discover_workspace_sources(str(ws))
    assert res["status"] == "ok"
    sources_by_id = {s["source_id"]: s for s in res["sources"]}

    assert set(sources_by_id.keys()) == {
        "WinSession0Service",
        "DesktopHMI",
        "CoreDll",
        "PLC_Control",
    }
    assert "SolutionDocs" not in sources_by_id

    svc = sources_by_id["WinSession0Service"]
    assert svc["source_type"] == "win_service"
    assert svc["language"] == "cpp"
    assert "cpp" in svc["tech_stack"]

    hmi = sources_by_id["DesktopHMI"]
    assert hmi["source_type"] == "desktop_client"
    assert hmi["language"] == "cpp"
    assert "mfc" in hmi["tech_stack"]

    dll = sources_by_id["CoreDll"]
    assert dll["source_type"] == "native_lib"
    assert dll["language"] == "cpp"

    plc = sources_by_id["PLC_Control"]
    assert plc["source_type"] == "plc_st"
    assert plc["language"] == "st"
    assert set(plc["tech_stack"]) >= {"iec61131-st", "lasal"}

    dep_edges = {(r["src_source_id"], r["dst_source_id"], r["relation_type"]) for r in res["relations"]}
    assert ("DesktopHMI", "CoreDll", "depends_on") in dep_edges
    assert ("WinSession0Service", "CoreDll", "depends_on") in dep_edges

    shared_docs = {d["doc_key"]: d for d in res["shared_docs"]}
    assert "readme" in shared_docs


@pytest.mark.asyncio
async def test_bootstrap_and_cross_source_linker_session0_ipc_and_plc(
    isolated_legacy_hub_env: Dict[str, Any],
) -> None:
    """Full bootstrap & CrossSourceLinker creates all Session 0 IPC, DLL, COM, Socket, PLC, and Shift-JIS doc edges."""
    ws = _create_legacy_sln_workspace(isolated_legacy_hub_env["tmp_path"])
    project_id = "legacy/factory-suite"

    boot_res = await bootstrap_project_hub(
        project_id=project_id,
        workspace_root=str(ws),
        auto_discover=True,
        sync_graphs=True,
        auto_link=True,
    )
    assert boot_res["status"] == "ok"
    assert {s["source_id"] for s in boot_res["sources_registered"]} == {
        "WinSession0Service",
        "DesktopHMI",
        "CoreDll",
        "PLC_Control",
    }
    assert {d["doc_key"] for d in boot_res["shared_docs_ingested"]} >= {"readme"}

    # Verify Shift-JIS README decoded cleanly without replacement characters in stored document units
    dv_units = tenant_manager._project_root_fetchall(
        project_id,
        "SELECT markdown_text FROM document_units WHERE project_id = ? AND doc_key = 'readme'",
        (project_id,),
    )
    assert dv_units
    joined_md = "\n".join(str(u.get("markdown_text") or "") for u in dv_units)
    assert "レガシー制御システム仕様書" in joined_md
    assert "\ufffd" not in joined_md

    rels = tenant_manager.list_cross_source_relations(project_id)
    rel_triples = {(r["src_source_id"], r["dst_source_id"], r["relation_type"]) for r in rels}

    assert ("DesktopHMI", "WinSession0Service", "calls_pipe") in rel_triples
    assert ("DesktopHMI", "WinSession0Service", "shares_shm") in rel_triples
    assert ("DesktopHMI", "WinSession0Service", "controls_service") in rel_triples
    assert ("DesktopHMI", "WinSession0Service", "calls_com") in rel_triples
    assert ("WinSession0Service", "CoreDll", "calls_com") in rel_triples
    assert ("DesktopHMI", "WinSession0Service", "sends_win32_msg") in rel_triples
    assert ("DesktopHMI", "CoreDll", "loads_dll") in rel_triples
    assert ("DesktopHMI", "WinSession0Service", "calls_socket") in rel_triples
    assert ("DesktopHMI", "PLC_Control", "links_plc_channel") in rel_triples
    assert ("DesktopHMI", "PLC_Control", "shares_plc_tag") in rel_triples
    assert ("DesktopHMI", "CoreDll", "depends_on") in rel_triples
    assert ("WinSession0Service", "CoreDll", "depends_on") in rel_triples
    assert ("DesktopHMI", "WinSession0Service", "shares_schema") in rel_triples or (
        "WinSession0Service", "DesktopHMI", "shares_schema"
    ) in rel_triples
    assert any(r["relation_type"] == "documented_by" and r["dst_source_id"] == "__shared__" for r in rels)

    # Verify drift detection when CoreDll export signature changes and PLC channel type changes
    (ws / "CoreDll" / "core.cpp").write_text(
        "#include <stdint.h>\n#include <stddef.h>\n"
        'extern "C" __declspec(dllexport) uint64_t ComputeChecksum(const uint8_t* data, size_t len, uint32_t seed) {\n'
        "    return (uint64_t)(len + seed);\n"
        "}\n",
        encoding="utf-8",
    )
    (ws / "PLC_Control" / "motor_ctrl.st").write_bytes(
        (
            "VAR_GLOBAL\n    g_AlarmCode : DINT := 0;\nEND_VAR\n"
            "CLASS MotorController\n    SERVER\n        MotorSpeed : LREAL;\n    END_SERVER\nEND_CLASS\n"
        ).encode("cp932")
    )

    linker = CrossSourceLinker()
    relink = linker.auto_link_project(
        project_id=project_id,
        workspace_map={
            "WinSession0Service": ws / "WinSession0Service",
            "DesktopHMI": ws / "DesktopHMI",
            "CoreDll": ws / "CoreDll",
            "PLC_Control": ws / "PLC_Control",
        },
    )
    assert relink["drifted_count"] >= 2
    drifted_types = {r["relation_type"] for r in relink["relations"] if r["status"] == "drifted"}
    assert {"loads_dll", "links_plc_channel"} <= drifted_types


def test_standalone_legacy_manifests_and_preserve_modern_discovery(
    isolated_legacy_hub_env: Dict[str, Any],
) -> None:
    """Standalone legacy manifests (vcproj/CMake/Make/Qt/VB6/Delphi/ST) discover cleanly alongside modern stacks."""
    tmp_path = isolated_legacy_hub_env["tmp_path"]

    # 1. Polyglot legacy workspace with Qt, VB6, Delphi, CMake, Makefile, vcproj, and ST
    leg = tmp_path / "polyglot-legacy"
    (leg / "qt_panel").mkdir(parents=True)
    (leg / "qt_panel" / "panel.pro").write_text("QT += widgets\nTARGET = Panel\nSOURCES += main.cpp\n", encoding="utf-8")
    (leg / "vb6_client").mkdir(parents=True)
    (leg / "vb6_client" / "Client.vbp").write_text('Type=Exe\nModule=ModIpc; ModIpc.bas\n', encoding="utf-8")
    (leg / "vb6_client" / "ModIpc.bas").write_text(
        'Private Declare Function ComputeChecksum Lib "CoreDll.dll" (ByVal x As Long) As Long\n',
        encoding="utf-8",
    )
    (leg / "delphi_agent").mkdir(parents=True)
    (leg / "delphi_agent" / "Agent.dpr").write_text("program Agent;\nbegin\nend.\n", encoding="utf-8")
    (leg / "cmake_daemon").mkdir(parents=True)
    (leg / "cmake_daemon" / "CMakeLists.txt").write_text(
        "project(cmake_daemon)\nadd_executable(cmake_daemon main.cpp)\n",
        encoding="utf-8",
    )
    (leg / "make_driver").mkdir(parents=True)
    (leg / "make_driver" / "Makefile").write_text("all:\n\tgcc -c drv.c\n", encoding="utf-8")
    (leg / "make_driver" / "drv.c").write_text("int drv_init(void) { return 0; }\n", encoding="utf-8")
    (leg / "old_vc6").mkdir(parents=True)
    (leg / "old_vc6" / "OldLib.vcproj").write_text(
        '<VisualStudioProject><Configurations><Configuration Name="Release|Win32" ConfigurationType="2" /></Configurations></VisualStudioProject>\n',
        encoding="utf-8",
    )
    (leg / "raw_st_plc").mkdir(parents=True)
    (leg / "raw_st_plc" / "pump.st").write_text("PROGRAM PumpMain\nEND_PROGRAM\n", encoding="utf-8")

    disc = discover_workspace_sources(str(leg))
    smap = {s["source_id"]: s for s in disc["sources"]}
    assert smap["qt_panel"]["tech_stack"] == ["cpp", "qt"]
    assert smap["qt_panel"]["source_type"] == "desktop_client"
    assert smap["vb6_client"]["language"] == "vb6"
    assert smap["delphi_agent"]["language"] == "delphi"
    assert "cmake" in smap["cmake_daemon"]["tech_stack"]
    assert "make" in smap["make_driver"]["tech_stack"]
    assert smap["make_driver"]["language"] == "c"
    assert smap["old_vc6"]["source_type"] == "native_lib"
    assert smap["raw_st_plc"]["source_type"] == "plc_st"

    # 2. Modern Python/Node monorepo with root Makefile keeps existing Python/Node discovery 100% intact
    modern = tmp_path / "modern-with-make"
    modern.mkdir(parents=True, exist_ok=True)
    (modern / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    (modern / "src" / "api").mkdir(parents=True)
    (modern / "src" / "api" / "pyproject.toml").write_text('[project]\nname="api"\n', encoding="utf-8")
    (modern / "src" / "web").mkdir(parents=True)
    (modern / "src" / "web" / "package.json").write_text('{"name":"web","dependencies":{"react":"^18.0.0"}}', encoding="utf-8")

    modern_disc = discover_workspace_sources(str(modern))
    assert {s["source_id"] for s in modern_disc["sources"]} == {"api", "web"}


def test_cross_source_linker_vb6_csharp_utf16_shiftjis(isolated_legacy_hub_env: Dict[str, Any]) -> None:
    """Verify CrossSourceLinker handles C# NamedPipes/MemoryMappedFile/DllImport, VB6 Declare, and UTF-16/Shift-JIS files."""
    root = isolated_legacy_hub_env["tmp_path"] / "mixed_legacy_ipc"
    cs_dir = root / "CSharpClient"
    vb_dir = root / "VB6Ui"
    dll_dir = root / "NativeEngine"
    svc_dir = root / "PipeService"
    for d in (cs_dir, vb_dir, dll_dir, svc_dir):
        d.mkdir(parents=True, exist_ok=True)

    (cs_dir / "BridgeClient.cs").write_text(
        "using System.IO.Pipes;\n"
        "using System.IO.MemoryMappedFiles;\n"
        "using System.Runtime.InteropServices;\n\n"
        "public class BridgeClient {\n"
        '    [DllImport("NativeEngine.dll", EntryPoint = "VerifyFrame")]\n'
        "    public static extern int VerifyFrame(int code);\n\n"
        "    public void Run() {\n"
        '        using var pipe = new NamedPipeClientStream(".", "TelemetryPipe", PipeDirection.InOut);\n'
        '        using var mmf = MemoryMappedFile.OpenExisting("Global\\\\TelemetryShm");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (vb_dir / "ModBridge.bas").write_bytes(
        (
            'Attribute VB_Name = "ModBridge"\n'
            "' 日本語VB6モジュール (Shift-JIS)\n"
            'Private Declare Function VerifyFrame Lib "NativeEngine.dll" (ByVal code As Long) As Long\n'
        ).encode("cp932")
    )
    (dll_dir / "NativeEngine.def").write_bytes(
        b"\xff\xfe" + 'LIBRARY "NativeEngine"\r\nEXPORTS\r\n    VerifyFrame @1\r\n'.encode("utf-16-le")
    )
    (svc_dir / "pipe_srv.cpp").write_bytes(
        (
            "// パイプサーバ (Shift-JIS)\n"
            "#include <windows.h>\n"
            "void Serve() {\n"
            '    CreateNamedPipeA("\\\\\\\\.\\\\pipe\\\\TelemetryPipe", 3, 0, 1, 1024, 1024, 0, NULL);\n'
            '    CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, 4, 0, 256, "Global\\\\TelemetryShm");\n'
            "}\n"
        ).encode("cp932")
    )

    project_id = "legacy/mixed-ipc"
    tenant_manager.ensure_project_database(project_id)
    for sid, sdir in (
        ("CSharpClient", cs_dir),
        ("VB6Ui", vb_dir),
        ("NativeEngine", dll_dir),
        ("PipeService", svc_dir),
    ):
        tenant_manager.register_project_source(project_id, sid, workspace_subpath=str(sdir))

    linker = CrossSourceLinker()
    res = linker.auto_link_project(
        project_id=project_id,
        workspace_map={
            "CSharpClient": cs_dir,
            "VB6Ui": vb_dir,
            "NativeEngine": dll_dir,
            "PipeService": svc_dir,
        },
    )
    assert res["status"] == "ok"
    triples = {(r["src_source_id"], r["dst_source_id"], r["relation_type"]) for r in res["relations"]}
    assert ("CSharpClient", "PipeService", "calls_pipe") in triples
    assert ("CSharpClient", "PipeService", "shares_shm") in triples
    assert ("CSharpClient", "NativeEngine", "loads_dll") in triples
    assert ("VB6Ui", "NativeEngine", "loads_dll") in triples
