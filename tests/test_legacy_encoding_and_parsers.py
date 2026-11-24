"""Tests for legacy encoding detection and legacy language/resource parsers."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

from projectbrain.graphify_engine.extractor import CODE_EXTENSIONS, GraphifyExtractor
from projectbrain.graphify_engine.legacy_encoding import (
    decode_legacy_bytes,
    read_text_with_encoding,
)


@pytest.fixture
def extractor(tmp_path: Path) -> GraphifyExtractor:
    return GraphifyExtractor(cache_dir=tmp_path / ".projectbrain" / "cache")


# ---------------------------------------------------------------------------
# 1. legacy_encoding.py Tests
# ---------------------------------------------------------------------------
def test_decode_cp932_shift_jis_japanese_comments():
    """CP932 / Shift-JIS Japanese comments (including NEC/Windows-31J chars) decode cleanly."""
    original = "// 共有メモリ制御: セッション0サービス通信 ①\n#define ERR_PIPE_TIMEOUT 0x1001\n"
    raw = original.encode("cp932")

    text, enc = decode_legacy_bytes(raw)
    assert enc in ("cp932", "shift_jis")
    assert text == original
    assert "\ufffd" not in text


def test_decode_shift_jis_standard_file(tmp_path: Path):
    """read_text_with_encoding reads a Shift-JIS file from disk cleanly."""
    original = "(* モーター制御用ファンクションブロック *)\nFUNCTION_BLOCK FB_Motor\nEND_FUNCTION_BLOCK\n"
    st_file = tmp_path / "motor_sjis.st"
    st_file.write_bytes(original.encode("shift_jis"))

    text, enc = read_text_with_encoding(st_file)
    assert enc in ("cp932", "shift_jis")
    assert "モーター制御用ファンクションブロック" in text
    assert "\ufffd" not in text


def test_decode_utf16_le_rc_with_bom(tmp_path: Path):
    """UTF-16 LE .rc resource file with BOM decodes without stray BOM or NUL bytes."""
    original = (
        'IDD_MAIN_DLG DIALOGEX 0, 0, 320, 200\r\n'
        'CAPTION "セッション0管理画面"\r\n'
        'BEGIN\r\n'
        '    PUSHBUTTON "接続", IDC_BTN_CONNECT, 10, 10, 60, 14\r\n'
        'END\r\n'
    )
    rc_file = tmp_path / "app.rc"
    rc_file.write_bytes(b"\xff\xfe" + original.encode("utf-16-le"))

    text, enc = read_text_with_encoding(rc_file)
    assert enc in ("utf-16", "utf-16-le")
    assert not text.startswith("\ufeff")
    assert "\x00" not in text
    assert "セッション0管理画面" in text
    assert "IDC_BTN_CONNECT" in text


def test_decode_utf16_le_without_bom():
    """BOM-less UTF-16 LE Windows text is caught by null-byte heuristic before UTF-8."""
    original = "LIBRARY CoreEngine\r\nEXPORTS\r\n    StartEngine @1\r\n"
    raw = original.encode("utf-16-le")

    text, enc = decode_legacy_bytes(raw)
    assert enc == "utf-16-le"
    assert text == original
    assert "\x00" not in text


def test_decode_euc_jp_including_hiragana_overlap():
    """EUC-JP decodes accurately even when bytes overlap CP932 half-width Katakana range."""
    samples = [
        "/* 日本語仕様書: パイプ通信と共有メモリ */\nint main() { return 0; }\n",
        "/* あいうえおかきくけこ */\n",
    ]
    for original in samples:
        raw = original.encode("euc_jp")
        text, enc = decode_legacy_bytes(raw)
        assert enc == "euc_jp"
        assert text == original


def test_decode_iso2022_jp_and_utf8_and_bom():
    """UTF-8 (with/without BOM) and ISO-2022-JP streams decode accurately."""
    utf8_src = "// UTF-8 日本語テスト\nint x = 1;\n"
    t_utf8, e_utf8 = decode_legacy_bytes(utf8_src.encode("utf-8"))
    assert e_utf8 == "utf-8"
    assert t_utf8 == utf8_src

    t_bom, e_bom = decode_legacy_bytes(b"\xef\xbb\xbf" + utf8_src.encode("utf-8"))
    assert e_bom == "utf-8-sig"
    assert t_bom == utf8_src

    iso_src = "/* 日本語テスト */\n"
    t_iso, e_iso = decode_legacy_bytes(iso_src.encode("iso2022_jp"))
    assert e_iso == "iso2022_jp"
    assert t_iso == iso_src


def test_decode_invalid_bytes_fallback():
    """Arbitrary invalid bytes never raise UnicodeDecodeError and fall back cleanly."""
    # Bytes invalid in UTF-8, CP932/Shift-JIS, EUC-JP, ISO-2022-JP, and strict CP1252
    bad_bytes = b"legacy_prefix_\x81\xfd\xfe_suffix"
    text, enc = decode_legacy_bytes(bad_bytes)
    assert isinstance(text, str)
    assert enc == "latin-1"
    assert "legacy_prefix_" in text
    assert "_suffix" in text

    # Bytes with 0xFF must not be mis-classified as CP932 PUA (\uf8f3)
    win_bytes = b"caf\xe9 \xff"
    w_text, w_enc = decode_legacy_bytes(win_bytes)
    assert isinstance(w_text, str)
    assert w_enc in ("cp1252", "latin-1")
    assert "\uf8f3" not in w_text

    empty_text, empty_enc = decode_legacy_bytes(b"")
    assert empty_text == ""
    assert empty_enc == "utf-8"


# ---------------------------------------------------------------------------
# 2. CODE_EXTENSIONS Coverage
# ---------------------------------------------------------------------------
def test_code_extensions_include_legacy_and_st():
    expected = {
        ".st", ".scl", ".exp", ".pou",
        ".idl", ".def", ".rc", ".inl", ".tpp",
        ".bas", ".cls", ".frm", ".pas", ".dfm", ".cbl", ".cob", ".sql", ".bat", ".cmd", ".reg",
    }
    assert expected.issubset(CODE_EXTENSIONS)


# ---------------------------------------------------------------------------
# 3. IEC 61131-3 ST & PLC LASAL Extraction Tests
# ---------------------------------------------------------------------------
def test_extract_st_and_lasal_cp932(tmp_path: Path, extractor: GraphifyExtractor):
    """Verify ST/LASAL extraction for PROGRAM, FB, FUNCTION, CLASS, METHOD, STRUCT, VAR*, SERVER/CLIENT, and calls."""
    st_code = """(* 軸状態構造体 *)
TYPE ST_AxisState :
STRUCT
    bReady : BOOL;
    rPos : REAL;
END_STRUCT
END_TYPE

VAR_GLOBAL
    g_nMachineState : INT := 0;
END_VAR

FUNCTION CalcVelocity : REAL
VAR_INPUT
    rDist : REAL;
    rTime : REAL;
END_VAR
    CalcVelocity := rDist / rTime;
END_FUNCTION

(* モーター制御ブロック *)
FUNCTION_BLOCK FB_ServoAxis
VAR_INPUT
    bEnable : BOOL;
END_VAR
VAR_OUTPUT
    rActualVel : REAL;
END_VAR
    IF bEnable THEN
        rActualVel := CalcVelocity(rDist := 100.0, rTime := 2.0);
    END_IF;
END_FUNCTION_BLOCK

CLASS AxisController
    SERVER
        TargetSpeed : REAL;
        AxisReady : DINT;
    END_SERVER
    CLIENT
        ServoLink : FB_ServoAxis;
    END_CLIENT
    METHOD GLOBAL StepCycle
        ServoLink(bEnable := TRUE);
    END_METHOD
END_CLASS

PROGRAM MAIN_PLC
VAR
    fbAxis1 : FB_ServoAxis;
END_VAR
    fbAxis1(bEnable := TRUE);
END_PROGRAM
"""
    st_file = tmp_path / "axis_control.st"
    st_file.write_bytes(st_code.encode("cp932"))

    delta = extractor.extract_workspace(
        workspace_path=tmp_path,
        project_id="plc_proj",
        scoped_paths=["axis_control.st"],
        use_cache=False,
    )

    by_label = {n.label: n for n in delta.nodes}
    assert "ST_AxisState" in by_label
    assert by_label["ST_AxisState"].metadata["kind"] == "struct"
    assert [f["name"] for f in by_label["ST_AxisState"].metadata["fields"]] == ["bReady", "rPos"]

    assert "g_nMachineState" in by_label
    assert by_label["g_nMachineState"].metadata["kind"] == "variable"
    assert by_label["g_nMachineState"].metadata["var_scope"] == "VAR_GLOBAL"

    assert "CalcVelocity()" in by_label
    assert by_label["CalcVelocity()"].metadata["kind"] == "function"

    assert "FB_ServoAxis" in by_label
    assert by_label["FB_ServoAxis"].metadata["kind"] == "class"
    assert by_label["FB_ServoAxis"].metadata["st_kind"] == "function_block"
    assert "モーター制御ブロック" in by_label["FB_ServoAxis"].metadata.get("docstring", "")

    assert "bEnable" in by_label
    assert by_label["bEnable"].metadata["var_scope"] == "VAR_INPUT"
    assert "rActualVel" in by_label
    assert by_label["rActualVel"].metadata["var_scope"] == "VAR_OUTPUT"

    assert "AxisController" in by_label
    assert "TargetSpeed" in by_label
    assert by_label["TargetSpeed"].metadata["kind"] == "server"
    assert "AxisReady" in by_label
    assert by_label["AxisReady"].metadata["kind"] == "server"
    assert "ServoLink" in by_label
    assert by_label["ServoLink"].metadata["kind"] == "client"
    assert "AxisController.StepCycle()" in by_label
    assert by_label["AxisController.StepCycle()"].metadata["kind"] == "method"

    assert "MAIN_PLC" in by_label
    assert by_label["MAIN_PLC"].metadata["st_kind"] == "program"

    call_pairs = {(e.source, e.target) for e in delta.edges if e.relation == "calls"}
    assert (by_label["FB_ServoAxis"].id, by_label["CalcVelocity()"].id) in call_pairs
    assert (by_label["MAIN_PLC"].id, by_label["FB_ServoAxis"].id) in call_pairs
    assert (by_label["AxisController.StepCycle()"].id, by_label["FB_ServoAxis"].id) in call_pairs


def test_extract_lasal_xml_pou_and_scl(tmp_path: Path, extractor: GraphifyExtractor):
    """Verify .pou XML with LASAL Server/Client tags and .scl file extraction."""
    pou_xml = """<?xml version="1.0" encoding="utf-8"?>
<Class Name="SafetyMonitor" Comment="安全監視クラス">
    <Server Name="EmergencyStop" DataType="DINT" />
    <Client Name="DoorSensor" DataType="DINT" />
    <Implementation><ST><![CDATA[
FUNCTION CheckDoor : BOOL
    CheckDoor := TRUE;
END_FUNCTION
]]></ST></Implementation>
</Class>
"""
    (tmp_path / "safety.pou").write_text(pou_xml, encoding="utf-8")

    scl_code = """FUNCTION_BLOCK FB_BatchCalc
VAR_INPUT
    nMode : INT;
END_VAR
    nMode := nMode + 1;
END_FUNCTION_BLOCK
"""
    (tmp_path / "batch.scl").write_text(scl_code, encoding="utf-8")

    delta = extractor.extract_workspace(tmp_path, project_id="plc_xml", use_cache=False)
    labels = {n.label: n for n in delta.nodes}
    assert "SafetyMonitor" in labels
    assert "EmergencyStop" in labels
    assert labels["EmergencyStop"].metadata["kind"] == "server"
    assert "DoorSensor" in labels
    assert labels["DoorSensor"].metadata["kind"] == "client"
    assert "CheckDoor()" in labels
    assert "FB_BatchCalc" in labels


# ---------------------------------------------------------------------------
# 4. C/C++ Shift-JIS, #pragma pack Structs, Macros, Win32 Session 0 APIs
# ---------------------------------------------------------------------------
def test_extract_cpp_legacy_shift_jis_and_session0_apis(tmp_path: Path, extractor: GraphifyExtractor):
    """Verify C++ Shift-JIS comments, #pragma pack struct, #define, enum, prototype, and Win32 calls."""
    cpp_code = """// セッション0サービス通信ヘッダ
#include <windows.h>

#define ERR_PIPE_CONNECT 0x8001 // パイプ接続エラー
#define PIPE_BUF_SIZE 4096

// IPC電文ヘッダ
#pragma pack(push, 1)
typedef struct _IPC_PACKET {
    DWORD dwMagic;
    WORD wCmd;
    char szPayload[256];
} IPC_PACKET, *PIPC_PACKET;
#pragma pack(pop)

enum ServiceState {
    STATE_STOPPED = 0,
    STATE_RUNNING = 1
};

BOOL WINAPI InitSessionBridge(LPCWSTR lpPipeName);

// ワーカー起動関数
void RunSessionService() {
    HANDLE hPipe = CreateNamedPipeW(L"\\\\.\\pipe\\MySession0", 3, 0, 1, PIPE_BUF_SIZE, PIPE_BUF_SIZE, 0, NULL);
    HANDLE hMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, 4, 0, 4096, L"Global\\SharedState");
    SC_HANDLE hSvc = OpenServiceW(NULL, L"MySvc", 1);
    StartServiceW(hSvc, 0, NULL);
    RegisterServiceCtrlHandlerW(L"MySvc", NULL);
    SendMessageW(NULL, 0x004A, 0, 0);
    PostMessageW(NULL, 0x8001, 0, 0);
    CoCreateInstance(CLSID_NULL, NULL, 1, IID_IUnknown, NULL);
    HMODULE hDll = LoadLibraryW(L"CoreEngine.dll");
    GetProcAddress(hDll, "ProcessCommand");
    WSADATA wsa;
    WSAStartup(0x0202, &wsa);
    InitSessionBridge(L"\\\\.\\pipe\\MySession0");
}
"""
    cpp_file = tmp_path / "session_host.cpp"
    cpp_file.write_bytes(cpp_code.encode("cp932"))

    delta = extractor.extract_workspace(
        workspace_path=tmp_path,
        project_id="win32_svc",
        scoped_paths=["session_host.cpp"],
        use_cache=False,
    )

    by_label = {n.label: n for n in delta.nodes}

    # #define macros + Japanese Shift-JIS comment
    assert "ERR_PIPE_CONNECT" in by_label
    assert by_label["ERR_PIPE_CONNECT"].metadata["kind"] == "macro"
    assert by_label["ERR_PIPE_CONNECT"].metadata["value"] == "0x8001"
    assert "パイプ接続エラー" in by_label["ERR_PIPE_CONNECT"].metadata.get("docstring", "")
    assert "PIPE_BUF_SIZE" in by_label

    # #pragma pack(push, 1) typedef struct
    assert "IPC_PACKET" in by_label
    pkt = by_label["IPC_PACKET"]
    assert pkt.metadata["kind"] == "struct"
    assert pkt.metadata["pack"] == 1
    assert pkt.metadata["packed"] is True
    assert [f["name"] for f in pkt.metadata["fields"]] == ["dwMagic", "wCmd", "szPayload"]
    assert "IPC電文ヘッダ" in pkt.metadata.get("docstring", "")

    # enum
    assert "ServiceState" in by_label
    assert by_label["ServiceState"].metadata["kind"] == "enum"
    assert by_label["ServiceState"].metadata["members"] == ["STATE_STOPPED", "STATE_RUNNING"]

    # Function declaration & definition
    assert "InitSessionBridge()" in by_label
    assert "RunSessionService()" in by_label
    assert "ワーカー起動関数" in by_label["RunSessionService()"].metadata.get("docstring", "")

    # Win32 Session 0 API call edges
    expected_apis = {
        "CreateNamedPipeW",
        "CreateFileMappingW",
        "OpenServiceW",
        "StartServiceW",
        "RegisterServiceCtrlHandlerW",
        "SendMessageW",
        "PostMessageW",
        "CoCreateInstance",
        "LoadLibraryW",
        "GetProcAddress",
        "WSAStartup",
    }
    for api in expected_apis:
        assert api in by_label, f"Missing Win32 API node: {api}"
        assert by_label[api].metadata["kind"] == "win32_api"

    runner_id = by_label["RunSessionService()"].id
    runner_calls = {e.target for e in delta.edges if e.source == runner_id and e.relation == "calls"}
    for api in expected_apis:
        assert by_label[api].id in runner_calls
    assert by_label["InitSessionBridge()"].id in runner_calls


# ---------------------------------------------------------------------------
# 5. COM/IDL (.idl), DLL Exports (.def), and Win32 Resource (.rc)
# ---------------------------------------------------------------------------
def test_extract_idl_def_and_utf16_rc(tmp_path: Path, extractor: GraphifyExtractor):
    """Verify .idl COM interfaces/UUIDs/coclasses, .def exports, and UTF-16 LE .rc dialogs."""
    idl_code = """import "oaidl.idl";
[
    object,
    uuid(12345678-1234-1234-1234-1234567890AB),
    helpstring("ISessionBridge Interface")
]
interface ISessionBridge : IDispatch {
    HRESULT SendCommand([in] BSTR cmd);
};

[
    uuid(AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE),
    version(1.0)
]
library SessionBridgeLib {
    [
        uuid(11111111-2222-3333-4444-555555555555)
    ]
    coclass SessionBridge {
        [default] interface ISessionBridge;
    };
};
"""
    (tmp_path / "bridge.idl").write_text(idl_code, encoding="utf-8")

    def_code = """; Core DLL Exports
LIBRARY "CoreEngine"
EXPORTS
    InitializeEngine @1
    ProcessCommand = ImplProcessCommand @2 NONAME
"""
    (tmp_path / "core.def").write_text(def_code, encoding="cp932")

    rc_code = """#include "resource.h"

IDD_MAIN_DIALOG DIALOGEX 0, 0, 320, 200
CAPTION "サービス設定ダイアログ"
BEGIN
    DEFPUSHBUTTON "開始", IDC_BTN_START, 14, 170, 50, 14
    EDITTEXT IDC_EDIT_PIPE, 14, 20, 200, 14
END

IDR_MAIN_MENU MENU
BEGIN
    MENUITEM "終了", IDM_EXIT
END

STRINGTABLE
BEGIN
    IDS_ERR_CONN "接続失敗"
END
"""
    (tmp_path / "ui.rc").write_bytes(b"\xff\xfe" + rc_code.encode("utf-16-le"))

    delta = extractor.extract_workspace(tmp_path, project_id="com_win32", use_cache=False)
    by_label = {n.label: n for n in delta.nodes}

    # .idl assertions
    assert "ISessionBridge" in by_label
    assert by_label["ISessionBridge"].metadata["kind"] == "interface"
    assert by_label["ISessionBridge"].metadata["uuid"] == "12345678-1234-1234-1234-1234567890ab"
    assert "SendCommand()" in by_label
    assert "SessionBridgeLib" in by_label
    assert by_label["SessionBridgeLib"].metadata["kind"] == "library"
    assert "SessionBridge" in by_label
    assert by_label["SessionBridge"].metadata["kind"] == "coclass"
    assert by_label["SessionBridge"].metadata["uuid"] == "11111111-2222-3333-4444-555555555555"

    # .def assertions
    assert "CoreEngine" in by_label
    assert "InitializeEngine" in by_label
    assert by_label["InitializeEngine"].metadata["kind"] == "export"
    assert by_label["InitializeEngine"].metadata["ordinal"] == 1
    assert "ProcessCommand" in by_label
    assert by_label["ProcessCommand"].metadata["internal_name"] == "ImplProcessCommand"

    # .rc UTF-16 assertions
    assert "IDD_MAIN_DIALOG" in by_label
    assert by_label["IDD_MAIN_DIALOG"].metadata["kind"] == "dialog"
    assert by_label["IDD_MAIN_DIALOG"].metadata["caption"] == "サービス設定ダイアログ"
    assert "IDC_BTN_START" in by_label
    assert by_label["IDC_BTN_START"].metadata["kind"] == "control"
    assert "IDC_EDIT_PIPE" in by_label
    assert "IDR_MAIN_MENU" in by_label
    assert by_label["IDR_MAIN_MENU"].metadata["kind"] == "menu"
    assert "IDM_EXIT" in by_label
    assert "IDS_ERR_CONN" in by_label
    assert by_label["IDS_ERR_CONN"].metadata["value"] == "接続失敗"


# ---------------------------------------------------------------------------
# 6. VB6 (.bas), Delphi (.pas), and COBOL (.cbl)
# ---------------------------------------------------------------------------
def test_extract_vb6_delphi_cobol(tmp_path: Path, extractor: GraphifyExtractor):
    """Verify VB6 Declare/Sub, Delphi unit/class/procedure/calls, and COBOL PROGRAM-ID/PERFORM/CALL."""
    vb6_code = """Attribute VB_Name = "ModSessionIpc"
Private Declare Function FindWindowA Lib "user32" (ByVal lpClass As String, ByVal lpName As String) As Long

Public Sub PingService()
    Dim h As Long
    h = FindWindowA("SessionDlg", "Title")
End Sub
"""
    (tmp_path / "ModSessionIpc.bas").write_text(vb6_code, encoding="utf-8")

    pas_code = """unit MainServiceFrm;

interface
uses Windows, SysUtils;

type
  TMainForm = class(TForm)
    procedure BtnConnectClick(Sender: TObject);
  end;

implementation

procedure DoConnect;
begin
end;

procedure TMainForm.BtnConnectClick(Sender: TObject);
begin
  DoConnect();
end;

end.
"""
    (tmp_path / "MainServiceFrm.pas").write_text(pas_code, encoding="utf-8")

    cbl_code = """       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYROLL-BATCH.
       PROCEDURE DIVISION.
       MAIN-SECTION SECTION.
       MAIN-PARA.
           PERFORM CALC-TAX.
           CALL 'LEGACY-PRINT'.
           STOP RUN.
       CALC-TAX.
           EXIT.
"""
    (tmp_path / "payroll.cbl").write_text(cbl_code, encoding="utf-8")

    delta = extractor.extract_workspace(tmp_path, project_id="legacy_langs", use_cache=False)
    by_label = {n.label: n for n in delta.nodes}

    # VB6
    assert "ModSessionIpc" in by_label
    assert "FindWindowA()" in by_label
    assert by_label["FindWindowA()"].metadata["is_declare"] is True
    assert by_label["FindWindowA()"].metadata["dll_lib"] == "user32"
    assert "PingService()" in by_label
    assert any(
        e.source == by_label["PingService()"].id
        and e.target == by_label["FindWindowA()"].id
        and e.relation == "calls"
        for e in delta.edges
    )

    # Delphi
    assert "MainServiceFrm" in by_label
    assert by_label["MainServiceFrm"].metadata["kind"] == "unit"
    assert "TMainForm" in by_label
    assert by_label["TMainForm"].metadata["kind"] == "class"
    assert "TMainForm.BtnConnectClick()" in by_label
    assert "DoConnect()" in by_label
    assert any(
        e.source == by_label["TMainForm.BtnConnectClick()"].id
        and e.target == by_label["DoConnect()"].id
        and e.relation == "calls"
        for e in delta.edges
    )

    # COBOL
    assert "PAYROLL-BATCH" in by_label
    assert by_label["PAYROLL-BATCH"].metadata["kind"] == "program"
    assert "MAIN-SECTION SECTION" in by_label
    assert by_label["MAIN-SECTION SECTION"].metadata["kind"] == "section"
    assert "MAIN-PARA" in by_label
    assert "CALC-TAX" in by_label
    cbl_calls = {
        (e.source, e.target) for e in delta.edges if e.relation == "calls" and e.source == by_label["MAIN-PARA"].id
    }
    assert (by_label["MAIN-PARA"].id, by_label["CALC-TAX"].id) in cbl_calls
    assert (by_label["MAIN-PARA"].id, "legacy_print") in cbl_calls


# ---------------------------------------------------------------------------
# 7. Edge Cases: UTF-16 C++/REG, Multi-File Dedup, SQL/BAT, Shift-JIS Markdown
# ---------------------------------------------------------------------------
def test_extract_utf16_cpp_inl_reg_sql_bat_and_shiftjis_doc(tmp_path: Path, extractor: GraphifyExtractor):
    """Verify UTF-16 C++/.reg, .inl, .exp, .sql, .bat, Shift-JIS .md, and cross-file Win32 node dedup."""
    # Two C++ files calling the same Win32 API -> external Win32 node must be deduplicated
    (tmp_path / "svc_a.cpp").write_text(
        "void StartA() {\n    CreateNamedPipeW(0, 0, 0, 0, 0, 0, 0, 0);\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "svc_b.inl").write_bytes(
        (
            "class HelperB {\n"
            "public:\n"
            "    void StartB() {\n"
            "        CreateNamedPipeW(0, 0, 0, 0, 0, 0, 0, 0);\n"
            "    }\n"
            "};\n"
        ).encode("utf-16-le")
    )

    # UTF-16 LE with BOM .reg
    reg_content = (
        "Windows Registry Editor Version 5.00\r\n\r\n"
        "[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\MySessionSvc]\r\n"
        '"Start"=dword:00000002\r\n'
    )
    (tmp_path / "service.reg").write_bytes(b"\xff\xfe" + reg_content.encode("utf-16-le"))

    # .exp ST export
    (tmp_path / "pump.exp").write_text(
        "PROGRAM PumpMain\nVAR_GLOBAL\n    g_bPumpRun : BOOL;\nEND_VAR\nEND_PROGRAM\n",
        encoding="utf-8",
    )

    # .sql and .bat
    (tmp_path / "schema.sql").write_text("CREATE TABLE ipc_audit (id INT PRIMARY KEY);\n", encoding="utf-8")
    (tmp_path / "install_svc.bat").write_text(":install_step\ncall :verify_step\n:verify_step\n", encoding="utf-8")

    # Shift-JIS Markdown doc
    (tmp_path / "design.md").write_bytes("# 設計書\n`IPC_PACKET` を使用します。\n".encode("cp932"))

    delta = extractor.extract_workspace(tmp_path, project_id="edge_proj", use_cache=False)

    node_ids = [n.id for n in delta.nodes]
    # No duplicate node IDs across workspace
    assert len(node_ids) == len(set(node_ids))

    win32_pipe_nodes = [n for n in delta.nodes if n.label == "CreateNamedPipeW"]
    assert len(win32_pipe_nodes) == 1

    by_label = {n.label: n for n in delta.nodes}
    assert "HelperB" in by_label
    assert "StartB()" in by_label
    assert "HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\MySessionSvc" in by_label
    assert "PumpMain" in by_label
    assert "ipc_audit" in by_label
    assert "install_step" in by_label
    assert any("設計書" in n.label for n in delta.nodes if n.file_type == "document")

