"""Zero-Touch External Agent Hub Bootstrap & Workspace Auto-Discovery.

Discovers multi-source repository topologies (Python, JS/TS, Go, Rust, Java, Infra)
and project-wide shared documentation/contracts, then orchestrates single-shot
ProjectBrain Multi-Source Hub provisioning, Graphify AST extraction, versioned
shared document ingestion, HSG memory indexing, and cross-source contract linking.
"""

from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.tenant_manager import parse_tenant_spec, tenant_manager
from ..memory.hsg import (
    add_hsg_memory,
    invalidate_query_cache,
    invalidate_source_query_cache,
)
from .cross_source_linker import CrossSourceLinker
from .doc_versioning import derive_doc_key, ingest_document_versioned
from .extractor import CODE_EXTENSIONS, GraphifyExtractor
from .legacy_encoding import (
    _looks_like_utf16_no_bom,
    decode_legacy_bytes,
    read_text_with_encoding,
)
from .store import get_graph_store

logger = logging.getLogger(__name__)

_SKIP_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".projectbrain",
    "data",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "graphify-out",
    ".agents",
    ".agent",
    "target",
    ".next",
    ".nuxt",
    ".tox",
    ".eggs",
}

_CONTAINER_DIRS: Tuple[str, ...] = ("apps", "services", "packages", "src")

_FORBIDDEN_SYSTEM_PREFIXES: Tuple[str, ...] = (
    "/etc",
    "/private/etc",
    "/proc",
    "/sys",
    "/var/run",
    "/private/var/run",
    "/root",
    "/dev",
    "/boot",
)
_FORBIDDEN_CREDENTIAL_DIRS: Tuple[str, ...] = (".ssh", ".aws", ".gnupg")

_ROOT_SHARED_DOC_NAMES: Tuple[str, ...] = (
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger.json",
    "architecture.md",
    "readme.md",
)

_MAX_SHARED_DOCS = 25
_MAX_SHARED_DOC_BYTES = 2 * 1024 * 1024  # 2 MiB guard against huge assets
_MAX_SOURCE_MEMORY_FILES = 30
_MAX_SOURCE_MEMORY_FILE_BYTES = 32 * 1024


def _check_forbidden_path(raw_str: str, resolved: Path) -> None:
    """Reject sensitive system prefixes, credential dirs, and PB_WORKSPACE_ROOT escapes."""
    norm_abs = os.path.abspath(str(Path(raw_str).expanduser()))
    for cand in (norm_abs, str(resolved)):
        for prefix in _FORBIDDEN_SYSTEM_PREFIXES:
            if cand == prefix or cand.startswith(prefix + "/"):
                raise ValueError(f"Access to sensitive system path '{raw_str}' is forbidden")

    home_raw = Path.home()
    home_resolved = home_raw.resolve()
    forbidden_home_dirs = []
    for sub in _FORBIDDEN_CREDENTIAL_DIRS:
        forbidden_home_dirs.append(Path(os.path.abspath(str(home_raw / sub))))
        forbidden_home_dirs.append((home_resolved / sub).resolve())

    for cand_path in (Path(norm_abs), resolved):
        for fdir in forbidden_home_dirs:
            if cand_path == fdir or fdir in cand_path.parents:
                raise ValueError(
                    f"Access to sensitive credential directory '{raw_str}' is forbidden"
                )
        if any(part in _FORBIDDEN_CREDENTIAL_DIRS for part in cand_path.parts):
            raise ValueError(f"Access to sensitive credential path '{raw_str}' is forbidden")

    pb_ws_root = os.getenv("PB_WORKSPACE_ROOT")
    if pb_ws_root:
        ws_root_resolved = Path(pb_ws_root).expanduser().resolve()
        try:
            resolved.relative_to(ws_root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"Path '{raw_str}' escapes configured PB_WORKSPACE_ROOT"
            ) from exc


def _validate_safe_workspace_dir(workspace_root: str) -> Path:
    """Validate and resolve `workspace_root` to an existing, safe directory."""
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        raise ValueError("workspace_root must be a non-empty string")
    if "\x00" in workspace_root:
        raise ValueError("Null byte is not allowed in workspace_root")

    clean = workspace_root.strip()
    norm_slashes = clean.replace("\\", "/")
    raw_path = Path(clean)
    if ".." in raw_path.parts or ".." in norm_slashes.split("/"):
        raise ValueError(f"Path traversal ('..') is not allowed in workspace_root: {workspace_root}")

    resolved = raw_path.expanduser().resolve()
    _check_forbidden_path(clean, resolved)

    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Workspace root directory does not exist: {workspace_root}")
    return resolved


def _resolve_safe_subpath(
    root: Path,
    subpath: str,
    *,
    expect_dir: bool = True,
    allow_external: bool = True,
) -> Path:
    """Safely resolve a source or shared-doc path relative to `root` or as an explicit existing path."""
    if not isinstance(subpath, str) or not subpath.strip():
        raise ValueError("subpath must be a non-empty string")
    if "\x00" in subpath:
        raise ValueError("Null byte is not allowed in subpath")

    clean = subpath.strip()
    norm_slashes = clean.replace("\\", "/")
    raw_path = Path(clean)
    if ".." in raw_path.parts or ".." in norm_slashes.split("/"):
        raise ValueError(f"Path traversal ('..') is not allowed in subpath: {subpath}")

    if clean == ".":
        return root

    if raw_path.is_absolute():
        resolved = raw_path.expanduser().resolve()
        _check_forbidden_path(clean, resolved)
        if resolved.is_relative_to(root):
            return resolved
        exists_ok = resolved.is_dir() if expect_dir else resolved.is_file()
        if allow_external and exists_ok:
            return resolved
        raise ValueError(f"Subpath '{subpath}' escapes workspace root '{root}'")

    resolved = (root / raw_path).resolve()
    _check_forbidden_path(clean, resolved)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Subpath '{subpath}' escapes workspace root '{root}'")
    return resolved


def _get_bootstrap_graph_store():
    """Return a GraphStore using the active ProjectBrain environment DB configuration."""
    db_path = (
        os.getenv("PB_GRAPH_DB_PATH")
        or os.getenv("OM_GRAPH_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or os.getenv("OM_DB_PATH")
    )
    if not db_path:
        try:
            from ..core.config import env

            db_url = getattr(env, "database_url", "")
            if db_url.startswith("sqlite:///"):
                db_path = db_url.replace("sqlite:///", "")
        except Exception:
            pass
    return get_graph_store(db_path=db_path)


def _is_monorepo_root(root: Path) -> bool:
    """Return True if root's package.json explicitly declares workspaces."""
    pkg = root / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict) and data.get("workspaces"):
            return True
    except Exception:
        pass
    return False


def _has_typescript_files(dir_path: Path) -> bool:
    """Check top-level and `src/` for .ts/.tsx files to distinguish TS vs JS projects."""
    for check_dir in (dir_path, dir_path / "src"):
        if not check_dir.is_dir():
            continue
        try:
            for item in check_dir.iterdir():
                if item.is_file() and item.suffix.lower() in (".ts", ".tsx"):
                    return True
        except Exception:
            pass
    return False


_SLN_SOLUTION_FOLDER_GUID = "2150E333-8FDC-42A3-9474-1A3956D46DE8"

_SLN_TYPE_GUID_LANG: Dict[str, str] = {
    "8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942": "cpp",
    "FAE04EC0-301F-11D3-BF4B-00C04F79EFBC": "csharp",
    "9A19103F-16F7-4668-BE54-9A1E7A4F7556": "csharp",
    "F184B08F-C81C-45F6-A57F-5ABD9991F28F": "vbnet",
    "778DAE3C-4631-46EA-AA77-85C1314464D9": "vbnet",
}

_VS_EXT_LANG: Dict[str, str] = {
    ".vcxproj": "cpp",
    ".vcproj": "cpp",
    ".csproj": "csharp",
    ".vbproj": "vbnet",
}

_LEGACY_TEXT_DOC_EXTS: Set[str] = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".adoc",
    ".json",
    ".yml",
    ".yaml",
    ".ini",
    ".conf",
}

_LEGACY_MANIFEST_EXTS: Set[str] = {
    ".sln",
    ".vcxproj",
    ".vcproj",
    ".csproj",
    ".vbproj",
    ".lcp",
    ".lxp",
    ".pro",
    ".vbp",
    ".dpr",
    ".dproj",
}

_LEGACY_MANIFEST_NAMES: Set[str] = {
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "CMakeLists.txt",
    "Makefile",
    "GNUmakefile",
}


def _xml_local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _inspect_legacy_code_hints(dir_path: Path, entries: Dict[str, Path]) -> Dict[str, Any]:
    """Quickly inspect project source files for Windows Service, DLL, or Desktop UI indicators."""
    hints: Dict[str, Any] = {
        "is_service": False,
        "is_dll": False,
        "is_desktop": False,
        "has_mfc": False,
        "has_win32": False,
        "has_rc": False,
        "has_def": False,
        "has_c_only": True,
        "has_cpp": False,
    }
    scan_exts = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".cs", ".rc", ".def", ".idl"}
    files_to_scan: List[Path] = []
    for name, p in sorted(entries.items()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext == ".rc":
            hints["has_rc"] = True
            hints["is_desktop"] = True
        elif ext == ".def":
            hints["has_def"] = True
            hints["is_dll"] = True
        if ext in (".cpp", ".hpp", ".cc", ".cxx"):
            hints["has_cpp"] = True
            hints["has_c_only"] = False
        elif ext == ".c":
            pass
        if ext in scan_exts:
            files_to_scan.append(p)

    # Also check immediate src/ or include/ files if top dir only holds manifest
    for sub_name in ("src", "source", "include"):
        sub_p = entries.get(sub_name)
        if sub_p is not None and sub_p.is_dir():
            try:
                for item in sorted(sub_p.iterdir()):
                    if item.is_file() and item.suffix.lower() in scan_exts:
                        if item.suffix.lower() in (".cpp", ".hpp", ".cc", ".cxx"):
                            hints["has_cpp"] = True
                            hints["has_c_only"] = False
                        files_to_scan.append(item)
            except Exception:
                pass

    svc_re = re.compile(
        r"\b(?:RegisterServiceCtrlHandler(?:Ex)?[AW]?|StartServiceCtrlDispatcher[AW]?|ServiceMain|ServiceBase|SERVICE_STATUS|SERVICE_TABLE_ENTRY)\b"
    )
    ui_re = re.compile(
        r"\b(?:SendMessage(?:Timeout)?[AW]?|PostMessage[AW]?|FindWindow(?:Ex)?[AW]?|DialogBox(?:Param)?[AW]?|CreateWindow(?:Ex)?[AW]?|Application\.Run|InitializeComponent)\b"
    )
    mfc_re = re.compile(r"\b(?:CDialog|CWinApp|CWnd|BEGIN_MESSAGE_MAP|ON_MESSAGE|afxwin\.h)\b")
    win32_re = re.compile(r"\b(?:CreateNamedPipe|CreateFileMapping|OpenFileMapping|CoCreateInstance|LoadLibrary|WSAStartup)\b")

    for fpath in files_to_scan[:20]:
        try:
            if fpath.stat().st_size > 512 * 1024:
                continue
            txt, _ = read_text_with_encoding(fpath)
        except Exception:
            continue
        if svc_re.search(txt):
            hints["is_service"] = True
        if "__declspec" in txt and "dllexport" in txt:
            hints["is_dll"] = True
        if ui_re.search(txt):
            hints["is_desktop"] = True
        if mfc_re.search(txt):
            hints["has_mfc"] = True
            hints["is_desktop"] = True
        if win32_re.search(txt):
            hints["has_win32"] = True

    return hints


def _parse_vs_project_manifest(proj_file: Path, entries: Dict[str, Path]) -> Dict[str, Any]:
    """Parse `.vcxproj`/`.vcproj`/`.csproj`/`.vbproj` XML for language, source_type, and references."""
    ext = proj_file.suffix.lower()
    lang = _VS_EXT_LANG.get(ext, "cpp")
    tech_stack: List[str] = [lang]
    if lang == "cpp":
        tech_stack.append("msvc")
    elif lang in ("csharp", "vbnet"):
        tech_stack.append("dotnet")

    config_type = ""
    subsystem = ""
    output_type = ""
    use_mfc = ""
    use_atl = ""
    use_winforms = ""
    use_wpf = ""
    project_guid = ""
    target_name = ""
    project_refs: List[Dict[str, str]] = []
    lib_deps: List[str] = []

    try:
        raw_xml, _ = read_text_with_encoding(proj_file)
        root_elem = ET.fromstring(raw_xml)
        for elem in root_elem.iter():
            tag = _xml_local_tag(elem.tag)
            txt = (elem.text or "").strip()
            if tag == "ConfigurationType" and txt:
                config_type = txt
            elif tag == "SubSystem" and txt:
                subsystem = txt
            elif tag == "OutputType" and txt:
                output_type = txt
            elif tag == "UseOfMfc" and txt:
                use_mfc = txt
            elif tag == "UseOfAtl" and txt:
                use_atl = txt
            elif tag == "UseWindowsForms" and txt:
                use_winforms = txt
            elif tag == "UseWPF" and txt:
                use_wpf = txt
            elif tag == "ProjectGuid" and txt:
                project_guid = txt.strip("{}").upper()
            elif tag in ("TargetName", "AssemblyName") and txt and not target_name:
                target_name = txt
            elif tag == "ProjectReference":
                inc = elem.get("Include") or ""
                ref_guid = ""
                for child in elem:
                    if _xml_local_tag(child.tag) == "Project" and child.text:
                        ref_guid = child.text.strip("{}").upper()
                if inc or ref_guid:
                    project_refs.append({"include": inc, "guid": ref_guid})
            elif tag == "AdditionalDependencies" and txt:
                for tok in txt.split(";"):
                    clean_tok = tok.strip()
                    if clean_tok and not clean_tok.startswith("%"):
                        lib_deps.append(clean_tok)
            elif tag == "Configuration" and ext == ".vcproj":
                ct_attr = elem.get("ConfigurationType") or ""
                if ct_attr == "2":
                    config_type = "DynamicLibrary"
                elif ct_attr == "4":
                    config_type = "StaticLibrary"
                elif ct_attr == "1" and not config_type:
                    config_type = "Application"
    except Exception:
        pass

    hints = _inspect_legacy_code_hints(proj_file.parent, entries)
    dir_low = proj_file.parent.name.lower()

    if (use_mfc and use_mfc.lower() not in ("false", "0", "none")) or hints["has_mfc"]:
        if "mfc" not in tech_stack:
            tech_stack.append("mfc")
    if use_atl and use_atl.lower() not in ("false", "0", "none"):
        tech_stack.append("atl")
    if use_winforms.lower() == "true":
        tech_stack.append("winforms")
    if use_wpf.lower() == "true":
        tech_stack.append("wpf")
    if hints["has_win32"] or hints["is_service"] or hints["has_rc"]:
        if "win32" not in tech_stack:
            tech_stack.append("win32")

    ct_low = config_type.lower()
    ot_low = output_type.lower()
    if ct_low in ("dynamiclibrary", "staticlibrary") or ot_low == "library" or (hints["has_def"] and not hints["is_service"]):
        source_type = "native_lib"
    elif hints["is_service"] or any(tok in dir_low for tok in ("service", "svc", "daemon")):
        source_type = "win_service"
    elif (
        subsystem.lower() == "windows"
        or ot_low == "winexe"
        or use_winforms.lower() == "true"
        or use_wpf.lower() == "true"
        or (use_mfc and use_mfc.lower() not in ("false", "0", "none"))
        or hints["is_desktop"]
        or any(tok in dir_low for tok in ("hmi", "ui", "gui", "desktop", "client", "viewer"))
    ):
        source_type = "desktop_client"
    else:
        source_type = "backend"

    return {
        "source_type": source_type,
        "tech_stack": tech_stack,
        "language": lang,
        "markers": [proj_file.name],
        "_project_guid": project_guid,
        "_target_name": target_name or proj_file.stem,
        "_project_file": str(proj_file.resolve()),
        "_project_references": project_refs,
        "_additional_dependencies": lib_deps,
    }


def _has_dir_st_lasal_files(dir_path: Path, entries: Dict[str, Path]) -> Tuple[List[str], bool]:
    """Check for LASAL `.lcp`/`.lxp` or Structured Text `.st`/`.scl`/`.pou` files."""
    lcp_files = sorted(
        name for name, p in entries.items() if p.is_file() and p.suffix.lower() in (".lcp", ".lxp")
    )
    st_files = sorted(
        name for name, p in entries.items() if p.is_file() and p.suffix.lower() in (".st", ".scl", ".pou")
    )
    is_lasal = bool(lcp_files) or any(name.lower().endswith(".pou") for name in st_files)

    if lcp_files and not st_files:
        for sub_name in ("Source", "source", "src"):
            sub_p = entries.get(sub_name)
            if sub_p is not None and sub_p.is_dir():
                try:
                    for item in sorted(sub_p.iterdir()):
                        if item.is_file() and item.suffix.lower() in (".st", ".scl", ".pou", ".exp"):
                            st_files.append(f"{sub_name}/{item.name}")
                except Exception:
                    pass

    if not is_lasal and st_files:
        for rel_st in st_files[:5]:
            try:
                txt, _ = read_text_with_encoding(dir_path / rel_st)
                if re.search(r"\b(?:SERVER|CLIENT|END_SERVER|END_CLIENT|LASAL)\b", txt, re.IGNORECASE):
                    is_lasal = True
                    break
            except Exception:
                pass

    return lcp_files + st_files, is_lasal


def _detect_legacy_source_markers(
    dir_path: Path,
    entries: Dict[str, Path],
    rel_parts: Tuple[str, ...] = (),
) -> Optional[Dict[str, Any]]:
    """Inspect legacy C/C++/VS/PLC-ST/Qt/VB6/Delphi/CMake/Make manifests inside `dir_path`."""
    # 7. Visual Studio project files (.vcxproj, .vcproj, .csproj, .vbproj)
    for vs_ext in (".vcxproj", ".vcproj", ".csproj", ".vbproj"):
        vs_files = sorted(
            p for name, p in entries.items() if p.is_file() and name.lower().endswith(vs_ext)
        )
        if vs_files:
            return _parse_vs_project_manifest(vs_files[0], entries)

    # 8. PLC / Structured Text / Sigmatek LASAL (.lcp, .lxp, *.st, *.scl, *.pou)
    plc_markers, is_lasal = _has_dir_st_lasal_files(dir_path, entries)
    if plc_markers:
        plc_stack = ["iec61131-st"]
        if is_lasal:
            plc_stack.append("lasal")
        return {
            "source_type": "plc_st",
            "tech_stack": plc_stack,
            "language": "st",
            "markers": plc_markers[:5],
        }

    # 9. Qt project (*.pro)
    pro_files = sorted(
        p for name, p in entries.items() if p.is_file() and name.lower().endswith(".pro")
    )
    if pro_files:
        pro_txt = ""
        try:
            pro_txt, _ = read_text_with_encoding(pro_files[0])
        except Exception:
            pass
        if re.search(r"\b(?:QT|TARGET|SOURCES|HEADERS|TEMPLATE)\s*[+=]", pro_txt) or any(
            p.is_file() and p.suffix.lower() in (".cpp", ".hpp", ".c", ".h") for p in entries.values()
        ):
            is_lib = bool(re.search(r"\bTEMPLATE\s*=\s*lib\b", pro_txt, re.IGNORECASE))
            return {
                "source_type": "native_lib" if is_lib else "desktop_client",
                "tech_stack": ["cpp", "qt"],
                "language": "cpp",
                "markers": [pro_files[0].name],
            }

    # 10. VB6 project (*.vbp)
    vbp_files = sorted(
        p for name, p in entries.items() if p.is_file() and name.lower().endswith(".vbp")
    )
    if vbp_files:
        vbp_txt = ""
        try:
            vbp_txt, _ = read_text_with_encoding(vbp_files[0])
        except Exception:
            pass
        is_dll = bool(re.search(r"^\s*Type\s*=\s*(?:OleDll|Control)\b", vbp_txt, re.IGNORECASE | re.MULTILINE))
        return {
            "source_type": "native_lib" if is_dll else "desktop_client",
            "tech_stack": ["vb6"],
            "language": "vb6",
            "markers": [vbp_files[0].name],
        }

    # 11. Delphi project (*.dpr, *.dproj)
    dpr_files = sorted(
        p
        for name, p in entries.items()
        if p.is_file() and name.lower().endswith((".dpr", ".dproj"))
    )
    if dpr_files:
        dpr_txt = ""
        try:
            dpr_txt, _ = read_text_with_encoding(dpr_files[0])
        except Exception:
            pass
        is_lib = bool(re.match(r"^\s*library\b", dpr_txt, re.IGNORECASE))
        is_svc = "TService" in dpr_txt
        return {
            "source_type": "native_lib" if is_lib else ("win_service" if is_svc else "desktop_client"),
            "tech_stack": ["delphi"],
            "language": "delphi",
            "markers": [dpr_files[0].name],
        }

    # 12. CMakeLists.txt
    if "CMakeLists.txt" in entries and entries["CMakeLists.txt"].is_file():
        cm_txt = ""
        try:
            cm_txt, _ = read_text_with_encoding(entries["CMakeLists.txt"])
        except Exception:
            pass
        hints = _inspect_legacy_code_hints(dir_path, entries)
        lang = "c" if hints["has_c_only"] and not hints["has_cpp"] else "cpp"
        stack = [lang, "cmake"]
        if "qt" in cm_txt.lower():
            stack.append("qt")
        has_lib = bool(re.search(r"\badd_library\s*\(", cm_txt, re.IGNORECASE))
        has_exe = bool(re.search(r"\badd_executable\s*\(", cm_txt, re.IGNORECASE))
        if has_lib and not has_exe:
            src_type = "native_lib"
        elif hints["is_service"]:
            src_type = "win_service"
        elif hints["is_desktop"] or "qt" in stack:
            src_type = "desktop_client"
        else:
            src_type = "backend"
        return {
            "source_type": src_type,
            "tech_stack": stack,
            "language": lang,
            "markers": ["CMakeLists.txt"],
        }

    # 13. Makefile / GNUmakefile
    mk_name = next((m for m in ("Makefile", "GNUmakefile") if m in entries and entries[m].is_file()), None)
    if mk_name:
        hints = _inspect_legacy_code_hints(dir_path, entries)
        lang = "c" if hints["has_c_only"] and not hints["has_cpp"] else "cpp"
        src_type = (
            "win_service"
            if hints["is_service"]
            else ("native_lib" if hints["is_dll"] else ("desktop_client" if hints["is_desktop"] else "backend"))
        )
        return {
            "source_type": src_type,
            "tech_stack": [lang, "make"],
            "language": lang,
            "markers": [mk_name],
        }

    return None


def _detect_source_markers(
    dir_path: Path,
    *,
    rel_parts: Tuple[str, ...] = (),
) -> Optional[Dict[str, Any]]:
    """Inspect marker files inside `dir_path` to classify source_type and tech_stack."""
    try:
        entries = {p.name: p for p in dir_path.iterdir()}
    except Exception:
        return None

    dir_name_low = dir_path.name.lower()
    is_worker_name = any(tok in dir_name_low for tok in ("worker", "celery", "queue"))
    markers: List[str] = []

    # 1. Python markers
    py_markers = [m for m in ("pyproject.toml", "requirements.txt", "setup.py") if (m in entries and entries[m].is_file())]
    if py_markers:
        markers.extend(py_markers)
        combined_text = ""
        for pm in py_markers:
            try:
                combined_text += "\n" + entries[pm].read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                pass
        tech_stack: List[str] = ["python"]
        for fw in ("fastapi", "flask", "django"):
            if fw in combined_text and fw not in tech_stack:
                tech_stack.append(fw)
        source_type = "worker" if is_worker_name else "backend"
        return {
            "source_type": source_type,
            "tech_stack": tech_stack,
            "markers": markers,
        }

    # 2. Node / JS / TS marker
    if "package.json" in entries and entries["package.json"].is_file():
        markers.append("package.json")
        raw_pkg = ""
        pkg_tokens: Set[str] = set()
        script_text = ""
        pkg_data: Any = None
        try:
            raw_pkg = entries["package.json"].read_text(encoding="utf-8", errors="replace")
            pkg_data = json.loads(raw_pkg)
            if isinstance(pkg_data, dict):
                for dep_key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    deps = pkg_data.get(dep_key)
                    if isinstance(deps, dict):
                        for k in deps.keys():
                            pkg_tokens.add(str(k).lower())
                scripts = pkg_data.get("scripts")
                if isinstance(scripts, dict):
                    script_text = " ".join(str(v).lower() for v in scripts.values())
        except Exception:
            pkg_data = None
        raw_low = raw_pkg.lower()

        fe_frameworks = ("react", "next", "vue", "svelte", "vite", "angular")
        be_frameworks = ("express", "nestjs", "fastify")

        def _has_pkg_fw(fw: str) -> bool:
            if isinstance(pkg_data, dict):
                if fw in pkg_tokens:
                    return True
                if any(tok.startswith(f"@{fw}/") or tok.startswith(f"{fw}-") for tok in pkg_tokens):
                    return True
                if fw == "vite" and any("vite" in tok for tok in pkg_tokens):
                    return True
                if fw == "angular" and any(tok.startswith("@angular/") for tok in pkg_tokens):
                    return True
                if fw == "nestjs" and any(tok.startswith("@nestjs/") for tok in pkg_tokens):
                    return True
                return bool(re.search(rf"\b{re.escape(fw)}\b", script_text))
            return bool(re.search(rf'"{re.escape(fw)}"', raw_low))

        detected_fe = [fw for fw in fe_frameworks if _has_pkg_fw(fw)]
        detected_be = [fw for fw in be_frameworks if _has_pkg_fw(fw)]

        is_ts = (
            "typescript" in pkg_tokens
            or (pkg_data is None and "typescript" in raw_low)
            or ("tsconfig.json" in entries and entries["tsconfig.json"].is_file())
            or _has_typescript_files(dir_path)
        )
        tech_stack = ["typescript" if is_ts else "javascript"]
        for fw in detected_fe + detected_be:
            if fw not in tech_stack:
                tech_stack.append(fw)

        source_type = "frontend" if detected_fe else "backend"
        return {
            "source_type": source_type,
            "tech_stack": tech_stack,
            "markers": markers,
        }

    # 3. Go
    if "go.mod" in entries and entries["go.mod"].is_file():
        return {
            "source_type": "backend",
            "tech_stack": ["go"],
            "markers": ["go.mod"],
        }

    # 4. Rust
    if "Cargo.toml" in entries and entries["Cargo.toml"].is_file():
        return {
            "source_type": "backend",
            "tech_stack": ["rust"],
            "markers": ["Cargo.toml"],
        }

    # 5. Java
    java_markers = [m for m in ("pom.xml", "build.gradle", "build.gradle.kts") if (m in entries and entries[m].is_file())]
    if java_markers:
        return {
            "source_type": "backend",
            "tech_stack": ["java"],
            "markers": java_markers,
        }

    # 6. Infra (*.tf or docker-compose.yml / Dockerfile inside dedicated infra/ or deploy/)
    tf_files = sorted(name for name, p in entries.items() if p.is_file() and name.lower().endswith(".tf"))
    compose_files = sorted(
        name
        for name, p in entries.items()
        if p.is_file() and name.lower() in ("docker-compose.yml", "docker-compose.yaml")
    )
    dockerfile = ["Dockerfile"] if ("Dockerfile" in entries and entries["Dockerfile"].is_file()) else []
    is_dedicated_infra = any(
        part.lower() in ("infra", "deploy", "deployment", "terraform")
        for part in ((*rel_parts, dir_path.name))
        if part
    )
    if tf_files or (is_dedicated_infra and (compose_files or dockerfile)):
        infra_markers = tf_files[:3] + compose_files + dockerfile
        infra_stack: List[str] = []
        if tf_files:
            infra_stack.append("terraform")
        if compose_files or dockerfile:
            infra_stack.append("docker")
        return {
            "source_type": "infra",
            "tech_stack": infra_stack or ["infra"],
            "markers": infra_markers,
        }

    # 7-13. Legacy C/C++, VS, PLC/LASAL ST, Qt, VB6, Delphi, CMake, Makefile
    return _detect_legacy_source_markers(dir_path, entries, rel_parts=rel_parts)


def _discover_sln_projects(
    root: Path,
) -> Tuple[List[Tuple[Path, Dict[str, Any]]], List[Dict[str, Any]]]:
    """Parse `.sln` files under `root` to discover child VS project directories and dependencies."""
    sln_files: List[Path] = []
    try:
        sln_files = sorted(
            (p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".sln"),
            key=lambda p: p.name.lower(),
        )
    except Exception:
        return [], []

    if not sln_files:
        return [], []

    proj_header_re = re.compile(
        r'^\s*Project\(\s*"\{?([0-9A-Fa-f-]+)\}?"\s*\)\s*=\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"\{?([0-9A-Fa-f-]+)\}?"',
        re.IGNORECASE,
    )
    dep_guid_re = re.compile(r"^\s*\{?([0-9A-Fa-f-]+)\}?\s*=\s*\{?[0-9A-Fa-f-]+\}?\s*$")

    discovered: List[Tuple[Path, Dict[str, Any]]] = []
    raw_sln_deps: List[Dict[str, str]] = []

    for sln_path in sln_files:
        try:
            content, _ = read_text_with_encoding(sln_path)
        except Exception:
            continue

        lines = content.splitlines()
        idx = 0
        while idx < len(lines):
            m_proj = proj_header_re.match(lines[idx])
            if not m_proj:
                idx += 1
                continue

            type_guid = m_proj.group(1).upper()
            proj_name = m_proj.group(2).strip()
            raw_rel = m_proj.group(3).strip().replace("\\", "/")
            proj_guid = m_proj.group(4).upper()
            idx += 1

            dep_guids: List[str] = []
            in_deps = False
            while idx < len(lines):
                line_s = lines[idx].strip()
                if line_s.startswith("EndProject"):
                    idx += 1
                    break
                if re.match(r"^ProjectSection\s*\(\s*ProjectDependencies\s*\)", line_s, re.IGNORECASE):
                    in_deps = True
                elif line_s.startswith("EndProjectSection"):
                    in_deps = False
                elif in_deps:
                    dm = dep_guid_re.match(line_s)
                    if dm:
                        dep_guids.append(dm.group(1).upper())
                idx += 1

            # Ignore Solution Folders ({2150E333-8FDC-42A3-9474-1A3956D46DE8})
            if type_guid == _SLN_SOLUTION_FOLDER_GUID:
                continue
            if not raw_rel or raw_rel.startswith(("http://", "https://")):
                continue

            rel_parts = Path(raw_rel).parts
            if ".." in rel_parts or Path(raw_rel).is_absolute():
                continue

            cand_target = (sln_path.parent / raw_rel).resolve()
            try:
                _check_forbidden_path(raw_rel, cand_target)
                if not cand_target.is_relative_to(root):
                    continue
            except Exception:
                continue

            if cand_target.is_dir():
                proj_dir = cand_target
                proj_file: Optional[Path] = None
            else:
                proj_dir = cand_target.parent
                proj_file = cand_target if cand_target.is_file() else None

            if not proj_dir.is_dir():
                continue

            try:
                dir_entries = {p.name: p for p in proj_dir.iterdir()}
            except Exception:
                dir_entries = {}

            if proj_file is not None and proj_file.suffix.lower() in _VS_EXT_LANG:
                meta = _parse_vs_project_manifest(proj_file, dir_entries)
            else:
                meta = _detect_source_markers(proj_dir, rel_parts=proj_dir.relative_to(root).parts[:-1]) or {}
                if not meta:
                    inferred_lang = _SLN_TYPE_GUID_LANG.get(type_guid) or _VS_EXT_LANG.get(
                        Path(raw_rel).suffix.lower(), "cpp"
                    )
                    meta = {
                        "source_type": "backend",
                        "tech_stack": [inferred_lang],
                        "language": inferred_lang,
                        "markers": [Path(raw_rel).name],
                    }

            if "language" not in meta:
                meta["language"] = _SLN_TYPE_GUID_LANG.get(type_guid, "cpp")
            markers = list(meta.get("markers") or [])
            if sln_path.name not in markers:
                markers.append(sln_path.name)
            meta["markers"] = markers
            meta["_sln_guid"] = proj_guid
            meta["_sln_name"] = proj_name
            meta["_sln_file"] = sln_path.name
            if proj_file is not None:
                meta["_project_file"] = str(proj_file.resolve())
            discovered.append((proj_dir, meta))

            for dg in dep_guids:
                raw_sln_deps.append(
                    {
                        "from_guid": proj_guid,
                        "from_dir": str(proj_dir.resolve()),
                        "to_guid": dg,
                        "sln_file": sln_path.name,
                    }
                )

    return discovered, raw_sln_deps


def _classify_shared_doc_role(rel_path: str) -> str:
    """Assign heuristic doc_role from filename/relative path."""
    low = rel_path.lower()
    name_low = Path(rel_path).name.lower()
    if "openapi" in name_low or "swagger" in name_low:
        return "api_contract"
    if re.search(r"(?:^|[/_.-])adr(?:[0-9/_.-]|$)", low):
        return "adr"
    if "arch" in low:
        return "architecture"
    if re.search(r"(?:^|[/_.-])(prd|spec)(?:[/_.-]|$)", low) or "spec" in name_low:
        return "prd"
    return "reference"


def _is_probably_binary(file_path: Path) -> bool:
    """Return True when file exceeds size limit or contains non-UTF16 NUL bytes in prefix."""
    try:
        if file_path.stat().st_size > _MAX_SHARED_DOC_BYTES:
            return True
        with file_path.open("rb") as fh:
            chunk = fh.read(2048)
        if b"\x00" not in chunk:
            return False
        # Allow UTF-16 LE/BE text files (with BOM or BOM-less heuristic)
        if chunk.startswith(b"\xff\xfe") and not chunk.startswith(b"\xff\xfe\x00\x00"):
            return False
        if chunk.startswith(b"\xfe\xff"):
            return False
        if _looks_like_utf16_no_bom(chunk) is not None:
            return False
        return True
    except Exception:
        return True


def _format_display_name(source_id: str) -> str:
    words = [w for w in re.split(r"[-_./]+", source_id) if w]
    if not words:
        return source_id
    out: List[str] = []
    for w in words:
        if w.lower() in ("api", "ui", "sdk", "cli", "db", "mcp"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _slugify_project_id(raw_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (raw_name or "").strip().lower()).strip("-._")
    return slug or "default-project"


def _discover_shared_docs(root: Path) -> List[Dict[str, Any]]:
    """Discover candidate project-wide shared documentation & API contracts."""
    candidates: List[Path] = []
    seen_resolved: Set[Path] = set()

    # 1. Root-level key files (in deterministic priority order)
    try:
        root_files_by_low = {
            p.name.lower(): p for p in root.iterdir() if p.is_file()
        }
    except Exception:
        root_files_by_low = {}

    for target_low in _ROOT_SHARED_DOC_NAMES:
        cand = root_files_by_low.get(target_low)
        if cand is not None:
            res = cand.resolve()
            if res not in seen_resolved and not _is_probably_binary(cand):
                seen_resolved.add(res)
                candidates.append(cand)

    # 2. docs/ subtree (excluding docs/reports/ and docs/kb/exports/)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        doc_walked: List[Path] = []
        for cur_root, dirs, files in os.walk(docs_dir):
            cur_p = Path(cur_root)
            rel_from_docs = cur_p.relative_to(docs_dir)
            rel_parts_low = [part.lower() for part in rel_from_docs.parts]
            if "reports" in rel_parts_low or ("kb" in rel_parts_low and "exports" in rel_parts_low):
                dirs[:] = []
                continue
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in _SKIP_DIRS
                and not d.startswith(".")
                and d.lower() != "reports"
            )
            for fname in sorted(files):
                fpath = cur_p / fname
                low_name = fname.lower()
                if low_name.endswith(".md") or low_name in (
                    "openapi.yaml",
                    "openapi.yml",
                    "openapi.json",
                    "swagger.json",
                ):
                    doc_walked.append(fpath)

        for fpath in sorted(doc_walked, key=lambda p: str(p.relative_to(root)).replace("\\", "/")):
            res = fpath.resolve()
            if res not in seen_resolved and not _is_probably_binary(fpath):
                seen_resolved.add(res)
                candidates.append(fpath)

    # Cap at _MAX_SHARED_DOCS and assign unique doc_keys
    used_doc_keys: Set[str] = set()
    shared_docs: List[Dict[str, Any]] = []
    for doc_path in candidates[:_MAX_SHARED_DOCS]:
        rel_str = str(doc_path.relative_to(root)).replace("\\", "/")
        doc_role = _classify_shared_doc_role(rel_str)
        base_key = derive_doc_key(doc_path.name)
        doc_key = base_key
        if doc_key in used_doc_keys:
            doc_key = derive_doc_key(rel_str.replace("/", "_"))
        suffix = 2
        while doc_key in used_doc_keys:
            doc_key = f"{base_key}_{suffix}"
            suffix += 1
        used_doc_keys.add(doc_key)

        shared_docs.append(
            {
                "path": str(doc_path.resolve()),
                "relative_path": rel_str,
                "filename": doc_path.name,
                "doc_key": doc_key,
                "doc_role": doc_role,
                "tags": ["shared_doc", doc_role],
            }
        )

    return shared_docs


def _merge_discovered_entry(
    discovered_map: Dict[Path, Tuple[Path, Dict[str, Any]]],
    src_dir: Path,
    meta: Dict[str, Any],
) -> None:
    """Insert or merge discovered sub-source directory metadata keyed by resolved Path."""
    res_dir = src_dir.resolve()
    if res_dir not in discovered_map:
        discovered_map[res_dir] = (src_dir, dict(meta))
        return

    _, existing = discovered_map[res_dir]
    markers = list(existing.get("markers") or [])
    for m in meta.get("markers") or []:
        if m not in markers:
            markers.append(m)
    existing["markers"] = markers

    tech = list(existing.get("tech_stack") or [])
    for t in meta.get("tech_stack") or []:
        if t not in tech:
            tech.append(t)
    existing["tech_stack"] = tech

    for key in ("_sln_guid", "_sln_name", "_sln_file", "_project_guid", "_project_file", "_target_name", "language"):
        if not existing.get(key) and meta.get(key):
            existing[key] = meta[key]
    if not existing.get("_project_references") and meta.get("_project_references"):
        existing["_project_references"] = list(meta["_project_references"])
    if not existing.get("_additional_dependencies") and meta.get("_additional_dependencies"):
        existing["_additional_dependencies"] = list(meta["_additional_dependencies"])


def _resolve_discovered_relations(
    root: Path,
    sources_meta: List[Tuple[str, Path, Dict[str, Any]]],
    raw_sln_deps: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Resolve `.sln` ProjectDependencies and `.vcxproj` `<ProjectReference>` into `depends_on` relations."""
    by_guid: Dict[str, str] = {}
    by_proj_file: Dict[Path, str] = {}
    by_dir: Dict[Path, str] = {}
    by_target_low: Dict[str, str] = {}

    for sid, sdir, meta in sources_meta:
        res_dir = sdir.resolve()
        by_dir[res_dir] = sid
        by_target_low.setdefault(sid.lower(), sid)
        if meta.get("_sln_guid"):
            by_guid[str(meta["_sln_guid"]).upper()] = sid
        if meta.get("_project_guid"):
            by_guid[str(meta["_project_guid"]).upper()] = sid
        if meta.get("_sln_name"):
            by_target_low.setdefault(str(meta["_sln_name"]).lower(), sid)
        if meta.get("_target_name"):
            by_target_low.setdefault(str(meta["_target_name"]).lower(), sid)
        if meta.get("_project_file"):
            by_proj_file[Path(str(meta["_project_file"])).resolve()] = sid

    relations: List[Dict[str, Any]] = []
    seen_edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def _add_dep(src_sid: str, dst_sid: str, via: str, extra: Dict[str, Any]) -> None:
        if not src_sid or not dst_sid or src_sid == dst_sid:
            return
        ek = (src_sid, dst_sid, "depends_on")
        if ek in seen_edges:
            prev_via = seen_edges[ek]["contract_metadata"].get("via", "")
            if via not in prev_via:
                seen_edges[ek]["contract_metadata"]["via"] = f"{prev_via}+{via}" if prev_via else via
            return
        rel_obj = {
            "src_source_id": src_sid,
            "dst_source_id": dst_sid,
            "relation_type": "depends_on",
            "contract_key": f"project_ref:{dst_sid}",
            "contract_metadata": {"via": via, **extra},
        }
        seen_edges[ek] = rel_obj
        relations.append(rel_obj)

    # 1. .sln ProjectSection(ProjectDependencies)
    for dep in raw_sln_deps:
        src_sid = by_guid.get(dep["from_guid"]) or by_dir.get(Path(dep["from_dir"]).resolve())
        dst_sid = by_guid.get(dep["to_guid"])
        if src_sid and dst_sid:
            _add_dep(
                src_sid,
                dst_sid,
                "sln_project_dependencies",
                {"sln_file": dep.get("sln_file"), "target_guid": dep["to_guid"]},
            )

    # 2. .vcxproj / .csproj <ProjectReference>
    for sid, _sdir, meta in sources_meta:
        proj_file_str = meta.get("_project_file")
        if not proj_file_str:
            continue
        proj_file = Path(str(proj_file_str))
        for pref in meta.get("_project_references") or []:
            inc_raw = str(pref.get("include") or "").strip().replace("\\", "/")
            ref_guid = str(pref.get("guid") or "").strip("{}").upper()
            dst_sid: Optional[str] = None
            target_proj_rel = inc_raw
            if inc_raw and not Path(inc_raw).is_absolute():
                cand = (proj_file.parent / inc_raw).resolve()
                try:
                    _check_forbidden_path(inc_raw, cand)
                    if cand.is_relative_to(root):
                        dst_sid = by_proj_file.get(cand) or by_dir.get(cand.parent)
                except Exception:
                    dst_sid = None
            if dst_sid is None and ref_guid:
                dst_sid = by_guid.get(ref_guid)
            if dst_sid is None and inc_raw:
                dst_sid = by_target_low.get(Path(inc_raw).stem.lower())
            if dst_sid:
                _add_dep(
                    sid,
                    dst_sid,
                    "vcxproj_project_reference",
                    {
                        "project_file": proj_file.name,
                        "target_project_file": target_proj_rel,
                    },
                )

    return relations


def discover_workspace_sources(workspace_root: str = ".") -> Dict[str, Any]:
    """Scan a workspace directory and auto-detect sub-sources, dependencies, and shared project docs."""
    root = _validate_safe_workspace_dir(workspace_root)
    root_marker = _detect_source_markers(root)
    has_monorepo_containers = any((root / c).is_dir() for c in ("apps", "services", "packages"))
    root_is_generic_make = bool(
        root_marker
        and set(root_marker.get("markers") or []) <= {"Makefile", "GNUmakefile", "CMakeLists.txt"}
    )
    root_is_single_module = (
        bool(root_marker)
        and not has_monorepo_containers
        and not _is_monorepo_root(root)
        and not root_is_generic_make
    )

    discovered_map: Dict[Path, Tuple[Path, Dict[str, Any]]] = {}
    raw_sln_deps: List[Dict[str, str]] = []

    # 1. Discover Visual Studio `.sln` child projects at root (excluding root `.` unless single-project)
    sln_entries, sln_deps = _discover_sln_projects(root)
    raw_sln_deps.extend(sln_deps)
    sln_root_entries: List[Tuple[Path, Dict[str, Any]]] = []
    for proj_dir, meta in sln_entries:
        if proj_dir.resolve() == root.resolve():
            sln_root_entries.append((proj_dir, meta))
        else:
            _merge_discovered_entry(discovered_map, proj_dir, meta)

    try:
        top_children = sorted(
            (p for p in root.iterdir() if p.is_dir() and p.name not in _SKIP_DIRS and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
    except Exception:
        top_children = []

    for child in top_children:
        rel_parts = child.relative_to(root).parts[:-1]
        direct_meta = _detect_source_markers(child, rel_parts=rel_parts)
        if direct_meta is not None:
            _merge_discovered_entry(discovered_map, child, direct_meta)
            continue

        # Check for nested `.sln` inside a dedicated subfolder (e.g. `legacy/App.sln`)
        if child.name not in _CONTAINER_DIRS:
            nested_sln, nested_deps = _discover_sln_projects(child)
            if nested_sln:
                raw_sln_deps.extend(nested_deps)
                for proj_dir, meta in nested_sln:
                    _merge_discovered_entry(discovered_map, proj_dir, meta)
                continue

        if child.name in _CONTAINER_DIRS:
            if child.name == "src" and root_is_single_module:
                continue
            # Scan 1-2 levels deep under apps/, services/, packages/, src/
            try:
                lvl1_dirs = sorted(
                    (
                        p
                        for p in child.iterdir()
                        if p.is_dir() and p.name not in _SKIP_DIRS and not p.name.startswith(".")
                    ),
                    key=lambda p: p.name.lower(),
                )
            except Exception:
                lvl1_dirs = []

            for lvl1 in lvl1_dirs:
                lvl1_meta = _detect_source_markers(lvl1, rel_parts=lvl1.relative_to(root).parts[:-1])
                if lvl1_meta is not None:
                    _merge_discovered_entry(discovered_map, lvl1, lvl1_meta)
                    continue

                try:
                    lvl2_dirs = sorted(
                        (
                            p
                            for p in lvl1.iterdir()
                            if p.is_dir() and p.name not in _SKIP_DIRS and not p.name.startswith(".")
                        ),
                        key=lambda p: p.name.lower(),
                    )
                except Exception:
                    lvl2_dirs = []
                for lvl2 in lvl2_dirs:
                    lvl2_meta = _detect_source_markers(lvl2, rel_parts=lvl2.relative_to(root).parts[:-1])
                    if lvl2_meta is not None:
                        _merge_discovered_entry(discovered_map, lvl2, lvl2_meta)

    discovered_dirs: List[Tuple[Path, Dict[str, Any]]] = sorted(
        discovered_map.values(),
        key=lambda item: str(item[0].relative_to(root)).replace("\\", "/").lower(),
    )

    # Avoid treating root `.` as a sub-source when child sub-sources were found
    if not discovered_dirs:
        for _, sln_root_meta in sln_root_entries:
            discovered_dirs.append((root, sln_root_meta))
        if not discovered_dirs and root_marker is not None:
            discovered_dirs.append((root, root_marker))

    # Build normalized source descriptors with unique source_ids
    raw_names = [d.name for d, _ in discovered_dirs]
    name_counts: Dict[str, int] = {}
    for rn in raw_names:
        name_counts[rn] = name_counts.get(rn, 0) + 1

    sources: List[Dict[str, Any]] = []
    sources_meta: List[Tuple[str, Path, Dict[str, Any]]] = []
    used_source_ids: Set[str] = set()
    for src_dir, meta in discovered_dirs:
        if src_dir == root:
            rel_subpath = "."
            cand_id = _slugify_project_id(root.name)
        else:
            rel_subpath = str(src_dir.relative_to(root)).replace("\\", "/")
            if name_counts.get(src_dir.name, 0) > 1:
                cand_id = rel_subpath.replace("/", "-")
            else:
                cand_id = src_dir.name

        cand_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", cand_id).strip("-") or "source"
        if cand_id in ("__default__", "__shared__"):
            cand_id = f"src-{cand_id.strip('_')}"
        unique_id = cand_id
        suffix = 2
        while unique_id in used_source_ids:
            unique_id = f"{cand_id}-{suffix}"
            suffix += 1
        used_source_ids.add(unique_id)

        desc: Dict[str, Any] = {
            "source_id": unique_id,
            "display_name": _format_display_name(unique_id),
            "source_type": meta["source_type"],
            "tech_stack": list(meta["tech_stack"]),
            "workspace_subpath": rel_subpath,
            "path": str(src_dir.resolve()),
            "markers": list(meta["markers"]),
            "inherit_shared_docs": True,
        }
        if meta.get("language"):
            desc["language"] = meta["language"]
        sources.append(desc)
        sources_meta.append((unique_id, src_dir, meta))

    discovered_relations = _resolve_discovered_relations(root, sources_meta, raw_sln_deps)
    shared_docs = _discover_shared_docs(root)

    return {
        "status": "ok",
        "workspace_root": str(root),
        "sources": sources,
        "shared_docs": shared_docs,
        "relations": discovered_relations,
        "suggested_project_id": _slugify_project_id(root.name),
    }


async def _sync_source_hsg_memories(
    *,
    base_proj: str,
    src_id: str,
    src_type: str,
    tech_stack: List[str],
    branch: str,
    src_dir: Path,
    delta: Any,
) -> int:
    """Persist lightweight source-scoped HSG memories so `projectbrain_query` works immediately."""
    created = 0
    mem_user_id = f"{base_proj}@{src_id}:{branch}"
    base_tags = ["codebase", "source", src_id, src_type]

    # Collect prioritized source files (AST-extracted files first, then manifest/code files)
    prioritized_rel: List[str] = []
    seen_rel: Set[str] = set()
    for sf in getattr(delta, "synced_files", []) or []:
        rel = sf.get("file_path") if isinstance(sf, dict) else str(sf)
        rel_norm = str(rel or "").replace("\\", "/")
        if rel_norm and rel_norm not in seen_rel:
            seen_rel.add(rel_norm)
            prioritized_rel.append(rel_norm)

    for cur_root, dirs, files in os.walk(src_dir):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for fname in sorted(files):
            fpath = Path(cur_root) / fname
            ext = fpath.suffix.lower()
            if (
                ext in CODE_EXTENSIONS
                or ext in _LEGACY_MANIFEST_EXTS
                or fname in _LEGACY_MANIFEST_NAMES
            ):
                rel_norm = str(fpath.relative_to(src_dir)).replace("\\", "/")
                if rel_norm not in seen_rel:
                    seen_rel.add(rel_norm)
                    prioritized_rel.append(rel_norm)

    for rel_norm in prioritized_rel[:_MAX_SOURCE_MEMORY_FILES]:
        fpath = src_dir / rel_norm
        if not fpath.is_file():
            continue
        try:
            if fpath.stat().st_size > _MAX_SOURCE_MEMORY_FILE_BYTES:
                continue
            decoded_text, _ = read_text_with_encoding(fpath)
            text = decoded_text.strip()
        except Exception:
            continue
        if not text:
            continue

        meta = {
            "source": "hub_bootstrap",
            "project_id": base_proj,
            "branch": branch,
            "source_id": src_id,
            "scope": "source",
            "file_path": rel_norm,
            "filename": fpath.name,
        }
        try:
            await add_hsg_memory(
                content=f"[{src_id}] File: {rel_norm}\n\n{text}",
                tags=json.dumps(base_tags),
                metadata=meta,
                user_id=mem_user_id,
                source_id=src_id,
            )
            created += 1
        except Exception as exc:
            logger.debug("HSG source file ingest skipped for %s/%s: %s", src_id, rel_norm, exc)

    # Always store a concise source-level symbol & stack summary memory
    node_labels = [
        str(getattr(n, "label", "") or "")
        for n in (getattr(delta, "nodes", []) or [])[:40]
        if getattr(n, "label", None)
    ]
    summary_text = (
        f"Source `{src_id}` ({src_type}) in project `{base_proj}` on branch `{branch}`. "
        f"Tech stack: {', '.join(tech_stack) if tech_stack else 'unknown'}. "
        f"Files: {', '.join(prioritized_rel[:15])}. "
        f"Symbols: {', '.join(node_labels)}."
    )
    try:
        await add_hsg_memory(
            content=summary_text,
            tags=json.dumps(base_tags + ["summary"]),
            metadata={
                "source": "hub_bootstrap_summary",
                "project_id": base_proj,
                "branch": branch,
                "source_id": src_id,
                "scope": "source",
            },
            user_id=mem_user_id,
            source_id=src_id,
        )
        created += 1
    except Exception as exc:
        logger.debug("HSG summary ingest skipped for %s: %s", src_id, exc)

    return created


async def bootstrap_project_hub(
    project_id: str,
    workspace_root: str = ".",
    sources: Optional[List[Dict[str, Any]]] = None,
    shared_docs: Optional[List[Dict[str, Any]]] = None,
    relations: Optional[List[Dict[str, Any]]] = None,
    auto_discover: bool = True,
    sync_graphs: bool = True,
    auto_link: bool = True,
    upgrade_mode: str = "delta",
    root_branch: str = "main",
    author: str = "agent",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """One-shot zero-touch ProjectBrain Multi-Source Hub bootstrapper."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id is required")

    root = _validate_safe_workspace_dir(workspace_root)
    eff_root_branch = (root_branch or "main").strip() or "main"
    eff_author = (author or "agent").strip() or "agent"
    eff_upgrade_mode = (upgrade_mode or "delta").strip().lower() or "delta"

    # Step 1: Normalize project_id & ensure physical project DB
    base_proj, _, _ = parse_tenant_spec(project_id, default_branch=eff_root_branch)
    proj_info = tenant_manager.ensure_project_database(
        base_proj,
        name=base_proj,
        description=description,
        root_branch=eff_root_branch,
    )
    db_name = proj_info.get("db_name")

    # Step 2: Auto-discover sources & shared docs when omitted
    discovered: Optional[Dict[str, Any]] = None
    if (sources is None or shared_docs is None) and auto_discover:
        discovered = discover_workspace_sources(str(root))

    eff_sources: List[Dict[str, Any]] = (
        list(sources)
        if sources is not None
        else (list(discovered["sources"]) if discovered else [])
    )
    eff_shared_docs: List[Dict[str, Any]] = (
        list(shared_docs)
        if shared_docs is not None
        else (list(discovered["shared_docs"]) if discovered else [])
    )

    extractor = GraphifyExtractor(cache_dir=root / ".projectbrain" / "cache")
    store = _get_bootstrap_graph_store()

    sources_registered: List[Dict[str, Any]] = []
    sync_results: List[Dict[str, Any]] = []
    shared_docs_ingested: List[Dict[str, Any]] = []
    relations_applied: List[Dict[str, Any]] = []
    workspace_map: Dict[str, str] = {}

    try:
        # Step 3: Register sources, ensure branch schemas, sync Graphify AST & HSG memories
        for src_entry in eff_sources:
            if not isinstance(src_entry, dict):
                continue
            raw_src_id = str(src_entry.get("source_id") or "").strip()
            if not raw_src_id:
                raise ValueError("Each source entry must include a non-empty 'source_id'")

            raw_sub = (
                src_entry.get("path")
                or src_entry.get("workspace_subpath")
                or raw_src_id
            )
            resolved_src_dir = _resolve_safe_subpath(
                root, str(raw_sub), expect_dir=True, allow_external=True
            )
            if not resolved_src_dir.is_dir():
                raise ValueError(
                    f"Source directory for '{raw_src_id}' does not exist: {raw_sub}"
                )

            br = str(src_entry.get("branch") or eff_root_branch).strip() or eff_root_branch
            disp = src_entry.get("display_name") or _format_display_name(raw_src_id)
            src_type = str(src_entry.get("source_type") or "service").strip() or "service"
            tech_stack = src_entry.get("tech_stack")
            if isinstance(tech_stack, str):
                tech_stack_list = [t.strip() for t in tech_stack.split(",") if t.strip()]
            elif isinstance(tech_stack, list):
                tech_stack_list = [str(t).strip() for t in tech_stack if str(t).strip()]
            else:
                tech_stack_list = []

            cur_ver = str(src_entry.get("current_version") or "1.0.0").strip() or "1.0.0"
            inherit_shared = bool(src_entry.get("inherit_shared_docs", True))

            try:
                rel_sub = str(resolved_src_dir.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel_sub = str(resolved_src_dir)

            reg_src = tenant_manager.register_project_source(
                project_id=base_proj,
                source_id=raw_src_id,
                display_name=disp,
                source_type=src_type,
                tech_stack=tech_stack_list,
                workspace_subpath=str(resolved_src_dir),
                root_branch=eff_root_branch,
                current_version=cur_ver,
                inherit_shared_docs=inherit_shared,
                metadata={
                    "workspace_root": str(root),
                    "relative_subpath": rel_sub,
                    "markers": list(src_entry.get("markers") or []),
                    **({"language": src_entry["language"]} if src_entry.get("language") else {}),
                },
            )
            tenant_manager.ensure_source_branch_schema(
                base_proj,
                raw_src_id,
                br,
                base_branch=eff_root_branch,
                author=eff_author,
                has_graph=True,
                has_doc=True,
                source_version=cur_ver,
            )
            sources_registered.append(reg_src)
            workspace_map[raw_src_id] = str(resolved_src_dir)

            if sync_graphs and resolved_src_dir.is_dir():
                delta = extractor.extract_workspace(
                    workspace_path=resolved_src_dir,
                    project_id=base_proj,
                    branch=br,
                    include_docs=(resolved_src_dir != root),
                )
                if delta.nodes:
                    try:
                        delta = extractor.cluster_graph(delta)
                    except Exception as ce:
                        logger.debug("Cluster skipped for %s@%s: %s", base_proj, raw_src_id, ce)

                synced_files_list = [
                    sf["file_path"] if isinstance(sf, dict) else str(sf)
                    for sf in delta.synced_files
                ]
                graph_target = f"{base_proj}@{raw_src_id}"
                sync_res = store.sync_branch_delta(
                    project_id=graph_target,
                    branch_name=br,
                    delta=delta,
                    partial=False,
                    synced_files=synced_files_list,
                    deleted_files=delta.deleted_files,
                    author=eff_author,
                    source_id=raw_src_id,
                    upgrade_mode=eff_upgrade_mode,
                )
                hsg_count = await _sync_source_hsg_memories(
                    base_proj=base_proj,
                    src_id=raw_src_id,
                    src_type=src_type,
                    tech_stack=tech_stack_list,
                    branch=br,
                    src_dir=resolved_src_dir,
                    delta=delta,
                )
                upg = tenant_manager.record_source_upgrade(
                    project_id=base_proj,
                    source_id=raw_src_id,
                    branch_name=br,
                    to_version=cur_ver,
                    upgrade_mode=eff_upgrade_mode,
                    nodes_delta=len(delta.nodes),
                    edges_delta=len(delta.edges),
                    actor=eff_author,
                )
                sync_results.append(
                    {
                        "source_id": raw_src_id,
                        "branch": br,
                        "nodes_count": len(delta.nodes),
                        "edges_count": len(delta.edges),
                        "hsg_memories_created": hsg_count,
                        "sync_generation": upg.get("sync_generation"),
                        "schema_name": sync_res.get("schema_name"),
                    }
                )

        # Step 4: Ingest shared docs into pb_root (__shared__) + sync doc graph
        for doc_entry in eff_shared_docs:
            if not isinstance(doc_entry, dict):
                continue
            raw_doc_path = doc_entry.get("path")
            explicit_content = doc_entry.get("content") or doc_entry.get("markdown_content")
            doc_role = str(doc_entry.get("doc_role") or "reference").strip() or "reference"
            explicit_doc_key = doc_entry.get("doc_key")
            raw_tags = doc_entry.get("tags")
            if isinstance(raw_tags, list):
                doc_tags = [str(t).strip() for t in raw_tags if str(t).strip()]
            elif isinstance(raw_tags, str):
                doc_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            else:
                doc_tags = ["shared_doc", doc_role]

            file_bytes: Optional[bytes] = None
            filename = str(doc_entry.get("filename") or "").strip()
            if raw_doc_path:
                doc_path = _resolve_safe_subpath(
                    root, str(raw_doc_path), expect_dir=False, allow_external=True
                )
                if not doc_path.is_file():
                    raise ValueError(f"Shared doc file does not exist: {raw_doc_path}")
                if not filename:
                    filename = doc_path.name
                if explicit_content is None:
                    file_bytes = doc_path.read_bytes()
                    if doc_path.suffix.lower() in _LEGACY_TEXT_DOC_EXTS:
                        decoded_doc, _ = decode_legacy_bytes(file_bytes)
                        explicit_content = decoded_doc
            elif not filename:
                filename = f"{explicit_doc_key or 'shared_doc'}.md"

            if explicit_content is None and (file_bytes is None or not file_bytes.strip()):
                continue
            if explicit_content is not None and not str(explicit_content).strip():
                continue

            ver_res = await ingest_document_versioned(
                filename=filename,
                file_bytes=file_bytes if explicit_content is None else None,
                markdown_content=str(explicit_content) if explicit_content is not None else None,
                project_id=base_proj,
                branch=eff_root_branch,
                author=eff_author,
                tags=doc_tags,
                doc_key=explicit_doc_key,
                source_id="__shared__",
                scope="project_shared",
                doc_role=doc_role,
            )

            eff_md = str(ver_res.get("markdown_content") or explicit_content or "")
            doc_meta = {
                "author": eff_author,
                "file_type": "document",
                "doc_key": ver_res["doc_key"],
                "doc_version": ver_res["doc_version"],
                "scope": "project_shared",
                "doc_role": doc_role,
                "source_id": "__shared__",
                "snippet": eff_md[:1500],
            }
            doc_delta = extractor.extract_document_graph(
                file_path=filename,
                markdown_content=eff_md,
                project_id=base_proj,
                branch=eff_root_branch,
                metadata=doc_meta,
            )
            doc_sync_res = store.sync_branch_delta(
                project_id=f"{base_proj}@__shared__",
                branch_name=eff_root_branch,
                delta=doc_delta,
                partial=True,
                synced_files=[filename],
                author=eff_author,
                source_id="__shared__",
                upgrade_mode=eff_upgrade_mode,
            )
            shared_docs_ingested.append(
                {
                    "filename": filename,
                    "doc_key": ver_res["doc_key"],
                    "doc_version": ver_res["doc_version"],
                    "doc_role": doc_role,
                    "memories_created": ver_res["memories_created"],
                    "nodes_upserted": doc_sync_res.get("nodes_upserted", len(doc_delta.nodes)),
                    "edges_upserted": doc_sync_res.get("edges_upserted", len(doc_delta.edges)),
                }
            )

        # Step 5: Apply explicit manual relations + run CrossSourceLinker auto-link
        for rel in relations or []:
            if not isinstance(rel, dict):
                continue
            saved_rel = tenant_manager.upsert_cross_source_relation(
                project_id=base_proj,
                src_source_id=str(rel["src_source_id"]),
                dst_source_id=str(rel["dst_source_id"]),
                relation_type=str(rel["relation_type"]),
                src_branch=str(rel.get("src_branch") or eff_root_branch),
                dst_branch=str(rel.get("dst_branch") or eff_root_branch),
                src_node_id=str(rel.get("src_node_id") or ""),
                dst_node_id=str(rel.get("dst_node_id") or ""),
                src_file_path=rel.get("src_file_path"),
                dst_doc_key=rel.get("dst_doc_key"),
                origin="manual",
                confidence="MANUAL",
                contract_key=str(rel.get("contract_key") or ""),
                contract_metadata=rel.get("contract_metadata"),
                status="pinned" if rel.get("pin", True) else str(rel.get("status") or "active"),
                is_bidirectional=bool(rel.get("is_bidirectional", False)),
                created_by=eff_author,
            )
            relations_applied.append(saved_rel)

        if relations is None and not auto_link and discovered and discovered.get("relations"):
            for d_rel in discovered["relations"]:
                saved_rel = tenant_manager.upsert_cross_source_relation(
                    project_id=base_proj,
                    src_source_id=str(d_rel["src_source_id"]),
                    dst_source_id=str(d_rel["dst_source_id"]),
                    relation_type=str(d_rel["relation_type"]),
                    src_branch=eff_root_branch,
                    dst_branch=eff_root_branch,
                    origin="auto_detected",
                    confidence="high",
                    confidence_score=0.95,
                    contract_key=str(d_rel.get("contract_key") or ""),
                    contract_metadata=d_rel.get("contract_metadata"),
                    status="active",
                    is_bidirectional=False,
                    created_by="hub_bootstrap",
                )
                relations_applied.append(saved_rel)

        auto_link_summary: Dict[str, Any] = {
            "status": "skipped",
            "relations_upserted": 0,
            "drifted_count": 0,
            "stale_count": 0,
            "relations": [],
        }
        if auto_link and (sources_registered or shared_docs_ingested):
            linker = CrossSourceLinker()
            auto_link_summary = linker.auto_link_project(
                base_proj,
                branch=eff_root_branch,
                workspace_map=workspace_map or None,
            )
    finally:
        store.close()

    # Step 6: Invalidate query caches and return rich summary
    invalidate_source_query_cache(base_proj)
    invalidate_query_cache()

    first_src = sources_registered[0]["source_id"] if sources_registered else None
    user_spec = f"{base_proj}@{first_src}:{eff_root_branch}" if first_src else f"{base_proj}:{eff_root_branch}"

    mcp_query_args: Dict[str, Any] = {
        "query": "architecture API endpoints schemas",
        "user_id": user_spec,
        "include_shared_docs": True,
        "relation_depth": 1,
    }
    mcp_hybrid_args: Dict[str, Any] = {
        "query": "architecture API endpoints schemas",
        "project_id": base_proj,
        "branch": eff_root_branch,
        "include_shared_docs": True,
        "relation_depth": 1,
    }
    if first_src:
        mcp_query_args["source_id"] = first_src
        mcp_hybrid_args["source_id"] = first_src

    return {
        "status": "ok",
        "project_id": base_proj,
        "db_name": db_name,
        "workspace_root": str(root),
        "root_branch": eff_root_branch,
        "sources_registered": sources_registered,
        "shared_docs_ingested": shared_docs_ingested,
        "sync_results": sync_results,
        "relations_applied": relations_applied,
        "auto_link": auto_link_summary,
        "ready_queries": {
            "mcp_query_example": {
                "tool": "projectbrain_query",
                "arguments": mcp_query_args,
            },
            "mcp_hybrid_example": {
                "tool": "projectbrain_hybrid_query",
                "arguments": mcp_hybrid_args,
            },
        },
    }


def build_agent_setup_playbook(
    project_id: Optional[str] = None,
    workspace_root: str = ".",
) -> Dict[str, Any]:
    """Build zero-touch self-setup playbook, MCP sequence, and rules snippet for external AI agents."""
    resolved_root_str = workspace_root
    discovered_preview: Optional[Dict[str, Any]] = None
    try:
        discovered = discover_workspace_sources(workspace_root)
        resolved_root_str = discovered["workspace_root"]
        discovered_preview = {
            "suggested_project_id": discovered["suggested_project_id"],
            "sources_count": len(discovered["sources"]),
            "source_ids": [s["source_id"] for s in discovered["sources"]],
            "shared_docs_count": len(discovered["shared_docs"]),
            "shared_doc_keys": [d["doc_key"] for d in discovered["shared_docs"]],
        }
    except Exception:
        try:
            resolved_root_str = str(Path(workspace_root).expanduser().resolve())
        except Exception:
            resolved_root_str = str(workspace_root)

    if project_id and project_id.strip():
        eff_proj = parse_tenant_spec(project_id.strip())[0]
    elif discovered_preview and discovered_preview.get("suggested_project_id"):
        eff_proj = str(discovered_preview["suggested_project_id"])
    else:
        eff_proj = "my-project"

    first_src = (
        discovered_preview["source_ids"][0]
        if discovered_preview and discovered_preview.get("source_ids")
        else "backend-api"
    )

    mcp_sequence = [
        {
            "step": 1,
            "title": "Discover workspace sources & shared docs (read-only)",
            "tool": "projectbrain_discover_workspace_sources",
            "arguments": {"workspace_root": resolved_root_str},
        },
        {
            "step": 2,
            "title": "Bootstrap multi-source hub in one shot",
            "tool": "projectbrain_bootstrap_project_hub",
            "arguments": {
                "project_id": eff_proj,
                "workspace_root": resolved_root_str,
                "auto_discover": True,
                "sync_graphs": True,
                "auto_link": True,
            },
        },
        {
            "step": 3,
            "title": "Verify database & source topology",
            "tool": "projectbrain_database_topology",
            "arguments": {"project_id": eff_proj},
        },
        {
            "step": 4,
            "title": "Query source + shared specs + connected sibling services",
            "tool": "projectbrain_hybrid_query",
            "arguments": {
                "query": "API contracts schemas endpoints",
                "project_id": eff_proj,
                "branch": "main",
                "source_id": first_src,
                "include_shared_docs": True,
                "relation_depth": 1,
            },
        },
        {
            "step": 5,
            "title": "Query HSG cognitive memories with cross-source expansion",
            "tool": "projectbrain_query",
            "arguments": {
                "query": "API contracts schemas endpoints",
                "user_id": f"{eff_proj}@{first_src}:main",
                "source_id": first_src,
                "include_shared_docs": True,
                "relation_depth": 1,
            },
        },
    ]

    steps = [
        {
            "step": 1,
            "phase": "discover",
            "description": "Call `projectbrain_discover_workspace_sources` (read-only) to detect services, frontends, workers, infra, and shared docs.",
        },
        {
            "step": 2,
            "phase": "bootstrap",
            "description": "Call `projectbrain_bootstrap_project_hub` once to provision the project DB, register sub-sources, sync Graphify ASTs, ingest `__shared__` specs, and auto-link cross-source contracts.",
        },
        {
            "step": 3,
            "phase": "retrieve",
            "description": "Query using `projectbrain_hybrid_query` or `projectbrain_query` with `source_id`, `include_shared_docs=True`, and `relation_depth=1` so hits include source code, `__shared__` specs, and linked sibling services.",
        },
        {
            "step": 4,
            "phase": "impact_and_drift",
            "description": "Before changing any shared DTO or API route, call `projectbrain_cross_source_impact` to inspect affected downstream/upstream sources and drift.",
        },
        {
            "step": 5,
            "phase": "incremental_sync",
            "description": "After editing a sub-source or shared doc, call `projectbrain_sync_graphify` (with `source_id`) or `projectbrain_upload_document` (with `scope='project_shared'`).",
        },
    ]

    rules_snippet = (
        f"## ProjectBrain Multi-Source Hub Rules (`{eff_proj}`)\n\n"
        "1. **Zero-Touch First-Time Setup**: If ProjectBrain has not been initialized for this workspace yet:\n"
        "   - Call `projectbrain_discover_workspace_sources(workspace_root=\".\")` to inspect detected sources and shared docs.\n"
        f"   - Call `projectbrain_bootstrap_project_hub(project_id=\"{eff_proj}\", workspace_root=\".\")` once.\n"
        "2. **Always Scope Queries by Sub-Source**:\n"
        f"   - Use `projectbrain_hybrid_query(project_id=\"{eff_proj}\", source_id=\"<your-source>\", include_shared_docs=True, relation_depth=1, query=...)`.\n"
        f"   - Use `projectbrain_query(user_id=\"{eff_proj}@<your-source>:main\", source_id=\"<your-source>\", include_shared_docs=True, relation_depth=1, query=...)`.\n"
        "3. **Check Cross-Source Impact Before Contract Changes**:\n"
        f"   - Run `projectbrain_cross_source_impact(project_id=\"{eff_proj}\", target=\"<endpoint-or-schema>\", source_id=\"<your-source>\")` before modifying APIs, DTOs, or events.\n"
        "4. **Keep Hub Fresh Incrementally**:\n"
        f"   - After code edits: `projectbrain_sync_graphify(project_id=\"{eff_proj}\", source_id=\"<your-source>\", workspace_path=\"<subpath>\")`.\n"
        f"   - After shared spec/ADR edits: `projectbrain_upload_document(project_id=\"{eff_proj}\", scope=\"project_shared\", file_path=\"<doc>\")`.\n"
    )

    agent_configs = {
        "Claude Code": {
            "target_file": "CLAUDE.md",
            "instruction": "Paste `rules_snippet` into CLAUDE.md and connect ProjectBrain MCP server.",
        },
        "Cursor": {
            "target_file": ".cursor/rules/projectbrain.mdc or .cursorrules",
            "instruction": "Paste `rules_snippet` into Cursor project rules.",
        },
        "Antigravity": {
            "target_file": ".agents/rules/projectbrain.md",
            "instruction": "Paste `rules_snippet` or read MCP resource `projectbrain://guides/agent-multi-source-setup`.",
        },
        "Codex": {
            "target_file": "AGENTS.md",
            "instruction": "Paste `rules_snippet` into AGENTS.md at repository root.",
        },
    }

    return {
        "status": "ok",
        "project_id": eff_proj,
        "workspace_root": resolved_root_str,
        "resource_uri": "projectbrain://guides/agent-multi-source-setup",
        "supported_agents": ["Claude Code", "Cursor", "Antigravity", "Codex"],
        "discovered_preview": discovered_preview,
        "steps": steps,
        "mcp_sequence": mcp_sequence,
        "rules_snippet": rules_snippet,
        "agent_configs": agent_configs,
    }


__all__ = [
    "discover_workspace_sources",
    "bootstrap_project_hub",
    "build_agent_setup_playbook",
]
