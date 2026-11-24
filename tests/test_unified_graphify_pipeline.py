"""Tests for the unified Graphify extraction, staging, registry, clustering, and analysis pipeline.

Covers three pillars:
1. UTF-8 staging for UTF-16 LE (with/without BOM) and CP932/Shift-JIS C++ files
   through `graphify.extract` Tree-sitter + `_parse_cpp_legacy` augmentation.
2. `ExtractorRegistry` + optional `LanguageConfig` runtime probe vs
   `LegacyStructural` fallback.
3. `extract_workspace()` batch `graphify.extract` pass-2 cross-file resolution
   and unified `graphify.cluster` + `graphify.analyze` across mixed
   C++/ST/IDL/RC/DEF/Python workspaces.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from projectbrain.graphify_engine import extractor_registry as registry_mod
from projectbrain.graphify_engine.extractor import GraphifyExtractor


@pytest.fixture
def extractor(tmp_path: Path) -> GraphifyExtractor:
    """Provide an isolated GraphifyExtractor backed by a temporary cache dir."""
    return GraphifyExtractor(cache_dir=tmp_path / ".projectbrain" / "cache")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_cp932_cpp(path: Path) -> None:
    cpp_code = (
        "// セッション0通信エンジン (CP932 Shift-JIS)\n"
        "#include <windows.h>\n"
        "#define ERR_SESSION_TIMEOUT 0x8002 // タイムアウトエラー\n"
        "\n"
        "#pragma pack(push, 1)\n"
        "typedef struct _IPC_HEADER {\n"
        "    DWORD dwMagic;\n"
        "    WORD wCmd;\n"
        "} IPC_HEADER, *PIPC_HEADER;\n"
        "#pragma pack(pop)\n"
        "\n"
        "enum BridgeMode {\n"
        "    MODE_IDLE = 0,\n"
        "    MODE_ACTIVE = 1\n"
        "};\n"
        "\n"
        "class SessionBridge {\n"
        "public:\n"
        "    void DispatchPipe() {\n"
        "        CreateNamedPipeW(0, 0, 0, 0, 0, 0, 0, 0);\n"
        "        CreateFileMappingW(0, 0, 0, 0, 0, 0);\n"
        "    }\n"
        "};\n"
        "\n"
        "// エントリポイント\n"
        "void StartSessionHost() {\n"
        "    SessionBridge bridge;\n"
        "    bridge.DispatchPipe();\n"
        "}\n"
    )
    path.write_bytes(cpp_code.encode("cp932"))


def _write_utf16_le_cpp(path: Path, with_bom: bool = True) -> None:
    cpp_code = (
        "// 共有メモリワーカー (UTF-16 LE)\r\n"
        "#define MAX_IPC_SLOTS 16 // 最大スロット数\r\n"
        "\r\n"
        "#pragma pack(push, 1)\r\n"
        "typedef struct _SLOT_RECORD {\r\n"
        "    DWORD dwSlotId;\r\n"
        "    DWORD dwFlags;\r\n"
        "} SLOT_RECORD;\r\n"
        "#pragma pack(pop)\r\n"
        "\r\n"
        "class SharedMemoryWorker {\r\n"
        "public:\r\n"
        "    void MapSharedRegion() {\r\n"
        "        CreateFileMappingW(0, 0, 0, 0, 0, 0);\n"
        "        MapViewOfFile(0, 0, 0, 0, 0);\n"
        "    }\r\n"
        "};\r\n"
        "\r\n"
        "void RunMemoryWorker() {\r\n"
        "    SharedMemoryWorker worker;\r\n"
        "    worker.MapSharedRegion();\r\n"
        "}\r\n"
    )
    raw = cpp_code.encode("utf-16-le")
    if with_bom:
        raw = b"\xff\xfe" + raw
    path.write_bytes(raw)


# ---------------------------------------------------------------------------
# Pillar 1: UTF-8 Staging for CP932 & UTF-16 LE C++ through Tree-sitter + Legacy
# ---------------------------------------------------------------------------
def test_cp932_cpp_utf8_staging_tree_sitter_and_legacy_augmentation(
    tmp_path: Path, extractor: GraphifyExtractor
):
    """CP932 C++ files are staged as UTF-8 for graphify Tree-sitter and augmented by _parse_cpp_legacy."""
    import graphify.extract as g_extract

    cpp_path = tmp_path / "session_cp932.cpp"
    _write_cp932_cpp(cpp_path)

    observed_extract_calls: List[List[str]] = []
    observed_staged_contents: List[str] = []
    real_extract = g_extract.extract

    def spy_extract(paths: List[Path]) -> Dict[str, Any]:
        observed_extract_calls.append([str(p) for p in paths])
        for p in paths:
            if p.suffix.lower() in (".cpp", ".c", ".h", ".hpp"):
                # Staged file passed to graphify Tree-sitter MUST be valid UTF-8
                observed_staged_contents.append(Path(p).read_text(encoding="utf-8", errors="strict"))
        return real_extract(paths)

    with patch("graphify.extract.extract", side_effect=spy_extract):
        delta = extractor.extract_workspace(
            workspace_path=tmp_path,
            project_id="proj_utf8_stage",
            scoped_paths=["session_cp932.cpp"],
            use_cache=False,
        )

    assert len(observed_extract_calls) >= 1, "graphify.extract.extract must be invoked for CP932 C++ files"
    assert observed_staged_contents, "Expected UTF-8 staged C++ file readable with strict UTF-8"
    assert "セッション0通信エンジン" in observed_staged_contents[0]
    assert "\ufffd" not in observed_staged_contents[0]

    by_label = {n.label: n for n in delta.nodes}
    # Tree-sitter + legacy reconciled symbols
    assert "SessionBridge" in by_label
    assert "StartSessionHost()" in by_label
    # Legacy augmentation symbols
    assert "ERR_SESSION_TIMEOUT" in by_label
    assert "タイムアウトエラー" in by_label["ERR_SESSION_TIMEOUT"].metadata.get("docstring", "")
    assert "IPC_HEADER" in by_label
    assert by_label["IPC_HEADER"].metadata.get("packed") is True
    assert "BridgeMode" in by_label
    assert "CreateNamedPipeW" in by_label
    assert "CreateFileMappingW" in by_label

    # Every node/edge source_file must point back to the workspace relative path, never a temp staging path
    for n in delta.nodes:
        if n.source_file:
            assert n.source_file == "session_cp932.cpp"
    for e in delta.edges:
        if e.source_file:
            assert e.source_file == "session_cp932.cpp"

    # No duplicate node IDs
    node_ids = [n.id for n in delta.nodes]
    assert len(node_ids) == len(set(node_ids))


@pytest.mark.parametrize("with_bom", [True, False], ids=["utf16_le_bom", "utf16_le_no_bom"])
def test_utf16_le_cpp_utf8_staging_tree_sitter_and_legacy(
    tmp_path: Path, extractor: GraphifyExtractor, with_bom: bool
):
    """UTF-16 LE C++ files (BOM and BOM-less) are staged as UTF-8 for Tree-sitter + _parse_cpp_legacy."""
    import graphify.extract as g_extract

    cpp_path = tmp_path / "worker_utf16.cpp"
    _write_utf16_le_cpp(cpp_path, with_bom=with_bom)

    staged_utf8_texts: List[str] = []
    real_extract = g_extract.extract

    def spy_extract(paths: List[Path]) -> Dict[str, Any]:
        for p in paths:
            if p.suffix.lower() == ".cpp":
                staged_utf8_texts.append(Path(p).read_text(encoding="utf-8", errors="strict"))
        return real_extract(paths)

    with patch("graphify.extract.extract", side_effect=spy_extract):
        delta = extractor.extract_workspace(
            workspace_path=tmp_path,
            project_id="proj_utf16",
            scoped_paths=["worker_utf16.cpp"],
            use_cache=False,
        )

    assert staged_utf8_texts, "UTF-16 LE C++ file must be staged to UTF-8 and routed through graphify.extract"
    assert "共有メモリワーカー" in staged_utf8_texts[0]
    assert "\x00" not in staged_utf8_texts[0]

    by_label = {n.label: n for n in delta.nodes}
    assert "SharedMemoryWorker" in by_label
    assert "RunMemoryWorker()" in by_label
    assert "MAX_IPC_SLOTS" in by_label
    assert "SLOT_RECORD" in by_label
    assert by_label["SLOT_RECORD"].metadata.get("packed") is True
    assert "CreateFileMappingW" in by_label
    assert "MapViewOfFile" in by_label

    for n in delta.nodes:
        if n.source_file:
            assert n.source_file == "worker_utf16.cpp"
            assert n.metadata.get("encoding") in (None, "utf-16-le", "utf-16")


# ---------------------------------------------------------------------------
# Pillar 2: ExtractorRegistry + LanguageConfig Probe vs LegacyStructural Fallback
# ---------------------------------------------------------------------------
def test_extractor_registry_runtime_probe_and_legacy_fallback():
    """ExtractorRegistry selects LanguageConfig when tree-sitter grammar probes true and falls back to LegacyStructural."""
    registry_mod = pytest.importorskip(
        "projectbrain.graphify_engine.extractor_registry",
        reason="Wave U1 extractor_registry.py not landed yet",
    )

    ExtractorRegistry = getattr(registry_mod, "ExtractorRegistry")
    registry = ExtractorRegistry()

    # Built-in Tree-sitter language (C++ / Python) must probe as available
    cpp_strategy = registry.resolve(".cpp")
    assert cpp_strategy is not None

    # Uninstalled optional grammar (e.g. Pascal / Delphi .pas or COBOL .cbl) must fall back to LegacyStructural
    pas_strategy = registry.resolve(".pas")
    assert pas_strategy is not None
    assert "legacy" in type(pas_strategy).__name__.lower() or getattr(pas_strategy, "is_legacy", False)

    # Simulate an optional LanguageConfig probe succeeding at runtime (e.g. fake tree_sitter_sql)
    fake_ts_sql = types.ModuleType("tree_sitter_sql")
    fake_ts_sql.language = lambda: None  # pragma: no cover

    with patch.dict(sys.modules, {"tree_sitter_sql": fake_ts_sql}):
        with patch.object(importlib.util, "find_spec", return_value=object()):
            probe_ok = registry.probe_language_config("tree_sitter_sql")
            assert probe_ok is True

    # When the optional grammar cannot be imported, probe returns False and fallback stays active
    with patch("importlib.import_module", side_effect=ImportError("no tree_sitter_sql")):
        with patch.object(importlib.util, "find_spec", return_value=None):
            probe_fail = registry.probe_language_config("tree_sitter_sql")
            assert probe_fail is False


def test_extractor_registry_extracts_delphi_and_sql_via_legacy_fallback(
    tmp_path: Path, extractor: GraphifyExtractor
):
    """When optional tree-sitter grammars for .pas/.sql are missing, extract_workspace uses LegacyStructural fallback."""
    (tmp_path / "MainFrm.pas").write_text(
        "unit MainFrm;\ninterface\ntype\n  TMainFrm = class(TForm)\n  end;\nimplementation\n"
        "procedure InitEngine;\nbegin\nend;\nend.\n",
        encoding="utf-8",
    )
    (tmp_path / "audit.sql").write_text(
        "CREATE TABLE session_events (id INT PRIMARY KEY, payload TEXT);\n",
        encoding="utf-8",
    )

    delta = extractor.extract_workspace(
        workspace_path=tmp_path,
        project_id="proj_fallback",
        use_cache=False,
    )
    by_label = {n.label: n for n in delta.nodes}
    assert "MainFrm" in by_label
    assert "TMainFrm" in by_label
    assert "InitEngine()" in by_label
    assert "session_events" in by_label


# ---------------------------------------------------------------------------
# Pillar 3: Batch graphify.extract Pass-2 + Unified graphify.cluster & analyze
# ---------------------------------------------------------------------------
def test_extract_workspace_batch_graphify_pass2_cross_file_resolution(
    tmp_path: Path, extractor: GraphifyExtractor
):
    """extract_workspace batches Tree-sitter files into graphify.extract for pass-2 cross-file INFERRED edges."""
    import graphify.extract as g_extract

    pkg = tmp_path / "svc"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "models.py").write_text(
        "class Response:\n"
        "    def ok(self) -> bool:\n"
        "        return True\n",
        encoding="utf-8",
    )
    (pkg / "auth.py").write_text(
        "from .models import Response\n\n"
        "class DigestAuth:\n"
        "    def handle(self) -> Response:\n"
        "        return Response()\n",
        encoding="utf-8",
    )

    batch_sizes: List[int] = []
    real_extract = g_extract.extract

    def spy_extract(paths: List[Path]) -> Dict[str, Any]:
        batch_sizes.append(len(paths))
        return real_extract(paths)

    with patch("graphify.extract.extract", side_effect=spy_extract):
        delta = extractor.extract_workspace(
            workspace_path=tmp_path,
            project_id="proj_pass2",
            use_cache=False,
        )

    assert any(sz >= 2 for sz in batch_sizes), (
        f"Expected batch graphify.extract call with >=2 files for pass-2 resolution, got {batch_sizes}"
    )

    by_label = {n.label: n for n in delta.nodes}
    assert "DigestAuth" in by_label
    assert "Response" in by_label

    # Pass-2 cross-file resolution emits INFERRED `uses` edge from DigestAuth -> Response
    inferred_uses = [
        e
        for e in delta.edges
        if e.source == by_label["DigestAuth"].id
        and e.target == by_label["Response"].id
        and e.relation == "uses"
        and e.confidence == "INFERRED"
    ]
    assert inferred_uses, "Expected pass-2 cross-file INFERRED `uses` edge DigestAuth -> Response"


def test_mixed_cpp_st_idl_rc_workspace_cluster_and_analyze(
    tmp_path: Path, extractor: GraphifyExtractor
):
    """Mixed C++ (CP932 & UTF-16 LE), ST (CP932), IDL, RC (UTF-16 LE), and DEF workspace clusters and analyzes cleanly."""
    # 1. C++ CP932 host service
    _write_cp932_cpp(tmp_path / "service_host.cpp")

    # 2. C++ UTF-16 LE worker sharing CreateFileMappingW Win32 API hub
    _write_utf16_le_cpp(tmp_path / "shared_worker.cpp", with_bom=True)

    # 3. IEC 61131-3 / LASAL ST (CP932)
    st_code = (
        "(* モーター制御 *)\n"
        "FUNCTION CalcTorque : REAL\n"
        "VAR_INPUT\n"
        "    rCurrent : REAL;\n"
        "END_VAR\n"
        "    CalcTorque := rCurrent * 2.5;\n"
        "END_FUNCTION\n"
        "\n"
        "FUNCTION_BLOCK FB_DriveAxis\n"
        "    CalcTorque(rCurrent := 1.0);\n"
        "END_FUNCTION_BLOCK\n"
        "\n"
        "PROGRAM MAIN_PLC\n"
        "VAR\n"
        "    fbDrive : FB_DriveAxis;\n"
        "END_VAR\n"
        "    fbDrive();\n"
        "END_PROGRAM\n"
    )
    (tmp_path / "drive_axis.st").write_bytes(st_code.encode("cp932"))

    # 4. COM/IDL
    idl_code = (
        'import "oaidl.idl";\n'
        '[object, uuid(12345678-1234-1234-1234-1234567890AB)]\n'
        "interface ISessionControl : IDispatch {\n"
        "    HRESULT DispatchPipe([in] BSTR pipeName);\n"
        "};\n"
        '[uuid(AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE)]\n'
        "library SessionControlLib {\n"
        '    [uuid(11111111-2222-3333-4444-555555555555)]\n'
        "    coclass SessionControl {\n"
        "        [default] interface ISessionControl;\n"
        "    };\n"
        "};\n"
    )
    (tmp_path / "session_ctrl.idl").write_text(idl_code, encoding="utf-8")

    # 5. Win32 RC (UTF-16 LE with BOM)
    rc_code = (
        '#include "resource.h"\r\n'
        "IDD_SESSION_DLG DIALOGEX 0, 0, 320, 200\r\n"
        'CAPTION "セッション監視"\r\n'
        "BEGIN\r\n"
        '    DEFPUSHBUTTON "開始", IDC_BTN_RUN, 10, 10, 50, 14\r\n'
        "END\r\n"
    )
    (tmp_path / "session_ui.rc").write_bytes(b"\xff\xfe" + rc_code.encode("utf-16-le"))

    # 6. DEF exports
    (tmp_path / "session_core.def").write_text(
        'LIBRARY "SessionCore"\nEXPORTS\n    StartSessionHost @1\n',
        encoding="utf-8",
    )

    # Extract full mixed workspace
    delta = extractor.extract_workspace(
        workspace_path=tmp_path,
        project_id="proj_mixed_legacy",
        use_cache=False,
    )

    node_ids = [n.id for n in delta.nodes]
    assert len(node_ids) == len(set(node_ids)), "Node IDs must be unique across mixed workspace"

    by_label = {n.label: n for n in delta.nodes}
    assert "SessionBridge" in by_label
    assert "SharedMemoryWorker" in by_label
    assert "FB_DriveAxis" in by_label
    assert "MAIN_PLC" in by_label
    assert "ISessionControl" in by_label
    assert "IDD_SESSION_DLG" in by_label
    assert "SessionCore" in by_label
    # Shared Win32 API hub deduplicated across service_host.cpp and shared_worker.cpp
    assert len([n for n in delta.nodes if n.label == "CreateFileMappingW"]) == 1

    # Cluster unified graph using graphify.cluster
    clustered = extractor.cluster_graph(delta, device="cpu")
    communities = {n.community for n in clustered.nodes if n.community is not None}
    assert len(communities) >= 2, "Mixed multi-language workspace should partition into multiple communities"
    for n in clustered.nodes:
        if n.community is not None:
            assert "cohesion_score" in n.metadata
            assert 0.0 <= float(n.metadata["cohesion_score"]) <= 1.0

    # Analyze god nodes and cross-file/cross-community bridges using graphify.analyze
    analysis = extractor.compute_god_nodes_and_bridges(clustered, top_k=5, device="cpu")
    assert analysis["total_nodes"] == len(clustered.nodes)
    assert analysis["total_edges"] == len(clustered.edges)
    assert len(analysis["god_nodes"]) >= 1
    assert len(analysis["communities"]) >= 2
    assert isinstance(analysis["surprising_connections"], list)

    # God nodes must exclude file-level hub nodes
    file_names = {
        "service_host.cpp",
        "shared_worker.cpp",
        "drive_axis.st",
        "session_ctrl.idl",
        "session_ui.rc",
        "session_core.def",
    }
    for g in analysis["god_nodes"]:
        assert g.get("label") not in file_names


# ---------------------------------------------------------------------------
# Pillar 4: Fallback, Optional LanguageConfig, Cleanup, Concurrency & Security
# ---------------------------------------------------------------------------
def test_batch_failure_falls_back_to_python_ast_and_cpp_legacy(
    tmp_path: Path, extractor: GraphifyExtractor
):
    """When batch graphify.extract raises, extract_workspace falls back to remaining_tasks, _python_ast_fallback, and _parse_cpp_legacy."""
    (tmp_path / "worker.py").write_text(
        "class FallbackWorker:\n"
        "    def run_task(self) -> int:\n"
        "        return 42\n",
        encoding="utf-8",
    )
    _write_cp932_cpp(tmp_path / "legacy_bridge.cpp")

    with patch("graphify.extract.extract", side_effect=RuntimeError("batch tree-sitter failure")) as mock_extract:
        delta = extractor.extract_workspace(
            workspace_path=tmp_path,
            project_id="proj_batch_fail",
            use_cache=False,
        )

    assert mock_extract.call_count >= 2, "Expected batch attempt plus per-file remaining_tasks fallback attempts"
    by_label = {n.label: n for n in delta.nodes}
    assert "FallbackWorker" in by_label
    assert "run_task()" in by_label
    assert "ERR_SESSION_TIMEOUT" in by_label
    assert "IPC_HEADER" in by_label
    assert "CreateNamedPipeW" in by_label


def test_optional_language_config_extraction_and_root_only_or_error_fallback(tmp_path: Path):
    """Optional LanguageConfig succeeds when symbols > 1 node and falls back to legacy_structural on root-only/error."""
    pas_file = tmp_path / "MainFrm.pas"
    pas_code = (
        "unit MainFrm;\ninterface\ntype\n  TMainFrm = class(TForm)\n  end;\nimplementation\n"
        "procedure InitEngine;\nbegin\nend;\nend.\n"
    )
    pas_file.write_text(pas_code, encoding="utf-8")

    # .pou must be excluded from optional tree_sitter_structured_text specs
    st_specs = registry_mod._build_default_optional_ts_specs()
    for exts, spec in st_specs:
        if spec.get("ts_module") == "tree_sitter_structured_text":
            assert ".pou" not in exts

    fake_ts_pascal = types.ModuleType("tree_sitter_pascal")
    fake_ts_pascal.language = lambda: None  # pragma: no cover

    reg = registry_mod.ExtractorRegistry.create_default(auto_probe_optional_ts=False)
    with patch.dict(sys.modules, {"tree_sitter_pascal": fake_ts_pascal}):
        with patch.object(importlib.util, "find_spec", return_value=object()):
            assert reg.register_optional_treesitter_language(
                ".pas",
                {
                    "ts_module": "tree_sitter_pascal",
                    "ts_language_fn": "language",
                    "class_types": frozenset({"declClass"}),
                    "function_types": frozenset({"defProc"}),
                },
            )

    # 1. Successful optional TS extraction with symbols beyond root file node (len(nodes) > 1)
    rich_payload = {
        "nodes": [
            {"id": "file_mainfrm", "label": "MainFrm.pas", "source_file": str(pas_file)},
            {"id": "cls_tmainfrm_ts", "label": "TMainFrmTS", "source_file": str(pas_file)},
        ],
        "edges": [
            {"source": "file_mainfrm", "target": "cls_tmainfrm_ts", "relation": "contains"},
        ],
    }
    with patch("graphify.extract._extract_generic", return_value=rich_payload):
        delta_ts = reg.extract_code(pas_file, "MainFrm.pas", "proj_opt", "main")
    labels_ts = {n.label for n in delta_ts.nodes}
    assert "TMainFrmTS" in labels_ts
    assert "InitEngine()" not in labels_ts

    # 2. Root-only emission (len(nodes) == 1, len(edges) == 0) must fall back to legacy_structural
    root_only_payload = {
        "nodes": [{"id": "file_mainfrm", "label": "MainFrm.pas", "source_file": str(pas_file)}],
        "edges": [],
    }
    with patch("graphify.extract._extract_generic", return_value=root_only_payload):
        delta_root_fb = reg.extract_code(pas_file, "MainFrm.pas", "proj_opt", "main")
    labels_root_fb = {n.label for n in delta_root_fb.nodes}
    assert "TMainFrm" in labels_root_fb
    assert "InitEngine()" in labels_root_fb

    # 3. Error dict or exception must fall back to legacy_structural
    with patch("graphify.extract._extract_generic", return_value={"nodes": [], "edges": [], "error": "grammar error"}):
        delta_err_fb = reg.extract_code(pas_file, "MainFrm.pas", "proj_opt", "main")
    assert "TMainFrm" in {n.label for n in delta_err_fb.nodes}

    with patch("graphify.extract._extract_generic", side_effect=RuntimeError("ts crash")):
        delta_exc_fb = reg.extract_code(pas_file, "MainFrm.pas", "proj_opt", "main")
    assert "TMainFrm" in {n.label for n in delta_exc_fb.nodes}


def test_temp_staging_cleanup_on_success_and_failure(tmp_path: Path):
    """Temporary _utf8_stage_*, pb_utf8_*, and pb_batch_* directories are always cleaned up on success and failure."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_cp932_cpp(ws / "bridge.cpp")
    (ws / "UPPER.PY").write_text("class UpperMod:\n    pass\n", encoding="utf-8")

    created_dirs: List[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args: Any, **kwargs: Any) -> str:
        res = real_mkdtemp(*args, **kwargs)
        created_dirs.append(Path(res))
        return res

    ext = GraphifyExtractor(cache_dir=tmp_path / "cache")

    # 1. Success path in extract_workspace
    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        ext.extract_workspace(ws, project_id="proj_clean_ok", use_cache=False)

    assert created_dirs, "Expected temporary staging directories to be created"
    assert not (ws / ".projectbrain").exists(), "Auto-created .projectbrain dir should be removed when empty"
    for d in created_dirs:
        assert not d.exists(), f"Leaked staging dir on success: {d}"

    # 2. Failure path in extract_workspace (graphify.extract.extract raises)
    created_dirs.clear()
    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        with patch("graphify.extract.extract", side_effect=RuntimeError("boom")):
            ext.extract_workspace(ws, project_id="proj_clean_fail", use_cache=False)

    assert created_dirs
    assert not (ws / ".projectbrain").exists()
    for d in created_dirs:
        assert not d.exists(), f"Leaked staging dir on failure: {d}"

    # 3. Direct extract_graphify_ts_batch with staging_dir=None creates and cleans pb_batch_*
    created_dirs.clear()
    reg = registry_mod.ExtractorRegistry.create_default(auto_probe_optional_ts=False)
    items = [(0, ws / "bridge.cpp", "bridge.cpp"), (1, ws / "UPPER.PY", "UPPER.PY")]
    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        res_ok = reg.extract_graphify_ts_batch(items, "proj_batch_dir", "main", staging_dir=None)
    assert len(res_ok) == 2
    assert any(d.name.startswith("pb_batch_") for d in created_dirs)
    assert any(d.name.startswith("pb_utf8_") for d in created_dirs)
    for d in created_dirs:
        assert not d.exists(), f"Leaked batch staging dir on success: {d}"

    created_dirs.clear()
    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        with patch("graphify.extract.extract", side_effect=RuntimeError("batch fail")):
            res_fail = reg.extract_graphify_ts_batch(items, "proj_batch_dir", "main", staging_dir=None)
    assert res_fail == {}
    for d in created_dirs:
        assert not d.exists(), f"Leaked batch staging dir on failure: {d}"

    # 4. Write error inside staged_utf8_file cleans up pb_utf8_* before re-raising
    created_dirs.clear()
    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                registry_mod.staged_utf8_file(ws / "bridge.cpp", "int x;", "cp932")
    assert created_dirs
    for d in created_dirs:
        assert not d.exists(), f"Leaked pb_utf8_* dir on write_text error: {d}"


def test_concurrent_threadpool_extraction_workers_2(
    tmp_path: Path, extractor: GraphifyExtractor
):
    """Concurrent ThreadPoolExecutor extraction (workers=2 with >= 4 tasks) runs cleanly without races."""
    (tmp_path / "plc_axis.st").write_text(
        "PROGRAM PLC_Main\nVAR x : INT; END_VAR\nEND_PROGRAM\n",
        encoding="utf-8",
    )
    (tmp_path / "ctrl.idl").write_text(
        "interface IConcurrentCtrl : IDispatch { HRESULT Ping(); };\n",
        encoding="utf-8",
    )
    (tmp_path / "form.pas").write_text(
        "unit FormMod;\ninterface\ntype\n  TFormMod = class(TObject)\n  end;\nimplementation\nend.\n",
        encoding="utf-8",
    )
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE concurrent_audit (id INT PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (tmp_path / "exports.def").write_text(
        'LIBRARY "ConcurrentCore"\nEXPORTS\n    RunCheck @1\n',
        encoding="utf-8",
    )

    delta = extractor.extract_workspace(
        workspace_path=tmp_path,
        project_id="proj_concurrent",
        workers=2,
        use_cache=False,
    )

    by_label = {n.label: n for n in delta.nodes}
    assert "PLC_Main" in by_label
    assert "IConcurrentCtrl" in by_label
    assert "TFormMod" in by_label
    assert "concurrent_audit" in by_label
    assert "ConcurrentCore" in by_label
    node_ids = [n.id for n in delta.nodes]
    assert len(node_ids) == len(set(node_ids))

    # Also exercise concurrent extract_code calls across >= 4 Tree-sitter / UTF-8-staged files
    _write_cp932_cpp(tmp_path / "c1.cpp")
    _write_utf16_le_cpp(tmp_path / "c2.cpp", with_bom=True)
    (tmp_path / "p1.py").write_text("class Alpha:\n    pass\n", encoding="utf-8")
    (tmp_path / "p2.py").write_text("class Beta:\n    pass\n", encoding="utf-8")

    reg = registry_mod.ExtractorRegistry.create_default(auto_probe_optional_ts=False)
    tasks = [
        (tmp_path / "c1.cpp", "c1.cpp"),
        (tmp_path / "c2.cpp", "c2.cpp"),
        (tmp_path / "p1.py", "p1.py"),
        (tmp_path / "p2.py", "p2.py"),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        deltas = list(
            pool.map(
                lambda t: reg.extract_code(t[0], t[1], "proj_conc_code", "main"),
                tasks,
            )
        )
    all_labels = {n.label for d in deltas for n in d.nodes}
    assert {"SessionBridge", "SharedMemoryWorker", "Alpha", "Beta"} <= all_labels


def test_security_probe_and_register_rejects_unsafe_module_fn_and_keys():
    """probe_language_config and register_optional_treesitter_language reject arbitrary modules, fns, and callable keys."""
    reg = registry_mod.ExtractorRegistry.create_default(auto_probe_optional_ts=False)

    with patch("importlib.import_module") as mock_import, patch.object(importlib.util, "find_spec") as mock_spec:
        # Arbitrary modules & dotted paths rejected before find_spec / import_module
        for bad_mod in ("os", "subprocess", "os.path", "tree_sitter_sql.evil", "../evil", ""):
            assert reg.probe_language_config(bad_mod) is False
            assert reg.probe_language_config({"ts_module": bad_mod}) is False
            assert reg.register_optional_treesitter_language(".sql", {"ts_module": bad_mod}) is False

        # Unsafe ts_language_fn rejected
        assert reg.probe_language_config("tree_sitter_sql", ts_language_fn="system") is False
        assert reg.probe_language_config({"ts_module": "tree_sitter_sql", "ts_language_fn": "system"}) is False
        assert reg.register_optional_treesitter_language(
            ".sql",
            {"ts_module": "tree_sitter_sql", "ts_language_fn": "system"},
        ) is False

        # Disallowed dict keys (hook callables) rejected
        for bad_key in ("import_handler", "resolve_function_name_fn", "extra_walk_fn", "unexpected"):
            bad_cfg = {"ts_module": "tree_sitter_sql", bad_key: lambda *a, **kw: None}
            assert reg.probe_language_config(bad_cfg) is False
            assert reg.register_optional_treesitter_language(".sql", bad_cfg) is False

        mock_spec.assert_not_called()
        mock_import.assert_not_called()


def test_security_extract_workspace_rejects_projectbrain_symlink(tmp_path: Path):
    """extract_workspace refuses to write staging dirs into a symlinked .projectbrain directory."""
    ws = tmp_path / "ws"
    ws.mkdir()
    evil_target = tmp_path / "evil_outside"
    evil_target.mkdir()
    (ws / ".projectbrain").symlink_to(evil_target)

    _write_cp932_cpp(ws / "service.cpp")

    ext = GraphifyExtractor(cache_dir=tmp_path / "isolated_cache")
    delta = ext.extract_workspace(ws, project_id="proj_symlink", use_cache=False)

    assert list(evil_target.iterdir()) == [], "Must not write staging dirs through .projectbrain symlink"
    assert (ws / ".projectbrain").is_symlink()
    assert "SessionBridge" in {n.label for n in delta.nodes}


def test_security_staged_utf8_file_path_guards_and_max_file_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """staged_utf8_file blocks empty/dot/traversal names and MAX_SOURCE_FILE_BYTES guards oversized files."""
    for bad_name in ("", ".", "..", "/", "sub\\evil.cpp"):
        with pytest.raises(ValueError):
            registry_mod.staged_utf8_file(bad_name, "int x;", "cp932")
    with pytest.raises(ValueError):
        registry_mod.staged_utf8_file("", "int x;", "utf-8")

    # Oversized file guard in extract_code & extract_graphify_ts_batch
    big_cpp = tmp_path / "oversized.cpp"
    big_cpp.write_text("int big_symbol = 1;\n" * 10, encoding="utf-8")
    monkeypatch.setattr(registry_mod, "MAX_SOURCE_FILE_BYTES", 32)

    reg = registry_mod.ExtractorRegistry.create_default(auto_probe_optional_ts=False)
    single_delta = reg.extract_code(big_cpp, "oversized.cpp", "proj_max", "main")
    assert single_delta.nodes == []
    assert single_delta.edges == []

    batch_deltas = reg.extract_graphify_ts_batch([(0, big_cpp, "oversized.cpp")], "proj_max", "main")
    assert 0 in batch_deltas
    assert batch_deltas[0].nodes == []
    assert batch_deltas[0].edges == []


def test_uppercase_extension_utf8_staging_and_encoding_normalization(tmp_path: Path):
    """Uppercase extensions stage cleanly to lowercase without sentinel strings, and encoding metadata normalizes case/underscores."""
    upper_cpp = tmp_path / "UPPER_TEST.CPP"
    upper_cpp.write_text("class UpperClass {};\n", encoding="utf-8")

    staged_path, is_staged = registry_mod.staged_utf8_file(
        upper_cpp, "class UpperClass {};\n", "UTF-8", staging_dir=tmp_path / "stage"
    )
    try:
        assert is_staged is True
        assert staged_path.name == "UPPER_TEST.cpp"
        assert staged_path.suffix == ".cpp"
        assert oct(staged_path.parent.stat().st_mode & 0o777) == "0o700"
    finally:
        registry_mod.cleanup_staged_file(staged_path, is_staged)

    # _convert_raw_graphify_payload normalizes UTF-8 variants so metadata["encoding"] isn't set
    from projectbrain.graphify_engine.models import GraphEdge, GraphNode

    for utf8_variant in ("UTF-8", "utf_8", "UTF_8_SIG", "ASCII"):
        nodes, _ = registry_mod._convert_raw_graphify_payload(
            {"nodes": [{"id": "n1", "label": "Foo"}], "edges": []},
            rel_path="Foo.cpp",
            project_id="p1",
            graph_node_cls=GraphNode,
            graph_edge_cls=GraphEdge,
            detected_encoding=utf8_variant,
        )
        assert "encoding" not in nodes[0].metadata

    nodes_cp932, _ = registry_mod._convert_raw_graphify_payload(
        {"nodes": [{"id": "n1", "label": "Foo"}], "edges": []},
        rel_path="Foo.cpp",
        project_id="p1",
        graph_node_cls=GraphNode,
        graph_edge_cls=GraphEdge,
        detected_encoding="cp932",
    )
    assert nodes_cp932[0].metadata.get("encoding") == "cp932"
