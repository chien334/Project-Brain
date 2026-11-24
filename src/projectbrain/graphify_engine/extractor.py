"""
Graphify AST & Semantic Document Extractor Engine.

Integrates with `graphify` (v0.3.28) for zero-token deterministic Tree-sitter AST
extraction across 22 programming languages, Python AST fallback, Markdown & document
semantic entity/rationale parsing, SHA256 content+path caching, Leiden/Louvain community
clustering, architectural God-node & cross-community bridge analysis, and local partial graph persistence.
"""

from __future__ import annotations

import ast
import bisect
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

from projectbrain.graphify_engine.extractor_registry import get_default_registry
from projectbrain.graphify_engine.legacy_encoding import (
    decode_legacy_bytes,
    read_text_with_encoding,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Concurrency-Safe Defensive DTO Import Pattern
# ---------------------------------------------------------------------------
try:
    from projectbrain.graphify_engine.models import (
        GraphDelta,
        GraphEdge,
        GraphHyperedge,
        GraphNode,
    )
except ImportError:
    try:
        from projectbrain.graphify_engine.store import (  # type: ignore
            GraphDelta,
            GraphEdge,
            GraphHyperedge,
            GraphNode,
        )
    except ImportError:
        from pydantic import BaseModel, Field

        class BaseGraphModel(BaseModel):
            def to_dict(self) -> Dict[str, Any]:
                if hasattr(self, "model_dump"):
                    return self.model_dump()
                return self.dict()

            class Config:
                extra = "allow"

        class GraphNode(BaseGraphModel):
            id: str
            project_id: str = "default"
            label: str
            file_type: str = "code"
            source_file: str
            source_location: Optional[str] = None
            community: Optional[int] = None
            metadata: Dict[str, Any] = Field(default_factory=dict)
            updated_at: Optional[str] = None

        class GraphEdge(BaseGraphModel):
            project_id: str = "default"
            source: str
            target: str
            relation: str
            confidence: str = "EXTRACTED"
            confidence_score: float = 1.0
            source_file: Optional[str] = None
            source_location: Optional[str] = None
            weight: float = 1.0

        class GraphHyperedge(BaseGraphModel):
            id: str
            project_id: str = "default"
            label: str
            nodes: List[str]
            relation: str = "participate_in"
            confidence: str = "EXTRACTED"
            confidence_score: float = 1.0
            source_file: Optional[str] = None

        class GraphDelta(BaseGraphModel):
            project_id: str = "default"
            branch: str = "main"
            nodes: List[GraphNode] = Field(default_factory=list)
            edges: List[GraphEdge] = Field(default_factory=list)
            hyperedges: List[GraphHyperedge] = Field(default_factory=list)
            node_tombstones: List[str] = Field(default_factory=list)
            edge_tombstones: List[Dict[str, Any]] = Field(default_factory=list)
            hyperedge_tombstones: List[str] = Field(default_factory=list)
            synced_files: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Supported Extensions & Constants
# ---------------------------------------------------------------------------
CODE_EXTENSIONS: Set[str] = {
    ".py", ".pyw",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".inl", ".tpp",
    ".idl", ".def", ".rc",
    ".st", ".scl", ".exp", ".pou",
    ".bas", ".cls", ".frm",
    ".pas", ".dfm",
    ".cbl", ".cob",
    ".sql", ".bat", ".cmd", ".reg",
    ".cs",
    ".rb",
    ".php",
    ".kt", ".kts",
    ".swift",
    ".scala",
    ".ex", ".exs",
    ".lua",
    ".jl",
    ".zig",
    ".ps1",
    ".m",
}

_CPP_LEGACY_EXTS: Set[str] = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".inl", ".tpp"}
_ST_LASAL_EXTS: Set[str] = {".st", ".scl", ".exp", ".pou"}
_IDL_EXTS: Set[str] = {".idl"}
_DEF_EXTS: Set[str] = {".def"}
_RC_EXTS: Set[str] = {".rc"}
_VB6_EXTS: Set[str] = {".bas", ".cls", ".frm"}
_DELPHI_EXTS: Set[str] = {".pas", ".dfm"}
_COBOL_EXTS: Set[str] = {".cbl", ".cob"}
_SQL_EXTS: Set[str] = {".sql"}
_SCRIPT_REG_EXTS: Set[str] = {".bat", ".cmd", ".reg"}

DOC_EXTENSIONS: Set[str] = {
    ".md", ".markdown", ".mdown",
    ".txt",
    ".rst",
    ".adoc",
}

IGNORED_DIRS: Set[str] = {
    ".git",
    ".projectbrain",
    ".agents",
    "graphify-out",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".tox",
    "dist",
    "build",
    ".eggs",
    "tools",
    "orchestrator",
}


def _strip_markdown_frontmatter(content: bytes) -> bytes:
    """Strip YAML frontmatter from Markdown bytes before computing semantic hash."""
    text, _ = decode_legacy_bytes(content)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].encode("utf-8")
    return content


def _slugify(val: str) -> str:
    """Sanitize arbitrary strings into clean alphanumeric node ID slugs."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", val.strip()).lower()
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "entity"


# ---------------------------------------------------------------------------
# Legacy Language & Resource Heuristic Parsers
# ---------------------------------------------------------------------------
_WIN32_SESSION0_APIS: Dict[str, str] = {
    # Named Pipes
    "CreateNamedPipe": "named_pipe",
    "CallNamedPipe": "named_pipe",
    "ConnectNamedPipe": "named_pipe",
    "DisconnectNamedPipe": "named_pipe",
    "WaitNamedPipe": "named_pipe",
    "PeekNamedPipe": "named_pipe",
    "SetNamedPipeHandleState": "named_pipe",
    "GetNamedPipeClientProcessId": "named_pipe",
    "ImpersonateNamedPipeClient": "named_pipe",
    "TransactNamedPipe": "named_pipe",
    "CreateFile": "named_pipe",
    "ReadFile": "named_pipe",
    "WriteFile": "named_pipe",
    # Shared Memory / File Mapping
    "CreateFileMapping": "shared_memory",
    "OpenFileMapping": "shared_memory",
    "MapViewOfFile": "shared_memory",
    "MapViewOfFileEx": "shared_memory",
    "UnmapViewOfFile": "shared_memory",
    "FlushViewOfFile": "shared_memory",
    # Windows Service Control Manager (SCM)
    "OpenSCManager": "scm_service",
    "CreateService": "scm_service",
    "OpenService": "scm_service",
    "StartService": "scm_service",
    "ControlService": "scm_service",
    "DeleteService": "scm_service",
    "CloseServiceHandle": "scm_service",
    "RegisterServiceCtrlHandler": "scm_service",
    "RegisterServiceCtrlHandlerEx": "scm_service",
    "SetServiceStatus": "scm_service",
    "StartServiceCtrlDispatcher": "scm_service",
    "QueryServiceStatus": "scm_service",
    "QueryServiceStatusEx": "scm_service",
    "ChangeServiceConfig": "scm_service",
    # Win32 Window Messages & UI IPC
    "SendMessage": "win32_msg",
    "SendMessageTimeout": "win32_msg",
    "PostMessage": "win32_msg",
    "PostThreadMessage": "win32_msg",
    "RegisterWindowMessage": "win32_msg",
    "FindWindow": "win32_msg",
    "FindWindowEx": "win32_msg",
    "BroadcastSystemMessage": "win32_msg",
    # COM / DCOM / RPC
    "CoInitialize": "com",
    "CoInitializeEx": "com",
    "CoUninitialize": "com",
    "CoCreateInstance": "com",
    "CoCreateInstanceEx": "com",
    "CoGetClassObject": "com",
    "CoRegisterClassObject": "com",
    "CoRevokeClassObject": "com",
    "CoInitializeSecurity": "com",
    "RpcServerListen": "rpc",
    "RpcServerUseProtseqEp": "rpc",
    # Dynamic DLL Loading
    "LoadLibrary": "dll_loader",
    "LoadLibraryEx": "dll_loader",
    "GetProcAddress": "dll_loader",
    "FreeLibrary": "dll_loader",
    "GetModuleHandle": "dll_loader",
    # Winsock / Sockets
    "WSAStartup": "winsock",
    "WSACleanup": "winsock",
    "WSASocket": "winsock",
    "socket": "winsock",
    "bind": "winsock",
    "listen": "winsock",
    "accept": "winsock",
    "connect": "winsock",
    "send": "winsock",
    "recv": "winsock",
    # Synchronization & Session 0 Bridging
    "CreateMutex": "sync_session0",
    "OpenMutex": "sync_session0",
    "CreateEvent": "sync_session0",
    "OpenEvent": "sync_session0",
    "SetEvent": "sync_session0",
    "ResetEvent": "sync_session0",
    "CreateSemaphore": "sync_session0",
    "OpenSemaphore": "sync_session0",
    "WaitForSingleObject": "sync_session0",
    "WaitForMultipleObjects": "sync_session0",
    "WTSQueryUserToken": "sync_session0",
    "WTSEnumerateSessions": "sync_session0",
    "WTSSendMessage": "sync_session0",
    "CreateProcessAsUser": "sync_session0",
    "DuplicateTokenEx": "sync_session0",
    "DeviceIoControl": "sync_session0",
}

_ST_IGNORED_CALLS: Set[str] = {
    "if", "elsif", "else", "case", "of", "for", "while", "repeat", "until",
    "return", "exit", "continue", "and", "or", "xor", "not", "mod",
    "adr", "sizeof", "ref", "super", "this", "array", "string", "wstring",
    "abs", "sqrt", "sin", "cos", "tan", "min", "max", "limit", "sel", "mux",
    "move", "shl", "shr", "rol", "ror", "len", "concat", "find",
}

_ST_PRIMITIVE_TYPES: Set[str] = {
    "bool", "byte", "word", "dword", "lword", "sint", "usint", "int", "uint",
    "dint", "udint", "lint", "ulint", "real", "lreal", "time", "ltime",
    "date", "tod", "dt", "string", "wstring", "char", "wchar", "any", "pointer",
}

_CPP_IGNORED_NAMES: Set[str] = {
    "if", "else", "for", "while", "switch", "case", "catch", "return",
    "sizeof", "alignof", "decltype", "typedef", "struct", "class", "enum",
    "union", "namespace", "using", "delete", "new", "throw", "public",
    "private", "protected", "do", "goto", "break", "continue", "defined",
}


def _lookup_win32_api(name: str) -> Optional[str]:
    """Return Win32 IPC/Session-0 category if ``name`` matches a known API (A/W aware)."""
    if name in _WIN32_SESSION0_APIS:
        return _WIN32_SESSION0_APIS[name]
    if len(name) > 2 and name[-1] in ("A", "W") and name[0].isupper():
        base = name[:-1]
        if base in _WIN32_SESSION0_APIS:
            return _WIN32_SESSION0_APIS[base]
    return None


def _strip_comments_preserve_lines(
    text: str,
    *,
    line_prefixes: Tuple[str, ...] = ("//",),
    block_pairs: Tuple[Tuple[str, str], ...] = (("/*", "*/"),),
    string_delims: Tuple[str, ...] = ('"', "'"),
    backslash_escapes: bool = True,
) -> Tuple[str, str]:
    """Strip comments (and optionally string interiors) while keeping exact line numbers."""
    n = len(text)
    no_comments: List[str] = []
    no_strings: List[str] = []
    i = 0
    block_close: Optional[str] = None
    in_line_comment = False
    quote_char: Optional[str] = None

    while i < n:
        ch = text[i]
        if block_close is not None:
            if text.startswith(block_close, i):
                pad = " " * len(block_close)
                no_comments.append(pad)
                no_strings.append(pad)
                i += len(block_close)
                block_close = None
            else:
                out = "\n" if ch == "\n" else " "
                no_comments.append(out)
                no_strings.append(out)
                i += 1
        elif in_line_comment:
            if ch == "\n":
                no_comments.append("\n")
                no_strings.append("\n")
                in_line_comment = False
            else:
                no_comments.append(" ")
                no_strings.append(" ")
            i += 1
        elif quote_char is not None:
            if backslash_escapes and ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                no_comments.append(ch + nxt)
                no_strings.append(" " + ("\n" if nxt == "\n" else " "))
                i += 2
            elif ch == quote_char:
                if not backslash_escapes and i + 1 < n and text[i + 1] == quote_char:
                    no_comments.append(ch + ch)
                    no_strings.append("  ")
                    i += 2
                else:
                    no_comments.append(ch)
                    no_strings.append(ch)
                    quote_char = None
                    i += 1
            else:
                no_comments.append(ch)
                no_strings.append("\n" if ch == "\n" else " ")
                i += 1
        else:
            matched_block = False
            for open_tok, close_tok in block_pairs:
                if text.startswith(open_tok, i):
                    pad = " " * len(open_tok)
                    no_comments.append(pad)
                    no_strings.append(pad)
                    block_close = close_tok
                    i += len(open_tok)
                    matched_block = True
                    break
            if matched_block:
                continue

            matched_line = False
            for pref in line_prefixes:
                if text.startswith(pref, i):
                    pad = " " * len(pref)
                    no_comments.append(pad)
                    no_strings.append(pad)
                    in_line_comment = True
                    i += len(pref)
                    matched_line = True
                    break
            if matched_line:
                continue

            if ch in string_delims:
                quote_char = ch
                no_comments.append(ch)
                no_strings.append(ch)
                i += 1
            else:
                no_comments.append(ch)
                no_strings.append(ch)
                i += 1

    return "".join(no_comments), "".join(no_strings)


def _compute_line_starts(text: str) -> List[int]:
    """Return 0-based byte/char offsets where each 1-based line begins."""
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def _line_of_offset(line_starts: List[int], offset: int) -> int:
    """Map character offset to 1-based line number."""
    return bisect.bisect_right(line_starts, offset)


def _find_matching_delimiter(text: str, open_idx: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Find matching closing delimiter from ``open_idx``; returns ``-1`` if unmatched."""
    depth = 0
    n = len(text)
    for idx in range(open_idx, n):
        c = text[idx]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _clean_comment_fragment(raw_line: str) -> str:
    """Strip common comment markers from a single comment line."""
    s = raw_line.strip()
    for prefix in ("///", "//!", "//", "*>", "/*", "(*", ";", "'"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    for suffix in ("*/", "*)"):
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
    if s.startswith("*") and not s.startswith("*/"):
        s = s[1:].strip()
    if s.startswith("{") and s.endswith("}") and not s.startswith("{$"):
        s = s[1:-1].strip()
    return s


def _extract_inline_comment(line: str, prefixes: Tuple[str, ...] = ("//", "(*", ";", "'", "*>")) -> str:
    """Extract an inline trailing comment outside string literals on ``line``."""
    in_quote: Optional[str] = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_quote = ch
            i += 1
            continue
        if line.startswith("/*", i):
            end = line.find("*/", i + 2)
            body = line[i + 2:end] if end != -1 else line[i + 2:]
            return body.strip()
        if line.startswith("(*", i):
            end = line.find("*)", i + 2)
            body = line[i + 2:end] if end != -1 else line[i + 2:]
            return body.strip()
        for pref in prefixes:
            if pref not in ("(*",) and line.startswith(pref, i):
                return line[i + len(pref):].strip()
        i += 1
    return ""


def _extract_comment_near_line(
    lines: List[str],
    line_num: int,
    *,
    line_prefixes: Tuple[str, ...] = ("//",),
    block_open: str = "/*",
    block_close: str = "*/",
    skip_directives: bool = False,
) -> str:
    """Extract inline or immediately preceding comments as a clean symbol docstring."""
    if line_num < 1 or line_num > len(lines):
        return ""

    inline = _extract_inline_comment(lines[line_num - 1], prefixes=line_prefixes + (block_open,))
    collected: List[str] = []
    idx = line_num - 2
    in_block = False
    steps = 0

    while idx >= 0 and steps < 10:
        raw = lines[idx].strip()
        if not raw:
            if collected:
                break
            idx -= 1
            steps += 1
            continue
        if skip_directives and not in_block and raw.startswith("#pragma"):
            idx -= 1
            steps += 1
            continue
        if raw.endswith(block_close):
            in_block = True
            cleaned = _clean_comment_fragment(raw)
            if cleaned:
                collected.append(cleaned)
            if raw.startswith(block_open):
                in_block = False
            idx -= 1
            steps += 1
            continue
        if in_block:
            cleaned = _clean_comment_fragment(raw)
            if cleaned:
                collected.append(cleaned)
            if block_open in raw:
                in_block = False
            idx -= 1
            steps += 1
            continue
        if any(raw.startswith(p) for p in line_prefixes) or (
            raw.startswith(block_open) and raw.endswith(block_close)
        ):
            cleaned = _clean_comment_fragment(raw)
            if cleaned:
                collected.append(cleaned)
            idx -= 1
            steps += 1
            continue
        break

    collected.reverse()
    if inline and inline not in collected:
        collected.append(inline)
    return " ".join(c for c in collected if c).strip()


class _LegacyParseContext:
    """Shared GraphNode/GraphEdge accumulator with deduplication and tree-sitter merging."""

    def __init__(
        self,
        project_id: str,
        rel_path: str,
        language: str,
        encoding: str,
        existing_nodes: Optional[List[GraphNode]] = None,
        existing_edges: Optional[List[GraphEdge]] = None,
    ):
        self.project_id = project_id
        self.rel_path = rel_path
        self.language = language
        self.encoding = encoding
        self.rel_slug = _slugify(rel_path)
        self.nodes: List[GraphNode] = list(existing_nodes or [])
        self.edges: List[GraphEdge] = list(existing_edges or [])
        self.nodes_by_id: Dict[str, GraphNode] = {n.id: n for n in self.nodes}
        self.edges_seen: Set[Tuple[str, str, str]] = {
            (e.source, e.target, e.relation) for e in self.edges
        }
        self.label_index: Dict[str, List[GraphNode]] = {}
        for n in self.nodes:
            self.label_index.setdefault(n.label.rstrip("()").lower(), []).append(n)

    def ensure_file_node(self, docstring: str = "") -> str:
        """Return existing file node ID or create a new file root node."""
        file_name = Path(self.rel_path).name
        for n in self.nodes:
            if n.label == file_name and n.source_file == self.rel_path:
                n.metadata.setdefault("kind", "file")
                n.metadata.setdefault("language", self.language)
                n.metadata.setdefault("encoding", self.encoding)
                if docstring and not n.metadata.get("docstring"):
                    n.metadata["docstring"] = docstring
                return n.id

        file_node_id = f"file_{self.rel_slug}"
        meta: Dict[str, Any] = {
            "kind": "file",
            "language": self.language,
            "encoding": self.encoding,
        }
        if docstring:
            meta["docstring"] = docstring
        return self.add_symbol(
            file_node_id,
            file_name,
            "file",
            line=1,
            parent_id=None,
            extra_meta=meta,
        )

    def _find_reconcilable_node(self, clean_label: str, kind: str) -> Optional[GraphNode]:
        """Find an existing node from tree-sitter that represents the same symbol."""
        cands = self.label_index.get(clean_label.rstrip("()").lower(), [])
        for cand in cands:
            if cand.metadata.get("kind") == "file":
                continue
            cand_kind = str(cand.metadata.get("kind") or "").lower()
            if not cand_kind or cand_kind == kind or {cand_kind, kind} <= {"function", "method"}:
                return cand
        return None

    def add_symbol(
        self,
        nid: str,
        label: str,
        kind: str,
        line: int,
        parent_id: Optional[str] = None,
        relation: str = "contains",
        extra_meta: Optional[Dict[str, Any]] = None,
        reconcile_by_label: bool = False,
    ) -> str:
        """Add or merge a symbol node and optionally connect it to ``parent_id``."""
        meta: Dict[str, Any] = {
            "kind": kind,
            "language": self.language,
            "encoding": self.encoding,
        }
        if extra_meta:
            for k, v in extra_meta.items():
                if v is not None and v != "":
                    meta[k] = v

        target_node = self.nodes_by_id.get(nid)
        if target_node is None and reconcile_by_label:
            target_node = self._find_reconcilable_node(label, kind)

        if target_node is not None:
            for k, v in meta.items():
                if k not in target_node.metadata or not target_node.metadata[k]:
                    target_node.metadata[k] = v
            resolved_id = target_node.id
        else:
            node = GraphNode(
                id=nid,
                project_id=self.project_id,
                label=label,
                file_type="code",
                source_file=self.rel_path,
                source_location=f"L{max(1, line)}",
                metadata=meta,
            )
            self.nodes.append(node)
            self.nodes_by_id[nid] = node
            self.label_index.setdefault(label.rstrip("()").lower(), []).append(node)
            resolved_id = nid

        if parent_id and parent_id != resolved_id:
            self.add_edge(parent_id, resolved_id, relation, line=line)
        return resolved_id

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        line: int = 1,
        confidence: str = "EXTRACTED",
        confidence_score: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a deduplicated ``GraphEdge``."""
        if not source or not target or source == target:
            return
        key = (source, target, relation)
        if key in self.edges_seen:
            return
        self.edges_seen.add(key)
        kwargs: Dict[str, Any] = {
            "project_id": self.project_id,
            "source": source,
            "target": target,
            "relation": relation,
            "confidence": confidence,
            "confidence_score": confidence_score,
            "source_file": self.rel_path,
            "source_location": f"L{max(1, line)}",
        }
        if metadata:
            kwargs["metadata"] = metadata
        self.edges.append(GraphEdge(**kwargs))


def _parse_st_lasal(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse IEC 61131-3 Structured Text (.st/.scl/.exp/.pou) and PLC LASAL blocks/XML."""
    ctx = _LegacyParseContext(project_id, rel_path, "structured_text", encoding)
    lines = text.splitlines()
    file_doc = _extract_comment_near_line(lines, 1, line_prefixes=("//",), block_open="(*", block_close="*)")
    file_id = ctx.ensure_file_node(docstring=file_doc)

    local_symbols: Dict[str, str] = {}
    fb_instances: Dict[str, str] = {}

    def _parse_lasal_xml_fragment(xml_text: str) -> List[str]:
        st_snippets: List[str] = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return st_snippets

        def _walk(elem: ET.Element, parent_id: str, parent_obj: Optional[str]) -> None:
            tag = elem.tag.split("}")[-1].lower()
            curr_parent = parent_id
            curr_obj = parent_obj

            if tag in ("class", "component", "object", "program", "functionblock"):
                name = elem.get("Name") or elem.get("ID") or elem.get("Type")
                if name:
                    cls_id = f"{ctx.rel_slug}_cls_{_slugify(name)}"
                    cls_type = elem.get("Class") or elem.get("BaseClass")
                    st_kind = "program" if tag == "program" else ("function_block" if tag == "functionblock" else "class")
                    curr_parent = ctx.add_symbol(
                        cls_id,
                        name,
                        "class",
                        line=1,
                        parent_id=file_id,
                        extra_meta={
                            "st_kind": st_kind,
                            "symbol_type": st_kind,
                            "docstring": elem.get("Comment") or "",
                            "base_class": cls_type or "",
                        },
                    )
                    local_symbols[name.lower()] = curr_parent
                    if cls_type and tag == "object":
                        fb_instances[name.lower()] = cls_type
                        ctx.add_edge(curr_parent, _slugify(cls_type), "instantiates", line=1)
                    elif cls_type:
                        ctx.add_edge(curr_parent, _slugify(cls_type), "inherits", line=1)
                    curr_obj = "this" if tag == "class" else name
            elif tag in ("server", "client", "var", "channel"):
                name = elem.get("Name") or elem.get("ID")
                if name:
                    vkind = "server" if tag == "server" else ("client" if tag == "client" else "variable")
                    full_name = f"{curr_obj}.{name}" if curr_obj and curr_obj != "this" else name
                    vnid = f"{ctx.rel_slug}_{vkind}_{_slugify(full_name)}"
                    ctx.add_symbol(
                        vnid,
                        full_name,
                        vkind,
                        line=1,
                        parent_id=curr_parent,
                        extra_meta={
                            "data_type": elem.get("DataType") or "ANY",
                            "docstring": elem.get("Comment") or "",
                        },
                    )
                    local_symbols[full_name.lower()] = vnid
                    local_symbols[name.lower()] = vnid
            elif tag in ("connection", "link"):
                src = elem.get("Source") or elem.get("Client") or elem.get("From") or elem.get("ClientName")
                dest = elem.get("Destination") or elem.get("Server") or elem.get("To") or elem.get("ServerName")
                if src and dest:
                    src_id = local_symbols.get(src.lower(), f"{ctx.rel_slug}_client_{_slugify(src)}")
                    tgt_id = local_symbols.get(dest.lower(), _slugify(dest))
                    ctx.add_edge(src_id, tgt_id, "calls", line=1)
            elif tag in ("st", "declaration", "implementation"):
                inner = "".join(elem.itertext()).strip()
                if inner:
                    st_snippets.append(inner)

            for child in elem:
                _walk(child, curr_parent, curr_obj)

        _walk(root, file_id, None)
        return st_snippets

    def _parse_st_code(st_code: str) -> None:
        st_lines = st_code.splitlines()
        no_comm, no_str = _strip_comments_preserve_lines(
            st_code,
            line_prefixes=("//",),
            block_pairs=(("(*", "*)"),),
            string_delims=("'", '"'),
            backslash_escapes=False,
        )
        clean_lines = no_comm.splitlines()

        block_re = re.compile(
            r"^\s*(PROGRAM|FUNCTION_BLOCK|CLASS|INTERFACE)\s+"
            r"(?:(?:FINAL|ABSTRACT)\s+)?([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s*(?::|EXTENDS)\s*([A-Za-z_][A-Za-z0-9_]*))?",
            re.IGNORECASE,
        )
        fn_re = re.compile(
            r"^\s*(FUNCTION|METHOD)\s+"
            r"(?:(?:PUBLIC|PRIVATE|PROTECTED|INTERNAL|VIRTUAL|FINAL|OVERRIDE|GLOBAL)\s+)*"
            r"(?:([A-Za-z_][A-Za-z0-9_]*)\s*::\s*)?([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s*:\s*([A-Za-z_][A-Za-z0-9_\[\]]*))?",
            re.IGNORECASE,
        )
        end_block_re = re.compile(
            r"^\s*END_(PROGRAM|FUNCTION_BLOCK|CLASS|INTERFACE|FUNCTION|METHOD)\b",
            re.IGNORECASE,
        )
        inline_srv_cli_re = re.compile(
            r"^\s*(SERVER|CLIENT)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^;]+);?",
            re.IGNORECASE,
        )
        section_srv_cli_re = re.compile(r"^\s*(SERVER|CLIENT|VAR_SERVER|VAR_CLIENT)\b", re.IGNORECASE)
        end_srv_cli_re = re.compile(r"^\s*END_(SERVER|CLIENT|VAR)\b", re.IGNORECASE)
        var_start_re = re.compile(
            r"^\s*(VAR_GLOBAL|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|VAR_EXTERNAL|VAR_STAT|VAR)\b",
            re.IGNORECASE,
        )
        var_decl_re = re.compile(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)"
            r"(?:\s+AT\s+(%[IQMXiqmx][A-Za-z0-9_.]+))?"
            r"\s*:\s*([^:;]+?)(?:\s*:=\s*([^;]+))?;",
            re.IGNORECASE,
        )
        type_struct_inline_re = re.compile(
            r"^\s*TYPE\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*STRUCT\b",
            re.IGNORECASE,
        )
        type_header_re = re.compile(r"^\s*TYPE\s+([A-Za-z_][A-Za-z0-9_]*)\s*:?\s*(.*)$", re.IGNORECASE)
        struct_only_re = re.compile(r"^\s*STRUCT(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\b", re.IGNORECASE)

        scope_stack: List[Tuple[str, str, int]] = []  # (node_id, kind_label, start_line)
        body_ranges: List[Tuple[str, int, int]] = []
        active_var_scope: Optional[str] = None
        active_channel_kind: Optional[str] = None
        pending_type_name: Optional[str] = None
        active_struct: Optional[Tuple[str, int, List[Dict[str, str]], str]] = None
        pending_enum: Optional[Tuple[str, int, List[str], str]] = None

        for idx, raw_clean in enumerate(clean_lines):
            line_num = idx + 1
            cleaned = raw_clean.strip()
            if not cleaned:
                continue

            parent_id = scope_stack[-1][0] if scope_stack else file_id

            # Close active enum capture
            if pending_enum is not None:
                ename, eline, members, edoc = pending_enum
                chunk = cleaned.split(")")[0]
                for part in chunk.split(","):
                    m_tok = part.strip().split(":=")[0].strip()
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", m_tok):
                        members.append(m_tok)
                if ")" in cleaned or "END_TYPE" in cleaned.upper():
                    enid = f"{ctx.rel_slug}_enum_{_slugify(ename)}"
                    ctx.add_symbol(
                        enid,
                        ename,
                        "enum",
                        line=eline,
                        parent_id=file_id,
                        extra_meta={"members": members, "docstring": edoc},
                    )
                    local_symbols[ename.lower()] = enid
                    pending_enum = None
                continue

            # Inside TYPE ... STRUCT ... END_STRUCT
            if active_struct is not None:
                sname, sline, fields, sdoc = active_struct
                if re.match(r"^\s*END_STRUCT\b", cleaned, re.IGNORECASE):
                    snid = f"{ctx.rel_slug}_struct_{_slugify(sname)}"
                    ctx.add_symbol(
                        snid,
                        sname,
                        "struct",
                        line=sline,
                        parent_id=file_id,
                        extra_meta={"fields": fields, "docstring": sdoc},
                    )
                    local_symbols[sname.lower()] = snid
                    active_struct = None
                    pending_type_name = None
                    continue
                m_fld = var_decl_re.match(cleaned)
                if m_fld:
                    ftype = m_fld.group(3).strip()
                    for fname in m_fld.group(1).split(","):
                        fn = fname.strip()
                        if fn:
                            fields.append({"name": fn, "type": ftype})
                continue

            if re.match(r"^\s*END_TYPE\b", cleaned, re.IGNORECASE):
                pending_type_name = None
                continue

            m_tsi = type_struct_inline_re.match(cleaned)
            if m_tsi:
                sname = m_tsi.group(1)
                doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                active_struct = (sname, line_num, [], doc)
                pending_type_name = None
                continue

            if pending_type_name and struct_only_re.match(cleaned):
                doc = _extract_comment_near_line(st_lines, line_num - 1, line_prefixes=("//",), block_open="(*", block_close="*)")
                active_struct = (pending_type_name, line_num, [], doc)
                pending_type_name = None
                continue

            m_so = struct_only_re.match(cleaned)
            if m_so and m_so.group(1):
                doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                active_struct = (m_so.group(1), line_num, [], doc)
                continue

            m_th = type_header_re.match(cleaned)
            if m_th:
                tname = m_th.group(1)
                rest = m_th.group(2).strip()
                doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                if rest.startswith("("):
                    members: List[str] = []
                    after_paren = rest[1:]
                    chunk = after_paren.split(")")[0]
                    for part in chunk.split(","):
                        m_tok = part.strip().split(":=")[0].strip()
                        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", m_tok):
                            members.append(m_tok)
                    if ")" in after_paren:
                        enid = f"{ctx.rel_slug}_enum_{_slugify(tname)}"
                        ctx.add_symbol(
                            enid,
                            tname,
                            "enum",
                            line=line_num,
                            parent_id=file_id,
                            extra_meta={"members": members, "docstring": doc},
                        )
                        local_symbols[tname.lower()] = enid
                    else:
                        pending_enum = (tname, line_num, members, doc)
                else:
                    pending_type_name = tname
                continue

            # Close block scopes
            if end_block_re.match(cleaned):
                active_var_scope = None
                active_channel_kind = None
                if scope_stack:
                    closed_id, _, start_ln = scope_stack.pop()
                    body_ranges.append((closed_id, start_ln, line_num))
                continue

            # Block declarations: PROGRAM / FUNCTION_BLOCK / CLASS / INTERFACE
            m_blk = block_re.match(cleaned)
            if m_blk:
                active_var_scope = None
                active_channel_kind = None
                kw = m_blk.group(1).upper()
                bname = m_blk.group(2)
                base_name = m_blk.group(3)
                doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                if kw == "INTERFACE":
                    node_kind, st_kind = "interface", "interface"
                else:
                    node_kind, st_kind = "class", kw.lower()
                bnid = f"{ctx.rel_slug}_{st_kind}_{_slugify(bname)}"
                ctx.add_symbol(
                    bnid,
                    bname,
                    node_kind,
                    line=line_num,
                    parent_id=file_id,
                    extra_meta={
                        "st_kind": st_kind,
                        "symbol_type": st_kind,
                        "signature": st_lines[idx].strip(),
                        "docstring": doc,
                    },
                )
                local_symbols[bname.lower()] = bnid
                if base_name:
                    ctx.add_edge(bnid, local_symbols.get(base_name.lower(), _slugify(base_name)), "inherits", line=line_num)
                scope_stack.append((bnid, st_kind, line_num))
                continue

            # FUNCTION / METHOD
            m_fn = fn_re.match(cleaned)
            if m_fn:
                active_var_scope = None
                active_channel_kind = None
                kw = m_fn.group(1).upper()
                owner_cls = m_fn.group(2)
                fn_name = m_fn.group(3)
                ret_type = (m_fn.group(4) or "").strip()
                if fn_name.lower() in _ST_IGNORED_CALLS:
                    continue
                doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                if owner_cls:
                    owner_id = local_symbols.get(owner_cls.lower())
                    if owner_id is None:
                        owner_id = f"{ctx.rel_slug}_class_{_slugify(owner_cls)}"
                        ctx.add_symbol(owner_id, owner_cls, "class", line=line_num, parent_id=file_id, extra_meta={"st_kind": "class"})
                        local_symbols[owner_cls.lower()] = owner_id
                    qual_name = f"{owner_cls}.{fn_name}"
                    fn_kind = "method"
                    fn_parent = owner_id
                elif scope_stack and kw == "METHOD":
                    owner_lbl = ctx.nodes_by_id[parent_id].label
                    qual_name = f"{owner_lbl}.{fn_name}"
                    fn_kind = "method"
                    fn_parent = parent_id
                else:
                    qual_name = fn_name
                    fn_kind = "method" if scope_stack else "function"
                    fn_parent = parent_id

                fn_id = f"{ctx.rel_slug}_{fn_kind}_{_slugify(qual_name)}"
                ctx.add_symbol(
                    fn_id,
                    f"{qual_name}()",
                    fn_kind,
                    line=line_num,
                    parent_id=fn_parent,
                    extra_meta={
                        "st_kind": kw.lower(),
                        "qualified_name": qual_name,
                        "return_type": ret_type,
                        "signature": st_lines[idx].strip(),
                        "docstring": doc,
                    },
                )
                local_symbols[qual_name.lower()] = fn_id
                local_symbols.setdefault(fn_name.lower(), fn_id)
                scope_stack.append((fn_id, fn_kind, line_num))
                continue

            # Inline LASAL SERVER / CLIENT declaration
            m_isc = inline_srv_cli_re.match(cleaned)
            if m_isc:
                ch_kind = m_isc.group(1).lower()
                ch_name = m_isc.group(2)
                ch_type = m_isc.group(3).strip()
                doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                ch_id = f"{ctx.rel_slug}_{ch_kind}_{_slugify(ch_name)}"
                ctx.add_symbol(
                    ch_id,
                    ch_name,
                    ch_kind,
                    line=line_num,
                    parent_id=parent_id,
                    extra_meta={"data_type": ch_type, "channel_type": ch_kind, "docstring": doc},
                )
                local_symbols[ch_name.lower()] = ch_id
                base_ch_type = ch_type.split("[")[0].strip()
                if base_ch_type and base_ch_type.lower() not in _ST_PRIMITIVE_TYPES:
                    fb_instances[ch_name.lower()] = base_ch_type
                continue

            # LASAL SERVER / CLIENT block headers
            m_ssc = section_srv_cli_re.match(cleaned)
            if m_ssc:
                active_var_scope = None
                tok = m_ssc.group(1).upper()
                active_channel_kind = "server" if "SERVER" in tok else "client"
                continue

            if end_srv_cli_re.match(cleaned):
                active_var_scope = None
                active_channel_kind = None
                continue

            m_vs = var_start_re.match(cleaned)
            if m_vs:
                active_channel_kind = None
                active_var_scope = m_vs.group(1).upper()
                continue

            # Declarations inside SERVER/CLIENT or VAR* blocks
            if (active_channel_kind or active_var_scope) and ":" in cleaned:
                m_vd = var_decl_re.match(cleaned)
                if m_vd:
                    raw_names = m_vd.group(1)
                    io_addr = m_vd.group(2) or ""
                    vtype = m_vd.group(3).strip()
                    init_val = (m_vd.group(4) or "").strip()
                    doc = _extract_comment_near_line(st_lines, line_num, line_prefixes=("//",), block_open="(*", block_close="*)")
                    sym_kind = active_channel_kind if active_channel_kind else "variable"
                    base_type = re.sub(r"^ARRAY\s*\[[^\]]+\]\s*OF\s+", "", vtype, flags=re.IGNORECASE).strip()
                    for raw_v in raw_names.split(","):
                        vname = raw_v.strip()
                        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", vname):
                            continue
                        vid = f"{ctx.rel_slug}_{sym_kind}_{_slugify(vname)}"
                        extra: Dict[str, Any] = {
                            "data_type": vtype,
                            "io_address": io_addr,
                            "initial_value": init_val,
                            "docstring": doc,
                        }
                        if active_var_scope:
                            extra["var_scope"] = active_var_scope
                        if active_channel_kind:
                            extra["channel_type"] = active_channel_kind
                        ctx.add_symbol(vid, vname, sym_kind, line=line_num, parent_id=parent_id, extra_meta=extra)
                        local_symbols[vname.lower()] = vid
                        if base_type and base_type.lower() not in _ST_PRIMITIVE_TYPES:
                            fb_instances[vname.lower()] = base_type
                            target_fb = local_symbols.get(base_type.lower(), _slugify(base_type))
                            ctx.add_edge(vid, target_fb, "instantiates", line=line_num)

        # Close any unclosed scopes at EOF
        while scope_stack:
            closed_id, _, start_ln = scope_stack.pop()
            body_ranges.append((closed_id, start_ln, len(clean_lines)))

        # Second pass: resolve calls between ST blocks / FBs / functions
        no_str_lines = no_str.splitlines()
        call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\(")
        for caller_id, start_ln, end_ln in body_ranges:
            in_decl = False
            for ln in range(start_ln + 1, min(end_ln, len(no_str_lines)) + 1):
                s = no_str_lines[ln - 1].strip()
                if not s:
                    continue
                if var_start_re.match(s) or section_srv_cli_re.match(s):
                    in_decl = True
                    continue
                if end_srv_cli_re.match(s):
                    in_decl = False
                    continue
                if in_decl:
                    continue
                for cm in call_re.finditer(s):
                    callee = cm.group(1)
                    if callee.lower() in _ST_IGNORED_CALLS:
                        continue
                    if "." in callee:
                        inst, meth = callee.split(".", 1)
                        cls_type = fb_instances.get(inst.lower(), inst)
                        target = (
                            local_symbols.get(f"{cls_type}.{meth}".lower())
                            or local_symbols.get(cls_type.lower())
                            or local_symbols.get(meth.lower())
                            or _slugify(f"{cls_type}_{meth}")
                        )
                    else:
                        inst_type = fb_instances.get(callee.lower())
                        if inst_type:
                            target = local_symbols.get(inst_type.lower(), _slugify(inst_type))
                        else:
                            target = local_symbols.get(callee.lower(), _slugify(callee))
                    ctx.add_edge(caller_id, target, "calls", line=ln)

    stripped = text.lstrip()
    if stripped.startswith("<"):
        for snippet in _parse_lasal_xml_fragment(text):
            _parse_st_code(snippet)
    else:
        _parse_st_code(text)
        xml_match = re.search(r"\(\*!(.*?)\*\)", text, re.DOTALL)
        if xml_match:
            for snippet in _parse_lasal_xml_fragment(xml_match.group(1).strip()):
                _parse_st_code(snippet)

    return ctx.nodes, ctx.edges


def _parse_cpp_fields(body: str) -> List[Dict[str, str]]:
    """Extract struct fields from a C/C++ struct body."""
    fields: List[Dict[str, str]] = []
    for stmt in body.split(";"):
        s = " ".join(stmt.strip().split())
        if not s or "{" in s or "}" in s or "(" in s:
            continue
        # Strip bitfield width `: N`
        s = re.sub(r"\s*:\s*\d+\s*$", "", s)
        m = re.match(r"^(.+?)\s+([*&\s]*[A-Za-z_][A-Za-z0-9_]*)\s*(\[[^\]]*\])?$", s)
        if m:
            ftype = m.group(1).strip()
            fname = m.group(2).lstrip("*& ").strip()
            arr = (m.group(3) or "").strip()
            if fname and ftype not in ("typedef", "struct", "union", "enum"):
                fields.append({"name": fname, "type": f"{ftype}{arr}".strip()})
    return fields


def _parse_cpp_legacy(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
    existing_nodes: Optional[List[GraphNode]] = None,
    existing_edges: Optional[List[GraphEdge]] = None,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse or augment C/C++ legacy files (#pragma pack, structs, macros, enums, Win32 APIs)."""
    lang = "c" if Path(rel_path).suffix.lower() == ".c" else "cpp"
    ctx = _LegacyParseContext(
        project_id,
        rel_path,
        lang,
        encoding,
        existing_nodes=existing_nodes,
        existing_edges=existing_edges,
    )
    lines = text.splitlines()
    file_doc = _extract_comment_near_line(lines, 1)
    file_id = ctx.ensure_file_node(docstring=file_doc)

    no_comm, no_str = _strip_comments_preserve_lines(text)
    line_starts = _compute_line_starts(no_str)
    clean_lines = no_comm.splitlines()

    # 1. Track #pragma pack state across lines
    pack_stack: List[Optional[int]] = []
    current_pack: Optional[int] = None
    pack_at_line: Dict[int, Optional[int]] = {}
    pragma_pack_re = re.compile(r"^\s*#\s*pragma\s+pack\s*\(([^)]*)\)")
    for ln_idx, ln_str in enumerate(clean_lines, start=1):
        pm = pragma_pack_re.match(ln_str)
        if pm:
            args = [a.strip() for a in pm.group(1).split(",") if a.strip()]
            if not args:
                current_pack = None
            elif args[0].lower() == "push":
                pack_stack.append(current_pack)
                nums = [int(a) for a in args[1:] if a.isdigit()]
                if nums:
                    current_pack = nums[-1]
            elif args[0].lower() == "pop":
                current_pack = pack_stack.pop() if pack_stack else None
            elif args[0].isdigit():
                current_pack = int(args[0])
        pack_at_line[ln_idx] = current_pack

    # 2. #include directives
    inc_re = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
    for m in inc_re.finditer(no_comm):
        ln = _line_of_offset(line_starts, m.start())
        ctx.add_edge(file_id, _slugify(Path(m.group(1)).stem), "imports", line=ln)

    # 3. #define macros / error constants (with backslash continuation)
    idx = 0
    prev_ifndef_sym: Optional[str] = None
    define_re = re.compile(
        r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)(?:\(([^)]*)\))?(?:\s+(.*))?$"
    )
    ifndef_re = re.compile(r"^\s*#\s*ifndef\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
    while idx < len(lines):
        raw_line = lines[idx]
        ln = idx + 1
        m_ifn = ifndef_re.match(clean_lines[idx] if idx < len(clean_lines) else raw_line)
        if m_ifn:
            prev_ifndef_sym = m_ifn.group(1)
            idx += 1
            continue

        joined = clean_lines[idx] if idx < len(clean_lines) else raw_line
        raw_joined = raw_line
        while joined.rstrip().endswith("\\") and idx + 1 < len(lines):
            idx += 1
            joined = joined.rstrip()[:-1] + " " + (clean_lines[idx] if idx < len(clean_lines) else lines[idx]).strip()
            raw_joined = raw_joined.rstrip()[:-1] + " " + lines[idx].strip()

        m_def = define_re.match(joined)
        if m_def:
            mname = m_def.group(1)
            mparams = m_def.group(2)
            # Re-extract value from raw_joined so string values stay intact
            m_raw = define_re.match(raw_joined)
            raw_val = (m_raw.group(3) if m_raw and m_raw.group(3) else "").strip()
            inline_comm = _extract_inline_comment(raw_joined, prefixes=("//",))
            if inline_comm and "//" in raw_val:
                raw_val = raw_val.split("//", 1)[0].strip()
            raw_val = re.sub(r"/\*.*?\*/", "", raw_val).strip()

            # Skip pure header include guards (#ifndef FOO_H \n #define FOO_H)
            if prev_ifndef_sym == mname and not raw_val and mparams is None:
                prev_ifndef_sym = None
                idx += 1
                continue

            doc = _extract_comment_near_line(lines, ln)
            ctx.add_symbol(
                f"{ctx.rel_slug}_macro_{_slugify(mname)}",
                mname,
                "macro",
                line=ln,
                parent_id=file_id,
                extra_meta={
                    "value": raw_val,
                    "params": mparams or "",
                    "docstring": doc,
                    "signature": raw_joined.strip(),
                },
            )
        prev_ifndef_sym = None
        idx += 1

    # 4. Enums (typedef enum and enum / enum class)
    enum_spans: Set[int] = set()
    t_enum_re = re.compile(r"\btypedef\s+enum(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*\{")
    for m in t_enum_re.finditer(no_str):
        open_b = no_str.find("{", m.start())
        close_b = _find_matching_delimiter(no_str, open_b)
        if close_b == -1:
            continue
        enum_spans.add(open_b)
        ln = _line_of_offset(line_starts, m.start())
        tail = no_str[close_b + 1:close_b + 120].split(";", 1)[0]
        aliases = [a.strip().lstrip("*") for a in tail.split(",") if a.strip()]
        ename = next((a for a in aliases if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a)), None) or m.group(1)
        if ename:
            body = no_str[open_b + 1:close_b]
            members = [p.strip().split("=")[0].strip() for p in body.split(",") if p.strip()]
            members = [mb for mb in members if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", mb)]
            doc = _extract_comment_near_line(lines, ln)
            ctx.add_symbol(
                f"{ctx.rel_slug}_enum_{_slugify(ename)}",
                ename,
                "enum",
                line=ln,
                parent_id=file_id,
                extra_meta={"members": members, "docstring": doc},
            )

    enum_re = re.compile(r"\benum(?:\s+class)?\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z0-9_:]+)?\s*\{")
    for m in enum_re.finditer(no_str):
        open_b = no_str.find("{", m.start())
        if open_b in enum_spans:
            continue
        close_b = _find_matching_delimiter(no_str, open_b)
        if close_b == -1:
            continue
        enum_spans.add(open_b)
        ln = _line_of_offset(line_starts, m.start())
        ename = m.group(1)
        body = no_str[open_b + 1:close_b]
        members = [p.strip().split("=")[0].strip() for p in body.split(",") if p.strip()]
        members = [mb for mb in members if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", mb)]
        doc = _extract_comment_near_line(lines, ln)
        ctx.add_symbol(
            f"{ctx.rel_slug}_enum_{_slugify(ename)}",
            ename,
            "enum",
            line=ln,
            parent_id=file_id,
            extra_meta={"members": members, "docstring": doc},
        )

    # 5. Structs (typedef struct and struct)
    struct_spans: Set[int] = set()
    t_struct_re = re.compile(
        r"\btypedef\s+struct(?:\s+__declspec\s*\([^)]*\))?(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*\{"
    )
    for m in t_struct_re.finditer(no_str):
        open_b = no_str.find("{", m.start())
        close_b = _find_matching_delimiter(no_str, open_b)
        if close_b == -1:
            continue
        struct_spans.add(open_b)
        ln = _line_of_offset(line_starts, m.start())
        tag_name = m.group(1) or ""
        tail = no_str[close_b + 1:close_b + 160].split(";", 1)[0]
        aliases = [
            a.strip().lstrip("*&")
            for a in tail.split(",")
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a.strip().lstrip("*&"))
        ]
        # Prefer non-pointer alias (doesn't start with P/LP unless only alias)
        primary = next((a for a in aliases if not a.startswith(("P", "LP"))), None) or (aliases[0] if aliases else tag_name)
        if primary:
            fields = _parse_cpp_fields(no_str[open_b + 1:close_b])
            pack_val = pack_at_line.get(ln)
            packed = pack_val == 1 or "__attribute__" in tail or "packed" in tail
            doc = _extract_comment_near_line(lines, ln, skip_directives=True)
            ctx.add_symbol(
                f"{ctx.rel_slug}_struct_{_slugify(primary)}",
                primary,
                "struct",
                line=ln,
                parent_id=file_id,
                reconcile_by_label=True,
                extra_meta={
                    "struct_tag": tag_name,
                    "aliases": aliases,
                    "fields": fields,
                    "pack": pack_val,
                    "packed": packed,
                    "docstring": doc,
                },
            )

    struct_re = re.compile(
        r"\bstruct(?:\s+(?:__declspec\s*\([^)]*\)|[A-Z0-9_]+(?:_API|_EXPORT)))*\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^{;]+))?\{"
    )
    for m in struct_re.finditer(no_str):
        open_b = no_str.find("{", m.start())
        if open_b in struct_spans:
            continue
        struct_spans.add(open_b)
        close_b = _find_matching_delimiter(no_str, open_b)
        if close_b == -1:
            continue
        ln = _line_of_offset(line_starts, m.start())
        sname = m.group(1)
        fields = _parse_cpp_fields(no_str[open_b + 1:close_b])
        pack_val = pack_at_line.get(ln)
        tail = no_str[close_b + 1:close_b + 80]
        packed = pack_val == 1 or "packed" in tail
        doc = _extract_comment_near_line(lines, ln, skip_directives=True)
        snid = ctx.add_symbol(
            f"{ctx.rel_slug}_struct_{_slugify(sname)}",
            sname,
            "struct",
            line=ln,
            parent_id=file_id,
            reconcile_by_label=True,
            extra_meta={
                "fields": fields,
                "pack": pack_val,
                "packed": packed,
                "docstring": doc,
            },
        )
        bases = m.group(2) or ""
        for b in bases.split(","):
            bname = b.strip().split()[-1] if b.strip() else ""
            if bname:
                ctx.add_edge(snid, _slugify(bname), "inherits", line=ln)

    # 6. Classes & inline/declared methods
    class_spans: Set[int] = set()
    function_bodies: List[Tuple[str, int, int]] = []
    local_fn_map: Dict[str, str] = {}

    class_re = re.compile(
        r"\bclass(?:\s+(?:__declspec\s*\([^)]*\)|[A-Z0-9_]+(?:_API|_EXPORT)))*\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^{;]+))?\{"
    )
    method_def_re = re.compile(
        r"(?:^|[;:\n}])\s*(?:(?:static|inline|virtual|explicit|extern|__declspec\s*\([^)]*\))\s+)*"
        r"([A-Za-z_~][A-Za-z0-9_:<>\*&\s]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*(?:const|override|final|noexcept|\s)*\{"
    )
    method_decl_re = re.compile(
        r"(?:^|[;:\n])\s*(?:(?:static|inline|virtual|explicit)\s+)*"
        r"([A-Za-z_][A-Za-z0-9_:<>\*&\s]+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*(?:const|override|final|noexcept|=\s*0|\s)*;"
    )

    for m in class_re.finditer(no_str):
        open_b = no_str.find("{", m.start())
        close_b = _find_matching_delimiter(no_str, open_b)
        if close_b == -1:
            continue
        class_spans.add(open_b)
        ln = _line_of_offset(line_starts, m.start())
        cname = m.group(1)
        doc = _extract_comment_near_line(lines, ln)
        cnid = ctx.add_symbol(
            f"{ctx.rel_slug}_class_{_slugify(cname)}",
            cname,
            "class",
            line=ln,
            parent_id=file_id,
            reconcile_by_label=True,
            extra_meta={"docstring": doc},
        )
        local_fn_map[cname.lower()] = cnid
        for b in (m.group(2) or "").split(","):
            bname = b.strip().split()[-1] if b.strip() else ""
            if bname:
                ctx.add_edge(cnid, _slugify(bname), "inherits", line=ln)

        cls_body = no_str[open_b + 1:close_b]
        # Extract inline method definitions inside class
        pos = 0
        while pos < len(cls_body):
            mm = method_def_re.search(cls_body, pos)
            if not mm:
                break
            m_open_rel = cls_body.find("{", mm.start())
            m_close_rel = _find_matching_delimiter(cls_body, m_open_rel)
            if m_close_rel == -1:
                break
            mname = mm.group(2).strip()
            if mname.lower() not in _CPP_IGNORED_NAMES:
                mln = _line_of_offset(line_starts, open_b + 1 + mm.start(2))
                mdoc = _extract_comment_near_line(lines, mln)
                mnid = ctx.add_symbol(
                    f"{ctx.rel_slug}_{_slugify(cname)}_{_slugify(mname)}",
                    f"{mname}()",
                    "method",
                    line=mln,
                    parent_id=cnid,
                    relation="method",
                    reconcile_by_label=True,
                    extra_meta={
                        "qualified_name": f"{cname}::{mname}",
                        "return_type": mm.group(1).strip(),
                        "params": " ".join(mm.group(3).split()),
                        "docstring": mdoc,
                    },
                )
                local_fn_map[mname.lower()] = mnid
                local_fn_map[f"{cname}::{mname}".lower()] = mnid
                function_bodies.append((mnid, open_b + 1 + m_open_rel, open_b + 1 + m_close_rel))
            pos = m_close_rel + 1

        # Extract pure declarations inside class (only at depth 0 of class body)
        for dm in method_decl_re.finditer(cls_body):
            # Ensure offset is not inside a nested inline method body
            abs_off = open_b + 1 + dm.start(2)
            if any(b_s <= abs_off <= b_e for _, b_s, b_e in function_bodies):
                continue
            mname = dm.group(2).strip()
            if mname.lower() in _CPP_IGNORED_NAMES:
                continue
            mln = _line_of_offset(line_starts, abs_off)
            mdoc = _extract_comment_near_line(lines, mln)
            mnid = ctx.add_symbol(
                f"{ctx.rel_slug}_{_slugify(cname)}_{_slugify(mname)}",
                f"{mname}()",
                "method",
                line=mln,
                parent_id=cnid,
                relation="method",
                reconcile_by_label=True,
                extra_meta={
                    "qualified_name": f"{cname}::{mname}",
                    "return_type": dm.group(1).strip(),
                    "params": " ".join(dm.group(3).split()),
                    "declaration_only": True,
                    "docstring": mdoc,
                },
            )
            local_fn_map.setdefault(mname.lower(), mnid)

    # 7. Container-level (global / namespace / extern "C") function definitions & declarations
    top_fn_def_re = re.compile(
        r"(?:^|[;\n}])\s*(?:(?:extern\s+\"C\"|static|inline|virtual|WINAPI|CALLBACK|APIENTRY|__stdcall|__cdecl|__declspec\s*\([^)]*\))\s+)*"
        r"([A-Za-z_][A-Za-z0-9_:<>\*&\s]*?)\s+([A-Za-z_][A-Za-z0-9_:~]*)\s*\(([^;{}]*)\)\s*(?:const|noexcept|override|\s)*\{"
    )
    top_fn_decl_re = re.compile(
        r"^\s*(?:(?:extern\s+\"C\"|extern|static|inline|WINAPI|CALLBACK|APIENTRY|__stdcall|__cdecl|__declspec\s*\([^)]*\))\s+)*"
        r"([A-Za-z_][A-Za-z0-9_:<>\*&\s]+?)\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\(([^;{}]*)\)\s*(?:const|noexcept|\s)*;"
    )
    ns_or_extern_re = re.compile(r"^\s*(?:namespace(?:\s+[A-Za-z_][A-Za-z0-9_]*)?|extern\s+\"C\")\s*\{")

    def _scan_container(region_start: int, region_end: int) -> None:
        pos = region_start
        while pos < region_end:
            # Skip whitespace & preprocessor lines
            while pos < region_end and no_str[pos].isspace():
                pos += 1
            if pos >= region_end:
                break
            if no_str[pos] == "#":
                nl = no_str.find("\n", pos)
                while nl != -1 and nl > 0 and no_str[nl - 1] == "\\":
                    nl = no_str.find("\n", nl + 1)
                pos = (nl + 1) if nl != -1 else region_end
                continue

            # Enter namespace / extern "C" { ... }
            m_ns = ns_or_extern_re.match(no_str, pos)
            if m_ns:
                ob = no_str.find("{", pos)
                cb = _find_matching_delimiter(no_str, ob)
                if cb != -1 and cb <= region_end:
                    _scan_container(ob + 1, cb)
                    pos = cb + 1
                    continue

            # Skip previously parsed class / struct / enum brace blocks
            next_brace = no_str.find("{", pos, region_end)
            next_semi = no_str.find(";", pos, region_end)
            if next_brace != -1 and (next_semi == -1 or next_brace < next_semi):
                if next_brace in class_spans or next_brace in struct_spans or next_brace in enum_spans:
                    cb = _find_matching_delimiter(no_str, next_brace)
                    pos = (cb + 1) if cb != -1 else next_brace + 1
                    continue

            # Try function definition ending at next_brace
            if next_brace != -1 and (next_semi == -1 or next_brace < next_semi):
                stmt = no_str[pos:next_brace + 1]
                m_def = top_fn_def_re.search(stmt)
                cb = _find_matching_delimiter(no_str, next_brace)
                if cb == -1:
                    break
                if m_def:
                    ret_t = " ".join(m_def.group(1).split())
                    fn_raw = m_def.group(2).strip()
                    params = " ".join(m_def.group(3).split())
                    short_name = fn_raw.split("::")[-1]
                    if short_name.lower() not in _CPP_IGNORED_NAMES and not ret_t.startswith(("return", "if", "for", "while", "switch")):
                        ln = _line_of_offset(line_starts, pos + m_def.start(2))
                        doc = _extract_comment_near_line(lines, ln)
                        fn_kind = "method" if "::" in fn_raw else "function"
                        fn_nid = f"{ctx.rel_slug}_{_slugify(fn_raw)}"
                        resolved = ctx.add_symbol(
                            fn_nid,
                            f"{fn_raw}()",
                            fn_kind,
                            line=ln,
                            parent_id=file_id,
                            reconcile_by_label=True,
                            extra_meta={
                                "qualified_name": fn_raw,
                                "return_type": ret_t,
                                "params": params,
                                "docstring": doc,
                            },
                        )
                        local_fn_map[short_name.lower()] = resolved
                        local_fn_map[fn_raw.lower()] = resolved
                        function_bodies.append((resolved, next_brace, cb))
                pos = cb + 1
                continue

            # Try declaration/statement ending at next_semi
            if next_semi != -1:
                stmt = no_str[pos:next_semi + 1]
                m_decl = top_fn_decl_re.match(stmt)
                if m_decl:
                    ret_t = " ".join(m_decl.group(1).split())
                    fn_raw = m_decl.group(2).strip()
                    params = " ".join(m_decl.group(3).split())
                    short_name = fn_raw.split("::")[-1]
                    first_ret_tok = ret_t.split()[0].lower() if ret_t else ""
                    if (
                        short_name.lower() not in _CPP_IGNORED_NAMES
                        and first_ret_tok not in _CPP_IGNORED_NAMES
                        and "=" not in ret_t
                    ):
                        ln = _line_of_offset(line_starts, pos + m_decl.start(2))
                        doc = _extract_comment_near_line(lines, ln)
                        has_dllexport = "dllexport" in stmt
                        resolved = ctx.add_symbol(
                            f"{ctx.rel_slug}_decl_{_slugify(fn_raw)}",
                            f"{fn_raw}()",
                            "function",
                            line=ln,
                            parent_id=file_id,
                            reconcile_by_label=True,
                            extra_meta={
                                "qualified_name": fn_raw,
                                "return_type": ret_t,
                                "params": params,
                                "declaration_only": True,
                                "dllexport": has_dllexport,
                                "docstring": doc,
                            },
                        )
                        local_fn_map.setdefault(short_name.lower(), resolved)
                pos = next_semi + 1
                continue
            break

    _scan_container(0, len(no_str))

    # Also ensure graphify-extracted functions that weren't matched get scanned for Win32 APIs
    if not function_bodies and existing_nodes:
        for gn in existing_nodes:
            if gn.label.endswith("()"):
                local_fn_map.setdefault(gn.label[:-2].lower(), gn.id)

    # 8. Scan function bodies for Win32 Session 0 APIs & local calls
    call_pat = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for caller_id, b_start, b_end in function_bodies:
        body_slice = no_str[b_start:b_end]
        for cm in call_pat.finditer(body_slice):
            callee = cm.group(1)
            if callee.lower() in _CPP_IGNORED_NAMES:
                continue
            ln = _line_of_offset(line_starts, b_start + cm.start())
            win32_cat = _lookup_win32_api(callee)
            if win32_cat:
                api_nid = f"win32_{_slugify(callee)}"
                ctx.add_symbol(
                    api_nid,
                    callee,
                    "win32_api",
                    line=ln,
                    parent_id=None,
                    extra_meta={"api_category": win32_cat, "external": True},
                )
                ctx.add_edge(
                    caller_id,
                    api_nid,
                    "calls",
                    line=ln,
                    metadata={"win32_api": callee, "api_category": win32_cat},
                )
            elif callee.lower() in local_fn_map:
                tgt = local_fn_map[callee.lower()]
                if tgt != caller_id:
                    ctx.add_edge(caller_id, tgt, "calls", line=ln)

    return ctx.nodes, ctx.edges


def _parse_idl(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse COM/MIDL (.idl) interfaces, coclasses, libraries, UUIDs, and methods."""
    ctx = _LegacyParseContext(project_id, rel_path, "idl", encoding)
    lines = text.splitlines()
    file_id = ctx.ensure_file_node(docstring=_extract_comment_near_line(lines, 1))
    no_comm, no_str = _strip_comments_preserve_lines(text)
    line_starts = _compute_line_starts(no_str)

    # Imports (import "oaidl.idl"; / importlib("stdole2.tlb");)
    for m in re.finditer(r'\bimport(?:lib)?\s*\(?\s*"([^"]+)"', no_comm):
        ln = _line_of_offset(line_starts, m.start())
        ctx.add_edge(file_id, _slugify(Path(m.group(1)).stem), "imports", line=ln)

    def _parse_attrs(attr_block: str) -> Dict[str, Any]:
        meta: Dict[str, Any] = {}
        u = re.search(r"uuid\s*\(\s*([0-9a-fA-F\-]+)\s*\)", attr_block)
        if u:
            meta["uuid"] = u.group(1).lower()
        hs = re.search(r'helpstring\s*\(\s*"([^"]*)"\s*\)', attr_block)
        if hs:
            meta["helpstring"] = hs.group(1)
        ver = re.search(r"version\s*\(\s*([0-9.]+)\s*\)", attr_block)
        if ver:
            meta["version"] = ver.group(1)
        return meta

    decl_re = re.compile(
        r"(?:\[([^\]]*)\]\s*)?\b(library|dispinterface|interface|coclass)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\{"
    )
    iface_ids: Dict[str, str] = {}

    def _scan_idl_region(start: int, end: int, parent_id: str) -> None:
        pos = start
        while pos < end:
            m = decl_re.search(no_comm, pos, end)
            if not m:
                break
            open_b = no_comm.find("{", m.start())
            close_b = _find_matching_delimiter(no_comm, open_b)
            if close_b == -1 or close_b > end:
                break
            attrs = _parse_attrs(m.group(1) or "")
            kind_kw = m.group(2).lower()
            name = m.group(3)
            base_iface = m.group(4)
            ln = _line_of_offset(line_starts, m.start())
            doc = attrs.get("helpstring") or _extract_comment_near_line(lines, ln)

            if kind_kw in ("interface", "dispinterface"):
                nid = f"{ctx.rel_slug}_iface_{_slugify(name)}"
                ctx.add_symbol(
                    nid,
                    name,
                    "interface",
                    line=ln,
                    parent_id=parent_id,
                    extra_meta={**attrs, "docstring": doc},
                )
                iface_ids[name.lower()] = nid
                if base_iface:
                    ctx.add_edge(nid, iface_ids.get(base_iface.lower(), _slugify(base_iface)), "inherits", line=ln)
                # Methods inside interface
                body = no_comm[open_b + 1:close_b]
                for mm in re.finditer(r"(?:\[[^\]]*\]\s*)?(?:HRESULT|void|long|int|BOOL|BSTR|VARIANT)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*;", body):
                    mname = mm.group(1)
                    mln = _line_of_offset(line_starts, open_b + 1 + mm.start())
                    ctx.add_symbol(
                        f"{ctx.rel_slug}_{_slugify(name)}_{_slugify(mname)}",
                        f"{mname}()",
                        "method",
                        line=mln,
                        parent_id=nid,
                        relation="method",
                        extra_meta={"params": " ".join(mm.group(2).split())},
                    )
            elif kind_kw == "library":
                nid = f"{ctx.rel_slug}_lib_{_slugify(name)}"
                ctx.add_symbol(
                    nid,
                    name,
                    "library",
                    line=ln,
                    parent_id=parent_id,
                    extra_meta={**attrs, "docstring": doc},
                )
                _scan_idl_region(open_b + 1, close_b, nid)
            elif kind_kw == "coclass":
                nid = f"{ctx.rel_slug}_coclass_{_slugify(name)}"
                ctx.add_symbol(
                    nid,
                    name,
                    "coclass",
                    line=ln,
                    parent_id=parent_id,
                    extra_meta={**attrs, "docstring": doc},
                )
                body = no_comm[open_b + 1:close_b]
                for im in re.finditer(r"\b(?:disp)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", body):
                    iname = im.group(1)
                    iln = _line_of_offset(line_starts, open_b + 1 + im.start())
                    ctx.add_edge(nid, iface_ids.get(iname.lower(), _slugify(iname)), "implements", line=iln)

            pos = close_b + 1

    _scan_idl_region(0, len(no_comm), file_id)
    return ctx.nodes, ctx.edges


def _parse_def(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse Windows DLL Module-Definition (.def) LIBRARY and EXPORTS symbols."""
    ctx = _LegacyParseContext(project_id, rel_path, "def", encoding)
    lines = text.splitlines()
    file_id = ctx.ensure_file_node(docstring=_extract_comment_near_line(lines, 1, line_prefixes=(";",)))

    parent_id = file_id
    in_exports = False
    section_stop = {"SECTIONS", "SEGMENTS", "HEAPSIZE", "STACKSIZE", "STUB", "VERSION", "DESCRIPTION"}

    for idx, raw_line in enumerate(lines):
        ln = idx + 1
        code_part = raw_line.split(";", 1)[0].strip()
        if not code_part:
            continue

        m_lib = re.match(r'^(LIBRARY|NAME)\s+"?([A-Za-z0-9_.-]+)"?', code_part, re.IGNORECASE)
        if m_lib:
            in_exports = False
            lib_name = m_lib.group(2)
            parent_id = ctx.add_symbol(
                f"{ctx.rel_slug}_lib_{_slugify(lib_name)}",
                lib_name,
                "library",
                line=ln,
                parent_id=file_id,
                extra_meta={"def_directive": m_lib.group(1).upper()},
            )
            continue

        if re.match(r"^EXPORTS\b", code_part, re.IGNORECASE):
            in_exports = True
            rest = code_part[len("EXPORTS"):].strip()
            if not rest:
                continue
            code_part = rest

        first_tok = code_part.split()[0].upper()
        if first_tok in section_stop:
            in_exports = False
            continue

        if in_exports:
            m_exp = re.match(
                r"^([A-Za-z_@?][A-Za-z0-9_@?$]*)(?:\s*=\s*([A-Za-z_][A-Za-z0-9_.]*))?(.*)$",
                code_part,
            )
            if m_exp:
                exp_name = m_exp.group(1)
                internal_name = m_exp.group(2) or ""
                tail = m_exp.group(3).strip()
                ord_m = re.search(r"@(\d+)", tail)
                ordinal = int(ord_m.group(1)) if ord_m else None
                flags = [t for t in tail.split() if not t.startswith("@")]
                doc = _extract_comment_near_line(lines, ln, line_prefixes=(";",))
                enid = ctx.add_symbol(
                    f"{ctx.rel_slug}_export_{_slugify(exp_name)}",
                    exp_name,
                    "export",
                    line=ln,
                    parent_id=parent_id,
                    extra_meta={
                        "ordinal": ordinal,
                        "internal_name": internal_name,
                        "flags": flags,
                        "docstring": doc,
                    },
                )
                if internal_name:
                    ctx.add_edge(enid, _slugify(internal_name), "calls", line=ln)

    return ctx.nodes, ctx.edges


def _parse_rc(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse Win32 Resource (.rc) DIALOG/DIALOGEX, MENU, STRINGTABLE, and control IDs."""
    ctx = _LegacyParseContext(project_id, rel_path, "rc", encoding)
    lines = text.splitlines()
    file_id = ctx.ensure_file_node(docstring=_extract_comment_near_line(lines, 1))
    no_comm, _ = _strip_comments_preserve_lines(text)
    clean_lines = no_comm.splitlines()

    for idx, line in enumerate(clean_lines):
        m_inc = re.match(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', line)
        if m_inc:
            ctx.add_edge(file_id, _slugify(Path(m_inc.group(1)).stem), "imports", line=idx + 1)

    dlg_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(DIALOGEX|DIALOG)\b", re.IGNORECASE)
    menu_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(MENUEX|MENU)\b", re.IGNORECASE)
    strtbl_re = re.compile(r"^\s*STRINGTABLE\b", re.IGNORECASE)
    caption_re = re.compile(r'^\s*CAPTION\s+"([^"]*)"', re.IGNORECASE)
    ctrl_quoted_re = re.compile(
        r'^\s*(CONTROL|PUSHBUTTON|DEFPUSHBUTTON|CHECKBOX|RADIOBUTTON|GROUPBOX|LTEXT|RTEXT|CTEXT|AUTOCHECKBOX|AUTORADIOBUTTON)\s+"([^"]*)"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)',
        re.IGNORECASE,
    )
    ctrl_unquoted_re = re.compile(
        r"^\s*(EDITTEXT|COMBOBOX|LISTBOX|SCROLLBAR|ICON)\s+(?:\"[^\"]*\"\s*,\s*)?([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    menuitem_re = re.compile(r'^\s*MENUITEM\s+"([^"]*)"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)', re.IGNORECASE)
    str_entry_re = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s+"([^"]*)"')

    active_container_id: Optional[str] = None
    active_container_kind: Optional[str] = None
    block_depth = 0
    seen_begin = False

    for idx, raw in enumerate(clean_lines):
        ln = idx + 1
        s = raw.strip()
        if not s:
            continue

        if active_container_kind is None:
            m_dlg = dlg_re.match(s)
            if m_dlg:
                dlg_id_name = m_dlg.group(1)
                active_container_kind = "dialog"
                active_container_id = ctx.add_symbol(
                    f"{ctx.rel_slug}_dlg_{_slugify(dlg_id_name)}",
                    dlg_id_name,
                    "dialog",
                    line=ln,
                    parent_id=file_id,
                    extra_meta={"resource_type": m_dlg.group(2).upper()},
                )
                block_depth = 0
                seen_begin = False
                continue
            m_menu = menu_re.match(s)
            if m_menu:
                menu_name = m_menu.group(1)
                active_container_kind = "menu"
                active_container_id = ctx.add_symbol(
                    f"{ctx.rel_slug}_menu_{_slugify(menu_name)}",
                    menu_name,
                    "menu",
                    line=ln,
                    parent_id=file_id,
                    extra_meta={"resource_type": m_menu.group(2).upper()},
                )
                block_depth = 0
                seen_begin = False
                continue
            if strtbl_re.match(s):
                active_container_kind = "stringtable"
                active_container_id = file_id
                block_depth = 0
                seen_begin = False
                continue
            continue

        # Inside DIALOG / MENU / STRINGTABLE
        upper_tok = s.split()[0].upper()
        if upper_tok in ("BEGIN", "{"):
            block_depth += 1
            seen_begin = True
            continue
        if upper_tok in ("END", "}"):
            block_depth -= 1
            if seen_begin and block_depth <= 0:
                active_container_id = None
                active_container_kind = None
            continue

        if active_container_kind == "dialog" and active_container_id:
            m_cap = caption_re.match(s)
            if m_cap:
                node = ctx.nodes_by_id.get(active_container_id)
                if node:
                    node.metadata["caption"] = m_cap.group(1)
                    node.metadata.setdefault("docstring", m_cap.group(1))
                continue
            m_cq = ctrl_quoted_re.match(s)
            if m_cq:
                ctype, ctext, cid = m_cq.group(1).upper(), m_cq.group(2), m_cq.group(3)
                ctx.add_symbol(
                    f"{ctx.rel_slug}_ctrl_{_slugify(cid)}",
                    cid,
                    "control",
                    line=ln,
                    parent_id=active_container_id,
                    extra_meta={"control_type": ctype, "caption": ctext},
                )
                continue
            m_cu = ctrl_unquoted_re.match(s)
            if m_cu:
                ctype, cid = m_cu.group(1).upper(), m_cu.group(2)
                ctx.add_symbol(
                    f"{ctx.rel_slug}_ctrl_{_slugify(cid)}",
                    cid,
                    "control",
                    line=ln,
                    parent_id=active_container_id,
                    extra_meta={"control_type": ctype},
                )
        elif active_container_kind == "menu" and active_container_id:
            m_mi = menuitem_re.match(s)
            if m_mi:
                mtext, mid = m_mi.group(1), m_mi.group(2)
                ctx.add_symbol(
                    f"{ctx.rel_slug}_ctrl_{_slugify(mid)}",
                    mid,
                    "control",
                    line=ln,
                    parent_id=active_container_id,
                    extra_meta={"control_type": "MENUITEM", "caption": mtext},
                )
        elif active_container_kind == "stringtable":
            m_se = str_entry_re.match(s)
            if m_se:
                sid, sval = m_se.group(1), m_se.group(2)
                ctx.add_symbol(
                    f"{ctx.rel_slug}_str_{_slugify(sid)}",
                    sid,
                    "string_resource",
                    line=ln,
                    parent_id=file_id,
                    extra_meta={"value": sval, "docstring": sval},
                )

    return ctx.nodes, ctx.edges


def _parse_vb6(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse legacy VB6/VBA (.bas/.cls/.frm) modules, Declare APIs, subs/functions, and calls."""
    ctx = _LegacyParseContext(project_id, rel_path, "vb6", encoding)
    lines = text.splitlines()
    file_id = ctx.ensure_file_node(docstring=_extract_comment_near_line(lines, 1, line_prefixes=("'", "REM ")))
    ext = Path(rel_path).suffix.lower()

    container_id = file_id
    local_symbols: Dict[str, str] = {}
    body_ranges: List[Tuple[str, int, int]] = []
    current_proc: Optional[Tuple[str, int]] = None

    attr_name_re = re.compile(r'^\s*Attribute\s+VB_Name\s*=\s*"([^"]+)"', re.IGNORECASE)
    form_re = re.compile(r"^\s*Begin\s+VB\.(Form|MDIForm)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
    declare_re = re.compile(
        r'^\s*(?:(Public|Private|Friend)\s+)?Declare\s+(?:PtrSafe\s+)?(Function|Sub)\s+'
        r'([A-Za-z_][A-Za-z0-9_]*)\s+Lib\s+"([^"]+)"(?:\s+Alias\s+"([^"]+)")?',
        re.IGNORECASE,
    )
    proc_re = re.compile(
        r"^\s*(?:(Public|Private|Friend)\s+)?(?:Static\s+)?(Sub|Function|Property\s+(?:Get|Let|Set))\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    end_proc_re = re.compile(r"^\s*End\s+(Sub|Function|Property)\b", re.IGNORECASE)
    udt_re = re.compile(r"^\s*(?:(?:Public|Private)\s+)?Type\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
    enum_re = re.compile(r"^\s*(?:(?:Public|Private)\s+)?Enum\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

    for idx, raw_line in enumerate(lines):
        ln = idx + 1
        code_part = raw_line.split("'", 1)[0].strip()
        if not code_part or code_part.upper().startswith("REM "):
            continue

        m_attr = attr_name_re.match(code_part)
        if m_attr:
            mod_name = m_attr.group(1)
            vb_kind = "form" if ext == ".frm" else ("class" if ext == ".cls" else "module")
            container_id = ctx.add_symbol(
                f"{ctx.rel_slug}_mod_{_slugify(mod_name)}",
                mod_name,
                "class",
                line=ln,
                parent_id=file_id,
                extra_meta={"vb_kind": vb_kind},
            )
            continue

        m_frm = form_re.match(code_part)
        if m_frm and container_id == file_id:
            fname = m_frm.group(2)
            container_id = ctx.add_symbol(
                f"{ctx.rel_slug}_frm_{_slugify(fname)}",
                fname,
                "class",
                line=ln,
                parent_id=file_id,
                extra_meta={"vb_kind": "form"},
            )
            continue

        m_dec = declare_re.match(code_part)
        if m_dec:
            vis = (m_dec.group(1) or "Public").lower()
            fn_name = m_dec.group(3)
            dll_lib = m_dec.group(4)
            alias = m_dec.group(5) or ""
            doc = _extract_comment_near_line(lines, ln, line_prefixes=("'", "REM "))
            nid = ctx.add_symbol(
                f"{ctx.rel_slug}_decl_{_slugify(fn_name)}",
                f"{fn_name}()",
                "function",
                line=ln,
                parent_id=container_id,
                extra_meta={
                    "is_declare": True,
                    "dll_lib": dll_lib,
                    "alias": alias,
                    "visibility": vis,
                    "signature": code_part,
                    "docstring": doc,
                },
            )
            local_symbols[fn_name.lower()] = nid
            ctx.add_edge(container_id, _slugify(Path(dll_lib).stem), "imports", line=ln)
            continue

        if current_proc and end_proc_re.match(code_part):
            pid, sln = current_proc
            body_ranges.append((pid, sln, ln))
            current_proc = None
            continue

        m_proc = proc_re.match(code_part)
        if m_proc:
            vis = (m_proc.group(1) or "Public").lower()
            pname = m_proc.group(3)
            pkind = "method" if ext in (".cls", ".frm") else "function"
            doc = _extract_comment_near_line(lines, ln, line_prefixes=("'", "REM "))
            nid = ctx.add_symbol(
                f"{ctx.rel_slug}_{_slugify(pname)}",
                f"{pname}()",
                pkind,
                line=ln,
                parent_id=container_id,
                relation="method" if pkind == "method" else "contains",
                extra_meta={"visibility": vis, "signature": code_part, "docstring": doc},
            )
            local_symbols[pname.lower()] = nid
            current_proc = (nid, ln)
            continue

        m_udt = udt_re.match(code_part)
        if m_udt:
            ctx.add_symbol(f"{ctx.rel_slug}_udt_{_slugify(m_udt.group(1))}", m_udt.group(1), "struct", line=ln, parent_id=container_id)
            continue

        m_enum = enum_re.match(code_part)
        if m_enum:
            ctx.add_symbol(f"{ctx.rel_slug}_enum_{_slugify(m_enum.group(1))}", m_enum.group(1), "enum", line=ln, parent_id=container_id)
            continue

    call_re = re.compile(r"(?:\bCall\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for caller_id, sln, eln in body_ranges:
        for ln in range(sln + 1, eln):
            code_part = lines[ln - 1].split("'", 1)[0]
            for cm in call_re.finditer(code_part):
                callee = cm.group(1)
                if callee.lower() in local_symbols and local_symbols[callee.lower()] != caller_id:
                    ctx.add_edge(caller_id, local_symbols[callee.lower()], "calls", line=ln)

    return ctx.nodes, ctx.edges


def _parse_delphi(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse Delphi / Object Pascal (.pas/.dfm) units, sections, classes, and routines."""
    ctx = _LegacyParseContext(project_id, rel_path, "pascal", encoding)
    lines = text.splitlines()
    file_id = ctx.ensure_file_node(docstring=_extract_comment_near_line(lines, 1, line_prefixes=("//",), block_open="{", block_close="}"))
    ext = Path(rel_path).suffix.lower()

    if ext == ".dfm":
        if text.startswith("TPF0"):  # Binary DFM guard
            return ctx.nodes, ctx.edges
        stack: List[str] = [file_id]
        for idx, raw in enumerate(lines):
            ln = idx + 1
            s = raw.strip()
            m_obj = re.match(r"^(?:object|inherited)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", s, re.IGNORECASE)
            if m_obj:
                oname, otype = m_obj.group(1), m_obj.group(2)
                okind = "dialog" if len(stack) == 1 else "control"
                nid = ctx.add_symbol(
                    f"{ctx.rel_slug}_{_slugify(oname)}",
                    oname,
                    okind,
                    line=ln,
                    parent_id=stack[-1],
                    extra_meta={"dfm_type": otype},
                )
                stack.append(nid)
            elif s.lower() == "end" and len(stack) > 1:
                stack.pop()
        return ctx.nodes, ctx.edges

    no_comm, no_str = _strip_comments_preserve_lines(
        text,
        line_prefixes=("//",),
        block_pairs=(("(*", "*)"), ("{", "}")),
        string_delims=("'",),
        backslash_escapes=False,
    )
    line_starts = _compute_line_starts(no_str)
    clean_lines = no_comm.splitlines()

    unit_id = file_id
    unit_name = Path(rel_path).stem
    current_section = "interface"
    current_section_id: Optional[str] = None
    class_ids: Dict[str, str] = {}
    routine_ids: Dict[str, str] = {}
    body_spans: List[Tuple[str, int, int]] = []

    unit_re = re.compile(r"^\s*(unit|program|library)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.IGNORECASE)
    type_cls_re = re.compile(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:packed\s+)?(class|interface|record)\s*(?:\(([^)]+)\))?",
        re.IGNORECASE,
    )
    proc_re = re.compile(
        r"^\s*(?:class\s+)?(procedure|function|constructor|destructor)\s+(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )

    # uses ...; imports
    for um in re.finditer(r"\buses\b(.*?);", no_comm, re.IGNORECASE | re.DOTALL):
        ln = _line_of_offset(line_starts, um.start())
        for tok in um.group(1).split(","):
            uname = tok.strip().split()[0] if tok.strip() else ""
            if uname and re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", uname):
                ctx.add_edge(file_id, _slugify(uname), "imports", line=ln)

    active_routine: Optional[Tuple[str, int]] = None
    active_type_decl: Optional[Tuple[str, str]] = None  # (name, node_id)
    begin_depth = 0
    in_type_block = False

    for idx, raw in enumerate(clean_lines):
        ln = idx + 1
        s = raw.strip()
        if not s:
            continue

        m_u = unit_re.match(s)
        if m_u:
            unit_name = m_u.group(2)
            pas_kind = m_u.group(1).lower()
            if pas_kind in ("program", "library"):
                current_section = "implementation"
            unit_id = ctx.add_symbol(
                f"{ctx.rel_slug}_unit_{_slugify(unit_name)}",
                unit_name,
                "unit",
                line=ln,
                parent_id=file_id,
                extra_meta={"pas_kind": pas_kind},
            )
            continue

        low = s.lower()
        if low in ("interface", "implementation"):
            current_section = low
            in_type_block = False
            active_type_decl = None
            current_section_id = ctx.add_symbol(
                f"{ctx.rel_slug}_sec_{low}",
                f"{unit_name}.{low}",
                "section",
                line=ln,
                parent_id=unit_id,
            )
            continue

        if low == "type":
            in_type_block = True
            active_type_decl = None
            continue
        if low in ("var", "const", "resourcestring"):
            in_type_block = False
            active_type_decl = None
            continue

        if active_type_decl is not None and re.match(r"^end\s*;", low):
            active_type_decl = None
            continue

        m_tc = type_cls_re.match(s)
        if m_tc and (in_type_block or "=" in s):
            cname = m_tc.group(1)
            ckw = m_tc.group(2).lower()
            bases = m_tc.group(3) or ""
            ckind = "struct" if ckw == "record" else ("interface" if ckw == "interface" else "class")
            doc = _extract_comment_near_line(lines, ln, line_prefixes=("//",), block_open="{", block_close="}")
            cnid = ctx.add_symbol(
                f"{ctx.rel_slug}_{ckind}_{_slugify(cname)}",
                cname,
                ckind,
                line=ln,
                parent_id=current_section_id or unit_id,
                extra_meta={"section": current_section, "docstring": doc},
            )
            class_ids[cname.lower()] = cnid
            # Forward class declarations `TFoo = class;` have `;` at EOL and no body
            if not s.rstrip().endswith(";"):
                active_type_decl = (cname, cnid)
            for b in bases.split(","):
                bname = b.strip()
                if bname:
                    ctx.add_edge(cnid, class_ids.get(bname.lower(), _slugify(bname)), "inherits", line=ln)
            continue

        m_pr = proc_re.match(s)
        if m_pr and begin_depth == 0:
            pkw = m_pr.group(1).lower()
            cls_owner = m_pr.group(2) or (active_type_decl[0] if active_type_decl else None)
            pname = m_pr.group(3)
            doc = _extract_comment_near_line(lines, ln, line_prefixes=("//",), block_open="{", block_close="}")
            if cls_owner:
                owner_id = class_ids.get(cls_owner.lower()) or current_section_id or unit_id
                qual = f"{cls_owner}.{pname}"
                pkind = "method"
            else:
                owner_id = current_section_id or unit_id
                qual = pname
                pkind = "function"
            pnid = ctx.add_symbol(
                f"{ctx.rel_slug}_{_slugify(qual)}",
                f"{qual}()",
                pkind,
                line=ln,
                parent_id=owner_id,
                relation="method" if pkind == "method" else "contains",
                extra_meta={
                    "pas_kind": pkw,
                    "qualified_name": qual,
                    "section": current_section,
                    "docstring": doc,
                },
            )
            routine_ids[qual.lower()] = pnid
            routine_ids.setdefault(pname.lower(), pnid)
            if current_section == "implementation" and active_type_decl is None:
                active_routine = (pnid, ln)
                begin_depth = 0
            continue

        if active_routine is not None:
            for tok in re.findall(r"\b(?:begin|case|try|end)\b", low):
                if tok in ("begin", "case", "try"):
                    begin_depth += 1
                elif tok == "end":
                    begin_depth = max(0, begin_depth - 1)
                    if begin_depth == 0:
                        body_spans.append((active_routine[0], active_routine[1], ln))
                        active_routine = None
                        break

    no_str_lines = no_str.splitlines()
    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*(?:\(|;)")
    for caller_id, sln, eln in body_spans:
        for ln in range(sln + 1, min(eln, len(no_str_lines) + 1)):
            line_clean = no_str_lines[ln - 1]
            for cm in call_re.finditer(line_clean):
                callee = cm.group(1).lower()
                if callee in routine_ids and routine_ids[callee] != caller_id:
                    ctx.add_edge(caller_id, routine_ids[callee], "calls", line=ln)

    return ctx.nodes, ctx.edges


def _parse_cobol(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse COBOL (.cbl/.cob) PROGRAM-ID, DIVISION/SECTION, paragraphs, COPY, PERFORM, and CALL."""
    ctx = _LegacyParseContext(project_id, rel_path, "cobol", encoding)
    lines = text.splitlines()
    file_id = ctx.ensure_file_node()

    prog_id_re = re.compile(r"^\s*PROGRAM-ID\.\s*([A-Za-z0-9_-]+)", re.IGNORECASE)
    div_re = re.compile(r"^\s*(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\.", re.IGNORECASE)
    sec_re = re.compile(r"^\s*([A-Za-z0-9-]+)\s+SECTION\.", re.IGNORECASE)
    para_re = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\.\s*$")
    copy_re = re.compile(r"\bCOPY\s+([A-Za-z0-9_-]+)", re.IGNORECASE)
    perform_re = re.compile(r"\bPERFORM\s+([A-Za-z][A-Za-z0-9-]*)", re.IGNORECASE)
    call_re = re.compile(r"""\bCALL\s+['"]?([A-Za-z0-9_-]+)['"]?""", re.IGNORECASE)

    program_node_id = file_id
    current_section_id: Optional[str] = None
    current_para_id: Optional[str] = None
    para_ids: Dict[str, str] = {}
    deferred_calls: List[Tuple[str, str, int, str]] = []
    cobol_skip_perform = {"UNTIL", "VARYING", "TIMES", "WITH", "TEST", "INLINE"}

    def _strip_cobol_line(raw: str) -> Optional[str]:
        if len(raw) > 6 and raw[6] in ("*", "/"):
            return None
        code = raw.split("*>", 1)[0]
        if len(raw) > 6 and raw[:6].isdigit():
            code = code[6:]
        return code.strip()

    for idx, raw in enumerate(lines):
        ln = idx + 1
        cleaned = _strip_cobol_line(raw)
        if not cleaned:
            continue

        m_pid = prog_id_re.match(cleaned)
        if m_pid:
            pname = m_pid.group(1).upper()
            program_node_id = ctx.add_symbol(
                f"{ctx.rel_slug}_prog_{_slugify(pname)}",
                pname,
                "program",
                line=ln,
                parent_id=file_id,
            )
            continue

        if div_re.match(cleaned):
            current_section_id = None
            current_para_id = None
            continue

        m_sec = sec_re.match(cleaned)
        if m_sec:
            sname = m_sec.group(1).upper()
            current_section_id = ctx.add_symbol(
                f"{ctx.rel_slug}_sec_{_slugify(sname)}",
                f"{sname} SECTION",
                "section",
                line=ln,
                parent_id=program_node_id,
            )
            para_ids[sname] = current_section_id
            current_para_id = None
            continue

        m_para = para_re.match(cleaned)
        if m_para:
            pname = m_para.group(1).upper()
            if pname not in ("IDENTIFICATION", "ENVIRONMENT", "DATA", "PROCEDURE", "SECTION", "DIVISION"):
                pnid = ctx.add_symbol(
                    f"{ctx.rel_slug}_para_{_slugify(pname)}",
                    pname,
                    "function",
                    line=ln,
                    parent_id=current_section_id or program_node_id,
                    extra_meta={"cobol_kind": "paragraph"},
                )
                para_ids[pname] = pnid
                current_para_id = pnid
                continue

        caller = current_para_id or current_section_id or program_node_id
        for cm in copy_re.finditer(cleaned):
            ctx.add_edge(caller, _slugify(cm.group(1)), "imports", line=ln)
        for pm in perform_re.finditer(cleaned):
            tgt = pm.group(1).upper()
            if tgt not in cobol_skip_perform:
                deferred_calls.append((caller, tgt, ln, "perform"))
        for clm in call_re.finditer(cleaned):
            deferred_calls.append((caller, clm.group(1).upper(), ln, "call"))

    for caller, tgt_name, ln, _ in deferred_calls:
        target_id = para_ids.get(tgt_name, _slugify(tgt_name))
        ctx.add_edge(caller, target_id, "calls", line=ln)

    return ctx.nodes, ctx.edges


def _parse_sql_script_reg(
    text: str,
    rel_path: str,
    project_id: str,
    encoding: str,
) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Parse SQL DDL/routines (.sql), Windows batch scripts (.bat/.cmd), and Registry (.reg)."""
    ext = Path(rel_path).suffix.lower()
    lang = "sql" if ext == ".sql" else ("reg" if ext == ".reg" else "batch")
    ctx = _LegacyParseContext(project_id, rel_path, lang, encoding)
    file_id = ctx.ensure_file_node()

    if ext == ".sql":
        no_comm, _ = _strip_comments_preserve_lines(text, line_prefixes=("--",), block_pairs=(("/*", "*/"),))
        line_starts = _compute_line_starts(no_comm)
        for m in re.finditer(
            r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(TABLE|VIEW|PROCEDURE|FUNCTION|TRIGGER)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_0-9.\[\]`\"]+)",
            no_comm,
            re.IGNORECASE,
        ):
            obj_kw = m.group(1).lower()
            obj_name = m.group(2).strip('[]`"')
            ln = _line_of_offset(line_starts, m.start())
            kind = "table" if obj_kw in ("table", "view") else "function"
            ctx.add_symbol(f"{ctx.rel_slug}_{obj_kw}_{_slugify(obj_name)}", obj_name, kind, line=ln, parent_id=file_id)
    elif ext == ".reg":
        cur_key = file_id
        for idx, raw in enumerate(text.splitlines(), start=1):
            s = raw.strip()
            if not s or s.startswith(";"):
                continue
            if s.startswith("[") and s.endswith("]"):
                key_path = s[1:-1].strip()
                cur_key = ctx.add_symbol(
                    f"{ctx.rel_slug}_reg_{_slugify(key_path)}",
                    key_path,
                    "registry_key",
                    line=idx,
                    parent_id=file_id,
                )
            elif "=" in s:
                vname = s.split("=", 1)[0].strip().strip('"')
                if vname:
                    ctx.add_symbol(
                        f"{ctx.rel_slug}_regv_{_slugify(vname)}",
                        vname,
                        "variable",
                        line=idx,
                        parent_id=cur_key,
                    )
    else:  # .bat / .cmd
        labels: Dict[str, str] = {}
        for idx, raw in enumerate(text.splitlines(), start=1):
            s = raw.strip()
            if not s or s.lower().startswith(("rem ", "@rem ", "::")):
                continue
            m_lbl = re.match(r"^:([A-Za-z_][A-Za-z0-9_-]*)\b", s)
            if m_lbl:
                lbl = m_lbl.group(1)
                nid = ctx.add_symbol(f"{ctx.rel_slug}_lbl_{_slugify(lbl)}", lbl, "function", line=idx, parent_id=file_id)
                labels[lbl.lower()] = nid
                continue
            m_call = re.match(r"^(?:@?call)\s+:?([A-Za-z0-9_.-]+)", s, re.IGNORECASE)
            if m_call:
                tgt = m_call.group(1)
                ctx.add_edge(file_id, labels.get(tgt.lower(), _slugify(tgt)), "calls", line=idx)

    return ctx.nodes, ctx.edges


class GraphifyExtractor:
    """
    AST & Multimodal Semantic Extractor Engine.

    Coordinates Tree-sitter multi-language code parsing, Python AST fallbacks,
    document graph construction, SHA256 caching, community clustering, and partial
    local graph persistence.
    """

    def __init__(
        self,
        cache_dir: Optional[Union[str, Path]] = None,
        device: str = "auto",
        workers: Optional[int] = None,
        semantic_engine: str = "auto",
    ):
        self.cache_dir = Path(cache_dir or ".projectbrain/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.workers = workers
        self.semantic_engine = semantic_engine
        self._cache_lock = threading.Lock()
        self.last_acceleration_meta: Dict[str, Any] = {
            "device_used": "cpu",
            "backend_used": "networkx-cpu",
            "fallback_triggered": False,
        }

    # -----------------------------------------------------------------------
    # Caching Primitives
    # -----------------------------------------------------------------------
    def compute_file_hashes(self, file_path: Union[str, Path]) -> Tuple[str, str]:
        """
        Compute (cache_key, content_sha256) for a file.

        - content_sha256: raw SHA256 of the exact file bytes.
        - cache_key: SHA256(body + '\x00' + resolved_path), stripping Markdown
          YAML frontmatter so metadata-only edits don't bust semantic cache.
        """
        path = Path(file_path).resolve()
        raw_bytes = path.read_bytes()
        content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        body = (
            _strip_markdown_frontmatter(raw_bytes)
            if path.suffix.lower() in DOC_EXTENSIONS
            else raw_bytes
        )
        h = hashlib.sha256()
        h.update(body)
        h.update(b"\x00")
        h.update(str(path).encode("utf-8"))
        cache_key = h.hexdigest()
        return cache_key, content_sha256

    def load_cached_delta(self, cache_key: str, project_id: str, branch: str) -> Optional[GraphDelta]:
        """Load cached GraphDelta by cache_key from self.cache_dir."""
        entry = self.cache_dir / f"{cache_key}.json"
        if not entry.exists():
            return None
        try:
            with open(entry, "r", encoding="utf-8") as f:
                data = json.load(f)
            nodes = [GraphNode(**n) for n in data.get("nodes", [])]
            edges = [GraphEdge(**e) for e in data.get("edges", [])]
            hyperedges = [GraphHyperedge(**h) for h in data.get("hyperedges", [])]
            return GraphDelta(
                project_id=project_id,
                branch=branch,
                nodes=nodes,
                edges=edges,
                hyperedges=hyperedges,
                node_tombstones=data.get("node_tombstones", []),
                edge_tombstones=data.get("edge_tombstones", []),
                hyperedge_tombstones=data.get("hyperedge_tombstones", []),
                synced_files=data.get("synced_files", []),
            )
        except Exception as e:
            logger.debug("Failed to read cache entry %s: %s", entry, e)
            return None

    def save_cached_delta(self, cache_key: str, delta: GraphDelta) -> None:
        """Atomically and thread-safely persist extraction delta into self.cache_dir."""
        entry = self.cache_dir / f"{cache_key}.json"
        tmp = self.cache_dir / f"{cache_key}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            payload = delta.to_dict() if hasattr(delta, "to_dict") else delta.model_dump()
            with self._cache_lock:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, entry)
        except Exception as e:
            logger.debug("Failed to write cache entry %s: %s", entry, e)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # -----------------------------------------------------------------------
    # Code AST Extraction (graphify + Python AST + Legacy Parsers)
    # -----------------------------------------------------------------------
    def _extract_code_ast(
        self,
        file_path: Path,
        rel_path: str,
        project_id: str,
        branch: str,
    ) -> GraphDelta:
        """Extract code symbols via the unified ExtractorRegistry (UTF-8 staged Tree-sitter + legacy/C++ backends)."""
        registry = getattr(self, "_extractor_registry", None) or get_default_registry()
        return registry.extract_code(
            file_path=file_path,
            rel_path=rel_path,
            project_id=project_id,
            branch=branch,
            python_fallback_fn=self._python_ast_fallback,
        )

    def _python_ast_fallback(
        self,
        file_path: Path,
        rel_path: str,
        project_id: str,
    ) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """Fallback Python AST parser extracting classes, functions, calls, imports, and docstring rationale."""
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        file_slug = _slugify(Path(rel_path).stem)

        try:
            content, _ = read_text_with_encoding(file_path)
            tree = ast.parse(content, filename=str(file_path))
        except Exception as e:
            logger.debug("ast.parse failed on %s: %s", rel_path, e)
            return nodes, edges

        # Root file node
        file_node_id = f"file_{file_slug}"
        nodes.append(
            GraphNode(
                id=file_node_id,
                project_id=project_id,
                label=Path(rel_path).name,
                file_type="code",
                source_file=rel_path,
                source_location="L1",
                metadata={"kind": "module"},
            )
        )

        class AstVisitor(ast.NodeVisitor):
            def __init__(self):
                self.scope_stack: List[Tuple[str, str]] = [(file_node_id, file_slug)]

            def visit_Import(self, node: ast.Import):
                for alias in node.names:
                    target = _slugify(alias.name)
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=self.scope_stack[-1][0],
                            target=target,
                            relation="imports",
                            confidence="EXTRACTED",
                            confidence_score=1.0,
                            source_file=rel_path,
                            source_location=f"L{node.lineno}",
                        )
                    )
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    full_name = f"{mod}.{alias.name}" if mod else alias.name
                    target = _slugify(full_name)
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=self.scope_stack[-1][0],
                            target=target,
                            relation="imports",
                            confidence="EXTRACTED",
                            confidence_score=1.0,
                            source_file=rel_path,
                            source_location=f"L{node.lineno}",
                        )
                    )
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef):
                cls_slug = _slugify(node.name)
                cls_id = f"{file_slug}_{cls_slug}"
                parent_id = self.scope_stack[-1][0]

                nodes.append(
                    GraphNode(
                        id=cls_id,
                        project_id=project_id,
                        label=node.name,
                        file_type="code",
                        source_file=rel_path,
                        source_location=f"L{node.lineno}",
                        metadata={"kind": "class"},
                    )
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=parent_id,
                        target=cls_id,
                        relation="contains",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file=rel_path,
                        source_location=f"L{node.lineno}",
                    )
                )

                # Inheritance
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_slug = _slugify(base.id)
                        edges.append(
                            GraphEdge(
                                project_id=project_id,
                                source=cls_id,
                                target=base_slug,
                                relation="inherits",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                source_file=rel_path,
                                source_location=f"L{node.lineno}",
                            )
                        )

                # Rationale in class docstring
                doc = ast.get_docstring(node) or ""
                if any(w in doc.lower() for w in ("rationale", "decision", "because", "architecture")):
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=cls_id,
                            target=parent_id,
                            relation="rationale_for",
                            confidence="EXTRACTED",
                            confidence_score=0.9,
                            source_file=rel_path,
                            source_location=f"L{node.lineno}",
                        )
                    )

                self.scope_stack.append((cls_id, cls_slug))
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._handle_func(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._handle_func(node)

            def _handle_func(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
                fn_slug = _slugify(node.name)
                parent_id, parent_slug = self.scope_stack[-1]
                fn_id = f"{file_slug}_{parent_slug}_{fn_slug}" if parent_id != file_node_id else f"{file_slug}_{fn_slug}"
                rel = "method" if parent_id != file_node_id else "contains"

                nodes.append(
                    GraphNode(
                        id=fn_id,
                        project_id=project_id,
                        label=f"{node.name}()",
                        file_type="code",
                        source_file=rel_path,
                        source_location=f"L{node.lineno}",
                        metadata={"kind": "function"},
                    )
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=parent_id,
                        target=fn_id,
                        relation=rel,
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file=rel_path,
                        source_location=f"L{node.lineno}",
                    )
                )

                # Docstring rationale
                doc = ast.get_docstring(node) or ""
                if any(w in doc.lower() for w in ("rationale", "decision", "because", "constraint")):
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=fn_id,
                            target=parent_id,
                            relation="rationale_for",
                            confidence="EXTRACTED",
                            confidence_score=0.9,
                            source_file=rel_path,
                            source_location=f"L{node.lineno}",
                        )
                    )

                self.scope_stack.append((fn_id, fn_slug))
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_Call(self, node: ast.Call):
                callee_name = ""
                if isinstance(node.func, ast.Name):
                    callee_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    callee_name = node.func.attr
                if callee_name and len(callee_name) > 1 and not callee_name.startswith("__"):
                    caller_id = self.scope_stack[-1][0]
                    target_slug = _slugify(callee_name)
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=caller_id,
                            target=target_slug,
                            relation="calls",
                            confidence="EXTRACTED",
                            confidence_score=1.0,
                            source_file=rel_path,
                            source_location=f"L{getattr(node, 'lineno', 1)}",
                        )
                    )
                self.generic_visit(node)

        visitor = AstVisitor()
        visitor.visit(tree)
        return nodes, edges

    # -----------------------------------------------------------------------
    # Document Graph Extraction
    # -----------------------------------------------------------------------
    def extract_document_graph(
        self,
        file_path: str,
        markdown_content: str,
        project_id: str = "default",
        branch: str = "main",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GraphDelta:
        """
        Parse Markdown text, extracting sections, tables, code backtick symbols,
        links, citations, and rationale blocks into a rich epistemological GraphDelta.

        Assigns confidence levels:
        - EXTRACTED (1.0): Direct heading hierarchy, explicit links, explicit rationale keywords.
        - INFERRED (0.7-0.85): Backtick code symbol references, table entities.
        - AMBIGUOUS (0.2-0.3): Speculative mentions.
        """
        norm_path = str(file_path).replace("\\", "/")
        doc_slug = _slugify(Path(norm_path).stem)
        doc_root_id = f"doc_{doc_slug}"

        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        hyperedges: List[GraphHyperedge] = []

        base_meta = dict(metadata or {})
        doc_node = GraphNode(
            id=doc_root_id,
            project_id=project_id,
            label=Path(norm_path).name,
            file_type="document",
            source_file=norm_path,
            source_location="L1",
            metadata=base_meta,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        nodes.append(doc_node)

        lines = markdown_content.splitlines()
        heading_stack: List[Tuple[int, str]] = [(0, doc_root_id)]
        current_sec_id = doc_root_id
        current_sec_title = Path(norm_path).name

        seen_entities_in_section: Set[str] = set()
        section_counter = 0

        # Regex patterns
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
        backtick_re = re.compile(r"`([a-zA-Z0-9_\-.:/]+)`")
        link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        table_row_re = re.compile(r"^\s*\|(.+)\|\s*$")

        table_rows_buffer: List[str] = []
        table_index = 0

        def flush_table(buf: List[str], line_idx: int):
            nonlocal table_index
            if len(buf) < 2:
                return
            table_index += 1
            table_entities: Set[str] = set()
            for r in buf:
                for match in backtick_re.finditer(r):
                    sym = match.group(1).strip()
                    if len(sym) > 1 and not sym.isdigit():
                        table_entities.add(_slugify(sym))

            if len(table_entities) >= 3:
                hyper_id = f"hyper_{current_sec_id}_tbl_{table_index}"
                hyperedges.append(
                    GraphHyperedge(
                        id=hyper_id,
                        project_id=project_id,
                        label=f"Table Flow ({current_sec_title})",
                        nodes=sorted(list(table_entities)),
                        relation="participate_in",
                        confidence="INFERRED",
                        confidence_score=0.85,
                        source_file=norm_path,
                    )
                )

        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Handle Markdown Tables
            if table_row_re.match(stripped):
                table_rows_buffer.append(stripped)
            else:
                if table_rows_buffer:
                    flush_table(table_rows_buffer, line_num)
                    table_rows_buffer = []

            # 1. Heading Extraction
            h_match = heading_re.match(stripped)
            if h_match:
                level = len(h_match.group(1))
                title = h_match.group(2).strip()
                sec_slug = _slugify(title)
                section_counter += 1
                sec_id = f"{doc_root_id}_{sec_slug}_{section_counter}"
                current_sec_id = sec_id
                current_sec_title = title
                seen_entities_in_section = set()

                sec_node = GraphNode(
                    id=sec_id,
                    project_id=project_id,
                    label=f"§ {title}",
                    file_type="document",
                    source_file=norm_path,
                    source_location=f"L{line_num}",
                    metadata={"level": level, "title": title},
                )
                nodes.append(sec_node)

                # Pop heading stack to find immediate parent
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                parent_id = heading_stack[-1][1] if heading_stack else doc_root_id

                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=parent_id,
                        target=sec_id,
                        relation="defines",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file=norm_path,
                        source_location=f"L{line_num}",
                    )
                )
                heading_stack.append((level, sec_id))
                continue

            # 2. Backtick Code Symbol References
            for m in backtick_re.finditer(stripped):
                symbol = m.group(1).strip()
                # Exclude purely numeric, boolean, or very short terms
                if len(symbol) <= 1 or symbol.lower() in ("true", "false", "null", "none", "str", "int", "bool"):
                    continue

                target_id = _slugify(symbol)
                seen_entities_in_section.add(target_id)

                # Check if this line is a rationale / decision statement
                is_rationale = any(
                    k in stripped.lower()
                    for k in (
                        "rationale",
                        "decision",
                        "because",
                        "why",
                        "constraint",
                        "adr",
                        "architecture decision",
                        "> [!note]",
                        "> [!important]",
                        "> [!warning]",
                    )
                )
                relation = "rationale_for" if is_rationale else "cites"
                conf = "EXTRACTED" if is_rationale else "INFERRED"
                score = 0.95 if is_rationale else 0.8

                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=current_sec_id,
                        target=target_id,
                        relation=relation,
                        confidence=conf,
                        confidence_score=score,
                        source_file=norm_path,
                        source_location=f"L{line_num}",
                    )
                )

            # 3. Markdown Links
            for lm in link_re.finditer(stripped):
                link_text = lm.group(1).strip()
                link_url = lm.group(2).strip()
                if not link_url.startswith(("http://", "https://", "mailto:", "#")):
                    # Internal file reference
                    target_doc = f"doc_{_slugify(Path(link_url).stem)}"
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=current_sec_id,
                            target=target_doc,
                            relation="cites",
                            confidence="EXTRACTED",
                            confidence_score=1.0,
                            source_file=norm_path,
                            source_location=f"L{line_num}",
                        )
                    )

        # Flush any trailing table
        if table_rows_buffer:
            flush_table(table_rows_buffer, len(lines))

        # Check for multi-entity section hyperedge
        if len(seen_entities_in_section) >= 3:
            hyper_sec_id = f"hyper_{current_sec_id}_multi"
            hyperedges.append(
                GraphHyperedge(
                    id=hyper_sec_id,
                    project_id=project_id,
                    label=f"Multi-Entity Context in {current_sec_title}",
                    nodes=sorted(list(seen_entities_in_section)),
                    relation="participate_in",
                    confidence="INFERRED",
                    confidence_score=0.85,
                    source_file=norm_path,
                )
            )

        return GraphDelta(
            project_id=project_id,
            branch=branch,
            nodes=nodes,
            edges=edges,
            hyperedges=hyperedges,
        )

    # -----------------------------------------------------------------------
    # Workspace & Partial Scoped Extraction
    # -----------------------------------------------------------------------
    def extract_workspace(
        self,
        workspace_path: Union[str, Path],
        project_id: str = "default",
        branch: str = "main",
        scoped_paths: Optional[List[str]] = None,
        git_dirty_only: bool = False,
        include_docs: bool = True,
        use_cache: bool = True,
        base_branch: Optional[str] = None,
        device: str = "auto",
        workers: Optional[int] = None,
        semantic_engine: str = "auto",
        semantic_pass: bool = False,
    ) -> GraphDelta:
        """
        Extract code AST and semantic document entities across a workspace.

        Supports:
        - Full workspace scanning.
        - Partial scoped extraction via `scoped_paths`.
        - Incremental git dirty scan (`git_dirty_only=True`).
        - Git-diff auto-delta against `base_branch` (`base_branch...HEAD` + uncommitted changes).
        - Multi-worker parallel Tree-sitter AST & document extraction (`workers`).
        - SHA256 content+path caching (thread-safe).
        - Optional GPU / Local AI semantic similarity pass (`semantic_pass=True`).
        - Deletion tracking via `.projectbrain/local_graph/manifest.json`.
        """
        ws_root = Path(workspace_path).resolve()
        manifest_path = ws_root / ".projectbrain" / "local_graph" / "manifest.json"

        target_files: List[Path] = []
        deleted_rel_paths: List[str] = []
        used_git_diff = False

        # 1. Discover Target Files
        if git_dirty_only:
            target_files, deleted_rel_paths = self._discover_git_dirty(ws_root)
        elif scoped_paths:
            for p in scoped_paths:
                resolved = (ws_root / p).resolve()
                if resolved.is_file():
                    target_files.append(resolved)
                elif resolved.is_dir():
                    for root, dirs, files in os.walk(resolved):
                        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
                        for f in files:
                            target_files.append(Path(root) / f)
        elif base_branch and base_branch != branch:
            try:
                verify_res = subprocess.run(
                    ["git", "rev-parse", "--verify", base_branch],
                    cwd=str(ws_root),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if verify_res.returncode == 0:
                    target_files, deleted_rel_paths = self._discover_git_diff(ws_root, base_branch)
                    used_git_diff = True
            except Exception as e:
                logger.debug("git rev-parse verify failed for %s in %s: %s", base_branch, ws_root, e)

        if not git_dirty_only and not scoped_paths and not used_git_diff:
            # Full workspace walk
            for root, dirs, files in os.walk(ws_root):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
                for f in files:
                    target_files.append(Path(root) / f)

            # Compare with existing manifest to find deleted files
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                    stored_files = set(m_data.get("files", {}).keys())
                    current_rel_files = {
                        str(tf.relative_to(ws_root)).replace("\\", "/") for tf in target_files
                    }
                    for sf in stored_files:
                        if sf not in current_rel_files:
                            deleted_rel_paths.append(sf)
                except Exception as e:
                    logger.debug("Failed reading manifest at %s: %s", manifest_path, e)

        # 2. Filter, Check Cache & Extract (Parallel Multi-Worker when >= 4 uncached files)
        candidates: List[Tuple[str, Path, bool]] = []
        seen_rel_paths: Set[str] = set()
        for fpath in target_files:
            if not fpath.is_file():
                continue
            try:
                rel_path = str(fpath.relative_to(ws_root)).replace("\\", "/")
            except ValueError:
                rel_path = str(fpath).replace("\\", "/")

            if any(
                part in ("tools", "orchestrator", ".agents", "node_modules", ".venv", "venv", "__pycache__")
                for part in rel_path.split("/")
            ):
                continue
            if rel_path in seen_rel_paths:
                continue

            ext = fpath.suffix.lower()
            is_code = ext in CODE_EXTENSIONS
            is_doc = include_docs and (ext in DOC_EXTENSIONS)
            if not (is_code or is_doc):
                continue

            seen_rel_paths.add(rel_path)
            candidates.append((rel_path, fpath, is_code))

        # Deterministic sorted order so parallel extraction yields reproducible output
        candidates.sort(key=lambda item: item[0])

        synced_files: List[Dict[str, Any]] = []
        per_file_deltas: List[Optional[GraphDelta]] = [None] * len(candidates)
        uncached_tasks: List[Tuple[int, Path, str, bool, str, str]] = []

        for idx, (rel_path, fpath, is_code) in enumerate(candidates):
            cache_key, content_sha256 = self.compute_file_hashes(fpath)
            synced_files.append({"file_path": rel_path, "sha256": content_sha256})

            cached_delta: Optional[GraphDelta] = None
            if use_cache:
                cached_delta = self.load_cached_delta(cache_key, project_id, branch)

            if cached_delta is not None:
                per_file_deltas[idx] = cached_delta
            else:
                uncached_tasks.append((idx, fpath, rel_path, is_code, cache_key, content_sha256))

        def _extract_single_file(item: Tuple[int, Path, str, bool, str, str]) -> Tuple[int, GraphDelta]:
            idx, fpath, rel_path, is_code, cache_key, content_sha256 = item
            try:
                if is_code:
                    file_delta = self._extract_code_ast(fpath, rel_path, project_id, branch)
                else:
                    try:
                        text_content, _ = read_text_with_encoding(fpath)
                    except Exception:
                        text_content = ""
                    file_delta = self.extract_document_graph(
                        file_path=rel_path,
                        markdown_content=text_content,
                        project_id=project_id,
                        branch=branch,
                        metadata={"file_path": rel_path, "sha256": content_sha256},
                    )
                if use_cache:
                    self.save_cached_delta(cache_key, file_delta)
                return idx, file_delta
            except Exception as exc:
                logger.warning("Extraction failed for %s (%s); skipping file.", rel_path, exc)
                return idx, GraphDelta(
                    project_id=project_id,
                    branch=branch,
                    nodes=[],
                    edges=[],
                    hyperedges=[],
                )

        eff_workers = workers if workers is not None else self.workers
        resolved_workers = 1
        if len(uncached_tasks) >= 4 and eff_workers != 1:
            if eff_workers is None or eff_workers <= 0:
                resolved_workers = min(max(2, os.cpu_count() or 4), 16, len(uncached_tasks))
            else:
                resolved_workers = min(eff_workers, len(uncached_tasks))

        registry = getattr(self, "_extractor_registry", None) or get_default_registry()
        batch_ts_tasks: List[Tuple[int, Path, str, bool, str, str]] = []
        remaining_tasks: List[Tuple[int, Path, str, bool, str, str]] = []
        for task in uncached_tasks:
            _, fpath, _, is_code, _, _ = task
            if is_code and registry.is_graphify_ts_batch_eligible(fpath.suffix):
                batch_ts_tasks.append(task)
            else:
                remaining_tasks.append(task)

        # Batch graphify.extract execution for pass-2 cross-file INFERRED edge resolution
        if batch_ts_tasks:
            ws_root_resolved = ws_root.resolve()
            pb_dir = ws_root / ".projectbrain"
            pb_dir_existed = pb_dir.exists() or pb_dir.is_symlink()
            staging_dir: Optional[Path] = None
            used_pb_dir = False
            try:
                if not pb_dir.is_symlink() and pb_dir.resolve().is_relative_to(ws_root_resolved):
                    pb_dir.mkdir(parents=True, exist_ok=True)
                    if (
                        not pb_dir.is_symlink()
                        and pb_dir.is_dir()
                        and pb_dir.resolve().is_relative_to(ws_root_resolved)
                    ):
                        candidate_stage = Path(tempfile.mkdtemp(prefix="_utf8_stage_", dir=str(pb_dir)))
                        os.chmod(candidate_stage, 0o700)
                        if candidate_stage.resolve().is_relative_to(ws_root_resolved):
                            staging_dir = candidate_stage
                            used_pb_dir = True
                        else:
                            shutil.rmtree(candidate_stage, ignore_errors=True)
            except OSError:
                staging_dir = None

            if staging_dir is None:
                try:
                    staging_dir = Path(tempfile.mkdtemp(prefix="pb_batch_"))
                    os.chmod(staging_dir, 0o700)
                except OSError:
                    staging_dir = None

            try:
                batch_items = [(idx, fpath, rel_path) for idx, fpath, rel_path, _, _, _ in batch_ts_tasks]
                batch_deltas = registry.extract_graphify_ts_batch(
                    batch_items,
                    project_id=project_id,
                    branch=branch,
                    staging_dir=staging_dir,
                    python_fallback_fn=self._python_ast_fallback,
                )
                for task in batch_ts_tasks:
                    idx, _, _, _, cache_key, _ = task
                    if idx in batch_deltas:
                        file_delta = batch_deltas[idx]
                        per_file_deltas[idx] = file_delta
                        if use_cache:
                            self.save_cached_delta(cache_key, file_delta)
                    else:
                        remaining_tasks.append(task)
            finally:
                if staging_dir is not None and staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                if used_pb_dir and not pb_dir_existed and pb_dir.exists() and not pb_dir.is_symlink():
                    try:
                        pb_dir.rmdir()
                    except OSError:
                        pass

        if remaining_tasks:
            if resolved_workers > 1 and len(remaining_tasks) >= 2:
                with concurrent.futures.ThreadPoolExecutor(max_workers=resolved_workers) as executor:
                    for idx, file_delta in executor.map(_extract_single_file, remaining_tasks):
                        per_file_deltas[idx] = file_delta
            else:
                for task in remaining_tasks:
                    idx, file_delta = _extract_single_file(task)
                    per_file_deltas[idx] = file_delta

        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        hyperedges: List[GraphHyperedge] = []
        seen_node_ids: Set[str] = set()
        seen_edge_keys: Set[Tuple[str, str, str]] = set()
        for file_delta in per_file_deltas:
            if file_delta is not None:
                for n in file_delta.nodes:
                    if n.id not in seen_node_ids:
                        seen_node_ids.add(n.id)
                        nodes.append(n)
                for e in file_delta.edges:
                    ek = (e.source, e.target, e.relation)
                    if ek not in seen_edge_keys:
                        seen_edge_keys.add(ek)
                        edges.append(e)
                hyperedges.extend(file_delta.hyperedges)

        # 3. Handle Tombstones for Deleted Files
        node_tombstones: List[str] = []
        edge_tombstones: List[Dict[str, Any]] = []
        if deleted_rel_paths:
            # Query existing local partial graph to find entity IDs that belong to deleted files
            existing_graph_file = ws_root / ".projectbrain" / "local_graph" / "partial_graph.json"
            if existing_graph_file.exists():
                try:
                    with open(existing_graph_file, "r", encoding="utf-8") as f:
                        eg_data = json.load(f)
                    del_set = set(deleted_rel_paths)
                    for n in eg_data.get("nodes", []):
                        if n.get("source_file") in del_set:
                            node_tombstones.append(n["id"])
                    del_nodes_set = set(node_tombstones)
                    for e in eg_data.get("edges", []):
                        if (
                            e.get("source_file") in del_set
                            or e.get("source") in del_nodes_set
                            or e.get("target") in del_nodes_set
                        ):
                            edge_tombstones.append(
                                {
                                    "source": e["source"],
                                    "target": e["target"],
                                    "relation": e.get("relation", ""),
                                }
                            )
                except Exception as e:
                    logger.debug("Failed parsing existing partial graph for tombstones: %s", e)

        delta = GraphDelta(
            project_id=project_id,
            branch=branch,
            nodes=nodes,
            edges=edges,
            hyperedges=hyperedges,
            node_tombstones=node_tombstones,
            edge_tombstones=edge_tombstones,
            hyperedge_tombstones=[],
            synced_files=synced_files,
            deleted_files=deleted_rel_paths,
            base_branch=base_branch,
        )

        eff_device, eff_engine = self._resolve_device_and_engine(device, semantic_engine)
        if semantic_pass and len(delta.nodes) >= 2:
            delta = self.compute_semantic_edges(
                delta,
                device=eff_device,
                semantic_engine=eff_engine,
            )
        else:
            meta = {
                "device_used": "cpu" if eff_device == "cpu" else eff_device,
                "backend_used": "threadpool-ast" if resolved_workers > 1 else "sequential-ast",
                "fallback_triggered": False,
                "workers_used": resolved_workers,
                "uncached_files": len(uncached_tasks),
            }
            delta.acceleration_meta = meta
            self.last_acceleration_meta = meta

        return delta

    def _discover_git_diff(self, ws_root: Path, base_branch: str) -> Tuple[List[Path], List[str]]:
        """Run `git diff --name-status <base_branch>...HEAD` and merge with uncommitted git dirty changes."""
        target_map: Dict[str, Path] = {}
        deleted_set: Set[str] = set()

        try:
            verify_res = subprocess.run(
                ["git", "rev-parse", "--verify", base_branch],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if verify_res.returncode != 0:
                return [], []

            diff_res = subprocess.run(
                ["git", "diff", "--name-status", f"{base_branch}...HEAD"],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if diff_res.returncode == 0:
                for line in diff_res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t")
                    status = parts[0].strip()
                    if not status:
                        continue
                    if status.startswith("R") and len(parts) >= 3:
                        old_rel = parts[1].strip().strip('"').replace("\\", "/")
                        new_rel = parts[2].strip().strip('"').replace("\\", "/")
                        if old_rel:
                            deleted_set.add(old_rel)
                        if new_rel:
                            p = ws_root / new_rel
                            if p.is_file():
                                target_map[new_rel] = p
                    elif status.startswith("C") and len(parts) >= 3:
                        new_rel = parts[2].strip().strip('"').replace("\\", "/")
                        if new_rel:
                            p = ws_root / new_rel
                            if p.is_file():
                                target_map[new_rel] = p
                    elif status.startswith("D") and len(parts) >= 2:
                        del_rel = parts[1].strip().strip('"').replace("\\", "/")
                        if del_rel:
                            deleted_set.add(del_rel)
                    elif status[0] in ("A", "M", "T", "C") and len(parts) >= 2:
                        rel = parts[1].strip().strip('"').replace("\\", "/")
                        if rel:
                            p = ws_root / rel
                            if p.is_file():
                                target_map[rel] = p
        except Exception as e:
            logger.debug("git diff failed against %s in %s: %s", base_branch, ws_root, e)

        dirty_targets, dirty_deleted = self._discover_git_dirty(ws_root)
        for d_rel in dirty_deleted:
            norm_d = d_rel.replace("\\", "/")
            deleted_set.add(norm_d)
            target_map.pop(norm_d, None)

        for p in dirty_targets:
            try:
                norm_rel = str(p.relative_to(ws_root)).replace("\\", "/")
            except ValueError:
                norm_rel = str(p).replace("\\", "/")
            if p.is_file():
                target_map[norm_rel] = p
                deleted_set.discard(norm_rel)

        target_files = [p for rel, p in target_map.items() if p.is_file() and rel not in deleted_set]
        return target_files, sorted(deleted_set)

    def _discover_git_dirty(self, ws_root: Path) -> Tuple[List[Path], List[str]]:
        """Run `git status --porcelain` to isolate dirty, added, and deleted files."""
        target_files: List[Path] = []
        deleted_rel_paths: List[str] = []

        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if len(line) < 4:
                        continue
                    status = line[:2]
                    raw_rel = line[3:].strip().strip('"')
                    # If rename, e.g. "old -> new"
                    if " -> " in raw_rel:
                        parts = raw_rel.split(" -> ")
                        deleted_rel_paths.append(parts[0].strip())
                        raw_rel = parts[1].strip()

                    if "D" in status:
                        deleted_rel_paths.append(raw_rel)
                    else:
                        target_path = ws_root / raw_rel
                        if target_path.is_file():
                            target_files.append(target_path)
        except Exception as e:
            logger.debug("git status failed in %s: %s", ws_root, e)

        return target_files, deleted_rel_paths

    # -----------------------------------------------------------------------
    # GPU / Vectorized BLAS / Local AI Acceleration Helpers
    # -----------------------------------------------------------------------
    def _resolve_device_and_engine(
        self,
        device: str = "auto",
        semantic_engine: str = "auto",
    ) -> Tuple[str, str]:
        eff_device = self.device if (device == "auto" and self.device != "auto") else (device or self.device)
        eff_engine = (
            self.semantic_engine
            if (semantic_engine == "auto" and self.semantic_engine != "auto")
            else (semantic_engine or self.semantic_engine)
        )
        return eff_device, eff_engine

    @staticmethod
    def _has_numpy() -> bool:
        try:
            import numpy as _np  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _extract_acc_meta(acc: Any, default_meta: Dict[str, Any]) -> Dict[str, Any]:
        if acc is None:
            return dict(default_meta)
        raw_meta = None
        if hasattr(acc, "get_acceleration_meta") and callable(acc.get_acceleration_meta):
            try:
                raw_meta = acc.get_acceleration_meta()
            except Exception:
                raw_meta = None
        elif hasattr(acc, "acceleration_meta") and isinstance(acc.acceleration_meta, dict):
            raw_meta = acc.acceleration_meta
        elif hasattr(acc, "info") and callable(acc.info):
            try:
                raw_meta = acc.info()
            except Exception:
                raw_meta = None

        meta = dict(default_meta)
        if isinstance(raw_meta, dict):
            meta.update(raw_meta)
        for attr, key in (
            ("device_used", "device_used"),
            ("device", "device_used"),
            ("backend_used", "backend_used"),
            ("backend", "backend_used"),
            ("fallback_triggered", "fallback_triggered"),
        ):
            val = getattr(acc, attr, None)
            if val is not None and key not in (raw_meta or {}):
                meta[key] = val
        return meta

    def _get_gpu_accelerator(
        self,
        device: str = "auto",
        semantic_engine: str = "auto",
    ) -> Tuple[Optional[Any], Dict[str, Any]]:
        eff_device, eff_engine = self._resolve_device_and_engine(device, semantic_engine)
        if eff_device == "cpu":
            return None, {
                "device_used": "cpu",
                "backend_used": "numpy_blas_cpu" if self._has_numpy() else "networkx-cpu",
                "fallback_triggered": False,
            }

        default_meta = {
            "device_used": "cpu",
            "backend_used": "numpy_blas_cpu" if self._has_numpy() else "networkx-cpu",
            "fallback_triggered": True,
        }
        try:
            from projectbrain.graphify_engine.gpu_accelerator import GraphGPUAccelerator  # type: ignore

            if hasattr(GraphGPUAccelerator, "resolve_compute_backend"):
                resolved = GraphGPUAccelerator.resolve_compute_backend(device=eff_device)
                meta = {
                    "device_used": resolved.get("device", "cpu"),
                    "backend_used": resolved.get("backend", "numpy_blas_cpu"),
                    "fallback_triggered": bool(resolved.get("fallback_triggered", False)),
                }
                return GraphGPUAccelerator, meta

            try:
                acc = GraphGPUAccelerator(device=eff_device, semantic_engine=eff_engine)
            except TypeError:
                try:
                    acc = GraphGPUAccelerator(device=eff_device)
                except TypeError:
                    acc = GraphGPUAccelerator()
            meta = self._extract_acc_meta(
                acc,
                {
                    "device_used": eff_device if eff_device != "auto" else "cpu",
                    "backend_used": "graph-gpu-accelerator",
                    "fallback_triggered": False,
                },
            )
            return acc, meta
        except ImportError as exc:
            logger.debug("GraphGPUAccelerator not installed/available (%s); falling back to CPU.", exc)
            return None, default_meta
        except Exception as exc:
            logger.debug("GraphGPUAccelerator initialization failed (%s); falling back to CPU.", exc)
            return None, default_meta

    @staticmethod
    def _compute_cohesion_scores(G: nx.Graph, communities: Dict[int, List[str]]) -> Dict[int, float]:
        scores: Dict[int, float] = {}
        for cid, members in communities.items():
            n_count = len(members)
            if n_count <= 1:
                scores[cid] = 1.0
            else:
                sub = G.subgraph(members)
                actual = sub.number_of_edges()
                possible = n_count * (n_count - 1) / 2
                scores[cid] = round(actual / possible, 2) if possible > 0 else 0.0
        return scores

    @staticmethod
    def _vectorized_pagerank_and_degree(
        G: nx.Graph,
        damping: float = 0.85,
        max_iter: int = 50,
        tol: float = 1e-6,
    ) -> Tuple[Dict[str, float], Dict[str, int]]:
        """Fast vectorized sparse/BLAS PageRank & degree centrality."""
        deg = {nid: int(d) for nid, d in G.degree()}
        nodes = list(G.nodes())
        n = len(nodes)
        if n == 0:
            return {}, deg
        try:
            import numpy as np

            idx_map = {nid: i for i, nid in enumerate(nodes)}
            row_idx: List[int] = []
            col_idx: List[int] = []
            weights: List[float] = []
            for u, v, data in G.edges(data=True):
                iu, iv = idx_map[u], idx_map[v]
                w = float(data.get("weight", 1.0) or 1.0)
                row_idx.extend((iu, iv))
                col_idx.extend((iv, iu))
                weights.extend((w, w))

            if not row_idx:
                uniform = 1.0 / n
                return {nid: uniform for nid in nodes}, deg

            try:
                import scipy.sparse as sp  # type: ignore

                adj = sp.csr_matrix((weights, (row_idx, col_idx)), shape=(n, n), dtype=np.float64)
                row_sums = np.asarray(adj.sum(axis=1)).ravel()
                inv_sums = np.where(row_sums > 0, 1.0 / row_sums, 0.0)
                trans = sp.diags(inv_sums) @ adj
                pr = np.full(n, 1.0 / n, dtype=np.float64)
                teleport = (1.0 - damping) / n
                for _ in range(max_iter):
                    dangling_sum = pr[row_sums == 0].sum()
                    next_pr = teleport + damping * (trans.T @ pr + dangling_sum / n)
                    if np.abs(next_pr - pr).sum() < tol:
                        pr = next_pr
                        break
                    pr = next_pr
            except ImportError:
                adj_dense = np.zeros((n, n), dtype=np.float64)
                for r, c, w in zip(row_idx, col_idx, weights):
                    adj_dense[r, c] += w
                row_sums = adj_dense.sum(axis=1)
                safe_sums = np.where(row_sums > 0, row_sums, 1.0)
                trans_dense = adj_dense / safe_sums[:, None]
                pr = np.full(n, 1.0 / n, dtype=np.float64)
                teleport = (1.0 - damping) / n
                for _ in range(max_iter):
                    dangling_sum = pr[row_sums == 0].sum()
                    next_pr = teleport + damping * (trans_dense.T @ pr + dangling_sum / n)
                    if np.abs(next_pr - pr).sum() < tol:
                        pr = next_pr
                        break
                    pr = next_pr

            return {nid: float(pr[i]) for i, nid in enumerate(nodes)}, deg
        except Exception:
            try:
                pr_nx = nx.pagerank(G, max_iter=max_iter, tol=tol)
                return {k: float(v) for k, v in pr_nx.items()}, deg
            except Exception:
                uniform = 1.0 / n
                return {nid: uniform for nid in nodes}, deg

    @staticmethod
    def _normalize_topk_matches(raw_matches: Any, n_queries: int) -> List[List[Tuple[int, float]]]:
        """Normalize various top-k cosine return shapes into List[List[(target_idx, score)]]."""
        if raw_matches is None:
            return [[] for _ in range(n_queries)]
        if isinstance(raw_matches, tuple) and len(raw_matches) == 2:
            indices, scores = raw_matches
            out: List[List[Tuple[int, float]]] = []
            for row_i, row_s in zip(indices, scores):
                row_pairs: List[Tuple[int, float]] = []
                for ti, sc in zip(row_i, row_s):
                    ti_int = int(ti)
                    if ti_int >= 0:
                        row_pairs.append((ti_int, float(sc)))
                out.append(row_pairs)
            return out
        if isinstance(raw_matches, list):
            if not raw_matches:
                return [[] for _ in range(n_queries)]
            first = raw_matches[0]
            if isinstance(first, list):
                return [[(int(ti), float(sc)) for ti, sc in row] for row in raw_matches]
            if isinstance(first, tuple) and len(first) == 3:
                grouped: List[List[Tuple[int, float]]] = [[] for _ in range(n_queries)]
                for qi, ti, sc in raw_matches:
                    if 0 <= int(qi) < n_queries:
                        grouped[int(qi)].append((int(ti), float(sc)))
                return grouped
        return [[] for _ in range(n_queries)]

    @staticmethod
    def _vectorized_tfidf_topk(
        texts: List[str],
        top_k: int = 5,
        min_similarity: float = 0.55,
    ) -> List[List[Tuple[int, float]]]:
        """Fast vectorized NumPy BLAS TF-IDF cosine similarity top-k."""
        n = len(texts)
        if n < 2:
            return [[] for _ in range(n)]
        try:
            import numpy as np
        except ImportError:
            return [[] for _ in range(n)]

        token_re = re.compile(r"[a-zA-Z0-9_]{2,}")
        doc_tokens: List[List[str]] = []
        df: Dict[str, int] = {}
        for txt in texts:
            toks = [t.lower() for t in token_re.findall(txt)]
            doc_tokens.append(toks)
            for t in set(toks):
                df[t] = df.get(t, 0) + 1

        if not df:
            return [[] for _ in range(n)]

        vocab = {tok: idx for idx, tok in enumerate(sorted(df.keys()))}
        mat = np.zeros((n, len(vocab)), dtype=np.float32)
        for i, toks in enumerate(doc_tokens):
            if not toks:
                continue
            for t in toks:
                j = vocab[t]
                mat[i, j] += 1.0
        idf = np.array([np.log((1.0 + n) / (1.0 + df[tok])) + 1.0 for tok in sorted(df.keys())], dtype=np.float32)
        mat *= idf[None, :]
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        mat /= norms

        sim = mat @ mat.T
        np.fill_diagonal(sim, -1.0)

        results: List[List[Tuple[int, float]]] = []
        k_lim = min(top_k, n - 1)
        for i in range(n):
            row = sim[i]
            if k_lim <= 0:
                results.append([])
                continue
            cand_idx = np.argpartition(row, -k_lim)[-k_lim:]
            cand_idx = cand_idx[np.argsort(row[cand_idx])[::-1]]
            pairs = [
                (int(j), float(row[j]))
                for j in cand_idx
                if float(row[j]) >= min_similarity
            ]
            results.append(pairs)
        return results

    @staticmethod
    def _local_ai_embed_texts(texts: List[str], engine: str) -> Optional[Any]:
        """Optional local AI embedding call (Ollama / vLLM / LM Studio) when explicitly requested."""
        norm = engine.lower().strip()
        if norm not in ("ollama", "vllm", "lmstudio", "local_ai"):
            return None
        try:
            import httpx
            import numpy as np

            if norm == "ollama":
                base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
                model = os.getenv("OM_OLLAMA_EMBED_MODEL") or os.getenv("OLLAMA_MODEL") or "nomic-embed-text"
                vecs: List[List[float]] = []
                with httpx.Client(timeout=5.0) as client:
                    for txt in texts:
                        resp = client.post(f"{base_url}/api/embeddings", json={"model": model, "prompt": txt})
                        if resp.status_code != 200:
                            return None
                        emb = resp.json().get("embedding")
                        if not emb:
                            return None
                        vecs.append(emb)
                return np.asarray(vecs, dtype=np.float32)
            else:
                base_url = os.getenv("LOCAL_AI_URL", "http://localhost:1234/v1").rstrip("/")
                model = os.getenv("LOCAL_AI_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(f"{base_url}/embeddings", json={"model": model, "input": texts})
                    if resp.status_code != 200:
                        return None
                    data = resp.json().get("data", [])
                    if len(data) != len(texts):
                        return None
                    return np.asarray([d["embedding"] for d in data], dtype=np.float32)
        except Exception as exc:
            logger.debug("Local AI embedding (%s) unavailable: %s", engine, exc)
            return None

    # -----------------------------------------------------------------------
    # Optional GPU / Local AI Semantic Similarity Pass
    # -----------------------------------------------------------------------
    def compute_semantic_edges(
        self,
        delta: GraphDelta,
        top_k: int = 5,
        min_similarity: float = 0.55,
        device: str = "auto",
        semantic_engine: str = "auto",
        cross_domain_only: bool = False,
    ) -> GraphDelta:
        """
        Compute semantic similarity edges across code and document nodes using
        GraphGPUAccelerator (`embed_texts` + `cosine_similarity_topk`), optional Local AI
        embeddings (`Ollama`/`vLLM`/`LM Studio`), or vectorized NumPy BLAS TF-IDF fallback.
        """
        if len(delta.nodes) < 2:
            return delta

        eff_device, eff_engine = self._resolve_device_and_engine(device, semantic_engine)
        acc, acc_meta = self._get_gpu_accelerator(device=eff_device, semantic_engine=eff_engine)

        texts = [
            " ".join(
                str(part)
                for part in (
                    n.label,
                    n.file_type,
                    n.source_file,
                    n.metadata.get("kind", ""),
                    n.metadata.get("title", ""),
                    n.metadata.get("docstring", ""),
                )
                if part
            )
            for n in delta.nodes
        ]

        matches: Optional[List[List[Tuple[int, float]]]] = None
        if acc is not None and hasattr(acc, "embed_texts") and hasattr(acc, "cosine_similarity_topk"):
            try:
                try:
                    emb_res = acc.embed_texts(texts, engine=eff_engine, device=eff_device)
                except TypeError:
                    emb_res = acc.embed_texts(texts)

                emb_meta: Dict[str, Any] = {}
                if isinstance(emb_res, tuple) and len(emb_res) == 2 and isinstance(emb_res[1], dict):
                    embeddings, emb_meta = emb_res
                else:
                    embeddings = emb_res

                try:
                    raw_sim = acc.cosine_similarity_topk(
                        embeddings,
                        top_k=min(top_k + 1, len(texts)),
                        min_score=min_similarity,
                        device=eff_device,
                    )
                except TypeError:
                    try:
                        raw_sim = acc.cosine_similarity_topk(
                            embeddings,
                            embeddings,
                            top_k=min(top_k + 1, len(texts)),
                            threshold=min_similarity,
                        )
                    except TypeError:
                        raw_sim = acc.cosine_similarity_topk(embeddings, min(top_k + 1, len(texts)))

                sim_meta: Dict[str, Any] = {}
                if isinstance(raw_sim, tuple) and len(raw_sim) == 2 and isinstance(raw_sim[1], dict):
                    raw_pairs, sim_meta = raw_sim
                else:
                    raw_pairs = raw_sim

                matches = self._normalize_topk_matches(raw_pairs, len(texts))
                acc_meta = {
                    "device_used": sim_meta.get("device_used") or emb_meta.get("device_used") or acc_meta.get("device_used", "cpu"),
                    "backend_used": sim_meta.get("backend_used") or emb_meta.get("engine_used") or acc_meta.get("backend_used", "graph-gpu-accelerator"),
                    "fallback_triggered": bool(
                        emb_meta.get("fallback_triggered", False)
                        or sim_meta.get("fallback_triggered", False)
                        or acc_meta.get("fallback_triggered", False)
                    ),
                }
            except Exception as exc:
                logger.debug("GPU semantic similarity failed (%s); falling back to BLAS/LocalAI.", exc)
                acc_meta["fallback_triggered"] = True
                matches = None

        if matches is None and eff_engine.lower() in ("ollama", "vllm", "lmstudio", "local_ai"):
            local_vecs = self._local_ai_embed_texts(texts, eff_engine)
            if local_vecs is not None:
                try:
                    import numpy as np

                    norms = np.linalg.norm(local_vecs, axis=1, keepdims=True)
                    norms = np.where(norms > 0, norms, 1.0)
                    normed = local_vecs / norms
                    sim = normed @ normed.T
                    np.fill_diagonal(sim, -1.0)
                    k_lim = min(top_k, len(texts) - 1)
                    matches = []
                    for i in range(len(texts)):
                        row = sim[i]
                        cand_idx = np.argpartition(row, -k_lim)[-k_lim:]
                        cand_idx = cand_idx[np.argsort(row[cand_idx])[::-1]]
                        matches.append([(int(j), float(row[j])) for j in cand_idx if float(row[j]) >= min_similarity])
                    acc_meta = {
                        "device_used": eff_engine.lower(),
                        "backend_used": f"local-ai-{eff_engine.lower()}",
                        "fallback_triggered": False,
                    }
                except Exception:
                    matches = None

        if matches is None:
            matches = self._vectorized_tfidf_topk(texts, top_k=top_k, min_similarity=min_similarity)
            acc_meta = {
                "device_used": "cpu",
                "backend_used": "numpy-blas-tfidf" if self._has_numpy() else "cpu",
                "fallback_triggered": eff_device not in ("auto", "cpu"),
            }

        existing_pairs: Set[Tuple[str, str]] = {(e.source, e.target) for e in delta.edges}
        seen_sem: Set[Tuple[str, str]] = set()
        for src_idx, neighs in enumerate(matches):
            src_node = delta.nodes[src_idx]
            emitted = 0
            for tgt_idx, score in neighs:
                if tgt_idx == src_idx or not (0 <= tgt_idx < len(delta.nodes)):
                    continue
                if score < min_similarity:
                    continue
                tgt_node = delta.nodes[tgt_idx]
                if cross_domain_only and src_node.file_type == tgt_node.file_type:
                    continue
                pair = (src_node.id, tgt_node.id)
                if pair in existing_pairs or pair in seen_sem:
                    continue
                seen_sem.add(pair)
                clamped = round(min(1.0, max(0.0, float(score))), 4)
                delta.edges.append(
                    GraphEdge(
                        project_id=delta.project_id,
                        source=src_node.id,
                        target=tgt_node.id,
                        relation="semantically_similar_to",
                        confidence="INFERRED",
                        confidence_score=clamped,
                        source_file=src_node.source_file,
                        weight=clamped,
                    )
                )
                emitted += 1
                if emitted >= top_k:
                    break

        delta.acceleration_meta = acc_meta
        self.last_acceleration_meta = acc_meta
        return delta

    # -----------------------------------------------------------------------
    # Graph Clustering & Cohesion Analysis
    # -----------------------------------------------------------------------
    def cluster_graph(
        self,
        delta: GraphDelta,
        method: str = "leiden",
        device: str = "auto",
        workers: Optional[int] = None,
        semantic_engine: str = "auto",
    ) -> GraphDelta:
        """
        Apply community detection (GPU-accelerated via GraphGPUAccelerator when available,
        or Leiden via graspologic / Louvain fallback) to the GraphDelta, calculating cohesion
        scores and assigning community IDs to nodes.
        """
        eff_device, eff_engine = self._resolve_device_and_engine(device, semantic_engine)
        if not delta.nodes:
            meta = {
                "device_used": "cpu",
                "backend_used": "noop",
                "fallback_triggered": False,
            }
            delta.acceleration_meta = meta
            self.last_acceleration_meta = meta
            return delta

        G = nx.Graph()
        for n in delta.nodes:
            G.add_node(n.id, label=n.label, file_type=n.file_type, source_file=n.source_file)
        for e in delta.edges:
            G.add_edge(e.source, e.target, relation=e.relation, weight=e.weight)

        communities: Dict[int, List[str]] = {}
        scores: Dict[int, float] = {}
        acc, acc_meta = self._get_gpu_accelerator(device=eff_device, semantic_engine=eff_engine)

        if acc is not None:
            if hasattr(acc, "accelerated_clustering") and callable(acc.accelerated_clustering):
                try:
                    node_ids = [str(n.id) for n in delta.nodes]
                    edge_tuples = [
                        (str(e.source), str(e.target), float(e.weight or 1.0))
                        for e in delta.edges
                    ]
                    raw_res = acc.accelerated_clustering(
                        node_ids,
                        edge_tuples,
                        method=method,
                        device=eff_device,
                    )
                    if isinstance(raw_res, dict):
                        for comm in raw_res.get("communities", []):
                            cid = int(comm["cluster_id"])
                            communities[cid] = list(comm.get("nodes", []))
                        if not communities and isinstance(raw_res.get("clusters"), dict):
                            for nid, cid in raw_res["clusters"].items():
                                communities.setdefault(int(cid), []).append(str(nid))
                        scores = {int(k): float(v) for k, v in (raw_res.get("cohesion") or {}).items()}
                        acc_meta = {
                            "device_used": raw_res.get("device_used", acc_meta.get("device_used", "cpu")),
                            "backend_used": raw_res.get("backend_used", acc_meta.get("backend_used", "accelerated_clustering")),
                            "fallback_triggered": bool(
                                raw_res.get("fallback_triggered", False)
                                or acc_meta.get("fallback_triggered", False)
                            ),
                        }
                except Exception as exc:
                    logger.debug("GraphGPUAccelerator.accelerated_clustering failed (%s); falling back to CPU.", exc)
                    communities = {}
                    acc_meta["fallback_triggered"] = True
            else:
                cluster_fn = (
                    getattr(acc, "cluster_graph", None)
                    or getattr(acc, "detect_communities", None)
                    or getattr(acc, "leiden_communities", None)
                    or getattr(acc, "louvain_communities", None)
                    or getattr(acc, "cluster", None)
                )
                if callable(cluster_fn):
                    try:
                        try:
                            raw_res = cluster_fn(G, method=method)
                        except TypeError:
                            raw_res = cluster_fn(G)

                        if isinstance(raw_res, tuple) and len(raw_res) == 2 and isinstance(raw_res[0], dict):
                            communities, scores = raw_res[0], raw_res[1]
                        elif isinstance(raw_res, dict) and "communities" in raw_res:
                            communities = raw_res["communities"]
                            scores = raw_res.get("scores", {})
                            if isinstance(raw_res.get("acceleration_meta"), dict):
                                acc_meta.update(raw_res["acceleration_meta"])
                        elif isinstance(raw_res, dict):
                            if raw_res and all(isinstance(v, (list, set, tuple)) for v in raw_res.values()):
                                communities = {int(k): sorted(list(v)) for k, v in raw_res.items()}
                            elif raw_res and all(isinstance(v, int) for v in raw_res.values()):
                                for nid, cid in raw_res.items():
                                    communities.setdefault(int(cid), []).append(str(nid))
                        acc_meta = self._extract_acc_meta(acc, acc_meta)
                    except Exception as exc:
                        logger.debug("GraphGPUAccelerator clustering failed (%s); falling back to CPU.", exc)
                        communities = {}
                        acc_meta["fallback_triggered"] = True

        if not communities:
            if eff_device != "cpu":
                acc_meta["fallback_triggered"] = True
            try:
                import graphify.cluster as g_cluster  # type: ignore

                communities = g_cluster.cluster(G)
                scores = g_cluster.score_all(G, communities)
                acc_meta.setdefault("backend_used", "graphify-cluster")
                if acc_meta.get("device_used") != "cpu":
                    acc_meta["device_used"] = "cpu"
            except Exception as e:
                logger.debug("graphify.cluster error: %s. Using internal Louvain.", e)
                try:
                    raw_comm = nx.community.louvain_communities(G, seed=42)
                    communities = {i: sorted(list(c)) for i, c in enumerate(raw_comm)}
                    acc_meta["backend_used"] = "networkx-louvain"
                except Exception:
                    communities = {0: sorted(list(G.nodes()))}
                    acc_meta["backend_used"] = "networkx-fallback"
                acc_meta["device_used"] = "cpu"

        if not scores:
            scores = self._compute_cohesion_scores(G, communities)

        # Invert community map: node_id -> cid
        node_to_cid: Dict[str, int] = {}
        for cid, members in communities.items():
            for m in members:
                node_to_cid[m] = cid

        for node in delta.nodes:
            cid = node_to_cid.get(node.id)
            node.community = cid
            if cid is not None:
                node.metadata["cohesion_score"] = scores.get(cid, 1.0)

        delta.acceleration_meta = acc_meta
        self.last_acceleration_meta = acc_meta
        return delta

    # -----------------------------------------------------------------------
    # God Nodes & Surprising Connections
    # -----------------------------------------------------------------------
    def compute_god_nodes_and_bridges(
        self,
        delta: GraphDelta,
        top_k: int = 10,
        device: str = "auto",
        workers: Optional[int] = None,
        semantic_engine: str = "auto",
    ) -> Dict[str, Any]:
        """
        Isolate architectural core God nodes (GPU/vectorized PageRank & degree centrality,
        excluding file hubs) and cross-community bridge edges (surprising connections).
        """
        eff_device, eff_engine = self._resolve_device_and_engine(device, semantic_engine)
        G = nx.Graph()
        for n in delta.nodes:
            G.add_node(
                n.id,
                label=n.label,
                file_type=n.file_type,
                source_file=n.source_file,
                community=n.community,
            )
        for e in delta.edges:
            G.add_edge(
                e.source,
                e.target,
                relation=e.relation,
                confidence=e.confidence,
                weight=e.weight,
            )

        god_nodes_res: List[Dict[str, Any]] = []
        surprising_res: List[Dict[str, Any]] = []
        acc, acc_meta = self._get_gpu_accelerator(device=eff_device, semantic_engine=eff_engine)

        pagerank_map: Dict[str, float] = {}
        if acc is not None and G.number_of_nodes() > 0:
            if hasattr(acc, "accelerated_pagerank_and_centrality") and callable(acc.accelerated_pagerank_and_centrality):
                try:
                    node_ids = [str(n.id) for n in delta.nodes]
                    edge_tuples = [
                        (str(e.source), str(e.target), float(e.weight or 1.0))
                        for e in delta.edges
                    ]
                    pr_res = acc.accelerated_pagerank_and_centrality(
                        node_ids,
                        edge_tuples,
                        device=eff_device,
                    )
                    if isinstance(pr_res, dict):
                        pagerank_map = {str(k): float(v) for k, v in (pr_res.get("pagerank") or {}).items()}
                        acc_meta = {
                            "device_used": pr_res.get("device_used", acc_meta.get("device_used", "cpu")),
                            "backend_used": pr_res.get("backend_used", acc_meta.get("backend_used", "accelerated_pagerank")),
                            "fallback_triggered": bool(
                                pr_res.get("fallback_triggered", False)
                                or acc_meta.get("fallback_triggered", False)
                            ),
                        }
                except Exception as exc:
                    logger.debug("GraphGPUAccelerator.accelerated_pagerank_and_centrality failed (%s); falling back.", exc)
                    acc_meta["fallback_triggered"] = True
            else:
                god_fn = (
                    getattr(acc, "compute_god_nodes", None)
                    or getattr(acc, "god_nodes", None)
                    or getattr(acc, "compute_god_nodes_and_bridges", None)
                )
                pr_fn = getattr(acc, "pagerank", None) or getattr(acc, "compute_pagerank", None)
                if callable(god_fn):
                    try:
                        try:
                            raw_god = god_fn(G, top_k=top_k)
                        except TypeError:
                            raw_god = god_fn(G)
                        if isinstance(raw_god, dict) and "god_nodes" in raw_god:
                            god_nodes_res = list(raw_god.get("god_nodes", []))[:top_k]
                            surprising_res = list(raw_god.get("surprising_connections", []))[:top_k]
                            if isinstance(raw_god.get("acceleration_meta"), dict):
                                acc_meta.update(raw_god["acceleration_meta"])
                        elif isinstance(raw_god, list):
                            god_nodes_res = list(raw_god)[:top_k]
                        acc_meta = self._extract_acc_meta(acc, acc_meta)
                    except Exception as exc:
                        logger.debug("GraphGPUAccelerator god_nodes failed (%s); falling back.", exc)
                        god_nodes_res = []
                        acc_meta["fallback_triggered"] = True
                elif callable(pr_fn):
                    try:
                        raw_pr = pr_fn(G)
                        if isinstance(raw_pr, dict):
                            pagerank_map = {str(k): float(v) for k, v in raw_pr.items()}
                        acc_meta = self._extract_acc_meta(acc, acc_meta)
                    except Exception as exc:
                        logger.debug("GraphGPUAccelerator pagerank failed (%s); falling back.", exc)
                        acc_meta["fallback_triggered"] = True

        # Ensure normalized god_nodes format when returned from accelerator
        if god_nodes_res:
            deg_map = dict(G.degree())
            normalized_gods: List[Dict[str, Any]] = []
            for item in god_nodes_res:
                if not isinstance(item, dict) or "id" not in item:
                    continue
                nid = item["id"]
                data = G.nodes[nid] if nid in G.nodes else {}
                if data.get("file_type") == "document" and str(data.get("label", "")).endswith(".md"):
                    continue
                entry = dict(item)
                entry.setdefault("label", data.get("label", nid))
                entry.setdefault("edges", int(deg_map.get(nid, 0)))
                normalized_gods.append(entry)
            god_nodes_res = normalized_gods[:top_k]

        if not god_nodes_res and G.number_of_nodes() > 0:
            if not pagerank_map:
                try:
                    import graphify.analyze as g_analyze  # type: ignore

                    god_nodes_res = g_analyze.god_nodes(G, top_n=top_k)
                except Exception:
                    pass

            if not god_nodes_res:
                if not pagerank_map:
                    pagerank_map, deg = self._vectorized_pagerank_and_degree(G)
                    if eff_device != "cpu":
                        acc_meta["fallback_triggered"] = True
                    acc_meta.setdefault(
                        "backend_used",
                        "numpy-blas-pagerank" if self._has_numpy() else "networkx-cpu",
                    )
                    acc_meta["device_used"] = "cpu"
                else:
                    deg = {nid: int(d) for nid, d in G.degree()}

                ranked = sorted(
                    deg.items(),
                    key=lambda x: (x[1], pagerank_map.get(x[0], 0.0)),
                    reverse=True,
                )
                for nid, d in ranked:
                    data = G.nodes[nid]
                    if data.get("file_type") == "document" and str(data.get("label", "")).endswith(".md"):
                        continue
                    god_nodes_res.append(
                        {
                            "id": nid,
                            "label": data.get("label", nid),
                            "edges": d,
                            "pagerank": round(pagerank_map.get(nid, 0.0), 6),
                        }
                    )
                    if len(god_nodes_res) >= top_k:
                        break

        # Re-derive communities for surprise calculation if not already assigned
        comm_map: Dict[int, List[str]] = {}
        for n in delta.nodes:
            if n.community is not None:
                comm_map.setdefault(n.community, []).append(n.id)

        if not comm_map and G.number_of_nodes() > 0:
            try:
                import graphify.cluster as g_cluster  # type: ignore

                comm_map = g_cluster.cluster(G)
            except Exception:
                comm_map = {0: list(G.nodes())}

        if not surprising_res:
            try:
                import graphify.analyze as g_analyze  # type: ignore

                surprising_res = g_analyze.surprising_connections(G, communities=comm_map, top_n=top_k)
            except Exception:
                pass

        # Fallback surprising connections: edges bridging different non-zero communities
        if not surprising_res and len(comm_map) > 1:
            node_to_comm = {nid: cid for cid, nids in comm_map.items() for nid in nids}
            for u, v, dat in G.edges(data=True):
                cu, cv = node_to_comm.get(u), node_to_comm.get(v)
                if cu is not None and cv is not None and cu != cv:
                    surprising_res.append(
                        {
                            "source": G.nodes[u].get("label", u),
                            "target": G.nodes[v].get("label", v),
                            "relation": dat.get("relation", "relates"),
                            "confidence": dat.get("confidence", "INFERRED"),
                            "why": f"Cross-community bridge (community {cu} -> {cv})",
                        }
                    )
                    if len(surprising_res) >= top_k:
                        break

        # Compute cohesion per community
        try:
            import graphify.cluster as g_cluster  # type: ignore

            scores = g_cluster.score_all(G, comm_map)
        except Exception:
            scores = self._compute_cohesion_scores(G, comm_map)

        self.last_acceleration_meta = acc_meta
        return {
            "god_nodes": god_nodes_res,
            "surprising_connections": surprising_res,
            "communities": {
                cid: {
                    "node_count": len(members),
                    "cohesion_score": scores.get(cid, 1.0),
                    "nodes": members,
                }
                for cid, members in comm_map.items()
            },
            "total_nodes": len(delta.nodes),
            "total_edges": len(delta.edges),
            "total_hyperedges": len(delta.hyperedges),
            "acceleration_meta": acc_meta,
        }

    # -----------------------------------------------------------------------
    # Local Partial Graph Persistence
    # -----------------------------------------------------------------------
    def save_local_partial_graph(
        self,
        delta: GraphDelta,
        output_dir: str = ".projectbrain/local_graph",
        merge_existing: bool = True,
    ) -> str:
        """
        Save localized partial subgraph to `.projectbrain/local_graph/partial_graph.json`
        and update manifest.json.

        When `merge_existing=True`:
        - Merges new/modified nodes.
        - Deletes nodes matching `node_tombstones`.
        - Merges edges, removing those matching `edge_tombstones` or referencing tombstoned nodes.
        - Accumulates `synced_files`.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        graph_file = out_path / "partial_graph.json"
        manifest_file = out_path / "manifest.json"

        merged_nodes_map: Dict[str, Dict[str, Any]] = {}
        merged_edges_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        merged_hyper_map: Dict[str, Dict[str, Any]] = {}
        merged_synced_map: Dict[str, str] = {}

        if merge_existing and graph_file.exists():
            try:
                with open(graph_file, "r", encoding="utf-8") as gf:
                    existing = json.load(gf)
                for n in existing.get("nodes", []):
                    merged_nodes_map[n["id"]] = n
                for e in existing.get("edges", []):
                    key = (e["source"], e["target"], e.get("relation", ""))
                    merged_edges_map[key] = e
                for h in existing.get("hyperedges", []):
                    merged_hyper_map[h["id"]] = h
                for f in existing.get("synced_files", []):
                    merged_synced_map[f["file_path"]] = f.get("sha256", "")
            except Exception as e:
                logger.debug("Failed loading existing graph for merge: %s", e)

        # 1. Apply Tombstones
        for tid in delta.node_tombstones:
            merged_nodes_map.pop(tid, None)

        for et in delta.edge_tombstones:
            k = (et.get("source", ""), et.get("target", ""), et.get("relation", ""))
            merged_edges_map.pop(k, None)

        for ht in delta.hyperedge_tombstones:
            merged_hyper_map.pop(ht, None)

        # Remove edges pointing to deleted nodes
        deleted_node_ids = set(delta.node_tombstones)
        if deleted_node_ids:
            merged_edges_map = {
                k: v
                for k, v in merged_edges_map.items()
                if k[0] not in deleted_node_ids and k[1] not in deleted_node_ids
            }

        # 2. Upsert New / Modified Entities
        for n in delta.nodes:
            payload = n.to_dict() if hasattr(n, "to_dict") else n.model_dump()
            merged_nodes_map[n.id] = payload

        for e in delta.edges:
            k = (e.source, e.target, e.relation)
            payload = e.to_dict() if hasattr(e, "to_dict") else e.model_dump()
            merged_edges_map[k] = payload

        for h in delta.hyperedges:
            payload = h.to_dict() if hasattr(h, "to_dict") else h.model_dump()
            merged_hyper_map[h.id] = payload

        for sf in delta.synced_files:
            merged_synced_map[sf["file_path"]] = sf.get("sha256", "")

        # 3. Write partial_graph.json
        final_payload = {
            "project_id": delta.project_id,
            "branch": delta.branch,
            "nodes": list(merged_nodes_map.values()),
            "edges": list(merged_edges_map.values()),
            "hyperedges": list(merged_hyper_map.values()),
            "node_tombstones": delta.node_tombstones,
            "edge_tombstones": delta.edge_tombstones,
            "hyperedge_tombstones": delta.hyperedge_tombstones,
            "synced_files": [
                {"file_path": fp, "sha256": hsh} for fp, hsh in merged_synced_map.items()
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        tmp_graph = graph_file.with_suffix(".tmp")
        with open(tmp_graph, "w", encoding="utf-8") as f:
            json.dump(final_payload, f, indent=2)
        os.replace(tmp_graph, graph_file)

        # 4. Update manifest.json
        manifest_payload = {
            "project_id": delta.project_id,
            "branch": delta.branch,
            "files": {
                fp: {"sha256": hsh, "updated_at": datetime.now(timezone.utc).isoformat()}
                for fp, hsh in merged_synced_map.items()
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_mf = manifest_file.with_suffix(".tmp")
        with open(tmp_mf, "w", encoding="utf-8") as f:
            json.dump(manifest_payload, f, indent=2)
        os.replace(tmp_mf, manifest_file)

        return str(graph_file.resolve())
