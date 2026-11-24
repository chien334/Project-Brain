"""Cross-Source Contract Surface Extractor, Multi-Signal Linker, and Drift Detector (Wave 2A).

Extracts contract surfaces across heterogeneous project sources (HTTP endpoints, HTTP client
calls, Pydantic/TypeScript/SQL/OpenAPI/Go/Java/Proto schemas, async pub/sub events, infra
compose/Terraform/env definitions, and project-shared specification docs), persists them to
``cross_source_contract_artifacts`` in Project DB ``pb_root``, reconciles typed
``cross_source_relations`` (``calls_api``, ``shares_schema``, ``consumes_events``,
``deploys``, ``depends_on``, ``documented_by``), preserves manual/pinned/rejected links
unconditionally, and flags signature drift or missing providers as ``drifted`` / ``stale``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

import yaml

from projectbrain.core.tenant_manager import (
    _ROOT_ONLY_STATEMENTS,
    _adapt_ddl_for_postgres,
    parse_tenant_from_user_id,
    tenant_manager,
)
from projectbrain.graphify_engine.legacy_encoding import read_text_with_encoding

logger = logging.getLogger("cross_source_linker")

_WALK_EXTENSIONS: Set[str] = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".java",
    ".tf",
    ".yml",
    ".yaml",
    ".json",
    ".proto",
    ".md",
    ".sql",
    # Legacy C/C++, C#, Windows IPC/COM/RC/DEF, VB6, Delphi, COBOL, VS projects, PLC/LASAL
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".inl",
    ".tpp",
    ".cs",
    ".vb",
    ".idl",
    ".def",
    ".rc",
    ".reg",
    ".bat",
    ".cmd",
    ".vcxproj",
    ".vcproj",
    ".csproj",
    ".vbproj",
    ".sln",
    ".st",
    ".scl",
    ".exp",
    ".pou",
    ".lcp",
    ".lxp",
    ".bas",
    ".cls",
    ".frm",
    ".vbp",
    ".pas",
    ".dfm",
    ".dpr",
    ".cbl",
    ".cob",
}

_SKIP_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".projectbrain",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}

_HTTP_METHODS: Tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "OPTIONS",
    "HEAD",
)

_SCHEMA_BASE_NAMES: Set[str] = {
    "BaseModel",
    "BaseSettings",
    "BaseSchema",
    "Schema",
    "TypedDict",
    "SQLModel",
    "NamedTuple",
    "Struct",
    "Base",
}

_SCHEMA_SUFFIXES: Tuple[str, ...] = (
    "DTO",
    "Dto",
    "Schema",
    "Model",
    "Payload",
    "Request",
    "Response",
    "Event",
    "Entity",
    "Contract",
)

_IGNORED_ASSET_EXTS: Tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".scss",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)

_DOC_STOP_TOKENS: Set[str] = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "true",
    "false",
    "null",
    "none",
    "json",
    "yaml",
    "http",
    "https",
    "api",
    "main",
    "id",
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "str",
    "int",
    "float",
    "bool",
    "dict",
    "list",
    "any",
}

_BACKEND_EXTS: Set[str] = {
    ".py",
    ".go",
    ".java",
    ".sql",
    ".proto",
    ".yaml",
    ".yml",
    ".json",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".idl",
    ".def",
    ".st",
    ".scl",
    ".exp",
    ".pou",
    ".lcp",
    ".lxp",
    ".cs",
}
_FRONTEND_EXTS: Set[str] = {".ts", ".tsx", ".js", ".jsx"}

_CPP_CS_EXTS: Set[str] = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".inl", ".tpp", ".cs", ".vb"}
_ST_LASAL_EXTS: Set[str] = {".st", ".scl", ".exp", ".pou", ".lcp", ".lxp"}
_VB6_DELPHI_EXTS: Set[str] = {".bas", ".cls", ".frm", ".pas"}
_VS_PROJ_EXTS: Set[str] = {".vcxproj", ".vcproj", ".csproj", ".vbproj"}

_CPP_TOKEN_STOPWORDS: Set[str] = {
    "int", "void", "char", "bool", "true", "false", "null", "nullptr", "const", "static",
    "return", "struct", "class", "enum", "typedef", "public", "private", "protected",
    "virtual", "override", "inline", "extern", "dword", "word", "byte", "handle",
    "hmodule", "hwnd", "lparam", "wparam", "lresult", "hresult", "lpwstr", "lpcwstr",
    "lpstr", "lpcstr", "size_t", "uint32_t", "int32_t", "uint16_t", "uint8_t",
    "include", "define", "ifdef", "ifndef", "endif", "pragma", "using", "namespace",
    "string", "wstring", "std", "sizeof", "winapi", "stdcall", "cdecl", "callback",
    "invalid_handle_value", "open_existing", "generic_read", "generic_write",
    "page_readwrite", "file_map_all_access", "pipe_access_duplex", "pipe_type_message",
}

_LOCAL_SOCKET_HOSTS: Set[str] = {"", "localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}


def _sha256_json(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _schema_signature_hash(fields: List[Tuple[str, str]]) -> str:
    normalized = sorted(
        (str(name).strip(), re.sub(r"\s+", " ", str(ftype).strip()))
        for name, ftype in fields
        if str(name).strip()
    )
    return _sha256_json(normalized)


def _normalize_http_path(raw_path: str) -> Tuple[str, str]:
    """Normalize URL/route path into `(canonical_path, short_suffix)`."""
    cleaned = str(raw_path or "").strip().strip("`'\"")
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0].strip()

    # Strip leading scheme+host or leading template base URL expressions like ${API_URL}/... or {base_url}/...
    cleaned = re.sub(r"^https?://[^/]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:\$\{[^}]+\}|\{[a-zA-Z0-9_]*(?:url|base|host|origin|endpoint)[a-zA-Z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    cleaned = re.sub(r"/{2,}", "/", cleaned)
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/")

    segments: List[str] = []
    for seg in cleaned.split("/"):
        if not seg:
            continue
        if (
            (seg.startswith("{") and seg.endswith("}"))
            or (seg.startswith("${") and seg.endswith("}"))
            or (seg.startswith("<") and seg.endswith(">"))
            or seg.startswith(":")
            or re.fullmatch(r"\d+", seg)
        ):
            segments.append("{param}")
        else:
            segments.append(seg)

    norm_path = "/" + "/".join(segments) if segments else "/"
    non_param = [s for s in segments if s != "{param}"]
    if len(segments) >= 2:
        short_suffix = "/" + "/".join(segments[-2:])
    elif non_param:
        short_suffix = "/" + non_param[-1]
    else:
        short_suffix = norm_path
    return norm_path, short_suffix


def _join_url_paths(prefix: str, path: str) -> str:
    p1 = (prefix or "").strip()
    p2 = (path or "").strip()
    if not p1:
        return p2 or "/"
    if not p2 or p2 == "/":
        return p1 if p1.startswith("/") else "/" + p1
    if p2.startswith(p1.rstrip("/") + "/") or p2 == p1.rstrip("/"):
        return p2
    return "/" + "/".join(
        seg for seg in (p1.strip("/"), p2.strip("/")) if seg
    )


def _normalize_schema_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(name or "").strip().lower())


def _canonical_contract_cmp(key: str) -> str:
    raw = str(key or "").strip()
    m = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)[\s:]+(/.*)$", raw, re.IGNORECASE)
    if m:
        norm_p, _ = _normalize_http_path(m.group(2))
        return f"{m.group(1).upper()}:{norm_p}"
    return raw.lower()


def _derive_doc_key(filename: str) -> str:
    stem = Path(str(filename or "doc")).stem.lower()
    cleaned = re.sub(r"[^a-z0-9_\-]+", "_", stem).strip("_")
    return cleaned or "doc"


def _extract_balanced_block(text: str, open_idx: int, open_ch: str = "{", close_ch: str = "}") -> str:
    depth = 0
    in_sq = False
    in_dq = False
    in_bt = False
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        prev = text[i - 1] if i > 0 else ""
        if ch == "'" and not in_dq and not in_bt and prev != "\\":
            in_sq = not in_sq
        elif ch == '"' and not in_sq and not in_bt and prev != "\\":
            in_dq = not in_dq
        elif ch == "`" and not in_sq and not in_dq and prev != "\\":
            in_bt = not in_bt
        elif not in_sq and not in_dq and not in_bt:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1 : i]
        i += 1
    return text[open_idx + 1 :]


def _strip_c_comments(text: str) -> str:
    no_block = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"//[^\n]*", "", no_block)


def _strip_comments_string_aware(
    text: str,
    *,
    line_prefixes: Tuple[str, ...] = ("//",),
    block_pairs: Tuple[Tuple[str, str], ...] = (("/*", "*/"),),
    backslash_escapes: bool = True,
) -> str:
    """Strip comments outside string literals while preserving line structure."""
    out: List[str] = []
    n = len(text)
    i = 0
    block_close: Optional[str] = None
    in_line = False
    quote_ch: Optional[str] = None

    while i < n:
        ch = text[i]
        if block_close is not None:
            if text.startswith(block_close, i):
                out.append(" ")
                i += len(block_close)
                block_close = None
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
        elif in_line:
            if ch == "\n":
                out.append("\n")
                in_line = False
            else:
                out.append(" ")
            i += 1
        elif quote_ch is not None:
            if backslash_escapes and ch == "\\" and i + 1 < n:
                out.append(ch + text[i + 1])
                i += 2
            elif ch == quote_ch:
                if not backslash_escapes and i + 1 < n and text[i + 1] == quote_ch:
                    out.append(ch + ch)
                    i += 2
                else:
                    out.append(ch)
                    quote_ch = None
                    i += 1
            else:
                out.append(ch)
                i += 1
        else:
            matched_block = False
            for open_tok, close_tok in block_pairs:
                if text.startswith(open_tok, i):
                    out.append(" ")
                    block_close = close_tok
                    i += len(open_tok)
                    matched_block = True
                    break
            if matched_block:
                continue

            matched_line = False
            for pref in line_prefixes:
                if text.startswith(pref, i):
                    out.append(" ")
                    in_line = True
                    i += len(pref)
                    matched_line = True
                    break
            if matched_line:
                continue

            if ch in ('"', "'"):
                quote_ch = ch
            out.append(ch)
            i += 1

    return "".join(out)


def _split_top_level_args(arg_str: str) -> List[str]:
    """Split C/C++/C# call arguments at top-level commas."""
    args: List[str] = []
    buf: List[str] = []
    depth = 0
    quote_ch: Optional[str] = None
    i = 0
    n = len(arg_str)
    while i < n:
        ch = arg_str[i]
        if quote_ch is not None:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(arg_str[i + 1])
                i += 2
                continue
            if ch == quote_ch:
                quote_ch = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote_ch = ch
            buf.append(ch)
        elif ch in ("(", "[", "{"):
            depth += 1
            buf.append(ch)
        elif ch in (")", "]", "}"):
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf.clear()
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return args


def _unwrap_c_string_literal(expr: str) -> Optional[str]:
    """Unwrap C/C++/C# string literal wrappers like `L"..."`, `@"..."`, `_T("...")`, `TEXT("...")`."""
    s = str(expr or "").strip()
    m_wrap = re.match(r"^(?:_T|TEXT|_TEXT)\s*\(\s*(.+)\s*\)$", s)
    if m_wrap:
        s = m_wrap.group(1).strip()
    m_lit = re.match(r'^(?:u8|u|U|L|@|\$@|@\$)?("(?:[^"\\]|\\.)*")$', s, re.DOTALL)
    if m_lit:
        inner = m_lit.group(1)[1:-1]
        return inner
    m_raw = re.match(r'^R"[^(]*\(([\s\S]*)\)[^"]*"$', s)
    if m_raw:
        return m_raw.group(1)
    return None


def _extract_const_map(clean_text: str) -> Dict[str, str]:
    """Extract `#define` and `const`/`constexpr`/`static` string and numeric constants."""
    consts: Dict[str, str] = {}
    for m in re.finditer(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+([^\n]+)", clean_text, re.MULTILINE):
        name = m.group(1)
        val = m.group(2).strip()
        lit = _unwrap_c_string_literal(val)
        consts[name] = lit if lit is not None else val.strip("()")

    for m in re.finditer(
        r"\b(?:const|constexpr|static|readonly|final)\b[^;=\n]*\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;\n]+);",
        clean_text,
    ):
        name = m.group(1)
        val = m.group(2).strip()
        lit = _unwrap_c_string_literal(val)
        consts[name] = lit if lit is not None else val.strip("()")
    return consts


def _resolve_str_arg(raw_arg: str, const_map: Dict[str, str]) -> Optional[str]:
    """Resolve a call argument to its string value via literal unwrapping or `const_map`."""
    lit = _unwrap_c_string_literal(raw_arg)
    if lit is not None:
        return lit
    ident = str(raw_arg or "").strip()
    if ident in const_map:
        val = const_map[ident]
        lit2 = _unwrap_c_string_literal(val)
        return lit2 if lit2 is not None else val
    return None


def _normalize_pipe_name(raw: str) -> Optional[Tuple[str, str]]:
    """Normalize a Windows Named Pipe path into `(norm_key, display_name)`."""
    s = str(raw or "").strip().strip('"\'`')
    if not s:
        return None
    m = re.search(r"(?:\\\\|\\)+\.(?:\\\\|\\)+pipe(?:\\\\|\\)+([A-Za-z0-9_.\-/\\]+)$", s, re.IGNORECASE)
    if m:
        leaf = m.group(1).replace("\\\\", "/").replace("\\", "/").strip("/")
    elif "/" not in s and "\\" not in s and re.fullmatch(r"[A-Za-z0-9_.\-]+", s):
        # C# NamedPipeServerStream("MyLegacyPipe") style bare pipe name
        leaf = s
    else:
        return None
    if not leaf:
        return None
    display = "\\\\.\\pipe\\" + leaf.replace("/", "\\")
    return leaf.lower(), display


def _normalize_shm_name(raw: str, *, allow_unscoped: bool = False) -> Optional[Tuple[str, str]]:
    """Normalize `Global\\...` / `Local\\...` shared memory names into `(norm_key, display_name)`."""
    s = str(raw or "").strip().strip('"\'`')
    if not s or s.upper() in ("NULL", "NULLPTR", "0"):
        return None
    m = re.match(r"^(Global|Local)(?:\\\\|\\)+([A-Za-z0-9_.\-/]+)$", s, re.IGNORECASE)
    if m:
        scope = m.group(1).lower()
        name = m.group(2).strip()
        return f"{scope}/{name.lower()}", f"{m.group(1).capitalize()}\\{name}"
    if allow_unscoped and re.fullmatch(r"[A-Za-z0-9_.\-]+", s):
        return f"unscoped/{s.lower()}", s
    return None


def _normalize_uuid(raw: str) -> Optional[str]:
    """Normalize a COM/IDL UUID/GUID string to lowercase hyphenated form."""
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", str(raw or ""))
    return m.group(1).lower() if m else None


def _normalize_dll_stem(raw: str) -> Optional[Tuple[str, str]]:
    """Normalize a DLL/LIB path or name into `(norm_stem, display_filename)`."""
    s = str(raw or "").strip().strip('"\'`')
    if not s:
        return None
    leaf = s.replace("\\\\", "/").replace("\\", "/").split("/")[-1].strip()
    if not leaf:
        return None
    stem = re.sub(r"\.(?:dll|lib|so|dylib)$", "", leaf, flags=re.IGNORECASE)
    if not stem or not re.match(r"^[A-Za-z0-9_.\-]+$", stem):
        return None
    return stem.lower(), leaf


def _normalize_win32_msg_expr(raw: str, const_map: Optional[Dict[str, str]] = None) -> Optional[Tuple[str, str]]:
    """Normalize a Win32 message identifier (`WM_COPYDATA`, `WM_USER + N`, `WM_APP + N`)."""
    s = str(raw or "").strip()
    if not s:
        return None
    if const_map and s in const_map:
        s = const_map[s]
    s_clean = re.sub(r"\s+", " ", s.strip("() "))
    low = s_clean.lower()
    if low in ("wm_copydata", "0x004a", "0x4a", "74"):
        return "wm_copydata", "WM_COPYDATA"
    m_off = re.fullmatch(r"(WM_USER|WM_APP)\s*\+\s*(\d+|0x[0-9a-fA-F]+)", s_clean, re.IGNORECASE)
    if m_off:
        base = m_off.group(1).upper()
        off_raw = m_off.group(2)
        off_int = int(off_raw, 16) if off_raw.lower().startswith("0x") else int(off_raw)
        return f"{base.lower()}+{off_int}", f"{base} + {off_int}"
    if re.fullmatch(r"WM_[A-Z0-9_]+", s_clean) and s_clean not in (
        "WM_CLOSE", "WM_DESTROY", "WM_PAINT", "WM_CREATE", "WM_COMMAND", "WM_SIZE", "WM_INITDIALOG", "WM_TIMER",
    ):
        return s_clean.lower(), s_clean
    return None


def _parse_cpp_struct_fields(body: str) -> List[Tuple[str, str]]:
    """Parse C/C++/C# struct fields into `(field_name, field_type)` pairs."""
    fields: List[Tuple[str, str]] = []
    for raw_stmt in body.split(";"):
        stmt = " ".join(raw_stmt.strip().split())
        if not stmt or "{" in stmt or "}" in stmt or "(" in stmt:
            continue
        stmt = re.sub(r"\s*:\s*\d+\s*$", "", stmt)
        stmt = re.sub(r"^(?:public|private|protected|internal|readonly|volatile)\s+", "", stmt)
        m = re.match(r"^(.+?)\s+([*&\s]*[A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?$", stmt)
        if m:
            ftype = m.group(1).strip()
            fname = m.group(2).lstrip("*& ").strip()
            arr = (m.group(3) or "").strip()
            if fname and ftype not in ("typedef", "struct", "union", "enum", "class"):
                fields.append((fname, f"{ftype}{arr}".strip()))
    return fields


def _is_relevant_api_path(raw_path: str) -> bool:
    s = str(raw_path or "").strip()
    if not s or s.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return False
    low = s.split("?", 1)[0].lower()
    if low.endswith(_IGNORED_ASSET_EXTS):
        return False
    return s.startswith("/") or low.startswith(("http://", "https://")) or "/api/" in low


def _extract_host_service_from_url(val: str) -> Optional[str]:
    m = re.match(r"^https?://([a-zA-Z0-9_.\-]+?)(?::\d+)?(?:/|$)", str(val or "").strip(), re.IGNORECASE)
    if not m:
        return None
    host = m.group(1).lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return None
    return host.split(".", 1)[0]


def _make_artifact(
    *,
    project_id: str,
    source_id: str,
    branch: str,
    contract_kind: str,
    role: str,
    normalized_key: str,
    node_id: Optional[str] = None,
    file_path: Optional[str] = None,
    symbol_name: Optional[str] = None,
    signature_hash: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    sync_generation: int = 0,
) -> Dict[str, Any]:
    clean_file = str(file_path or "").replace("\\", "/")
    clean_sym = str(symbol_name or normalized_key)
    eff_node_id = str(node_id).strip() if node_id else f"{contract_kind}:{clean_file}:{clean_sym}"
    seed = f"{project_id}::{source_id}::{branch}::{contract_kind}::{role}::{normalized_key}::{eff_node_id}"
    art_id = f"art_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"
    return {
        "id": art_id,
        "project_id": project_id,
        "source_id": source_id,
        "branch": branch,
        "contract_kind": contract_kind,
        "role": role,
        "normalized_key": normalized_key,
        "node_id": eff_node_id,
        "file_path": clean_file or None,
        "symbol_name": symbol_name or normalized_key,
        "signature_hash": signature_hash or _sha256_json({"kind": contract_kind, "key": normalized_key}),
        "metadata": dict(metadata or {}),
        "sync_generation": int(sync_generation or 0),
    }


class CrossSourceLinker:
    """Extracts multi-source contract surfaces, links cross-source relations, and detects drift."""

    # ------------------------------------------------------------------
    # Schema bootstrap in Project DB pb_root (or single-DB fallback)
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_tables(project_id: str) -> str:
        conn, is_pg, proj = tenant_manager._get_project_root_conn(project_id)
        for stmt in _ROOT_ONLY_STATEMENTS:
            clean_stmt = stmt.strip()
            if is_pg:
                clean_stmt = _adapt_ddl_for_postgres(clean_stmt)
                with conn.cursor() as cur:
                    cur.execute(clean_stmt)
            else:
                conn.execute(clean_stmt)
        if not is_pg:
            try:
                conn.commit()
            except Exception:
                pass
        return proj

    # ------------------------------------------------------------------
    # Phase A: Contract Surface Extraction
    # ------------------------------------------------------------------
    def _extract_from_python(
        self,
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return self._extract_from_python_regex(
                content, rel_path, project_id, source_id, branch, sync_gen
            )

        router_prefixes: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func_name = ""
                if isinstance(node.value.func, ast.Name):
                    func_name = node.value.func.id
                elif isinstance(node.value.func, ast.Attribute):
                    func_name = node.value.func.attr
                if func_name in ("APIRouter", "Blueprint", "FastAPI"):
                    prefix_val = ""
                    for kw in node.value.keywords:
                        if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            prefix_val = kw.value.value
                    if prefix_val:
                        for tgt in node.targets:
                            if isinstance(tgt, ast.Name):
                                router_prefixes[tgt.id] = prefix_val

        def _const_str(expr: Optional[ast.AST]) -> Optional[str]:
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                return expr.value
            if isinstance(expr, ast.JoinedStr):
                parts: List[str] = []
                for v in expr.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                    elif isinstance(v, ast.FormattedValue):
                        try:
                            inner = ast.unparse(v.value)
                        except Exception:
                            inner = "param"
                        parts.append("{" + inner + "}")
                return "".join(parts)
            return None

        def _extract_methods_kw(call: ast.Call) -> List[str]:
            for kw in call.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                    methods = [
                        str(elt.value).upper()
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
                    if methods:
                        return [m for m in methods if m in _HTTP_METHODS] or ["GET"]
            return ["GET"]

        def _is_schema_class(cls_node: ast.ClassDef) -> bool:
            for base in cls_node.bases:
                bname = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
                if bname in _SCHEMA_BASE_NAMES:
                    return True
            for dec in cls_node.decorator_list:
                dname = (
                    dec.id
                    if isinstance(dec, ast.Name)
                    else (
                        dec.attr
                        if isinstance(dec, ast.Attribute)
                        else (
                            dec.func.id
                            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                            else (
                                dec.func.attr
                                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                                else ""
                            )
                        )
                    )
                )
                if dname == "dataclass":
                    return True
            if cls_node.name.endswith(_SCHEMA_SUFFIXES):
                return any(isinstance(st, ast.AnnAssign) for st in cls_node.body)
            return False

        for node in ast.walk(tree):
            # 1. Pydantic / dataclass / TypedDict / SQLAlchemy schemas
            if isinstance(node, ast.ClassDef) and _is_schema_class(node):
                fields: List[Tuple[str, str]] = []
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        fname = stmt.target.id
                        if fname.startswith("_") or fname in ("model_config", "Config"):
                            continue
                        try:
                            ftype = ast.unparse(stmt.annotation)
                        except Exception:
                            ftype = "Any"
                        if ftype.startswith("ClassVar"):
                            continue
                        fields.append((fname, ftype))
                    elif isinstance(stmt, ast.Assign):
                        for tgt in stmt.targets:
                            if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                                if isinstance(stmt.value, ast.Call):
                                    cname = (
                                        stmt.value.func.attr
                                        if isinstance(stmt.value.func, ast.Attribute)
                                        else (stmt.value.func.id if isinstance(stmt.value.func, ast.Name) else "")
                                    )
                                    if cname in ("Column", "mapped_column", "Field") and stmt.value.args:
                                        try:
                                            ftype = ast.unparse(stmt.value.args[0])
                                        except Exception:
                                            ftype = "Any"
                                        fields.append((tgt.id, ftype))
                sig_hash = _schema_signature_hash(fields)
                norm_key = _normalize_schema_key(node.name)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=norm_key,
                        file_path=rel_path,
                        symbol_name=node.name,
                        signature_hash=sig_hash,
                        metadata={
                            "schema_name": node.name,
                            "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                            "language": "python",
                        },
                        sync_generation=sync_gen,
                    )
                )

            # 2. Function route decorators & event subscriber decorators
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params_sig: List[Tuple[str, str]] = []
                for arg in node.args.args:
                    if arg.arg in ("self", "cls"):
                        continue
                    try:
                        ann = ast.unparse(arg.annotation) if arg.annotation else "Any"
                    except Exception:
                        ann = "Any"
                    params_sig.append((arg.arg, ann))
                try:
                    ret_sig = ast.unparse(node.returns) if node.returns else ""
                except Exception:
                    ret_sig = ""

                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    dec_attr = ""
                    dec_obj = ""
                    if isinstance(dec.func, ast.Attribute):
                        dec_attr = dec.func.attr
                        if isinstance(dec.func.value, ast.Name):
                            dec_obj = dec.func.value.id
                    elif isinstance(dec.func, ast.Name):
                        dec_attr = dec.func.id

                    attr_up = dec_attr.upper()
                    attr_low = dec_attr.lower()
                    if attr_up in _HTTP_METHODS or attr_low in ("route", "api_route"):
                        raw_p = _const_str(dec.args[0]) if dec.args else None
                        if not raw_p:
                            for kw in dec.keywords:
                                if kw.arg == "path":
                                    raw_p = _const_str(kw.value)
                        if raw_p:
                            prefix = router_prefixes.get(dec_obj, "")
                            full_raw = _join_url_paths(prefix, raw_p)
                            norm_p, short_suf = _normalize_http_path(full_raw)
                            methods = (
                                [attr_up]
                                if attr_up in _HTTP_METHODS
                                else _extract_methods_kw(dec)
                            )
                            for m in methods:
                                norm_key = f"{m}:{norm_p}"
                                sig_hash = _sha256_json(
                                    {
                                        "method": m,
                                        "path": norm_p,
                                        "params": sorted(params_sig),
                                        "return": ret_sig,
                                    }
                                )
                                artifacts.append(
                                    _make_artifact(
                                        project_id=project_id,
                                        source_id=source_id,
                                        branch=branch,
                                        contract_kind="http_endpoint",
                                        role="provider",
                                        normalized_key=norm_key,
                                        file_path=rel_path,
                                        symbol_name=f"{m} {norm_p}",
                                        signature_hash=sig_hash,
                                        metadata={
                                            "method": m,
                                            "path": norm_p,
                                            "raw_path": full_raw,
                                            "short_suffix": short_suf,
                                            "handler": node.name,
                                            "language": "python",
                                        },
                                        sync_generation=sync_gen,
                                    )
                                )
                    elif attr_low in ("subscribe", "consume", "on_event", "event_handler"):
                        topic = _const_str(dec.args[0]) if dec.args else None
                        if not topic:
                            for kw in dec.keywords:
                                if kw.arg in ("topic", "event", "channel"):
                                    topic = _const_str(kw.value)
                        if topic and topic.strip():
                            clean_topic = topic.strip().lower()
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="event_sub",
                                    role="consumer",
                                    normalized_key=clean_topic,
                                    file_path=rel_path,
                                    symbol_name=clean_topic,
                                    signature_hash=_sha256_json({"topic": clean_topic}),
                                    metadata={
                                        "topic": clean_topic,
                                        "handler": node.name,
                                        "language": "python",
                                    },
                                    sync_generation=sync_gen,
                                )
                            )

            # 3. Function calls: httpx / requests HTTP clients and event publish/emit/consume
            elif isinstance(node, ast.Call):
                callee_attr = ""
                callee_obj = ""
                if isinstance(node.func, ast.Attribute):
                    callee_attr = node.func.attr.lower()
                    if isinstance(node.func.value, ast.Name):
                        callee_obj = node.func.value.id.lower()
                    elif isinstance(node.func.value, ast.Attribute):
                        callee_obj = node.func.value.attr.lower()
                elif isinstance(node.func, ast.Name):
                    callee_attr = node.func.id.lower()

                # HTTP client calls
                if callee_obj in ("httpx", "requests", "client", "http_client", "session", "aiohttp") and (
                    callee_attr.upper() in _HTTP_METHODS or callee_attr == "request"
                ):
                    if callee_attr == "request" and len(node.args) >= 2:
                        m_str = (_const_str(node.args[0]) or "ANY").upper()
                        url_str = _const_str(node.args[1])
                        explicit_m = m_str in _HTTP_METHODS
                    else:
                        m_str = callee_attr.upper()
                        url_str = _const_str(node.args[0]) if node.args else None
                        if not url_str:
                            for kw in node.keywords:
                                if kw.arg == "url":
                                    url_str = _const_str(kw.value)
                        explicit_m = m_str in _HTTP_METHODS
                    if url_str and _is_relevant_api_path(url_str):
                        norm_p, short_suf = _normalize_http_path(url_str)
                        norm_key = f"{m_str}:{norm_p}" if explicit_m else f"ANY:{norm_p}"
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="http_client_call",
                                role="consumer",
                                normalized_key=norm_key,
                                file_path=rel_path,
                                symbol_name=f"{m_str if explicit_m else 'CALL'} {norm_p}",
                                metadata={
                                    "method": m_str if explicit_m else "ANY",
                                    "method_explicit": explicit_m,
                                    "path": norm_p,
                                    "raw_path": url_str,
                                    "short_suffix": short_suf,
                                    "language": "python",
                                },
                                sync_generation=sync_gen,
                            )
                        )

                # Async event publish / emit / consume
                elif callee_attr in ("publish", "emit", "send_event", "produce", "publish_event", "emit_event"):
                    topic = _const_str(node.args[0]) if node.args else None
                    if not topic:
                        for kw in node.keywords:
                            if kw.arg in ("topic", "event", "channel"):
                                topic = _const_str(kw.value)
                    if topic and topic.strip():
                        clean_topic = topic.strip().lower()
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="event_pub",
                                role="provider",
                                normalized_key=clean_topic,
                                file_path=rel_path,
                                symbol_name=clean_topic,
                                signature_hash=_sha256_json({"topic": clean_topic}),
                                metadata={"topic": clean_topic, "language": "python"},
                                sync_generation=sync_gen,
                            )
                        )
                elif callee_attr in ("consume",) or (callee_obj and callee_attr == "subscribe"):
                    # Avoid double-counting decorator calls already handled via FunctionDef
                    topic = _const_str(node.args[0]) if node.args else None
                    if topic and topic.strip():
                        clean_topic = topic.strip().lower()
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="event_sub",
                                role="consumer",
                                normalized_key=clean_topic,
                                file_path=rel_path,
                                symbol_name=clean_topic,
                                signature_hash=_sha256_json({"topic": clean_topic}),
                                metadata={"topic": clean_topic, "language": "python"},
                                sync_generation=sync_gen,
                            )
                        )

        return artifacts

    def _extract_from_python_regex(
        self,
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for m in re.finditer(
            r"@(?:app|router|bp|\w+_router)\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']",
            content,
            re.IGNORECASE,
        ):
            method = m.group(1).upper()
            norm_p, short_suf = _normalize_http_path(m.group(2))
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_endpoint",
                    role="provider",
                    normalized_key=f"{method}:{norm_p}",
                    file_path=rel_path,
                    symbol_name=f"{method} {norm_p}",
                    metadata={"method": method, "path": norm_p, "short_suffix": short_suf},
                    sync_generation=sync_gen,
                )
            )
        artifacts.extend(
            self._extract_events_regex(content, rel_path, project_id, source_id, branch, sync_gen)
        )
        return artifacts

    @staticmethod
    def _extract_events_regex(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        pub_re = re.compile(
            r"(?:\.(?:publish|emit|send_event|produce|dispatch)|(?:publish|emit)_event)\(\s*[\"'`]([A-Za-z0-9_.:/\-]+)[\"'`]"
        )
        sub_re = re.compile(
            r"(?:@(?:[A-Za-z_][\w.]*\.)?subscribe|\.(?:subscribe|consume|on_event|handle_event)|consume)\(\s*[\"'`]([A-Za-z0-9_.:/\-]+)[\"'`]"
        )
        for m in pub_re.finditer(content):
            topic = m.group(1).strip().lower()
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="event_pub",
                    role="provider",
                    normalized_key=topic,
                    file_path=rel_path,
                    symbol_name=topic,
                    signature_hash=_sha256_json({"topic": topic}),
                    metadata={"topic": topic},
                    sync_generation=sync_gen,
                )
            )
        for m in sub_re.finditer(content):
            topic = m.group(1).strip().lower()
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="event_sub",
                    role="consumer",
                    normalized_key=topic,
                    file_path=rel_path,
                    symbol_name=topic,
                    signature_hash=_sha256_json({"topic": topic}),
                    metadata={"topic": topic},
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    def _extract_from_ts_js(
        self,
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_c_comments(content)

        # 1. NestJS @Controller prefix + @Get/@Post...
        ctrl_match = re.search(r"@Controller\(\s*[\"'`]([^\"'`]*)[\"'`]\s*\)", clean)
        ctrl_prefix = ctrl_match.group(1) if ctrl_match else ""
        has_server_routes = bool(ctrl_match)

        for m in re.finditer(
            r"@(Get|Post|Put|Delete|Patch|Options|Head)\(\s*(?:[\"'`]([^\"'`]*)[\"'`])?\s*\)",
            clean,
        ):
            has_server_routes = True
            method = m.group(1).upper()
            sub_path = m.group(2) or ""
            full_raw = _join_url_paths(ctrl_prefix, sub_path)
            norm_p, short_suf = _normalize_http_path(full_raw)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_endpoint",
                    role="provider",
                    normalized_key=f"{method}:{norm_p}",
                    file_path=rel_path,
                    symbol_name=f"{method} {norm_p}",
                    metadata={
                        "method": method,
                        "path": norm_p,
                        "raw_path": full_raw,
                        "short_suffix": short_suf,
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 2. Express / Fastify routes: router.get("/...", ...) / app.post("/...", ...)
        for m in re.finditer(
            r"\b(?:app|router|fastify|server)\.(get|post|put|delete|patch|options|head)\(\s*[\"'`]([^\"'`]+)[\"'`]",
            clean,
            re.IGNORECASE,
        ):
            has_server_routes = True
            method = m.group(1).upper()
            raw_p = m.group(2)
            norm_p, short_suf = _normalize_http_path(raw_p)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_endpoint",
                    role="provider",
                    normalized_key=f"{method}:{norm_p}",
                    file_path=rel_path,
                    symbol_name=f"{method} {norm_p}",
                    metadata={
                        "method": method,
                        "path": norm_p,
                        "raw_path": raw_p,
                        "short_suffix": short_suf,
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 3. fetch(...) client calls
        for m in re.finditer(r"\bfetch\s*\(", clean):
            call_args = _extract_balanced_block(clean, m.end() - 1, "(", ")")
            url_m = re.match(r"^\s*[\"'`]([^\"'`]+)[\"'`]", call_args)
            if not url_m:
                continue
            raw_url = url_m.group(1)
            if not _is_relevant_api_path(raw_url):
                continue
            meth_m = re.search(r"\bmethod\s*:\s*[\"'`]?([A-Za-z]+)[\"'`]?", call_args, re.IGNORECASE)
            explicit_m = bool(meth_m and meth_m.group(1).upper() in _HTTP_METHODS)
            method = meth_m.group(1).upper() if explicit_m else "ANY"
            norm_p, short_suf = _normalize_http_path(raw_url)
            norm_key = f"{method}:{norm_p}" if explicit_m else f"ANY:{norm_p}"
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_client_call",
                    role="consumer",
                    normalized_key=norm_key,
                    file_path=rel_path,
                    symbol_name=f"{method if explicit_m else 'FETCH'} {norm_p}",
                    metadata={
                        "method": method,
                        "method_explicit": explicit_m,
                        "path": norm_p,
                        "raw_path": raw_url,
                        "short_suffix": short_suf,
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 4. axios.<method>(...) & generic client.<method>(...)
        for m in re.finditer(
            r"\b(?:axios|api|apiClient|httpClient|client)\.(get|post|put|delete|patch|head|options)\s*(?:<([^>]+)>)?\(\s*[\"'`]([^\"'`]+)[\"'`]",
            clean,
            re.IGNORECASE,
        ):
            method = m.group(1).upper()
            generic_type = (m.group(2) or "").strip()
            raw_url = m.group(3)
            if _is_relevant_api_path(raw_url):
                norm_p, short_suf = _normalize_http_path(raw_url)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_client_call",
                        role="consumer",
                        normalized_key=f"{method}:{norm_p}",
                        file_path=rel_path,
                        symbol_name=f"{method} {norm_p}",
                        metadata={
                            "method": method,
                            "method_explicit": True,
                            "path": norm_p,
                            "raw_path": raw_url,
                            "short_suffix": short_suf,
                            "language": "typescript",
                        },
                        sync_generation=sync_gen,
                    )
                )
            if generic_type:
                clean_gt = re.sub(r"[\[\]\s]", "", generic_type)
                if clean_gt and clean_gt[0].isupper() and clean_gt.lower() not in _DOC_STOP_TOKENS:
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="schema_ref",
                            role="consumer",
                            normalized_key=_normalize_schema_key(clean_gt),
                            file_path=rel_path,
                            symbol_name=clean_gt,
                            metadata={"schema_name": clean_gt, "language": "typescript"},
                            sync_generation=sync_gen,
                        )
                    )

        # 5. TypeScript interface / type object schemas
        schema_role = "provider" if has_server_routes else "consumer"
        for m in re.finditer(
            r"(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:extends\s+[^{]+)?\{",
            clean,
        ):
            name = m.group(1)
            body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
            fields = self._parse_ts_fields(body)
            sig_hash = _schema_signature_hash(fields)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="schema_def",
                    role=schema_role,
                    normalized_key=_normalize_schema_key(name),
                    file_path=rel_path,
                    symbol_name=name,
                    signature_hash=sig_hash,
                    metadata={
                        "schema_name": name,
                        "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(
            r"(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{",
            clean,
        ):
            name = m.group(1)
            body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
            fields = self._parse_ts_fields(body)
            sig_hash = _schema_signature_hash(fields)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="schema_def",
                    role=schema_role,
                    normalized_key=_normalize_schema_key(name),
                    file_path=rel_path,
                    symbol_name=name,
                    signature_hash=sig_hash,
                    metadata={
                        "schema_name": name,
                        "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 6. Events
        artifacts.extend(
            self._extract_events_regex(clean, rel_path, project_id, source_id, branch, sync_gen)
        )
        return artifacts

    @staticmethod
    def _parse_ts_fields(body: str) -> List[Tuple[str, str]]:
        fields: List[Tuple[str, str]] = []
        # Split top-level lines/semicolons
        for raw_stmt in re.split(r"[;\n]+", body):
            stmt = raw_stmt.strip().rstrip(",")
            if not stmt or stmt.startswith(("//", "/*")):
                continue
            m = re.match(r"^(?:readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)\??\s*:\s*(.+)$", stmt)
            if m:
                fields.append((m.group(1), m.group(2).strip()))
        return fields

    def _extract_from_go_java_proto(
        self,
        content: str,
        rel_path: str,
        ext: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_c_comments(content)

        if ext == ".go":
            for m in re.finditer(
                r"\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch)\(\s*\"([^\"]+)\"",
                clean,
            ):
                method = m.group(1).upper()
                norm_p, short_suf = _normalize_http_path(m.group(2))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_endpoint",
                        role="provider",
                        normalized_key=f"{method}:{norm_p}",
                        file_path=rel_path,
                        symbol_name=f"{method} {norm_p}",
                        metadata={"method": method, "path": norm_p, "short_suffix": short_suf, "language": "go"},
                        sync_generation=sync_gen,
                    )
                )
            for m in re.finditer(r"\btype\s+([A-Z][A-Za-z0-9_]*)\s+struct\s*\{", clean):
                name = m.group(1)
                body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
                fields: List[Tuple[str, str]] = []
                for ln in body.splitlines():
                    fm = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([^\s`]+)", ln)
                    if fm:
                        fields.append((fm.group(1), fm.group(2)))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=_schema_signature_hash(fields),
                        metadata={"schema_name": name, "fields": [{"name": n, "type": t} for n, t in sorted(fields)], "language": "go"},
                        sync_generation=sync_gen,
                    )
                )
        elif ext == ".java":
            cls_req = re.search(r"@RequestMapping\(\s*(?:(?:value|path)\s*=\s*)?\"([^\"]+)\"", clean)
            cls_prefix = cls_req.group(1) if cls_req else ""
            for m in re.finditer(
                r"@(Get|Post|Put|Delete|Patch)Mapping\(\s*(?:(?:value|path)\s*=\s*)?(?:\"([^\"]*)\")?\s*\)",
                clean,
            ):
                method = m.group(1).upper()
                full_raw = _join_url_paths(cls_prefix, m.group(2) or "")
                norm_p, short_suf = _normalize_http_path(full_raw)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_endpoint",
                        role="provider",
                        normalized_key=f"{method}:{norm_p}",
                        file_path=rel_path,
                        symbol_name=f"{method} {norm_p}",
                        metadata={"method": method, "path": norm_p, "short_suffix": short_suf, "language": "java"},
                        sync_generation=sync_gen,
                    )
                )
            for m in re.finditer(r"(?:public\s+)?(?:class|record)\s+([A-Z][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*\{", clean):
                name = m.group(1)
                if not name.endswith(_SCHEMA_SUFFIXES) and not m.group(2):
                    continue
                fields = []
                if m.group(2):
                    for part in m.group(2).split(","):
                        pm = re.match(r"^\s*([A-Za-z0-9_<>\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", part)
                        if pm:
                            fields.append((pm.group(2), pm.group(1)))
                body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
                for fm in re.finditer(r"(?:private|public|protected)\s+(?:final\s+)?([A-Za-z0-9_<>\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", body):
                    fields.append((fm.group(2), fm.group(1)))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=_schema_signature_hash(fields),
                        metadata={"schema_name": name, "fields": [{"name": n, "type": t} for n, t in sorted(fields)], "language": "java"},
                        sync_generation=sync_gen,
                    )
                )
        elif ext == ".proto":
            for m in re.finditer(r"\bmessage\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", clean):
                name = m.group(1)
                body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
                fields = []
                for fm in re.finditer(r"^\s*(?:repeated\s+|optional\s+)?([A-Za-z0-9_.]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\d+\s*;", body, re.MULTILINE):
                    fields.append((fm.group(2), fm.group(1)))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=_schema_signature_hash(fields),
                        metadata={"schema_name": name, "fields": [{"name": n, "type": t} for n, t in sorted(fields)], "language": "proto"},
                        sync_generation=sync_gen,
                    )
                )

        artifacts.extend(
            self._extract_events_regex(clean, rel_path, project_id, source_id, branch, sync_gen)
        )
        return artifacts

    @staticmethod
    def _extract_from_sql(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for m in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\"`\w]+\.)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s*\(",
            content,
            re.IGNORECASE,
        ):
            table_name = m.group(1)
            body = _extract_balanced_block(content, m.end() - 1, "(", ")")
            fields: List[Tuple[str, str]] = []
            # Split top-level commas outside parens like DECIMAL(10, 2)
            depth = 0
            buf: List[str] = []
            parts: List[str] = []
            for ch in body:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                if ch == "," and depth == 0:
                    parts.append("".join(buf))
                    buf.clear()
                else:
                    buf.append(ch)
            if buf:
                parts.append("".join(buf))
            for col_def in parts:
                cleaned = col_def.strip()
                if not cleaned:
                    continue
                upper = cleaned.upper()
                if upper.startswith(("PRIMARY ", "FOREIGN ", "UNIQUE ", "CHECK ", "CONSTRAINT ", "INDEX ", "KEY ")):
                    continue
                cm = re.match(r"^[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+([A-Za-z0-9_]+(?:\([^)]*\))?)", cleaned)
                if cm:
                    fields.append((cm.group(1), cm.group(2).upper()))
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="schema_def",
                    role="provider",
                    normalized_key=_normalize_schema_key(table_name),
                    file_path=rel_path,
                    symbol_name=table_name,
                    signature_hash=_schema_signature_hash(fields),
                    metadata={
                        "schema_name": table_name,
                        "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                        "language": "sql",
                    },
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_yaml_json(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        try:
            data = yaml.safe_load(content)
        except Exception:
            return artifacts
        if not isinstance(data, dict):
            return artifacts

        # 1. OpenAPI / Swagger paths & schemas
        if ("openapi" in data or "swagger" in data) and isinstance(data.get("paths"), dict):
            for raw_path, path_item in data["paths"].items():
                if not isinstance(path_item, dict):
                    continue
                norm_p, short_suf = _normalize_http_path(str(raw_path))
                for meth, op in path_item.items():
                    m_up = str(meth).upper()
                    if m_up not in _HTTP_METHODS or not isinstance(op, dict):
                        continue
                    sig_hash = _sha256_json(
                        {
                            "method": m_up,
                            "path": norm_p,
                            "parameters": op.get("parameters"),
                            "requestBody": op.get("requestBody"),
                            "responses": op.get("responses"),
                        }
                    )
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="http_endpoint",
                            role="provider",
                            normalized_key=f"{m_up}:{norm_p}",
                            file_path=rel_path,
                            symbol_name=str(op.get("operationId") or f"{m_up} {norm_p}"),
                            signature_hash=sig_hash,
                            metadata={
                                "method": m_up,
                                "path": norm_p,
                                "raw_path": raw_path,
                                "short_suffix": short_suf,
                                "openapi": True,
                            },
                            sync_generation=sync_gen,
                        )
                    )
            schemas = (data.get("components") or {}).get("schemas") if isinstance(data.get("components"), dict) else None
            if not isinstance(schemas, dict):
                schemas = data.get("definitions") if isinstance(data.get("definitions"), dict) else {}
            for sname, sdef in (schemas or {}).items():
                if not isinstance(sdef, dict):
                    continue
                props = sdef.get("properties") if isinstance(sdef.get("properties"), dict) else {}
                fields = [
                    (str(k), str(v.get("type") or v.get("$ref") or "any") if isinstance(v, dict) else "any")
                    for k, v in props.items()
                ]
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(str(sname)),
                        file_path=rel_path,
                        symbol_name=str(sname),
                        signature_hash=_schema_signature_hash(fields),
                        metadata={
                            "schema_name": str(sname),
                            "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                            "openapi": True,
                        },
                        sync_generation=sync_gen,
                    )
                )

        # 2. docker-compose services / depends_on / environment URLs
        if isinstance(data.get("services"), dict):
            for svc_name, svc_cfg in data["services"].items():
                svc_str = str(svc_name).strip()
                if not svc_str:
                    continue
                cfg = svc_cfg if isinstance(svc_cfg, dict) else {}
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="infra_deploy",
                        role="provider",
                        normalized_key=svc_str.lower(),
                        file_path=rel_path,
                        symbol_name=svc_str,
                        signature_hash=_sha256_json({"service": svc_str, "image": cfg.get("image"), "ports": cfg.get("ports")}),
                        metadata={"kind": "compose_service", "service": svc_str, "image": cfg.get("image")},
                        sync_generation=sync_gen,
                    )
                )
                deps = cfg.get("depends_on")
                dep_names: List[str] = []
                if isinstance(deps, list):
                    dep_names = [str(d).strip() for d in deps if str(d).strip()]
                elif isinstance(deps, dict):
                    dep_names = [str(k).strip() for k in deps.keys() if str(k).strip()]
                for dep in dep_names:
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="infra_deploy",
                            role="consumer",
                            normalized_key=dep.lower(),
                            file_path=rel_path,
                            symbol_name=f"{svc_str}->depends_on:{dep}",
                            metadata={"kind": "depends_on", "from_service": svc_str, "target_service": dep},
                            sync_generation=sync_gen,
                        )
                    )
                env_cfg = cfg.get("environment")
                env_pairs: List[Tuple[str, str]] = []
                if isinstance(env_cfg, dict):
                    env_pairs = [(str(k), str(v)) for k, v in env_cfg.items()]
                elif isinstance(env_cfg, list):
                    for item in env_cfg:
                        if isinstance(item, str) and "=" in item:
                            k, v = item.split("=", 1)
                            env_pairs.append((k.strip(), v.strip()))
                for ek, ev in env_pairs:
                    if ek.upper().endswith(("_API_URL", "_URL", "_ENDPOINT")):
                        host = _extract_host_service_from_url(ev)
                        if host:
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="infra_deploy",
                                    role="consumer",
                                    normalized_key=host.lower(),
                                    file_path=rel_path,
                                    symbol_name=f"{svc_str}:{ek}",
                                    metadata={"kind": "env_url", "env_var": ek, "value": ev, "target_service": host},
                                    sync_generation=sync_gen,
                                )
                            )
        return artifacts

    @staticmethod
    def _extract_from_terraform(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for m in re.finditer(r"\b(resource|service)\s+\"([^\"]+)\"(?:\s+\"([^\"]+)\")?", content):
            kind = m.group(1).lower()
            name = (m.group(3) or m.group(2)).strip()
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="infra_deploy",
                    role="provider",
                    normalized_key=name.lower(),
                    file_path=rel_path,
                    symbol_name=f"{kind}:{m.group(2)}" + (f".{m.group(3)}" if m.group(3) else ""),
                    metadata={"kind": "terraform", "tf_type": m.group(2), "tf_name": name},
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_env_file(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for ln in content.splitlines():
            stripped = ln.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            key = k.strip()
            val = v.strip().strip("\"'")
            if not re.search(r"(?:_API_URL|_SERVICE_URL|_URL|_ENDPOINT)$", key, re.IGNORECASE):
                continue
            host = _extract_host_service_from_url(val)
            if not host:
                # Fallback from env var prefix like ORDERS_API_URL -> orders
                prefix = re.sub(r"(?:_API_URL|_SERVICE_URL|_URL|_ENDPOINT)$", "", key, flags=re.IGNORECASE)
                host = prefix.lower().replace("_", "-") if prefix else ""
            if host:
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="infra_deploy",
                        role="consumer",
                        normalized_key=host.lower(),
                        file_path=rel_path,
                        symbol_name=key,
                        metadata={"kind": "env_url", "env_var": key, "value": val, "target_service": host},
                        sync_generation=sync_gen,
                    )
                )
        return artifacts

    # ------------------------------------------------------------------
    # Legacy Windows Session 0 IPC, COM/IDL, DLL, Socket & PLC/LASAL Extractors
    # ------------------------------------------------------------------
    def _extract_from_cpp_cs_legacy(
        self,
        content: str,
        rel_path: str,
        ext: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_comments_string_aware(content)
        const_map = _extract_const_map(clean)
        lang = "csharp" if ext == ".cs" else ("vbnet" if ext == ".vb" else "cpp")

        # 1. C/C++/C# Structs -> schema_def (also used to hash shared memory structs)
        struct_sigs: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {}
        for m in re.finditer(
            r"\btypedef\s+struct(?:\s+__declspec\s*\([^)]*\))?(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*\{",
            clean,
        ):
            open_b = m.end() - 1
            body = _extract_balanced_block(clean, open_b, "{", "}")
            end_pos = open_b + 1 + len(body) + 1
            tail = clean[end_pos : end_pos + 160].split(";", 1)[0]
            aliases = [
                a.strip().lstrip("*&")
                for a in tail.split(",")
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a.strip().lstrip("*&"))
            ]
            name = next((a for a in aliases if not a.startswith(("P", "LP"))), None) or (
                aliases[0] if aliases else (m.group(1) or "")
            )
            fields = _parse_cpp_struct_fields(body)
            if name and fields:
                sig_hash = _schema_signature_hash(fields)
                struct_sigs[name] = (sig_hash, fields)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=sig_hash,
                        metadata={
                            "schema_name": name,
                            "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                            "language": lang,
                        },
                        sync_generation=sync_gen,
                    )
                )

        for m in re.finditer(
            r"\bstruct(?:\s+(?:__declspec\s*\([^)]*\)|[A-Z0-9_]+(?:_API|_EXPORT)))*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{",
            clean,
        ):
            name = m.group(1)
            if name in struct_sigs:
                continue
            body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
            fields = _parse_cpp_struct_fields(body)
            if name and fields:
                sig_hash = _schema_signature_hash(fields)
                struct_sigs[name] = (sig_hash, fields)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=sig_hash,
                        metadata={
                            "schema_name": name,
                            "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                            "language": lang,
                        },
                        sync_generation=sync_gen,
                    )
                )

        # 2. Named Pipes (`named_pipe` -> `calls_pipe`)
        emitted_pipes: Set[Tuple[str, str]] = set()

        def _emit_pipe(role: str, raw_pipe: str, api_name: str) -> None:
            norm = _normalize_pipe_name(raw_pipe)
            if not norm:
                return
            nkey, disp = norm
            if (role, nkey) in emitted_pipes:
                return
            emitted_pipes.add((role, nkey))
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="named_pipe",
                    role=role,
                    normalized_key=nkey,
                    file_path=rel_path,
                    symbol_name=disp,
                    metadata={
                        "pipe_name": disp,
                        "pipe_leaf": nkey,
                        "api": api_name,
                        "language": lang,
                        "doc_tokens": [nkey, disp.lower(), f"\\\\.\\pipe\\{nkey}"],
                    },
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(
            r"\b(CreateNamedPipe(?:A|W)?|CallNamedPipe(?:A|W)?|WaitNamedPipe(?:A|W)?|CreateFile(?:A|W)?|NamedPipeServerStream|NamedPipeClientStream)\s*\(",
            clean,
        ):
            api = m.group(1)
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if not args:
                continue
            if api.startswith("CreateNamedPipe") or api == "NamedPipeServerStream":
                val = _resolve_str_arg(args[0], const_map)
                if val:
                    _emit_pipe("provider", val, api)
            elif api.startswith(("CallNamedPipe", "WaitNamedPipe")):
                val = _resolve_str_arg(args[0], const_map)
                if val:
                    _emit_pipe("consumer", val, api)
            elif api.startswith("CreateFile"):
                val = _resolve_str_arg(args[0], const_map)
                if val and "pipe" in val.lower():
                    _emit_pipe("consumer", val, api)
            elif api == "NamedPipeClientStream":
                cand_arg = args[1] if len(args) >= 2 and (_resolve_str_arg(args[0], const_map) in (".", "localhost", "127.0.0.1")) else args[0]
                val = _resolve_str_arg(cand_arg, const_map)
                if val:
                    _emit_pipe("consumer", val, api)

        has_pipe_srv = bool(re.search(r"\b(?:CreateNamedPipe|ConnectNamedPipe|NamedPipeServerStream)", clean))
        has_pipe_cli = bool(re.search(r"\b(?:CallNamedPipe|WaitNamedPipe|TransactNamedPipe|NamedPipeClientStream|CreateFile)", clean))
        if has_pipe_srv or has_pipe_cli:
            for pm in re.finditer(r'["\']((?:\\\\|\\)+\.(?:\\\\|\\)+pipe(?:\\\\|\\)+[^"\']+)["\']', clean, re.IGNORECASE):
                if has_pipe_srv:
                    _emit_pipe("provider", pm.group(1), "pipe_literal")
                if has_pipe_cli and not has_pipe_srv:
                    _emit_pipe("consumer", pm.group(1), "pipe_literal")

        # 3. Shared Memory / File Mapping (`shared_memory` -> `shares_shm`)
        mapped_struct_name: Optional[str] = None
        for cm in re.finditer(
            r"(?:\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\*\s*\)|(?:reinterpret_cast|static_cast)\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\s*\*\s*>)\s*(?:\(\s*)?MapViewOfFile",
            clean,
        ):
            cand_s = cm.group(1) or cm.group(2)
            if cand_s and cand_s in struct_sigs:
                mapped_struct_name = cand_s
                break
        if not mapped_struct_name:
            for sm in re.finditer(r"\bCreateFileMapping[\s\S]{0,200}?sizeof\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", clean):
                if sm.group(1) in struct_sigs:
                    mapped_struct_name = sm.group(1)
                    break

        emitted_shms: Set[Tuple[str, str]] = set()

        def _emit_shm(role: str, raw_shm: str, api_name: str, allow_unscoped: bool = True) -> None:
            norm = _normalize_shm_name(raw_shm, allow_unscoped=allow_unscoped)
            if not norm:
                return
            nkey, disp = norm
            if (role, nkey) in emitted_shms:
                return
            emitted_shms.add((role, nkey))
            sig_payload: Dict[str, Any] = {"kind": "shared_memory", "key": nkey}
            extra: Dict[str, Any] = {
                "shm_name": disp,
                "api": api_name,
                "language": lang,
                "doc_tokens": [nkey, disp.lower(), disp.split("\\")[-1].lower()],
            }
            if mapped_struct_name and mapped_struct_name in struct_sigs:
                s_hash, s_fields = struct_sigs[mapped_struct_name]
                sig_payload["struct"] = mapped_struct_name
                sig_payload["struct_hash"] = s_hash
                extra["mapped_struct"] = mapped_struct_name
                extra["mapped_fields"] = [{"name": n, "type": t} for n, t in sorted(s_fields)]
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="shared_memory",
                    role=role,
                    normalized_key=nkey,
                    file_path=rel_path,
                    symbol_name=disp,
                    signature_hash=_sha256_json(sig_payload),
                    metadata=extra,
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(
            r"\b(CreateFileMapping(?:A|W)?|OpenFileMapping(?:A|W)?|MemoryMappedFile\.(?:CreateNew|CreateOrOpen|OpenExisting))\s*\(",
            clean,
        ):
            api = m.group(1)
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if api.startswith("CreateFileMapping") and len(args) >= 6:
                val = _resolve_str_arg(args[5], const_map)
                if val:
                    _emit_shm("provider", val, api)
            elif api.startswith("OpenFileMapping") and len(args) >= 3:
                val = _resolve_str_arg(args[2], const_map)
                if val:
                    _emit_shm("consumer", val, api)
            elif api.startswith("MemoryMappedFile.") and args:
                val = _resolve_str_arg(args[0], const_map)
                if val:
                    r = "consumer" if api.endswith("OpenExisting") else "provider"
                    _emit_shm(r, val, api)

        has_shm_prov = bool(re.search(r"\b(?:CreateFileMapping|MemoryMappedFile\.CreateNew|MemoryMappedFile\.CreateOrOpen)", clean))
        has_shm_cons = bool(re.search(r"\b(?:OpenFileMapping|MemoryMappedFile\.OpenExisting)", clean))
        if has_shm_prov or has_shm_cons or "MapViewOfFile" in clean:
            for km in re.finditer(r'["\']((?:Global|Local)(?:\\\\|\\)+[A-Za-z0-9_.\-/]+)["\']', clean, re.IGNORECASE):
                if has_shm_prov:
                    _emit_shm("provider", km.group(1), "shm_literal", allow_unscoped=False)
                if has_shm_cons or (not has_shm_prov and "MapViewOfFile" in clean):
                    _emit_shm("consumer", km.group(1), "shm_literal", allow_unscoped=False)

        # 4. Windows Service SCM (`scm_service` -> `controls_service`)
        svc_handles: Dict[str, str] = {}
        emitted_svcs: Set[Tuple[str, str]] = set()

        def _emit_svc(role: str, svc_name: str, api_name: str, control_code: Optional[str] = None) -> None:
            clean_svc = str(svc_name or "").strip().strip('"\'')
            if not clean_svc or clean_svc.upper() in ("NULL", "NULLPTR", "0"):
                return
            nkey = clean_svc.lower()
            if (role, nkey) in emitted_svcs:
                return
            emitted_svcs.add((role, nkey))
            meta: Dict[str, Any] = {
                "service_name": clean_svc,
                "api": api_name,
                "language": lang,
                "doc_tokens": [nkey],
            }
            if control_code:
                meta["control_code"] = control_code
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="scm_service",
                    role=role,
                    normalized_key=nkey,
                    file_path=rel_path,
                    symbol_name=clean_svc,
                    metadata=meta,
                    sync_generation=sync_gen,
                )
            )

        has_svc_impl = bool(re.search(r"\b(?:RegisterServiceCtrlHandler|StartServiceCtrlDispatcher|ServiceMain|ServiceBase)\b", clean))
        for m in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*\s*=\s*)?(RegisterServiceCtrlHandler(?:Ex)?(?:A|W)?|OpenService(?:A|W)?|CreateService(?:A|W)?|StartService(?:A|W)?|ControlService)\s*\(",
            clean,
        ):
            assign_lhs = (m.group(1) or "").split("=")[0].strip()
            api = m.group(2)
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if api.startswith("RegisterServiceCtrlHandler") and args:
                val = _resolve_str_arg(args[0], const_map)
                if val:
                    _emit_svc("provider", val, api)
            elif api.startswith("OpenService") and len(args) >= 2:
                val = _resolve_str_arg(args[1], const_map)
                if val:
                    if assign_lhs:
                        svc_handles[assign_lhs] = val
                    _emit_svc("consumer", val, api)
            elif api.startswith("CreateService") and len(args) >= 2:
                val = _resolve_str_arg(args[1], const_map)
                if val:
                    _emit_svc("provider" if has_svc_impl else "consumer", val, api)
            elif api.startswith("StartService") and args and not api.startswith("StartServiceCtrl"):
                h_name = args[0].strip()
                if h_name in svc_handles:
                    _emit_svc("consumer", svc_handles[h_name], api)
            elif api == "ControlService" and len(args) >= 2:
                h_name = args[0].strip()
                if h_name in svc_handles:
                    _emit_svc("consumer", svc_handles[h_name], api, control_code=args[1].strip())

        for m in re.finditer(
            r'SERVICE_TABLE_ENTRY[AW]?\s+[A-Za-z_][A-Za-z0-9_]*\[\s*\]\s*=\s*\{([\s\S]*?)\};',
            clean,
        ):
            for entry in re.finditer(r'\{\s*(?:\(LPW?STR\)\s*)?(?:L?["\']([^"\']+)["\'])\s*,', m.group(1)):
                _emit_svc("provider", entry.group(1), "SERVICE_TABLE_ENTRY")

        if ext == ".cs":
            for m in re.finditer(r"\bServiceName\s*=\s*\"([^\"]+)\"", clean):
                _emit_svc("provider", m.group(1), "ServiceBase")
            for m in re.finditer(r"\bnew\s+ServiceController\s*\(\s*\"([^\"]+)\"", clean):
                _emit_svc("consumer", m.group(1), "ServiceController")

        # 5. COM / UUID / CoCreateInstance (`com_idl` -> `calls_com`)
        for m in re.finditer(
            r"(?:\[[^\]]*uuid\s*\(\s*\"?([0-9a-fA-F\-]+)\"?\s*\)[^\]]*\]|\[Guid\s*\(\s*\"([0-9a-fA-F\-]+)\"\s*\)\]|MIDL_INTERFACE\s*\(\s*\"([0-9a-fA-F\-]+)\"\s*\)|__declspec\s*\(\s*uuid\s*\(\s*\"([0-9a-fA-F\-]+)\"\s*\)\s*\))(?:\s*(?:public|internal|sealed|abstract)?\s*(?:interface|dispinterface|coclass|class|struct)\s+([A-Za-z_][A-Za-z0-9_]*))?",
            clean,
            re.IGNORECASE,
        ):
            raw_u = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
            uid = _normalize_uuid(raw_u)
            if not uid:
                continue
            sym = (m.group(5) or uid).strip()
            aliases = [uid]
            if m.group(5):
                s_low = sym.lower()
                aliases.extend([s_low, f"iid_{s_low}", f"clsid_{s_low}"])
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="com_idl",
                    role="provider",
                    normalized_key=uid,
                    file_path=rel_path,
                    symbol_name=sym,
                    signature_hash=_sha256_json({"uuid": uid, "symbol": sym}),
                    metadata={"uuid": uid, "symbol": sym, "match_aliases": aliases, "language": lang, "doc_tokens": aliases},
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(r"\bDEFINE_GUID\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,", clean):
            sym = m.group(1)
            short = re.sub(r"^(?:CLSID|IID)_", "", sym, flags=re.IGNORECASE).lower()
            aliases = [sym.lower()] + ([short] if short else [])
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="com_idl",
                    role="provider",
                    normalized_key=sym.lower(),
                    file_path=rel_path,
                    symbol_name=sym,
                    metadata={"symbol": sym, "match_aliases": aliases, "language": lang, "doc_tokens": aliases},
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(r"\b(CoCreateInstance(?:Ex)?|CoGetClassObject)\s*\(", clean):
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            cand_refs: List[str] = []
            if args:
                cand_refs.append(args[0])
            if m.group(1) == "CoCreateInstance" and len(args) >= 4:
                cand_refs.append(args[3])
            for raw_ref in cand_refs:
                ref_clean = raw_ref.strip().lstrip("&")
                if not ref_clean or ref_clean.upper() in ("NULL", "NULLPTR", "0", "CLSID_NULL", "IID_IUNKNOWN"):
                    continue
                uid = _normalize_uuid(_resolve_str_arg(ref_clean, const_map) or ref_clean)
                if uid:
                    nkey = uid
                    disp = uid
                    aliases = [uid]
                else:
                    um = re.match(r"^(?:__uuidof|typeof)\s*\(\s*([A-Za-z_][A-Za-z0-9_:]*)\s*\)$", ref_clean)
                    tok = um.group(1).split("::")[-1] if um else ref_clean
                    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tok):
                        continue
                    nkey = tok.lower()
                    disp = tok
                    short = re.sub(r"^(?:CLSID|IID)_", "", tok, flags=re.IGNORECASE).lower()
                    aliases = [nkey] + ([short] if short and short != nkey else [])
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="com_idl",
                        role="consumer",
                        normalized_key=nkey,
                        file_path=rel_path,
                        symbol_name=disp,
                        metadata={"com_ref": disp, "match_aliases": aliases, "api": m.group(1), "language": lang, "doc_tokens": aliases},
                        sync_generation=sync_gen,
                    )
                )

        # 6. Win32 Window Messages (`win32_msg` -> `sends_win32_msg`)
        is_msg_sender = bool(re.search(r"\b(?:SendMessage|PostMessage|PostThreadMessage|BroadcastSystemMessage)", clean))
        is_msg_receiver = bool(
            re.search(r"\b(?:WndProc|WindowProc|DefWindowProc|ON_MESSAGE|ON_REGISTERED_MESSAGE|GetMessage|DispatchMessage|COPYDATASTRUCT)\b", clean)
            or re.search(r"\bcase\s+(?:WM_COPYDATA|WM_USER|WM_APP)\b", clean)
        )

        for m in re.finditer(r"\bRegisterWindowMessage(?:A|W)?\s*\(", clean):
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if not args:
                continue
            msg_str = _resolve_str_arg(args[0], const_map)
            if msg_str and msg_str.strip():
                clean_msg = msg_str.strip()
                nkey = f"regmsg:{clean_msg.lower()}"
                if is_msg_sender and not is_msg_receiver:
                    role = "consumer"
                elif is_msg_receiver and not is_msg_sender:
                    role = "provider"
                else:
                    role = "shared"
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="win32_msg",
                        role=role,
                        normalized_key=nkey,
                        file_path=rel_path,
                        symbol_name=clean_msg,
                        metadata={"msg_kind": "registered", "msg_name": clean_msg, "language": lang, "doc_tokens": [clean_msg.lower()]},
                        sync_generation=sync_gen,
                    )
                )

        for m in re.finditer(r"\b(SendMessage(?:Timeout)?(?:A|W)?|PostMessage(?:A|W)?|PostThreadMessage(?:A|W)?)\s*\(", clean):
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if len(args) < 2:
                continue
            norm_msg = _normalize_win32_msg_expr(args[1], const_map)
            if norm_msg:
                nkey, disp = norm_msg
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="win32_msg",
                        role="consumer",
                        normalized_key=nkey,
                        file_path=rel_path,
                        symbol_name=disp,
                        metadata={"msg_kind": "window_msg", "msg_name": disp, "api": m.group(1), "language": lang, "doc_tokens": [nkey, disp.lower()]},
                        sync_generation=sync_gen,
                    )
                )

        for m in re.finditer(
            r"(?:\bcase\s+([^:]+):|\bON_MESSAGE\s*\(\s*([^,]+),|\bON_WM_COPYDATA\s*\(\s*\))",
            clean,
        ):
            raw_msg = "WM_COPYDATA" if m.group(0).startswith("ON_WM_COPYDATA") else (m.group(1) or m.group(2) or "")
            norm_msg = _normalize_win32_msg_expr(raw_msg, const_map)
            if norm_msg:
                nkey, disp = norm_msg
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="win32_msg",
                        role="provider",
                        normalized_key=nkey,
                        file_path=rel_path,
                        symbol_name=disp,
                        metadata={"msg_kind": "handler", "msg_name": disp, "language": lang, "doc_tokens": [nkey, disp.lower()]},
                        sync_generation=sync_gen,
                    )
                )

        if is_msg_receiver and "WM_COPYDATA" in clean and not any(a["contract_kind"] == "win32_msg" and a["normalized_key"] == "wm_copydata" and a["role"] == "provider" for a in artifacts):
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="win32_msg",
                    role="provider",
                    normalized_key="wm_copydata",
                    file_path=rel_path,
                    symbol_name="WM_COPYDATA",
                    metadata={"msg_kind": "handler", "msg_name": "WM_COPYDATA", "language": lang, "doc_tokens": ["wm_copydata"]},
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(r"\bFindWindow(?:Ex)?(?:A|W)?\s*\(", clean):
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            check_args = args[2:4] if "Ex" in m.group(0) else args[:2]
            for w_arg in check_args:
                w_str = _resolve_str_arg(w_arg, const_map)
                if w_str and w_str.strip():
                    w_clean = w_str.strip()
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="win32_msg",
                            role="consumer",
                            normalized_key=f"window:{w_clean.lower()}",
                            file_path=rel_path,
                            symbol_name=w_clean,
                            metadata={"msg_kind": "find_window", "window_name": w_clean, "language": lang, "doc_tokens": [w_clean.lower()]},
                            sync_generation=sync_gen,
                        )
                    )

        for m in re.finditer(r"\bCreateWindow(?:Ex)?(?:A|W)?\s*\(", clean):
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            off = 1 if "Ex" in m.group(0) else 0
            for idx in (off, off + 1):
                if idx < len(args):
                    w_str = _resolve_str_arg(args[idx], const_map)
                    if w_str and w_str.strip():
                        w_clean = w_str.strip()
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="win32_msg",
                                role="provider",
                                normalized_key=f"window:{w_clean.lower()}",
                                file_path=rel_path,
                                symbol_name=w_clean,
                                metadata={"msg_kind": "window_owner", "window_name": w_clean, "language": lang, "doc_tokens": [w_clean.lower()]},
                                sync_generation=sync_gen,
                            )
                        )

        # 7. DLL Exports & Dynamic Loading (`dll_export` -> `loads_dll`)
        export_macros: List[str] = []
        for dm in re.finditer(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+[^\n]*__declspec\s*\(\s*dllexport\s*\)", clean, re.MULTILINE):
            export_macros.append(re.escape(dm.group(1)))
        export_qual = r"(?:__declspec\s*\(\s*dllexport\s*\)" + (
            "|" + "|".join(export_macros) if export_macros else ""
        ) + r")"
        export_re = re.compile(
            rf'(?:extern\s+"C"\s+)?{export_qual}\s+(?:(?:extern\s+"C"|WINAPI|__stdcall|__cdecl|CALLBACK|APIENTRY)\s+)*([A-Za-z_][A-Za-z0-9_:<>*&\s]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{{}}]*)\)',
        )
        for m in export_re.finditer(clean):
            ret_t = " ".join(m.group(1).split())
            fn_name = m.group(2).strip()
            params = " ".join(m.group(3).split())
            if fn_name.lower() in _CPP_TOKEN_STOPWORDS:
                continue
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="dll_export",
                    role="provider",
                    normalized_key=fn_name.lower(),
                    file_path=rel_path,
                    symbol_name=fn_name,
                    signature_hash=_sha256_json({"export": fn_name, "return": ret_t, "params": params}),
                    metadata={
                        "export_kind": "dllexport",
                        "symbol": fn_name,
                        "return_type": ret_t,
                        "params": params,
                        "language": lang,
                        "doc_tokens": [fn_name.lower()],
                    },
                    sync_generation=sync_gen,
                )
            )

        dll_handles: Dict[str, str] = {}
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*\s*=\s*)?(LoadLibrary(?:Ex)?(?:A|W)?)\s*\(", clean):
            lhs = (m.group(1) or "").split("=")[0].strip()
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if not args:
                continue
            dll_raw = _resolve_str_arg(args[0], const_map)
            norm = _normalize_dll_stem(dll_raw or "")
            if norm:
                stem, disp = norm
                if lhs:
                    dll_handles[lhs] = disp
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="consumer",
                        normalized_key=f"dll:{stem}",
                        file_path=rel_path,
                        symbol_name=disp,
                        metadata={"import_kind": "load_library", "dll_name": disp, "dll_stem": stem, "language": lang, "doc_tokens": [stem, disp.lower()]},
                        sync_generation=sync_gen,
                    )
                )

        for m in re.finditer(r"\bGetProcAddress\s*\(", clean):
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            if len(args) < 2:
                continue
            proc_raw = _resolve_str_arg(args[1], const_map)
            if proc_raw and proc_raw.strip():
                proc = proc_raw.strip()
                dll_ref = dll_handles.get(args[0].strip(), "")
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="consumer",
                        normalized_key=proc.lower(),
                        file_path=rel_path,
                        symbol_name=proc,
                        metadata={"import_kind": "get_proc_address", "symbol": proc, "dll_name": dll_ref, "language": lang, "doc_tokens": [proc.lower()]},
                        sync_generation=sync_gen,
                    )
                )

        if ext == ".cs":
            for m in re.finditer(
                r'\[DllImport\s*\(\s*"([^"]+)"([^]]*)\)\s*\][^;{]*?\b([A-Za-z_][A-Za-z0-9_]*)\s*\(',
                clean,
                re.DOTALL,
            ):
                norm = _normalize_dll_stem(m.group(1))
                ep_m = re.search(r'EntryPoint\s*=\s*"([^"]+)"', m.group(2) or "")
                fn_name = ep_m.group(1) if ep_m else m.group(3)
                if norm:
                    stem, disp = norm
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="dll_export",
                            role="consumer",
                            normalized_key=f"dll:{stem}",
                            file_path=rel_path,
                            symbol_name=disp,
                            metadata={"import_kind": "dllimport", "dll_name": disp, "dll_stem": stem, "language": lang},
                            sync_generation=sync_gen,
                        )
                    )
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="consumer",
                        normalized_key=fn_name.lower(),
                        file_path=rel_path,
                        symbol_name=fn_name,
                        metadata={"import_kind": "dllimport", "symbol": fn_name, "dll_name": m.group(1), "language": lang},
                        sync_generation=sync_gen,
                    )
                )

        # 8. Winsock / TCP Socket IPC (`socket_ipc` -> `calls_socket`)
        socket_ports: List[int] = []
        for pm in re.finditer(r"\bhtons\s*\(\s*([A-Za-z0-9_]+)\s*\)", clean):
            raw_p = pm.group(1).strip()
            resolved_p = const_map.get(raw_p, raw_p)
            if str(resolved_p).isdigit():
                socket_ports.append(int(resolved_p))
        for pm in re.finditer(r"\b(?:TcpListener|TcpClient)\s*\([^,)]*,\s*([A-Za-z0-9_]+)\s*\)", clean):
            raw_p = pm.group(1).strip()
            resolved_p = const_map.get(raw_p, raw_p)
            if str(resolved_p).isdigit():
                socket_ports.append(int(resolved_p))
        socket_hosts: List[str] = []
        for hm in re.finditer(r'\b(?:inet_addr|inet_pton|getaddrinfo|TcpClient)\s*\([^)]*["\']([A-Za-z0-9_.\-]+)["\']', clean):
            socket_hosts.append(hm.group(1).lower())
        eff_host = socket_hosts[0] if socket_hosts else ""

        is_sock_srv = bool(re.search(r"\b(?:bind\s*\(|listen\s*\(|TcpListener)\b", clean)) and bool(
            re.search(r"\b(?:WSAStartup|socket\s*\(|listen\s*\(|TcpListener)\b", clean)
        )
        is_sock_cli = bool(re.search(r"\b(?:connect\s*\(|TcpClient)\b", clean)) and bool(
            re.search(r"\b(?:WSAStartup|socket\s*\(|TcpClient|connect\s*\()\b", clean)
        )
        for port in sorted(set(socket_ports)):
            if is_sock_srv:
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="socket_ipc",
                        role="provider",
                        normalized_key=f"port:{port}",
                        file_path=rel_path,
                        symbol_name=f"tcp:{port}",
                        metadata={"port": port, "host": eff_host, "language": lang, "doc_tokens": [f":{port}", f"port:{port}"]},
                        sync_generation=sync_gen,
                    )
                )
            if is_sock_cli:
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="socket_ipc",
                        role="consumer",
                        normalized_key=f"port:{port}",
                        file_path=rel_path,
                        symbol_name=f"tcp:{port}",
                        metadata={"port": port, "host": eff_host, "language": lang, "doc_tokens": [f":{port}", f"port:{port}"]},
                        sync_generation=sync_gen,
                    )
                )

        # 9. Explicit PLC/LASAL Client Calls & Candidate Token Index (`plc_lasal_channel`)
        plc_call_re = re.compile(
            r"\b((?:Lasal|Lsl|Plc|Opc|Ads|Modbus)[A-Za-z0-9_]*|(?:Read|Write|Subscribe|Get|Set|Bind|Register)(?:Plc|Lasal|Server|Channel|Tag)[A-Za-z0-9_]*)\s*\(",
            re.IGNORECASE,
        )
        for m in plc_call_re.finditer(clean):
            api = m.group(1)
            args = _split_top_level_args(_extract_balanced_block(clean, m.end() - 1, "(", ")"))
            for a in args[:2]:
                val = _resolve_str_arg(a, const_map)
                if not val or not val.strip():
                    continue
                vclean = val.strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", vclean):
                    continue
                leaf = vclean.split(".")[-1]
                is_tag_call = "tag" in api.lower() or leaf.startswith("g_") or leaf.startswith("%")
                prefix = "tag" if is_tag_call else "channel"
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="plc_lasal_channel",
                        role="consumer",
                        normalized_key=f"{prefix}:{leaf.lower()}",
                        file_path=rel_path,
                        symbol_name=vclean,
                        metadata={
                            "plc_kind": "client_call",
                            "raw_ref": vclean,
                            "leaf": leaf,
                            "api": api,
                            "language": lang,
                            "doc_tokens": [leaf.lower(), vclean.lower()],
                        },
                        sync_generation=sync_gen,
                    )
                )

        # Collect clean string literals and non-stopword identifiers for cross-language PLC channel/tag matching
        str_tokens: Set[str] = set()
        for sm in re.finditer(r'L?["\']([^"\'\n]{3,80})["\']', clean):
            s_val = sm.group(1).strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", s_val):
                leaf = s_val.split(".")[-1]
                if leaf.lower() not in _CPP_TOKEN_STOPWORDS and not leaf.lower().endswith((".dll", ".exe", ".lib")):
                    str_tokens.add(leaf.lower())
        ident_tokens: Set[str] = set()
        for im in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]{4,})\b", clean):
            tok = im.group(1)
            tlow = tok.lower()
            if tlow not in _CPP_TOKEN_STOPWORDS:
                if tok.startswith("g_") or "_" in tok or any(c.isupper() for c in tok[1:]):
                    ident_tokens.add(tlow)

        if str_tokens or ident_tokens:
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="plc_token_index",
                    role="consumer",
                    normalized_key=f"tokens:{rel_path}",
                    file_path=rel_path,
                    symbol_name=Path(rel_path).name,
                    metadata={
                        "string_tokens": sorted(str_tokens)[:200],
                        "ident_tokens": sorted(ident_tokens)[:300],
                        "language": lang,
                    },
                    sync_generation=sync_gen,
                )
            )

        return artifacts

    @staticmethod
    def _extract_from_idl(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_comments_string_aware(content)
        decl_re = re.compile(
            r"(?:\[([^\]]*)\]\s*)?\b(dispinterface|interface|coclass|library)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[^{;]+)?\{",
            re.IGNORECASE,
        )
        for m in decl_re.finditer(clean):
            attrs = m.group(1) or ""
            kind_kw = m.group(2).lower()
            name = m.group(3)
            um = re.search(r"uuid\s*\(\s*([0-9a-fA-F\-]+)\s*\)", attrs)
            uid = _normalize_uuid(um.group(1)) if um else None
            body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
            methods = re.findall(
                r"(?:\[[^\]]*\]\s*)?(?:HRESULT|void|long|int|BOOL|BSTR|VARIANT)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
                body,
            )
            nkey = uid or name.lower()
            s_low = name.lower()
            aliases = [s_low, f"iid_{s_low}", f"clsid_{s_low}"]
            if uid:
                aliases.insert(0, uid)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="com_idl",
                    role="provider",
                    normalized_key=nkey,
                    file_path=rel_path,
                    symbol_name=name,
                    signature_hash=_sha256_json({"uuid": uid or "", "kind": kind_kw, "name": name, "methods": sorted(methods)}),
                    metadata={
                        "uuid": uid,
                        "idl_kind": kind_kw,
                        "symbol": name,
                        "match_aliases": aliases,
                        "language": "idl",
                        "doc_tokens": aliases,
                    },
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_def(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        lib_stem = Path(rel_path).stem.lower()
        lib_disp = Path(rel_path).stem
        in_exports = False
        section_stop = {"SECTIONS", "SEGMENTS", "HEAPSIZE", "STACKSIZE", "STUB", "VERSION", "DESCRIPTION"}

        for raw_ln in content.splitlines():
            code = raw_ln.split(";", 1)[0].strip()
            if not code:
                continue
            m_lib = re.match(r'^(?:LIBRARY|NAME)\s+"?([A-Za-z0-9_.\-]+)"?', code, re.IGNORECASE)
            if m_lib:
                in_exports = False
                lib_disp = m_lib.group(1)
                norm = _normalize_dll_stem(lib_disp)
                if norm:
                    lib_stem = norm[0]
                continue
            if re.match(r"^EXPORTS\b", code, re.IGNORECASE):
                in_exports = True
                rest = code[len("EXPORTS"):].strip()
                if not rest:
                    continue
                code = rest
            if code.split()[0].upper() in section_stop:
                in_exports = False
                continue
            if in_exports:
                m_exp = re.match(r"^([A-Za-z_@?][A-Za-z0-9_@?$]*)(?:\s*=\s*([A-Za-z_][A-Za-z0-9_.]*))?(.*)$", code)
                if m_exp:
                    exp_sym = m_exp.group(1)
                    ord_m = re.search(r"@(\d+)", m_exp.group(3) or "")
                    ordinal = int(ord_m.group(1)) if ord_m else None
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="dll_export",
                            role="provider",
                            normalized_key=exp_sym.lower(),
                            file_path=rel_path,
                            symbol_name=exp_sym,
                            signature_hash=_sha256_json({"def_export": exp_sym, "ordinal": ordinal}),
                            metadata={
                                "export_kind": "def_export",
                                "symbol": exp_sym,
                                "ordinal": ordinal,
                                "dll_name": lib_disp,
                                "language": "def",
                                "doc_tokens": [exp_sym.lower()],
                            },
                            sync_generation=sync_gen,
                        )
                    )

        if lib_stem:
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="dll_export",
                    role="provider",
                    normalized_key=f"dll:{lib_stem}",
                    file_path=rel_path,
                    symbol_name=f"{lib_disp}.dll" if not lib_disp.lower().endswith(".dll") else lib_disp,
                    metadata={
                        "export_kind": "def_library",
                        "dll_name": lib_disp,
                        "dll_stem": lib_stem,
                        "language": "def",
                        "doc_tokens": [lib_stem, f"{lib_stem}.dll"],
                    },
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_rc(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_comments_string_aware(content)
        for m in re.finditer(r'^\s*CAPTION\s+"([^"]+)"', clean, re.IGNORECASE | re.MULTILINE):
            cap = m.group(1).strip()
            if cap:
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="win32_msg",
                        role="provider",
                        normalized_key=f"window:{cap.lower()}",
                        file_path=rel_path,
                        symbol_name=cap,
                        metadata={"msg_kind": "rc_caption", "window_name": cap, "language": "rc"},
                        sync_generation=sync_gen,
                    )
                )
        return artifacts

    @staticmethod
    def _extract_from_vb6_delphi(
        content: str,
        rel_path: str,
        ext: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        lang = "delphi" if ext == ".pas" else "vb6"

        if lang == "vb6":
            decl_re = re.compile(
                r'^\s*(?:(?:Public|Private)\s+)?Declare\s+(?:Function|Sub)\s+([A-Za-z_][A-Za-z0-9_]*)\s+Lib\s+"([^"]+)"(?:\s+Alias\s+"([^"]+)")?',
                re.IGNORECASE | re.MULTILINE,
            )
            for m in decl_re.finditer(content):
                fn_name = m.group(1)
                lib_raw = m.group(2)
                alias_name = m.group(3) or fn_name
                norm = _normalize_dll_stem(lib_raw)
                if norm:
                    stem, disp = norm
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="dll_export",
                            role="consumer",
                            normalized_key=f"dll:{stem}",
                            file_path=rel_path,
                            symbol_name=disp,
                            metadata={"import_kind": "vb6_declare", "dll_name": disp, "dll_stem": stem, "language": "vb6"},
                            sync_generation=sync_gen,
                        )
                    )
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="consumer",
                        normalized_key=alias_name.lower(),
                        file_path=rel_path,
                        symbol_name=alias_name,
                        metadata={"import_kind": "vb6_declare", "symbol": alias_name, "dll_name": lib_raw, "language": "vb6"},
                        sync_generation=sync_gen,
                    )
                )
        else:
            for m in re.finditer(
                r"\b(?:function|procedure)\s+([A-Za-z_][A-Za-z0-9_]*)[^;]*;\s*[^;]*\bexternal\s+'([^']+)'(?:\s+name\s+'([^']+)')?",
                content,
                re.IGNORECASE,
            ):
                fn_name = m.group(3) or m.group(1)
                lib_raw = m.group(2)
                norm = _normalize_dll_stem(lib_raw)
                if norm:
                    stem, disp = norm
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="dll_export",
                            role="consumer",
                            normalized_key=f"dll:{stem}",
                            file_path=rel_path,
                            symbol_name=disp,
                            metadata={"import_kind": "delphi_external", "dll_name": disp, "dll_stem": stem, "language": "delphi"},
                            sync_generation=sync_gen,
                        )
                    )
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="consumer",
                        normalized_key=fn_name.lower(),
                        file_path=rel_path,
                        symbol_name=fn_name,
                        metadata={"import_kind": "delphi_external", "symbol": fn_name, "dll_name": lib_raw, "language": "delphi"},
                        sync_generation=sync_gen,
                    )
                )

        # Also extract literal string tokens & CreateObject COM calls
        for m in re.finditer(r"\bCreate(?:Ole)?Object\s*\(\s*[\"']([^\"']+)[\"']\s*\)", content, re.IGNORECASE):
            progid = m.group(1).strip()
            cls_leaf = progid.split(".")[-1]
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="com_idl",
                    role="consumer",
                    normalized_key=cls_leaf.lower(),
                    file_path=rel_path,
                    symbol_name=progid,
                    metadata={"com_ref": progid, "match_aliases": [progid.lower(), cls_leaf.lower()], "language": lang},
                    sync_generation=sync_gen,
                )
            )

        str_tokens = {
            s.split(".")[-1].lower()
            for s in re.findall(r'["\']([A-Za-z_][A-Za-z0-9_]{2,}(?:\.[A-Za-z_][A-Za-z0-9_]*)?)["\']', content)
        }
        if str_tokens:
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="plc_token_index",
                    role="consumer",
                    normalized_key=f"tokens:{rel_path}",
                    file_path=rel_path,
                    symbol_name=Path(rel_path).name,
                    metadata={"string_tokens": sorted(str_tokens)[:200], "ident_tokens": [], "language": lang},
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_plc_st_lasal(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []

        def _parse_st_chunk(st_text: str) -> None:
            clean = _strip_comments_string_aware(
                st_text,
                line_prefixes=("//",),
                block_pairs=(("(*", "*)"),),
                backslash_escapes=False,
            )
            lines = clean.splitlines()
            active_section: Optional[str] = None
            active_struct_name: Optional[str] = None
            struct_fields: List[Tuple[str, str]] = []
            pending_type: Optional[str] = None

            inline_sc_re = re.compile(r"^\s*(SERVER|CLIENT)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^;]+);?", re.IGNORECASE)
            sec_re = re.compile(r"^\s*(SERVER|CLIENT|VAR_SERVER|VAR_CLIENT|VAR_GLOBAL|VAR_EXTERNAL)\b", re.IGNORECASE)
            end_sec_re = re.compile(r"^\s*END_(SERVER|CLIENT|VAR)\b", re.IGNORECASE)
            var_re = re.compile(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)"
                r"(?:\s+AT\s+(%[IQMXiqmx][A-Za-z0-9_.]+))?"
                r"\s*:\s*([^:;]+?)(?:\s*:=\s*([^;]+))?;",
                re.IGNORECASE,
            )

            for raw_ln in lines:
                s = raw_ln.strip()
                if not s:
                    continue

                if active_struct_name is not None:
                    if re.match(r"^\s*END_STRUCT\b", s, re.IGNORECASE):
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="schema_def",
                                role="provider",
                                normalized_key=_normalize_schema_key(active_struct_name),
                                file_path=rel_path,
                                symbol_name=active_struct_name,
                                signature_hash=_schema_signature_hash(struct_fields),
                                metadata={
                                    "schema_name": active_struct_name,
                                    "fields": [{"name": n, "type": t} for n, t in sorted(struct_fields)],
                                    "language": "st",
                                },
                                sync_generation=sync_gen,
                            )
                        )
                        active_struct_name = None
                        struct_fields = []
                        pending_type = None
                        continue
                    vm = var_re.match(s)
                    if vm:
                        ftype = vm.group(3).strip()
                        for fn in vm.group(1).split(","):
                            if fn.strip():
                                struct_fields.append((fn.strip(), ftype))
                    continue

                m_tsi = re.match(r"^\s*TYPE\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*STRUCT\b", s, re.IGNORECASE)
                if m_tsi:
                    active_struct_name = m_tsi.group(1)
                    struct_fields = []
                    continue
                m_th = re.match(r"^\s*TYPE\s+([A-Za-z_][A-Za-z0-9_]*)\b", s, re.IGNORECASE)
                if m_th:
                    pending_type = m_th.group(1)
                    continue
                if pending_type and re.match(r"^\s*STRUCT\b", s, re.IGNORECASE):
                    active_struct_name = pending_type
                    struct_fields = []
                    pending_type = None
                    continue
                if re.match(r"^\s*END_TYPE\b", s, re.IGNORECASE):
                    pending_type = None
                    continue

                m_isc = inline_sc_re.match(s)
                if m_isc:
                    kw = m_isc.group(1).upper()
                    cname = m_isc.group(2)
                    dtype = m_isc.group(3).strip()
                    is_srv = kw == "SERVER"
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="plc_lasal_channel",
                            role="provider" if is_srv else "consumer",
                            normalized_key=f"channel:{cname.lower()}",
                            file_path=rel_path,
                            symbol_name=cname,
                            signature_hash=_sha256_json({"plc_kind": "lasal_server" if is_srv else "lasal_client", "name": cname, "type": dtype}),
                            metadata={
                                "plc_kind": "lasal_server" if is_srv else "lasal_client",
                                "channel_name": cname,
                                "data_type": dtype,
                                "language": "st",
                                "doc_tokens": [cname.lower()],
                            },
                            sync_generation=sync_gen,
                        )
                    )
                    continue

                m_sec = sec_re.match(s)
                if m_sec:
                    active_section = m_sec.group(1).upper()
                    continue
                if end_sec_re.match(s) or re.match(r"^\s*(?:VAR|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP)\b", s, re.IGNORECASE):
                    active_section = None
                    continue

                if active_section and ":" in s:
                    vm = var_re.match(s)
                    if vm:
                        io_addr = (vm.group(2) or "").strip()
                        dtype = vm.group(3).strip()
                        for raw_name in vm.group(1).split(","):
                            vname = raw_name.strip()
                            if not vname:
                                continue
                            if "SERVER" in active_section:
                                pkind, role, prefix = "lasal_server", "provider", "channel"
                            elif "CLIENT" in active_section:
                                pkind, role, prefix = "lasal_client", "consumer", "channel"
                            elif active_section == "VAR_GLOBAL":
                                pkind, role, prefix = "var_global", "provider", "tag"
                            else:
                                pkind, role, prefix = "var_external", "consumer", "tag"
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="plc_lasal_channel",
                                    role=role,
                                    normalized_key=f"{prefix}:{vname.lower()}",
                                    file_path=rel_path,
                                    symbol_name=vname,
                                    signature_hash=_sha256_json({"plc_kind": pkind, "name": vname, "type": dtype, "io": io_addr}),
                                    metadata={
                                        "plc_kind": pkind,
                                        "channel_name": vname if prefix == "channel" else None,
                                        "tag_name": vname if prefix == "tag" else None,
                                        "data_type": dtype,
                                        "io_address": io_addr,
                                        "language": "st",
                                        "doc_tokens": [vname.lower()] + ([io_addr.lower()] if io_addr else []),
                                    },
                                    sync_generation=sync_gen,
                                )
                            )

        stripped = content.lstrip()
        if stripped.startswith("<"):
            try:
                root_elem = ET.fromstring(content)
                for elem in root_elem.iter():
                    tag = elem.tag.split("}")[-1].lower()
                    if tag in ("server", "client"):
                        name = elem.get("Name") or elem.get("ID")
                        if name:
                            dtype = elem.get("DataType") or "ANY"
                            is_srv = tag == "server"
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="plc_lasal_channel",
                                    role="provider" if is_srv else "consumer",
                                    normalized_key=f"channel:{name.lower()}",
                                    file_path=rel_path,
                                    symbol_name=name,
                                    signature_hash=_sha256_json({"plc_kind": f"lasal_{tag}", "name": name, "type": dtype}),
                                    metadata={"plc_kind": f"lasal_{tag}", "channel_name": name, "data_type": dtype, "language": "lasal", "doc_tokens": [name.lower()]},
                                    sync_generation=sync_gen,
                                )
                            )
                    elif tag in ("network", "plc", "target", "comm", "endpoint"):
                        port = elem.get("Port") or elem.get("TcpPort")
                        host = elem.get("IpAddress") or elem.get("Host") or elem.get("IP") or ""
                        if port and str(port).isdigit():
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="plc_lasal_channel",
                                    role="provider",
                                    normalized_key=f"port:{int(port)}",
                                    file_path=rel_path,
                                    symbol_name=f"{host}:{port}" if host else f"plc:{port}",
                                    metadata={"plc_kind": "plc_endpoint", "port": int(port), "host": host.lower(), "language": "lasal"},
                                    sync_generation=sync_gen,
                                )
                            )
                    elif tag in ("st", "declaration", "implementation"):
                        inner = "".join(elem.itertext()).strip()
                        if inner:
                            _parse_st_chunk(inner)
            except Exception:
                _parse_st_chunk(content)
        else:
            _parse_st_chunk(content)
            for xm in re.finditer(r"\(\*!(.*?)\*\)", content, re.DOTALL):
                _parse_st_chunk(xm.group(1))
        return artifacts

    @staticmethod
    def _extract_from_vs_project(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        proj_stem = Path(rel_path).stem
        artifacts.append(
            _make_artifact(
                project_id=project_id,
                source_id=source_id,
                branch=branch,
                contract_kind="project_dep",
                role="provider",
                normalized_key=proj_stem.lower(),
                file_path=rel_path,
                symbol_name=proj_stem,
                metadata={"project_name": proj_stem},
                sync_generation=sync_gen,
            )
        )
        try:
            root_elem = ET.fromstring(content)
        except Exception:
            return artifacts

        config_type = ""
        output_type = ""
        target_name = proj_stem
        for elem in root_elem.iter():
            tag = elem.tag.split("}", 1)[-1]
            txt = (elem.text or "").strip()
            if tag == "ConfigurationType" and txt:
                config_type = txt
            elif tag == "OutputType" and txt:
                output_type = txt
            elif tag in ("TargetName", "AssemblyName") and txt:
                target_name = txt
            elif tag == "ProjectReference":
                inc = (elem.get("Include") or "").strip().replace("\\", "/")
                if inc:
                    ref_stem = Path(inc).stem
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="project_dep",
                            role="consumer",
                            normalized_key=ref_stem.lower(),
                            file_path=rel_path,
                            symbol_name=ref_stem,
                            metadata={"via": "vcxproj_project_reference", "target_project_file": inc},
                            sync_generation=sync_gen,
                        )
                    )
            elif tag == "AdditionalDependencies" and txt:
                for tok in txt.split(";"):
                    norm = _normalize_dll_stem(tok)
                    if norm and not tok.strip().startswith("%"):
                        stem, disp = norm
                        if stem in ("kernel32", "user32", "gdi32", "advapi32", "ole32", "oleaut32", "ws2_32", "shell32", "uuid"):
                            continue
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="dll_export",
                                role="consumer",
                                normalized_key=f"dll:{stem}",
                                file_path=rel_path,
                                symbol_name=disp,
                                metadata={"import_kind": "lib_link", "dll_name": disp, "dll_stem": stem},
                                sync_generation=sync_gen,
                            )
                        )

        if config_type.lower() == "dynamiclibrary" or output_type.lower() == "library":
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="dll_export",
                    role="provider",
                    normalized_key=f"dll:{target_name.lower()}",
                    file_path=rel_path,
                    symbol_name=f"{target_name}.dll",
                    metadata={"export_kind": "vcxproj_dll", "dll_name": f"{target_name}.dll", "dll_stem": target_name.lower()},
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_script_reg(
        content: str,
        rel_path: str,
        ext: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        if ext in (".bat", ".cmd"):
            for m in re.finditer(r'\b(?:sc(?:\.exe)?\s+(?:create|start|stop|control|config|delete)|net\s+(?:start|stop))\s+"?([A-Za-z0-9_.\-]+)"?', content, re.IGNORECASE):
                svc = m.group(1).strip()
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="scm_service",
                        role="consumer",
                        normalized_key=svc.lower(),
                        file_path=rel_path,
                        symbol_name=svc,
                        metadata={"service_name": svc, "api": "sc_script", "language": "bat"},
                        sync_generation=sync_gen,
                    )
                )
        elif ext == ".reg":
            for m in re.finditer(r"CurrentControlSet\\Services\\([A-Za-z0-9_.\-]+)", content, re.IGNORECASE):
                svc = m.group(1).strip()
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="scm_service",
                        role="consumer",
                        normalized_key=svc.lower(),
                        file_path=rel_path,
                        symbol_name=svc,
                        metadata={"service_name": svc, "api": "reg_service", "language": "reg"},
                        sync_generation=sync_gen,
                    )
                )
        return artifacts

    @staticmethod
    def _extract_from_shared_doc(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
        doc_key: Optional[str] = None,
        doc_node_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        eff_doc_key = doc_key or _derive_doc_key(rel_path)
        seen_keys: Set[str] = set()

        def _emit_spec(ref_kind: str, raw_tok: str, norm_key: str, match_keys: List[str], heading: str = "") -> None:
            if norm_key in seen_keys:
                return
            seen_keys.add(norm_key)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="spec_req",
                    role="shared",
                    normalized_key=norm_key,
                    node_id=doc_node_id or f"spec_req:{eff_doc_key}:{norm_key}",
                    file_path=rel_path,
                    symbol_name=raw_tok,
                    signature_hash=_sha256_json({"doc_key": eff_doc_key, "key": norm_key}),
                    metadata={
                        "doc_key": eff_doc_key,
                        "ref_kind": ref_kind,
                        "raw_token": raw_tok,
                        "match_keys": match_keys,
                        "heading": heading,
                    },
                    sync_generation=sync_gen,
                )
            )

        # 1. Explicit METHOD + /path endpoints
        for m in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/[A-Za-z0-9_\-./{}:]+)", content):
            method = m.group(1).upper()
            norm_p, _ = _normalize_http_path(m.group(2))
            _emit_spec(
                "endpoint",
                f"{method} {norm_p}",
                f"endpoint:{norm_p}",
                [f"{method}:{norm_p}", norm_p],
            )

        # 2. Standalone /path endpoints (prose or backticked)
        for m in re.finditer(r"(?:^|[\s`(\"'])(/[a-zA-Z][A-Za-z0-9_\-]*(?:/[A-Za-z0-9_\-{}:.]+)+)", content):
            raw_p = m.group(1)
            if raw_p.lower().endswith(_IGNORED_ASSET_EXTS):
                continue
            norm_p, _ = _normalize_http_path(raw_p)
            _emit_spec("endpoint", norm_p, f"endpoint:{norm_p}", [norm_p])

        # 2b. Named Pipes (\\.\pipe\...), Shared Memory (Global\..., Local\...), and COM UUIDs in specs
        for pm in re.finditer(r"((?:\\\\|\\)+\.(?:\\\\|\\)+pipe(?:\\\\|\\)+[A-Za-z0-9_.\-/\\]+)", content, re.IGNORECASE):
            norm_p = _normalize_pipe_name(pm.group(1))
            if norm_p:
                leaf, disp = norm_p
                _emit_spec("pipe", disp, f"pipe:{leaf}", [leaf, disp.lower(), pm.group(1).lower()])

        for sm in re.finditer(r"\b((?:Global|Local)(?:\\\\|\\)+[A-Za-z0-9_.\-/]+)", content, re.IGNORECASE):
            norm_s = _normalize_shm_name(sm.group(1))
            if norm_s:
                nkey, disp = norm_s
                _emit_spec("shm", disp, f"shm:{nkey}", [nkey, disp.lower(), disp.split("\\")[-1].lower()])

        for um in re.finditer(r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b", content):
            uid = um.group(1).lower()
            _emit_spec("uuid", uid, f"uuid:{uid}", [uid])

        # 3. Backticked symbols
        for raw_cand in re.findall(r"`([^`\n]+)`", content):
            cand = raw_cand.strip()
            if not cand or len(cand) < 2 or cand.lower() in _DOC_STOP_TOKENS:
                continue
            norm_pipe = _normalize_pipe_name(cand) if "pipe" in cand.lower() else None
            if norm_pipe:
                leaf, disp = norm_pipe
                _emit_spec("pipe", disp, f"pipe:{leaf}", [leaf, disp.lower()])
                continue
            norm_shm = _normalize_shm_name(cand) if ("global\\" in cand.lower() or "local\\" in cand.lower()) else None
            if norm_shm:
                nkey, disp = norm_shm
                _emit_spec("shm", disp, f"shm:{nkey}", [nkey, disp.lower(), disp.split("\\")[-1].lower()])
                continue
            if cand.startswith("/"):
                norm_p, _ = _normalize_http_path(cand)
                _emit_spec("endpoint", norm_p, f"endpoint:{norm_p}", [norm_p])
                continue
            m_ep = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/.*)$", cand)
            if m_ep:
                norm_p, _ = _normalize_http_path(m_ep.group(2))
                _emit_spec(
                    "endpoint",
                    f"{m_ep.group(1)} {norm_p}",
                    f"endpoint:{norm_p}",
                    [f"{m_ep.group(1)}:{norm_p}", norm_p],
                )
                continue
            clean_sym = re.sub(r"\(.*\)$", "", cand).strip()
            if not clean_sym or clean_sym.lower() in _DOC_STOP_TOKENS:
                continue
            _emit_spec(
                "symbol",
                clean_sym,
                f"symbol:{clean_sym.lower()}",
                [clean_sym.lower(), _normalize_schema_key(clean_sym)],
            )

        # 4. Headings & significant tokens inside headings
        for m in re.finditer(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$", content, re.MULTILINE):
            h_text = re.sub(r"`", "", m.group(2)).strip()
            if not h_text:
                continue
            h_slug = re.sub(r"[^a-z0-9]+", "_", h_text.lower()).strip("_")
            if h_slug:
                _emit_spec("heading", h_text, f"heading:{h_slug}", [h_text.lower()], heading=h_text)
            for tok in re.findall(r"\b([A-Z][A-Za-z0-9_]{2,}|[a-z]+_[a-z0-9_]+)\b", h_text):
                if tok.lower() not in _DOC_STOP_TOKENS:
                    _emit_spec(
                        "symbol",
                        tok,
                        f"symbol:{tok.lower()}",
                        [tok.lower(), _normalize_schema_key(tok)],
                        heading=h_text,
                    )

        return artifacts

    def _extract_from_graph_nodes(
        self,
        graph_nodes: Iterable[Any],
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for raw in graph_nodes:
            node = raw if isinstance(raw, dict) else (raw.to_dict() if hasattr(raw, "to_dict") else raw.model_dump())
            nid = str(node.get("id") or "")
            lbl = str(node.get("label") or "")
            src_file = str(node.get("source_file") or "")
            ftype = str(node.get("file_type") or "code")
            meta = node.get("metadata") or {}

            if source_id == "__shared__" or ftype == "document":
                if source_id == "__shared__":
                    text_parts = [
                        lbl,
                        str(meta.get("heading_breadcrumb") or ""),
                        str(meta.get("content") or meta.get("snippet") or ""),
                    ]
                    combined = "\n".join(p for p in text_parts if p)
                    if combined:
                        artifacts.extend(
                            self._extract_from_shared_doc(
                                combined,
                                src_file or "shared_doc.md",
                                project_id,
                                source_id,
                                branch,
                                sync_gen,
                                doc_key=meta.get("doc_key"),
                                doc_node_id=nid or None,
                            )
                        )
                continue

            kind = str(meta.get("kind") or meta.get("symbol_type") or "").lower()
            m_route = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/.*)$", lbl.strip(), re.IGNORECASE)
            if kind == "route" or meta.get("route") or m_route:
                if m_route:
                    method = m_route.group(1).upper()
                    raw_p = m_route.group(2)
                else:
                    method = str(meta.get("method") or "GET").upper()
                    raw_p = str(meta.get("path") or lbl)
                norm_p, short_suf = _normalize_http_path(raw_p)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_endpoint",
                        role="provider",
                        normalized_key=f"{method}:{norm_p}",
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=f"{method} {norm_p}",
                        signature_hash=_sha256_json(
                            {"method": method, "path": norm_p, "signature": meta.get("signature") or ""}
                        ),
                        metadata={"method": method, "path": norm_p, "short_suffix": short_suf},
                        sync_generation=sync_gen,
                    )
                )
            elif kind in ("schema", "interface", "table", "struct") or (
                kind == "class" and (meta.get("fields") or lbl.endswith(_SCHEMA_SUFFIXES))
            ):
                if kind == "interface" and meta.get("uuid"):
                    uid = _normalize_uuid(str(meta["uuid"])) or lbl.lower()
                    s_low = lbl.lower()
                    aliases = [uid, s_low, f"iid_{s_low}", f"clsid_{s_low}"]
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="com_idl",
                            role="provider",
                            normalized_key=uid,
                            node_id=nid or None,
                            file_path=src_file,
                            symbol_name=lbl,
                            signature_hash=_sha256_json({"uuid": uid, "symbol": lbl}),
                            metadata={"uuid": uid, "symbol": lbl, "match_aliases": aliases, "doc_tokens": aliases},
                            sync_generation=sync_gen,
                        )
                    )
                else:
                    raw_fields = meta.get("fields") or []
                    fields = [
                        (str(f.get("name") or ""), str(f.get("type") or "Any"))
                        for f in raw_fields
                        if isinstance(f, dict)
                    ]
                    sig_hash = _schema_signature_hash(fields) if fields else _sha256_json({"name": lbl, "sig": meta.get("signature") or ""})
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="schema_def",
                            role="provider",
                            normalized_key=_normalize_schema_key(lbl),
                            node_id=nid or None,
                            file_path=src_file,
                            symbol_name=lbl,
                            signature_hash=sig_hash,
                            metadata={"schema_name": lbl, "fields": raw_fields},
                            sync_generation=sync_gen,
                        )
                    )
            elif kind == "coclass" and meta.get("uuid"):
                uid = _normalize_uuid(str(meta["uuid"])) or lbl.lower()
                s_low = lbl.lower()
                aliases = [uid, s_low, f"clsid_{s_low}", f"iid_{s_low}"]
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="com_idl",
                        role="provider",
                        normalized_key=uid,
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=lbl,
                        signature_hash=_sha256_json({"uuid": uid, "symbol": lbl}),
                        metadata={"uuid": uid, "symbol": lbl, "match_aliases": aliases, "doc_tokens": aliases},
                        sync_generation=sync_gen,
                    )
                )
            elif kind in ("server", "client"):
                pkind = f"lasal_{kind}"
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="plc_lasal_channel",
                        role="provider" if kind == "server" else "consumer",
                        normalized_key=f"channel:{lbl.lower()}",
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=lbl,
                        signature_hash=_sha256_json({"plc_kind": pkind, "name": lbl, "type": meta.get("data_type") or ""}),
                        metadata={"plc_kind": pkind, "channel_name": lbl, "data_type": meta.get("data_type") or "ANY", "doc_tokens": [lbl.lower()]},
                        sync_generation=sync_gen,
                    )
                )
            elif kind == "variable" and str(meta.get("var_scope") or "").upper() in ("VAR_GLOBAL", "VAR_EXTERNAL"):
                vscope = str(meta.get("var_scope") or "").upper()
                pkind = "var_global" if vscope == "VAR_GLOBAL" else "var_external"
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="plc_lasal_channel",
                        role="provider" if pkind == "var_global" else "consumer",
                        normalized_key=f"tag:{lbl.lower()}",
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=lbl,
                        signature_hash=_sha256_json({"plc_kind": pkind, "name": lbl, "type": meta.get("data_type") or ""}),
                        metadata={"plc_kind": pkind, "tag_name": lbl, "data_type": meta.get("data_type") or "ANY", "doc_tokens": [lbl.lower()]},
                        sync_generation=sync_gen,
                    )
                )
            elif kind == "export" or meta.get("dllexport"):
                clean_sym = lbl.rstrip("()")
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="provider",
                        normalized_key=clean_sym.lower(),
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=clean_sym,
                        signature_hash=_sha256_json({"export": clean_sym, "params": meta.get("params") or ""}),
                        metadata={"export_kind": kind or "dllexport", "symbol": clean_sym, "doc_tokens": [clean_sym.lower()]},
                        sync_generation=sync_gen,
                    )
                )
            elif meta.get("is_declare") and meta.get("dll_lib"):
                clean_sym = lbl.rstrip("()")
                norm = _normalize_dll_stem(str(meta["dll_lib"]))
                if norm:
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="dll_export",
                            role="consumer",
                            normalized_key=f"dll:{norm[0]}",
                            node_id=nid or None,
                            file_path=src_file,
                            symbol_name=norm[1],
                            metadata={"import_kind": "vb6_declare", "dll_name": norm[1], "dll_stem": norm[0]},
                            sync_generation=sync_gen,
                        )
                    )
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="dll_export",
                        role="consumer",
                        normalized_key=clean_sym.lower(),
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=clean_sym,
                        metadata={"import_kind": "vb6_declare", "symbol": clean_sym},
                        sync_generation=sync_gen,
                    )
                )
            elif meta.get("contract_kind") in ("event_pub", "event_sub") or meta.get("event_topic"):
                ck = str(meta.get("contract_kind") or "event_pub")
                topic = str(meta.get("event_topic") or lbl).strip().lower()
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind=ck,
                        role="provider" if ck == "event_pub" else "consumer",
                        normalized_key=topic,
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=topic,
                        signature_hash=_sha256_json({"topic": topic}),
                        metadata={"topic": topic},
                        sync_generation=sync_gen,
                    )
                )
        return artifacts

    def _load_shared_docs_from_db(
        self, project_id: str, branch: str
    ) -> List[Dict[str, Any]]:
        """Read active `project_shared` / `__shared__` documents & units from Project DB `pb_root`."""
        docs: List[Dict[str, Any]] = []
        try:
            ver_rows = tenant_manager._project_root_fetchall(
                project_id,
                "SELECT * FROM document_versions "
                "WHERE project_id = ? AND status = 'active' "
                "AND (scope = 'project_shared' OR source_id = '__shared__')",
                (project_id,),
            )
            for dv in ver_rows:
                d_br = str(dv.get("branch") or branch)
                d_key = str(dv.get("doc_key") or "")
                d_ver = str(dv.get("version") or "")
                fname = str(dv.get("filename") or f"{d_key}.md")
                units = tenant_manager._project_root_fetchall(
                    project_id,
                    "SELECT unit_title, markdown_text FROM document_units "
                    "WHERE project_id = ? AND branch = ? AND doc_key = ? AND version = ? "
                    "ORDER BY unit_index ASC",
                    (project_id, d_br, d_key, d_ver),
                )
                md_text = "\n\n".join(
                    str(u.get("markdown_text") or "") for u in units if u.get("markdown_text")
                )
                if md_text.strip():
                    docs.append(
                        {
                            "doc_key": d_key,
                            "filename": fname,
                            "content": md_text,
                            "version": d_ver,
                        }
                    )
        except Exception as exc:
            logger.debug("Shared doc DB load skipped for %s: %s", project_id, exc)
        return docs

    def extract_contract_artifacts(
        self,
        project_id: str,
        source_id: str,
        branch: str = "main",
        workspace_path: Optional[Union[str, Path]] = None,
        graph_nodes: Optional[List[Dict[str, Any]]] = None,
        docs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Phase A: Extract contract surface artifacts for `(project_id, source_id, branch)` and persist in `pb_root`."""
        proj = self._ensure_tables(project_id)
        eff_src = str(source_id or "__default__").strip() or "__default__"
        eff_branch = str(branch or "main").strip() or "main"

        sync_gen = 0
        try:
            src_meta = tenant_manager.get_project_source(proj, eff_src)
            if src_meta:
                sync_gen = int(src_meta.get("sync_generation") or 0)
        except Exception:
            sync_gen = 0

        extracted: List[Dict[str, Any]] = []

        # 1. Walk workspace files if provided
        if workspace_path is not None:
            ws = Path(workspace_path)
            if ws.is_file():
                file_iter: List[Tuple[Path, str]] = [(ws, ws.name)]
            elif ws.is_dir():
                file_iter = []
                for root, dirs, files in os.walk(ws):
                    dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
                    for fname in sorted(files):
                        fpath = Path(root) / fname
                        ext = fpath.suffix.lower()
                        if ext in _WALK_EXTENSIONS or fname.startswith(".env"):
                            rel = os.path.relpath(fpath, ws).replace("\\", "/")
                            file_iter.append((fpath, rel))
            else:
                file_iter = []

            for fpath, rel_path in file_iter:
                try:
                    text, _enc = read_text_with_encoding(fpath)
                except Exception:
                    continue
                ext = fpath.suffix.lower()
                fname_low = fpath.name.lower()
                if eff_src == "__shared__" and ext == ".md":
                    extracted.extend(
                        self._extract_from_shared_doc(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".py":
                    extracted.extend(
                        self._extract_from_python(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".ts", ".tsx", ".js", ".jsx"):
                    extracted.extend(
                        self._extract_from_ts_js(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".go", ".java", ".proto"):
                    extracted.extend(
                        self._extract_from_go_java_proto(
                            text, rel_path, ext, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".sql":
                    extracted.extend(
                        self._extract_from_sql(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".yml", ".yaml", ".json"):
                    extracted.extend(
                        self._extract_from_yaml_json(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".tf":
                    extracted.extend(
                        self._extract_from_terraform(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif fpath.name.startswith(".env") or fname_low.endswith(".env"):
                    extracted.extend(
                        self._extract_from_env_file(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in _CPP_CS_EXTS:
                    extracted.extend(
                        self._extract_from_cpp_cs_legacy(
                            text, rel_path, ext, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".idl":
                    extracted.extend(
                        self._extract_from_idl(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".def":
                    extracted.extend(
                        self._extract_from_def(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".rc":
                    extracted.extend(
                        self._extract_from_rc(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in _VB6_DELPHI_EXTS:
                    extracted.extend(
                        self._extract_from_vb6_delphi(
                            text, rel_path, ext, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in _ST_LASAL_EXTS:
                    extracted.extend(
                        self._extract_from_plc_st_lasal(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in _VS_PROJ_EXTS:
                    extracted.extend(
                        self._extract_from_vs_project(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".bat", ".cmd", ".reg"):
                    extracted.extend(
                        self._extract_from_script_reg(
                            text, rel_path, ext, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".md":
                    # Non-shared markdown inside a source workspace can still mention endpoints/events in API specs if needed,
                    # or keep shared docs as `spec_req`. Only emit `spec_req` for `__shared__` unless explicit.
                    pass

        # 2. Extract from explicit graph_nodes (or GraphStore fallback if no workspace_path and no graph_nodes passed)
        if graph_nodes is not None:
            extracted.extend(
                self._extract_from_graph_nodes(
                    graph_nodes, proj, eff_src, eff_branch, sync_gen
                )
            )
        elif workspace_path is None and docs is None and eff_src != "__shared__":
            try:
                from projectbrain.graphify_engine.store import get_graph_store

                store = get_graph_store(project_id=proj)
                try:
                    target_id = proj if eff_src == "__default__" else f"{proj}@{eff_src}"
                    sub = store.get_resolved_subgraph(target_id, eff_branch, limit=50000)
                    g_nodes = sub.get("nodes") or []
                    if g_nodes:
                        extracted.extend(
                            self._extract_from_graph_nodes(
                                g_nodes, proj, eff_src, eff_branch, sync_gen
                            )
                        )
                finally:
                    store.close()
            except Exception as exc:
                logger.debug("GraphStore fallback read skipped for %s@%s: %s", proj, eff_src, exc)

        # 3. Extract from explicit docs OR DB-persisted shared docs when source_id == "__shared__"
        eff_docs: List[Dict[str, Any]] = list(docs or [])
        if eff_src == "__shared__" and docs is None:
            eff_docs.extend(self._load_shared_docs_from_db(proj, eff_branch))

        for d in eff_docs:
            if not isinstance(d, dict):
                continue
            d_text = str(d.get("content") or d.get("markdown_text") or "")
            d_path = str(d.get("doc_path") or d.get("filename") or d.get("doc_key") or "shared.md")
            d_key = d.get("doc_key")
            d_nid = d.get("doc_node_id") or d.get("id")
            if d_text.strip():
                extracted.extend(
                    self._extract_from_shared_doc(
                        d_text,
                        d_path,
                        proj,
                        eff_src,
                        eff_branch,
                        sync_gen,
                        doc_key=d_key,
                        doc_node_id=d_nid,
                    )
                )

        # Deduplicate by unique key `(project_id, source_id, branch, contract_kind, role, normalized_key, node_id)`
        deduped: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for art in extracted:
            ukey = (
                art["contract_kind"],
                art["role"],
                art["normalized_key"],
                art["node_id"],
            )
            deduped[ukey] = art
        final_artifacts = list(deduped.values())

        # Persist into cross_source_contract_artifacts (delete stale rows for (project_id, source_id, branch) first)
        now_ms = int(time.time() * 1000)
        tenant_manager._project_root_execute(
            proj,
            "DELETE FROM cross_source_contract_artifacts WHERE project_id = ? AND source_id = ? AND branch = ?",
            (proj, eff_src, eff_branch),
        )
        for art in final_artifacts:
            meta_json = json.dumps(art.get("metadata") or {})
            tenant_manager._project_root_execute(
                proj,
                """
                INSERT INTO cross_source_contract_artifacts (
                    id, project_id, source_id, branch, contract_kind, role,
                    normalized_key, node_id, file_path, symbol_name, signature_hash,
                    metadata_json, sync_generation, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, source_id, branch, contract_kind, role, normalized_key, node_id)
                DO UPDATE SET
                    file_path = excluded.file_path,
                    symbol_name = excluded.symbol_name,
                    signature_hash = excluded.signature_hash,
                    metadata_json = excluded.metadata_json,
                    sync_generation = excluded.sync_generation,
                    updated_at = excluded.updated_at
                """,
                (
                    art["id"],
                    proj,
                    eff_src,
                    eff_branch,
                    art["contract_kind"],
                    art["role"],
                    art["normalized_key"],
                    art["node_id"],
                    art["file_path"],
                    art["symbol_name"],
                    art["signature_hash"],
                    meta_json,
                    art["sync_generation"],
                    now_ms,
                ),
            )
            art["metadata_json"] = meta_json
            art["updated_at"] = now_ms

        return final_artifacts

    # ------------------------------------------------------------------
    # Phase B: Multi-Signal Cross-Source Reconciliation & Drift Detection
    # ------------------------------------------------------------------
    def _load_artifacts(self, project_id: str, branch: str) -> List[Dict[str, Any]]:
        rows = tenant_manager._project_root_fetchall(
            project_id,
            "SELECT * FROM cross_source_contract_artifacts "
            "WHERE project_id = ? AND (branch = ? OR (source_id = '__shared__' AND branch = 'main'))",
            (project_id, branch),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["metadata"] = json.loads(item.get("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            out.append(item)
        return out

    @staticmethod
    def _schema_provider_rank(art: Dict[str, Any]) -> Tuple[int, int, str]:
        role = str(art.get("role") or "")
        role_score = 2 if role == "provider" else (1 if role == "shared" else 0)
        ext = Path(str(art.get("file_path") or "")).suffix.lower()
        lang_score = 2 if ext in _BACKEND_EXTS else (0 if ext in _FRONTEND_EXTS else 1)
        return (role_score, lang_score, str(art.get("source_id") or ""))

    def _build_relation_candidates(
        self,
        proj: str,
        eff_branch: str,
        artifacts: List[Dict[str, Any]],
        registered_sources: Set[str],
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen_cand_keys: Set[Tuple[str, str, str, str, str]] = set()

        def _add_candidate(
            *,
            src_art: Dict[str, Any],
            dst_source_id: str,
            dst_branch: str,
            dst_node_id: str,
            dst_doc_key: Optional[str],
            relation_type: str,
            contract_key: str,
            confidence: str,
            confidence_score: float,
            provider_signature_hash: str,
            drift_reason_default: str,
            is_bidirectional: bool = False,
            extra_meta: Optional[Dict[str, Any]] = None,
        ) -> None:
            src_id = str(src_art["source_id"])
            if not dst_source_id or src_id == dst_source_id:
                return
            dedup_k = (src_id, dst_source_id, relation_type, contract_key, str(src_art.get("node_id") or ""))
            if dedup_k in seen_cand_keys:
                return
            seen_cand_keys.add(dedup_k)
            meta = {
                "provider_signature_hash": provider_signature_hash,
                "consumer_signature_hash": str(src_art.get("signature_hash") or ""),
                "contract_kind": src_art.get("contract_kind"),
                **(extra_meta or {}),
            }
            candidates.append(
                {
                    "project_id": proj,
                    "src_source_id": src_id,
                    "src_branch": str(src_art.get("branch") or eff_branch),
                    "src_node_id": str(src_art.get("node_id") or ""),
                    "src_file_path": src_art.get("file_path"),
                    "dst_source_id": dst_source_id,
                    "dst_branch": dst_branch,
                    "dst_node_id": dst_node_id,
                    "dst_doc_key": dst_doc_key,
                    "relation_type": relation_type,
                    "confidence": confidence,
                    "confidence_score": confidence_score,
                    "contract_key": contract_key,
                    "contract_metadata": meta,
                    "is_bidirectional": is_bidirectional,
                    "drift_reason_default": drift_reason_default,
                }
            )

        by_kind: Dict[str, List[Dict[str, Any]]] = {}
        for art in artifacts:
            by_kind.setdefault(str(art["contract_kind"]), []).append(art)

        # 1. http_client_call -> http_endpoint (calls_api)
        endpoints = by_kind.get("http_endpoint", [])
        ep_by_exact: Dict[str, List[Dict[str, Any]]] = {}
        ep_by_path: Dict[str, List[Dict[str, Any]]] = {}
        ep_by_suffix: Dict[str, List[Dict[str, Any]]] = {}
        for ep in endpoints:
            ep_by_exact.setdefault(str(ep["normalized_key"]), []).append(ep)
            ep_path = str((ep.get("metadata") or {}).get("path") or "")
            if ep_path:
                ep_by_path.setdefault(ep_path, []).append(ep)
            ep_suf = str((ep.get("metadata") or {}).get("short_suffix") or "")
            if ep_suf and ep_suf != "/" and ep_suf != "/{param}":
                ep_by_suffix.setdefault(ep_suf, []).append(ep)

        for call in by_kind.get("http_client_call", []):
            c_meta = call.get("metadata") or {}
            c_path = str(c_meta.get("path") or "")
            c_suf = str(c_meta.get("short_suffix") or "")
            explicit_m = bool(c_meta.get("method_explicit"))

            matched_eps: List[Tuple[Dict[str, Any], str, float]] = []
            if explicit_m and str(call["normalized_key"]) in ep_by_exact:
                for ep in ep_by_exact[str(call["normalized_key"])]:
                    if ep["source_id"] != call["source_id"]:
                        matched_eps.append((ep, "high", 1.0))
            elif not explicit_m and c_path in ep_by_path:
                for ep in ep_by_path[c_path]:
                    if ep["source_id"] != call["source_id"]:
                        matched_eps.append((ep, "high", 0.95))
            elif not matched_eps and c_suf in ep_by_suffix:
                cands = [ep for ep in ep_by_suffix[c_suf] if ep["source_id"] != call["source_id"]]
                if len(cands) == 1:
                    matched_eps.append((cands[0], "medium", 0.75))

            for ep, conf, score in matched_eps:
                _add_candidate(
                    src_art=call,
                    dst_source_id=str(ep["source_id"]),
                    dst_branch=str(ep.get("branch") or eff_branch),
                    dst_node_id=str(ep.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="calls_api",
                    contract_key=str(ep["normalized_key"]),
                    confidence=conf,
                    confidence_score=score,
                    provider_signature_hash=str(ep.get("signature_hash") or ""),
                    drift_reason_default="Provider endpoint signature changed",
                    extra_meta={
                        "endpoint_path": (ep.get("metadata") or {}).get("path"),
                        "method": (ep.get("metadata") or {}).get("method"),
                        "dst_file_path": ep.get("file_path"),
                    },
                )

        # 2. schema_def / schema_ref across different sources (shares_schema)
        schema_groups: Dict[str, List[Dict[str, Any]]] = {}
        for s_art in by_kind.get("schema_def", []) + by_kind.get("schema_ref", []):
            nkey = str(s_art["normalized_key"])
            if nkey:
                schema_groups.setdefault(nkey, []).append(s_art)

        for nkey, group in schema_groups.items():
            # Keep strongest schema artifact per source (prefer schema_def over schema_ref)
            by_src: Dict[str, Dict[str, Any]] = {}
            for art in group:
                sid = str(art["source_id"])
                prev = by_src.get(sid)
                if prev is None or (prev["contract_kind"] == "schema_ref" and art["contract_kind"] == "schema_def"):
                    by_src[sid] = art
            if len(by_src) < 2:
                continue

            # Identify provider(s) and consumers
            all_arts = list(by_src.values())
            # Sort descending by provider rank; highest ranked schema_def is canonical provider
            defs = [a for a in all_arts if a["contract_kind"] == "schema_def"]
            if not defs:
                continue
            defs.sort(key=self._schema_provider_rank, reverse=True)
            provider = defs[0]
            schema_label = str((provider.get("metadata") or {}).get("schema_name") or provider.get("symbol_name") or nkey)
            for other in all_arts:
                if other["source_id"] == provider["source_id"]:
                    continue
                is_bidir = other["contract_kind"] == "schema_def" and other.get("role") == provider.get("role")
                _add_candidate(
                    src_art=other,
                    dst_source_id=str(provider["source_id"]),
                    dst_branch=str(provider.get("branch") or eff_branch),
                    dst_node_id=str(provider.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="shares_schema",
                    contract_key=f"schema:{schema_label}",
                    confidence="high",
                    confidence_score=0.95 if other["contract_kind"] == "schema_def" else 0.90,
                    provider_signature_hash=str(provider.get("signature_hash") or ""),
                    drift_reason_default="Provider schema signature changed",
                    is_bidirectional=is_bidir,
                    extra_meta={
                        "schema_name": schema_label,
                        "provider_fields": (provider.get("metadata") or {}).get("fields", []),
                        "consumer_fields": (other.get("metadata") or {}).get("fields", []),
                        "dst_file_path": provider.get("file_path"),
                    },
                )

        # 3. event_sub -> event_pub (consumes_events)
        pubs_by_topic: Dict[str, List[Dict[str, Any]]] = {}
        for pub in by_kind.get("event_pub", []):
            pubs_by_topic.setdefault(str(pub["normalized_key"]), []).append(pub)
        for sub in by_kind.get("event_sub", []):
            for pub in pubs_by_topic.get(str(sub["normalized_key"]), []):
                if pub["source_id"] == sub["source_id"]:
                    continue
                _add_candidate(
                    src_art=sub,
                    dst_source_id=str(pub["source_id"]),
                    dst_branch=str(pub.get("branch") or eff_branch),
                    dst_node_id=str(pub.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="consumes_events",
                    contract_key=f"event:{pub['normalized_key']}",
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(pub.get("signature_hash") or ""),
                    drift_reason_default="Provider event contract changed",
                    extra_meta={"topic": pub["normalized_key"], "dst_file_path": pub.get("file_path")},
                )

        # 4. infra_deploy -> target service (depends_on / deploys)
        infra_arts = by_kind.get("infra_deploy", [])
        infra_prov_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for ia in infra_arts:
            if ia["role"] == "provider":
                infra_prov_by_key.setdefault(str(ia["normalized_key"]), []).append(ia)
        reg_src_map = {s.lower(): s for s in registered_sources}

        for ia in infra_arts:
            key = str(ia["normalized_key"])
            if ia["role"] == "consumer":
                matched_provs = [p for p in infra_prov_by_key.get(key, []) if p["source_id"] != ia["source_id"]]
                for prov in matched_provs:
                    _add_candidate(
                        src_art=ia,
                        dst_source_id=str(prov["source_id"]),
                        dst_branch=str(prov.get("branch") or eff_branch),
                        dst_node_id=str(prov.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type="depends_on",
                        contract_key=f"infra:{key}",
                        confidence="high",
                        confidence_score=0.90,
                        provider_signature_hash=str(prov.get("signature_hash") or ""),
                        drift_reason_default="Target service deployment definition changed",
                    )
                if not matched_provs and key in reg_src_map and reg_src_map[key] != ia["source_id"]:
                    _add_candidate(
                        src_art=ia,
                        dst_source_id=reg_src_map[key],
                        dst_branch=eff_branch,
                        dst_node_id="",
                        dst_doc_key=None,
                        relation_type="depends_on",
                        contract_key=f"infra:{key}",
                        confidence="medium",
                        confidence_score=0.80,
                        provider_signature_hash=str(ia.get("signature_hash") or ""),
                        drift_reason_default="Target service deployment definition changed",
                    )
            elif ia["role"] == "provider":
                if key in reg_src_map and reg_src_map[key] != ia["source_id"]:
                    _add_candidate(
                        src_art=ia,
                        dst_source_id=reg_src_map[key],
                        dst_branch=eff_branch,
                        dst_node_id="",
                        dst_doc_key=None,
                        relation_type="deploys",
                        contract_key=f"deploy:{key}",
                        confidence="medium",
                        confidence_score=0.85,
                        provider_signature_hash=str(ia.get("signature_hash") or ""),
                        drift_reason_default="Deployment manifest changed",
                    )

        # 4b. project_dep (.vcxproj <ProjectReference> & .sln ProjectDependencies -> depends_on)
        proj_provs: Dict[str, List[Dict[str, Any]]] = {}
        for pa in by_kind.get("project_dep", []):
            if pa["role"] == "provider":
                proj_provs.setdefault(str(pa["normalized_key"]), []).append(pa)
        for pa in by_kind.get("project_dep", []):
            if pa["role"] != "consumer":
                continue
            key = str(pa["normalized_key"])
            matched_provs = [p for p in proj_provs.get(key, []) if p["source_id"] != pa["source_id"]]
            for prov in matched_provs:
                _add_candidate(
                    src_art=pa,
                    dst_source_id=str(prov["source_id"]),
                    dst_branch=str(prov.get("branch") or eff_branch),
                    dst_node_id=str(prov.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="depends_on",
                    contract_key=f"project_ref:{prov['source_id']}",
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(prov.get("signature_hash") or ""),
                    drift_reason_default="Referenced project manifest changed",
                    extra_meta={**(pa.get("metadata") or {}), "dst_file_path": prov.get("file_path")},
                )
            if not matched_provs and key in reg_src_map and reg_src_map[key] != pa["source_id"]:
                dst_sid = reg_src_map[key]
                _add_candidate(
                    src_art=pa,
                    dst_source_id=dst_sid,
                    dst_branch=eff_branch,
                    dst_node_id="",
                    dst_doc_key=None,
                    relation_type="depends_on",
                    contract_key=f"project_ref:{dst_sid}",
                    confidence="high",
                    confidence_score=0.90,
                    provider_signature_hash=str(pa.get("signature_hash") or ""),
                    drift_reason_default="Referenced project dependency changed",
                    extra_meta=dict(pa.get("metadata") or {}),
                )

        # 5. spec_req (from __shared__ docs) -> matching code/contract artifacts (documented_by)
        non_shared = [
            a
            for a in artifacts
            if a["source_id"] != "__shared__" and a["contract_kind"] not in ("spec_req", "plc_token_index")
        ]
        target_by_token: Dict[str, List[Dict[str, Any]]] = {}
        for t_art in non_shared:
            t_meta = t_art.get("metadata") or {}
            tokens: Set[str] = set()
            if t_art["contract_kind"] in ("http_endpoint", "http_client_call"):
                if t_meta.get("path"):
                    tokens.add(str(t_meta["path"]))
                tokens.add(str(t_art["normalized_key"]))
            elif t_art["contract_kind"] in ("schema_def", "schema_ref"):
                tokens.add(str(t_art["normalized_key"]))
                if t_art.get("symbol_name"):
                    tokens.add(str(t_art["symbol_name"]).lower())
            elif t_art["contract_kind"] in ("event_pub", "event_sub", "infra_deploy"):
                tokens.add(str(t_art["normalized_key"]))
            elif t_art["contract_kind"] in (
                "named_pipe",
                "shared_memory",
                "scm_service",
                "com_idl",
                "win32_msg",
                "dll_export",
                "socket_ipc",
                "plc_lasal_channel",
            ):
                tokens.add(str(t_art["normalized_key"]))
                if t_art.get("symbol_name"):
                    tokens.add(str(t_art["symbol_name"]).lower())
                for dt in t_meta.get("doc_tokens") or []:
                    if dt:
                        tokens.add(str(dt).lower())
            for tok in tokens:
                if tok:
                    target_by_token.setdefault(tok, []).append(t_art)

        for spec in by_kind.get("spec_req", []):
            s_meta = spec.get("metadata") or {}
            doc_key = str(s_meta.get("doc_key") or _derive_doc_key(str(spec.get("file_path") or "shared")))
            ref_kind = str(s_meta.get("ref_kind") or "symbol")
            match_keys = [str(k) for k in (s_meta.get("match_keys") or []) if k]
            matched_targets: List[Dict[str, Any]] = []
            seen_t_ids: Set[str] = set()
            for mk in match_keys:
                for t_art in target_by_token.get(mk, []):
                    tid = str(t_art["id"])
                    if tid not in seen_t_ids:
                        seen_t_ids.add(tid)
                        matched_targets.append(t_art)

            for t_art in matched_targets:
                target_label = str(t_art.get("symbol_name") or t_art.get("normalized_key"))
                _add_candidate(
                    src_art=t_art,
                    dst_source_id=str(spec["source_id"]),
                    dst_branch=str(spec.get("branch") or eff_branch),
                    dst_node_id=str(spec.get("node_id") or ""),
                    dst_doc_key=doc_key,
                    relation_type="documented_by",
                    contract_key=f"spec:{doc_key}:{t_art['normalized_key']}",
                    confidence="high" if ref_kind in ("endpoint", "symbol", "pipe", "shm", "uuid") else "medium",
                    confidence_score=1.0 if ref_kind in ("endpoint", "symbol", "pipe", "shm", "uuid") else 0.8,
                    provider_signature_hash=str(t_art.get("signature_hash") or ""),
                    drift_reason_default="Documented artifact signature changed",
                    extra_meta={
                        "doc_key": doc_key,
                        "spec_ref": spec.get("symbol_name"),
                        "spec_signature_hash": spec.get("signature_hash"),
                        "target_symbol": target_label,
                    },
                )

        # 6. named_pipe -> calls_pipe
        pipe_provs: Dict[str, List[Dict[str, Any]]] = {}
        for pa in by_kind.get("named_pipe", []):
            if pa["role"] == "provider":
                pipe_provs.setdefault(str(pa["normalized_key"]), []).append(pa)
        for ca in by_kind.get("named_pipe", []):
            if ca["role"] != "consumer":
                continue
            for prov in pipe_provs.get(str(ca["normalized_key"]), []):
                if prov["source_id"] == ca["source_id"]:
                    continue
                disp = str(prov.get("symbol_name") or ca.get("symbol_name") or ca["normalized_key"])
                _add_candidate(
                    src_art=ca,
                    dst_source_id=str(prov["source_id"]),
                    dst_branch=str(prov.get("branch") or eff_branch),
                    dst_node_id=str(prov.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="calls_pipe",
                    contract_key=f"pipe:{disp}",
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(prov.get("signature_hash") or ""),
                    drift_reason_default="Named pipe server contract changed",
                    extra_meta={"pipe_name": disp, "dst_file_path": prov.get("file_path")},
                )

        # 7. shared_memory -> shares_shm
        shm_arts = by_kind.get("shared_memory", [])
        shm_provs: Dict[str, List[Dict[str, Any]]] = {}
        for sa in shm_arts:
            if sa["role"] == "provider":
                shm_provs.setdefault(str(sa["normalized_key"]), []).append(sa)
        for sa in shm_arts:
            nkey = str(sa["normalized_key"])
            provs = [p for p in shm_provs.get(nkey, []) if p["source_id"] != sa["source_id"]]
            if sa["role"] == "consumer":
                for prov in provs:
                    disp = str(prov.get("symbol_name") or sa.get("symbol_name") or nkey)
                    _add_candidate(
                        src_art=sa,
                        dst_source_id=str(prov["source_id"]),
                        dst_branch=str(prov.get("branch") or eff_branch),
                        dst_node_id=str(prov.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type="shares_shm",
                        contract_key=f"shm:{disp}",
                        confidence="high",
                        confidence_score=0.95,
                        provider_signature_hash=str(prov.get("signature_hash") or ""),
                        drift_reason_default="Shared memory layout or mapping changed",
                        extra_meta={"shm_name": disp, "dst_file_path": prov.get("file_path")},
                    )
            elif sa["role"] == "provider" and len(provs) > 0:
                # Peer CreateFileMapping / CreateOrOpen across sources
                for prov in provs:
                    if str(sa["source_id"]) < str(prov["source_id"]):
                        disp = str(prov.get("symbol_name") or sa.get("symbol_name") or nkey)
                        _add_candidate(
                            src_art=sa,
                            dst_source_id=str(prov["source_id"]),
                            dst_branch=str(prov.get("branch") or eff_branch),
                            dst_node_id=str(prov.get("node_id") or ""),
                            dst_doc_key=None,
                            relation_type="shares_shm",
                            contract_key=f"shm:{disp}",
                            confidence="high",
                            confidence_score=0.90,
                            provider_signature_hash=str(prov.get("signature_hash") or ""),
                            drift_reason_default="Shared memory layout changed",
                            is_bidirectional=True,
                            extra_meta={"shm_name": disp, "dst_file_path": prov.get("file_path")},
                        )

        # 8. scm_service -> controls_service
        svc_provs: Dict[str, List[Dict[str, Any]]] = {}
        for sa in by_kind.get("scm_service", []):
            if sa["role"] == "provider":
                svc_provs.setdefault(str(sa["normalized_key"]), []).append(sa)
        for sa in by_kind.get("scm_service", []):
            if sa["role"] != "consumer":
                continue
            nkey = str(sa["normalized_key"])
            matched_provs = [p for p in svc_provs.get(nkey, []) if p["source_id"] != sa["source_id"]]
            for prov in matched_provs:
                disp = str(prov.get("symbol_name") or sa.get("symbol_name") or nkey)
                _add_candidate(
                    src_art=sa,
                    dst_source_id=str(prov["source_id"]),
                    dst_branch=str(prov.get("branch") or eff_branch),
                    dst_node_id=str(prov.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="controls_service",
                    contract_key=f"service:{disp}",
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(prov.get("signature_hash") or ""),
                    drift_reason_default="Windows Service handler changed",
                    extra_meta={"service_name": disp, "dst_file_path": prov.get("file_path")},
                )
            if not matched_provs and nkey in reg_src_map and reg_src_map[nkey] != sa["source_id"]:
                dst_sid = reg_src_map[nkey]
                disp = str(sa.get("symbol_name") or dst_sid)
                _add_candidate(
                    src_art=sa,
                    dst_source_id=dst_sid,
                    dst_branch=eff_branch,
                    dst_node_id="",
                    dst_doc_key=None,
                    relation_type="controls_service",
                    contract_key=f"service:{disp}",
                    confidence="medium",
                    confidence_score=0.80,
                    provider_signature_hash=str(sa.get("signature_hash") or ""),
                    drift_reason_default="Target Windows Service changed",
                    extra_meta={"service_name": disp},
                )

        # 9. com_idl -> calls_com
        com_provs_by_alias: Dict[str, List[Dict[str, Any]]] = {}
        com_arts = by_kind.get("com_idl", [])
        for ca in com_arts:
            if ca["role"] == "provider":
                aliases = set([str(ca["normalized_key"])] + [str(x).lower() for x in ((ca.get("metadata") or {}).get("match_aliases") or [])])
                for al in aliases:
                    if al:
                        com_provs_by_alias.setdefault(al, []).append(ca)
        for ca in com_arts:
            aliases = [str(ca["normalized_key"])] + [str(x).lower() for x in ((ca.get("metadata") or {}).get("match_aliases") or [])]
            matched: List[Dict[str, Any]] = []
            seen_ids: Set[str] = set()
            for al in aliases:
                for prov in com_provs_by_alias.get(al, []):
                    if prov["source_id"] == ca["source_id"]:
                        continue
                    pid = str(prov["id"])
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        matched.append(prov)
            if ca["role"] == "consumer":
                for prov in matched:
                    disp = str(prov.get("symbol_name") or ca.get("symbol_name") or prov["normalized_key"])
                    _add_candidate(
                        src_art=ca,
                        dst_source_id=str(prov["source_id"]),
                        dst_branch=str(prov.get("branch") or eff_branch),
                        dst_node_id=str(prov.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type="calls_com",
                        contract_key=f"com:{disp}",
                        confidence="high",
                        confidence_score=0.95,
                        provider_signature_hash=str(prov.get("signature_hash") or ""),
                        drift_reason_default="COM/IDL interface contract changed",
                        extra_meta={"com_target": disp, "uuid": (prov.get("metadata") or {}).get("uuid"), "dst_file_path": prov.get("file_path")},
                    )
            elif ca["role"] == "provider":
                for prov in matched:
                    if str(ca["source_id"]) < str(prov["source_id"]):
                        disp = str(prov.get("symbol_name") or ca.get("symbol_name") or prov["normalized_key"])
                        _add_candidate(
                            src_art=ca,
                            dst_source_id=str(prov["source_id"]),
                            dst_branch=str(prov.get("branch") or eff_branch),
                            dst_node_id=str(prov.get("node_id") or ""),
                            dst_doc_key=None,
                            relation_type="calls_com",
                            contract_key=f"com:{disp}",
                            confidence="medium",
                            confidence_score=0.85,
                            provider_signature_hash=str(prov.get("signature_hash") or ""),
                            drift_reason_default="COM UUID definition changed",
                            is_bidirectional=True,
                            extra_meta={"com_target": disp, "uuid": (prov.get("metadata") or {}).get("uuid"), "dst_file_path": prov.get("file_path")},
                        )

        # 10. win32_msg -> sends_win32_msg
        msg_arts = by_kind.get("win32_msg", [])
        msg_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for ma in msg_arts:
            msg_by_key.setdefault(str(ma["normalized_key"]), []).append(ma)
        for nkey, group in msg_by_key.items():
            senders = [a for a in group if a["role"] == "consumer"]
            receivers = [a for a in group if a["role"] == "provider"]
            peers = [a for a in group if a["role"] == "shared"]
            for snd in senders:
                for rcv in receivers + peers:
                    if snd["source_id"] == rcv["source_id"]:
                        continue
                    disp = str(rcv.get("symbol_name") or snd.get("symbol_name") or nkey)
                    _add_candidate(
                        src_art=snd,
                        dst_source_id=str(rcv["source_id"]),
                        dst_branch=str(rcv.get("branch") or eff_branch),
                        dst_node_id=str(rcv.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type="sends_win32_msg",
                        contract_key=f"win32_msg:{disp}",
                        confidence="high",
                        confidence_score=0.95,
                        provider_signature_hash=str(rcv.get("signature_hash") or ""),
                        drift_reason_default="Win32 window message contract changed",
                        extra_meta={"message": disp, "dst_file_path": rcv.get("file_path")},
                    )
            for p_a in peers:
                for rcv in receivers:
                    if p_a["source_id"] == rcv["source_id"]:
                        continue
                    disp = str(rcv.get("symbol_name") or p_a.get("symbol_name") or nkey)
                    _add_candidate(
                        src_art=p_a,
                        dst_source_id=str(rcv["source_id"]),
                        dst_branch=str(rcv.get("branch") or eff_branch),
                        dst_node_id=str(rcv.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type="sends_win32_msg",
                        contract_key=f"win32_msg:{disp}",
                        confidence="medium",
                        confidence_score=0.85,
                        provider_signature_hash=str(rcv.get("signature_hash") or ""),
                        drift_reason_default="Win32 registered message handler changed",
                        extra_meta={"message": disp, "dst_file_path": rcv.get("file_path")},
                    )
            if not senders and not receivers and len(peers) >= 2:
                for idx_i, pa in enumerate(peers):
                    for pb in peers[idx_i + 1 :]:
                        if pa["source_id"] == pb["source_id"]:
                            continue
                        disp = str(pb.get("symbol_name") or pa.get("symbol_name") or nkey)
                        _add_candidate(
                            src_art=pa,
                            dst_source_id=str(pb["source_id"]),
                            dst_branch=str(pb.get("branch") or eff_branch),
                            dst_node_id=str(pb.get("node_id") or ""),
                            dst_doc_key=None,
                            relation_type="sends_win32_msg",
                            contract_key=f"win32_msg:{disp}",
                            confidence="medium",
                            confidence_score=0.85,
                            provider_signature_hash=str(pb.get("signature_hash") or ""),
                            drift_reason_default="Win32 registered message changed",
                            is_bidirectional=True,
                            extra_meta={"message": disp, "dst_file_path": pb.get("file_path")},
                        )

        # 11. dll_export -> loads_dll (prefer dllexport C++ signature provider over .def for drift precision)
        dll_provs: Dict[str, List[Dict[str, Any]]] = {}
        for da in by_kind.get("dll_export", []):
            if da["role"] == "provider":
                dll_provs.setdefault(str(da["normalized_key"]), []).append(da)
        for key, plist in dll_provs.items():
            plist.sort(
                key=lambda a: 2 if (a.get("metadata") or {}).get("export_kind") == "dllexport" else 1,
                reverse=True,
            )
        for da in by_kind.get("dll_export", []):
            if da["role"] != "consumer":
                continue
            nkey = str(da["normalized_key"])
            matched_provs = [p for p in dll_provs.get(nkey, []) if p["source_id"] != da["source_id"]]
            seen_dst_sources: Set[str] = set()
            for prov in matched_provs:
                dsid = str(prov["source_id"])
                if dsid in seen_dst_sources:
                    continue
                seen_dst_sources.add(dsid)
                disp = str(da.get("symbol_name") or prov.get("symbol_name") or nkey)
                ck_prefix = "dll" if nkey.startswith("dll:") else "export"
                ck = f"dll:{disp}" if ck_prefix == "dll" else f"export:{disp}"
                _add_candidate(
                    src_art=da,
                    dst_source_id=dsid,
                    dst_branch=str(prov.get("branch") or eff_branch),
                    dst_node_id=str(prov.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="loads_dll",
                    contract_key=ck,
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(prov.get("signature_hash") or ""),
                    drift_reason_default="DLL export signature changed",
                    extra_meta={"export_or_dll": disp, "dst_file_path": prov.get("file_path")},
                )
            if not matched_provs and nkey.startswith("dll:"):
                dll_stem = nkey.split(":", 1)[1]
                if dll_stem in reg_src_map and reg_src_map[dll_stem] != da["source_id"]:
                    dst_sid = reg_src_map[dll_stem]
                    disp = str(da.get("symbol_name") or dll_stem)
                    _add_candidate(
                        src_art=da,
                        dst_source_id=dst_sid,
                        dst_branch=eff_branch,
                        dst_node_id="",
                        dst_doc_key=None,
                        relation_type="loads_dll",
                        contract_key=f"dll:{disp}",
                        confidence="medium",
                        confidence_score=0.80,
                        provider_signature_hash=str(da.get("signature_hash") or ""),
                        drift_reason_default="Target DLL source changed",
                        extra_meta={"export_or_dll": disp},
                    )

        # 12. socket_ipc -> calls_socket
        sock_provs: Dict[str, List[Dict[str, Any]]] = {}
        for sa in by_kind.get("socket_ipc", []):
            if sa["role"] == "provider":
                sock_provs.setdefault(str(sa["normalized_key"]), []).append(sa)
        for sa in by_kind.get("socket_ipc", []):
            if sa["role"] != "consumer":
                continue
            c_host = str((sa.get("metadata") or {}).get("host") or "").lower()
            for prov in sock_provs.get(str(sa["normalized_key"]), []):
                if prov["source_id"] == sa["source_id"]:
                    continue
                p_host = str((prov.get("metadata") or {}).get("host") or "").lower()
                if c_host not in _LOCAL_SOCKET_HOSTS and p_host not in _LOCAL_SOCKET_HOSTS and c_host != p_host:
                    continue
                _add_candidate(
                    src_art=sa,
                    dst_source_id=str(prov["source_id"]),
                    dst_branch=str(prov.get("branch") or eff_branch),
                    dst_node_id=str(prov.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="calls_socket",
                    contract_key=f"socket:{sa['normalized_key']}",
                    confidence="high",
                    confidence_score=0.90,
                    provider_signature_hash=str(prov.get("signature_hash") or ""),
                    drift_reason_default="Socket IPC endpoint changed",
                    extra_meta={"port": (prov.get("metadata") or {}).get("port"), "dst_file_path": prov.get("file_path")},
                )

        # 13. plc_lasal_channel -> links_plc_channel (LASAL SERVER/endpoint) / shares_plc_tag (VAR_GLOBAL/IO)
        plc_arts = by_kind.get("plc_lasal_channel", [])
        plc_provs_by_key: Dict[str, List[Dict[str, Any]]] = {}
        plc_provs_by_leaf: Dict[str, List[Dict[str, Any]]] = {}
        for pa in plc_arts:
            if pa["role"] != "provider":
                continue
            plc_provs_by_key.setdefault(str(pa["normalized_key"]), []).append(pa)
            leaf = str(pa.get("symbol_name") or "").split(".")[-1].strip().lower()
            if leaf:
                plc_provs_by_leaf.setdefault(leaf, []).append(pa)

        def _plc_rel_for_provider(prov: Dict[str, Any]) -> Tuple[str, str]:
            pkind = str((prov.get("metadata") or {}).get("plc_kind") or "")
            if pkind in ("var_global", "var_external", "io_address"):
                return "shares_plc_tag", f"plc_tag:{prov.get('symbol_name') or prov['normalized_key']}"
            return "links_plc_channel", f"plc_channel:{prov.get('symbol_name') or prov['normalized_key']}"

        for ca in plc_arts:
            if ca["role"] != "consumer":
                continue
            nkey = str(ca["normalized_key"])
            leaf = str((ca.get("metadata") or {}).get("leaf") or ca.get("symbol_name") or "").split(".")[-1].strip().lower()
            matched = list(plc_provs_by_key.get(nkey, []))
            if not matched and leaf:
                matched = list(plc_provs_by_leaf.get(leaf, []))
            for prov in matched:
                if prov["source_id"] == ca["source_id"]:
                    continue
                rel_t, c_key = _plc_rel_for_provider(prov)
                _add_candidate(
                    src_art=ca,
                    dst_source_id=str(prov["source_id"]),
                    dst_branch=str(prov.get("branch") or eff_branch),
                    dst_node_id=str(prov.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type=rel_t,
                    contract_key=c_key,
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(prov.get("signature_hash") or ""),
                    drift_reason_default="PLC channel or tag signature changed",
                    extra_meta={"plc_kind": (prov.get("metadata") or {}).get("plc_kind"), "dst_file_path": prov.get("file_path")},
                )

        # Also link C++/HMI/VB6 files referencing PLC SERVER channels / VAR_GLOBAL tags via plc_token_index
        for tok_art in by_kind.get("plc_token_index", []):
            t_meta = tok_art.get("metadata") or {}
            str_toks = {str(t).lower() for t in (t_meta.get("string_tokens") or [])}
            id_toks = {str(t).lower() for t in (t_meta.get("ident_tokens") or [])}
            for leaf, provs in plc_provs_by_leaf.items():
                if not leaf or len(leaf) < 3:
                    continue
                is_str_hit = leaf in str_toks
                is_ident_hit = leaf in id_toks and (leaf.startswith("g_") or len(leaf) >= 6)
                if not (is_str_hit or is_ident_hit):
                    continue
                for prov in provs:
                    if prov["source_id"] == tok_art["source_id"]:
                        continue
                    rel_t, c_key = _plc_rel_for_provider(prov)
                    _add_candidate(
                        src_art=tok_art,
                        dst_source_id=str(prov["source_id"]),
                        dst_branch=str(prov.get("branch") or eff_branch),
                        dst_node_id=str(prov.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type=rel_t,
                        contract_key=c_key,
                        confidence="high" if is_str_hit else "medium",
                        confidence_score=0.90 if is_str_hit else 0.80,
                        provider_signature_hash=str(prov.get("signature_hash") or ""),
                        drift_reason_default="PLC channel or tag signature changed",
                        extra_meta={
                            "match_mode": "string_literal" if is_str_hit else "identifier",
                            "plc_kind": (prov.get("metadata") or {}).get("plc_kind"),
                            "dst_file_path": prov.get("file_path"),
                        },
                    )

        return candidates

    def reconcile_relations(
        self,
        project_id: str,
        target_source_id: Optional[str] = None,
        branch: str = "main",
    ) -> Dict[str, Any]:
        """Phase B: Match contract artifacts across sources, preserve manual/pinned/rejected links, and flag drift/stale."""
        proj = self._ensure_tables(project_id)
        eff_branch = str(branch or "main").strip() or "main"
        target_src = str(target_source_id).strip() if target_source_id and str(target_source_id).strip() else None

        artifacts = self._load_artifacts(proj, eff_branch)
        try:
            reg_sources = {
                str(s["source_id"])
                for s in tenant_manager.list_project_sources(proj)
                if s.get("source_id")
            }
        except Exception:
            reg_sources = set()

        candidates = self._build_relation_candidates(proj, eff_branch, artifacts, reg_sources)
        if target_src is not None:
            candidates = [
                c
                for c in candidates
                if c["src_source_id"] == target_src or c["dst_source_id"] == target_src
            ]

        existing_rels = tenant_manager.list_cross_source_relations(proj)

        exact_map: Dict[Tuple[str, str, str, str, str, str, str, str], Dict[str, Any]] = {}
        contract_map: Dict[Tuple[str, str, str, str, str, str], List[Dict[str, Any]]] = {}
        for rel in existing_rels:
            ek = (
                str(rel.get("src_source_id") or ""),
                str(rel.get("src_branch") or "main"),
                str(rel.get("dst_source_id") or ""),
                str(rel.get("dst_branch") or "main"),
                str(rel.get("relation_type") or ""),
                str(rel.get("contract_key") or ""),
                str(rel.get("src_node_id") or ""),
                str(rel.get("dst_node_id") or ""),
            )
            exact_map[ek] = rel
            ck = (
                str(rel.get("src_source_id") or ""),
                str(rel.get("src_branch") or "main"),
                str(rel.get("dst_source_id") or ""),
                str(rel.get("dst_branch") or "main"),
                str(rel.get("relation_type") or ""),
                _canonical_contract_cmp(str(rel.get("contract_key") or "")),
            )
            contract_map.setdefault(ck, []).append(rel)

        seen_existing_ids: Set[str] = set()
        relations_upserted = 0
        drifted_count = 0
        stale_count = 0
        now_ms = int(time.time() * 1000)

        for cand in candidates:
            ek = (
                cand["src_source_id"],
                cand["src_branch"],
                cand["dst_source_id"],
                cand["dst_branch"],
                cand["relation_type"],
                cand["contract_key"],
                cand["src_node_id"],
                cand["dst_node_id"],
            )
            ck = (
                cand["src_source_id"],
                cand["src_branch"],
                cand["dst_source_id"],
                cand["dst_branch"],
                cand["relation_type"],
                _canonical_contract_cmp(cand["contract_key"]),
            )

            matching_contract_rows = contract_map.get(ck, [])
            protected_rows = [
                r
                for r in matching_contract_rows
                if str(r.get("origin") or "") == "manual"
                or str(r.get("status") or "") in ("pinned", "rejected")
            ]
            if protected_rows:
                for pr in protected_rows:
                    seen_existing_ids.add(str(pr["id"]))
                continue

            existing = exact_map.get(ek)
            if existing is None and matching_contract_rows:
                auto_rows = [
                    r for r in matching_contract_rows if str(r.get("origin") or "") == "auto_detected"
                ]
                if auto_rows:
                    existing = auto_rows[0]

            if existing is not None:
                seen_existing_ids.add(str(existing["id"]))
                ex_origin = str(existing.get("origin") or "")
                ex_status = str(existing.get("status") or "active")
                if ex_origin == "manual" or ex_status in ("pinned", "rejected"):
                    continue

                ex_meta = dict(existing.get("contract_metadata") or {})
                old_prov_hash = str(ex_meta.get("provider_signature_hash") or "")
                new_prov_hash = str(cand["contract_metadata"].get("provider_signature_hash") or "")

                if old_prov_hash and new_prov_hash and old_prov_hash != new_prov_hash:
                    reason = cand["drift_reason_default"]
                    merged_meta = {
                        **ex_meta,
                        **cand["contract_metadata"],
                        # Keep baseline provider_signature_hash so subsequent runs remain drifted until reconciled/pinned
                        "provider_signature_hash": old_prov_hash,
                        "latest_provider_signature_hash": new_prov_hash,
                        "drift_reason": reason,
                        "drifted_at": now_ms,
                    }
                    tenant_manager.update_cross_source_relation(
                        str(existing["id"]),
                        project_id=proj,
                        status="drifted",
                        src_node_id=cand["src_node_id"],
                        dst_node_id=cand["dst_node_id"],
                        src_file_path=cand["src_file_path"],
                        dst_doc_key=cand["dst_doc_key"],
                        contract_metadata=merged_meta,
                    )
                    drifted_count += 1
                    continue

                if ex_status == "drifted":
                    # Still drifted
                    drifted_count += 1
                    continue

                # Active (or recovered from stale) with unchanged signature
                tenant_manager.update_cross_source_relation(
                    str(existing["id"]),
                    project_id=proj,
                    status="active",
                    src_node_id=cand["src_node_id"],
                    dst_node_id=cand["dst_node_id"],
                    src_file_path=cand["src_file_path"],
                    dst_doc_key=cand["dst_doc_key"],
                    confidence=cand["confidence"],
                    confidence_score=cand["confidence_score"],
                    contract_metadata=cand["contract_metadata"],
                )
                relations_upserted += 1
            else:
                tenant_manager.upsert_cross_source_relation(
                    project_id=proj,
                    src_source_id=cand["src_source_id"],
                    dst_source_id=cand["dst_source_id"],
                    relation_type=cand["relation_type"],
                    src_branch=cand["src_branch"],
                    dst_branch=cand["dst_branch"],
                    src_node_id=cand["src_node_id"],
                    dst_node_id=cand["dst_node_id"],
                    src_file_path=cand["src_file_path"],
                    dst_doc_key=cand["dst_doc_key"],
                    origin="auto_detected",
                    confidence=cand["confidence"],
                    confidence_score=cand["confidence_score"],
                    contract_key=cand["contract_key"],
                    contract_metadata=cand["contract_metadata"],
                    status="active",
                    is_bidirectional=cand["is_bidirectional"],
                    created_by="cross_source_linker",
                )
                relations_upserted += 1

        # Stale detection: any existing auto_detected active/drifted relation in scope whose contract vanished
        for rel in existing_rels:
            rid = str(rel["id"])
            if rid in seen_existing_ids:
                continue
            if str(rel.get("origin") or "") != "auto_detected":
                continue
            r_status = str(rel.get("status") or "")
            if r_status in ("pinned", "rejected", "stale"):
                continue
            if target_src is not None and (
                rel.get("src_source_id") != target_src and rel.get("dst_source_id") != target_src
            ):
                continue
            if rel.get("src_branch") != eff_branch and rel.get("dst_branch") != eff_branch:
                continue

            r_meta = dict(rel.get("contract_metadata") or {})
            r_meta["drift_reason"] = "Provider or consumer contract artifact no longer present"
            r_meta["stale_at"] = now_ms
            tenant_manager.update_cross_source_relation(
                rid,
                project_id=proj,
                status="stale",
                contract_metadata=r_meta,
            )
            stale_count += 1

        updated_rels = tenant_manager.list_cross_source_relations(
            proj,
            source_id=target_src,
            branch=eff_branch if target_src is not None else None,
        )
        for rel in updated_rels:
            meta = rel.get("contract_metadata") or {}
            if meta.get("drift_reason"):
                rel["drift_reason"] = meta["drift_reason"]

        return {
            "relations_upserted": relations_upserted,
            "drifted_count": drifted_count,
            "stale_count": stale_count,
            "relations": updated_rels,
        }

    @staticmethod
    def _collect_sln_project_dep_artifacts(
        proj: str,
        eff_branch: str,
        eff_map: Dict[str, Optional[Union[str, Path]]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Parse parent `.sln` files for `ProjectSection(ProjectDependencies)` mapped to `eff_map` sources."""
        dir_to_sid: Dict[Path, str] = {}
        parent_dirs: Set[Path] = set()
        for sid, raw_p in eff_map.items():
            if sid == "__shared__" or not raw_p:
                continue
            p = Path(raw_p)
            if p.is_dir():
                res = p.resolve()
                dir_to_sid[res] = sid
                if res.parent != res:
                    parent_dirs.add(res.parent)

        if len(dir_to_sid) < 2:
            return {}

        sln_files: Set[Path] = set()
        for pdir in sorted(parent_dirs):
            try:
                for cand in pdir.iterdir():
                    if cand.is_file() and cand.suffix.lower() == ".sln":
                        sln_files.add(cand.resolve())
            except Exception:
                continue

        if not sln_files:
            return {}

        proj_re = re.compile(
            r'^\s*Project\(\s*"\{?([0-9A-Fa-f-]+)\}?"\s*\)\s*=\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"\{?([0-9A-Fa-f-]+)\}?"',
            re.IGNORECASE,
        )
        dep_re = re.compile(r"^\s*\{?([0-9A-Fa-f-]+)\}?\s*=\s*\{?[0-9A-Fa-f-]+\}?\s*$")
        out: Dict[str, List[Dict[str, Any]]] = {}

        for sln_path in sorted(sln_files):
            try:
                content, _ = read_text_with_encoding(sln_path)
            except Exception:
                continue
            guid_to_sid: Dict[str, str] = {}
            raw_deps: List[Tuple[str, str]] = []
            lines = content.splitlines()
            idx = 0
            while idx < len(lines):
                m_proj = proj_re.match(lines[idx])
                if not m_proj:
                    idx += 1
                    continue
                type_guid = m_proj.group(1).upper()
                raw_rel = m_proj.group(3).strip().replace("\\", "/")
                proj_guid = m_proj.group(4).upper()
                idx += 1

                dep_guids: List[str] = []
                in_deps = False
                while idx < len(lines):
                    ls = lines[idx].strip()
                    if ls.startswith("EndProject"):
                        idx += 1
                        break
                    if re.match(r"^ProjectSection\s*\(\s*ProjectDependencies\s*\)", ls, re.IGNORECASE):
                        in_deps = True
                    elif ls.startswith("EndProjectSection"):
                        in_deps = False
                    elif in_deps:
                        dm = dep_re.match(ls)
                        if dm:
                            dep_guids.append(dm.group(1).upper())
                    idx += 1

                if type_guid == "2150E333-8FDC-42A3-9474-1A3956D46DE8" or not raw_rel:
                    continue
                if ".." in Path(raw_rel).parts or Path(raw_rel).is_absolute():
                    continue
                target = (sln_path.parent / raw_rel).resolve()
                if not target.is_relative_to(sln_path.parent.resolve()):
                    continue
                proj_dir = target if target.is_dir() else target.parent
                sid = dir_to_sid.get(proj_dir)
                if sid:
                    guid_to_sid[proj_guid] = sid
                    for dg in dep_guids:
                        raw_deps.append((sid, dg))

            for src_sid, dst_guid in raw_deps:
                dst_sid = guid_to_sid.get(dst_guid)
                if not dst_sid or dst_sid == src_sid:
                    continue
                out.setdefault(src_sid, []).append(
                    _make_artifact(
                        project_id=proj,
                        source_id=src_sid,
                        branch=eff_branch,
                        contract_kind="project_dep",
                        role="consumer",
                        normalized_key=dst_sid.lower(),
                        file_path=sln_path.name,
                        symbol_name=dst_sid,
                        metadata={"via": "sln_project_dependencies", "sln_file": sln_path.name, "target_guid": dst_guid},
                    )
                )
        return out

    # ------------------------------------------------------------------
    # Combined Auto-Link Orchestrator (Phase A + Phase B)
    # ------------------------------------------------------------------
    def auto_link_project(
        self,
        project_id: str,
        source_id: Optional[str] = None,
        branch: str = "main",
        workspace_map: Optional[Dict[str, Union[str, Path]]] = None,
    ) -> Dict[str, Any]:
        """Extract contract surfaces across all (or target) sources and reconcile cross-source relations."""
        proj = self._ensure_tables(project_id)
        eff_branch = str(branch or "main").strip() or "main"
        target_src = str(source_id).strip() if source_id and str(source_id).strip() else None

        eff_map: Dict[str, Optional[Union[str, Path]]] = {}
        if workspace_map:
            for k, v in workspace_map.items():
                eff_map[str(k).strip()] = v

        # Merge registered project sources that have workspace_subpath / metadata workspace_path
        try:
            reg_sources = tenant_manager.list_project_sources(proj)
        except Exception:
            reg_sources = []

        for s in reg_sources:
            sid = str(s.get("source_id") or "").strip()
            if not sid or sid in eff_map:
                continue
            subpath = s.get("workspace_subpath") or (s.get("metadata") or {}).get("workspace_path")
            if subpath:
                cand_p = Path(str(subpath))
                if not cand_p.is_absolute():
                    cand_p = Path.cwd() / cand_p
                eff_map[sid] = cand_p if cand_p.exists() else None
            else:
                eff_map[sid] = None

        sln_dep_arts = self._collect_sln_project_dep_artifacts(proj, eff_branch, eff_map)

        total_artifacts = 0
        scanned_sources: List[str] = []

        for sid, ws_path in eff_map.items():
            if target_src is not None and sid not in (target_src, "__shared__"):
                continue
            arts = self.extract_contract_artifacts(
                project_id=proj,
                source_id=sid,
                branch=eff_branch,
                workspace_path=ws_path,
            )
            extra_sln = sln_dep_arts.get(sid) or []
            if extra_sln:
                now_ms = int(time.time() * 1000)
                for art in extra_sln:
                    meta_json = json.dumps(art.get("metadata") or {})
                    tenant_manager._project_root_execute(
                        proj,
                        """
                        INSERT INTO cross_source_contract_artifacts (
                            id, project_id, source_id, branch, contract_kind, role,
                            normalized_key, node_id, file_path, symbol_name, signature_hash,
                            metadata_json, sync_generation, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (project_id, source_id, branch, contract_kind, role, normalized_key, node_id)
                        DO UPDATE SET
                            file_path = excluded.file_path,
                            symbol_name = excluded.symbol_name,
                            signature_hash = excluded.signature_hash,
                            metadata_json = excluded.metadata_json,
                            sync_generation = excluded.sync_generation,
                            updated_at = excluded.updated_at
                        """,
                        (
                            art["id"],
                            proj,
                            sid,
                            eff_branch,
                            art["contract_kind"],
                            art["role"],
                            art["normalized_key"],
                            art["node_id"],
                            art["file_path"],
                            art["symbol_name"],
                            art["signature_hash"],
                            meta_json,
                            art["sync_generation"],
                            now_ms,
                        ),
                    )
                total_artifacts += len(extra_sln)
            total_artifacts += len(arts)
            scanned_sources.append(sid)

        # Always extract DB shared docs into __shared__ if __shared__ wasn't already scanned via workspace_map
        if "__shared__" not in eff_map:
            shared_arts = self.extract_contract_artifacts(
                project_id=proj,
                source_id="__shared__",
                branch=eff_branch,
            )
            total_artifacts += len(shared_arts)
            if shared_arts:
                scanned_sources.append("__shared__")

        rec = self.reconcile_relations(
            project_id=proj,
            target_source_id=target_src,
            branch=eff_branch,
        )
        return {
            "status": "ok",
            "project_id": proj,
            "branch": eff_branch,
            "source_id": target_src,
            "artifacts_extracted": total_artifacts,
            "sources_scanned": scanned_sources,
            "relations_upserted": rec["relations_upserted"],
            "drifted_count": rec["drifted_count"],
            "stale_count": rec["stale_count"],
            "relations": rec["relations"],
        }


__all__ = ["CrossSourceLinker"]
