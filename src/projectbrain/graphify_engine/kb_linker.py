"""
Hierarchical Structure-Aware Chunker, Multi-Signal Cross-Linker, and Unified KB Builder.

Part of Hybrid Graphify Engine v1.5.0 (PLAN-UNIFIED-KB-02).
Implements AC-KB-03 through AC-KB-09:
- Hierarchical document chunking preserving code fences and markdown tables
- Multi-key symbol, route, MCP, and CLI indexer
- Bidirectional cross-linking (EXTRACTED 1.0, INFERRED 0.60-0.90)
- Code-doc drift detection (signature mismatch, removed symbols, mtime)
- HSG cognitive memory and associative waypoints persistence
- Unified traceability inspector (trace_code_and_docs)
- Repository-wide coverage analytics (get_kb_coverage_report)
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from projectbrain.graphify_engine.models import (
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)
from projectbrain.graphify_engine.store import GraphStore, get_graph_store
from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.graphify_engine.doc_pipeline import (
    DocumentPipeline,
    OFFICE_EXTENSIONS,
    PAPER_EXTENSIONS,
)
from projectbrain.memory.hsg import add_hsg_memory
from projectbrain.core.db import db
from projectbrain.core.config import env

logger = logging.getLogger("kb_linker")
logger.setLevel(logging.INFO)

# High-frequency generic words excluded from unbackticked heuristic linking
GENERIC_STOP_WORDS: Set[str] = {
    "get", "set", "id", "run", "main", "app", "db", "data", "test",
    "to", "is", "as", "in", "for", "on", "all", "out", "by", "or",
    "and", "if", "not", "new", "old", "key", "val", "item", "node",
    "edge", "list", "dict", "str", "int", "bool", "file", "path",
    "type", "name", "root", "base", "args", "kwargs", "self", "cls",
    "true", "false", "none", "def", "class", "from", "import", "return",
    "with", "open", "read", "write", "close", "save", "load", "find",
}


class DocChunk(BaseModel):
    """Hierarchically scoped document segment."""
    chunk_id: str
    doc_node_id: str
    doc_path: str
    heading_breadcrumb: str  # e.g., "Architecture Overview > Storage > PostgreSQL Multi-Schema"
    level: int               # Heading depth (1, 2, 3...)
    start_line: int
    end_line: int
    content: str
    sha256: str
    tokens: int
    sector: str = "semantic"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CodeSymbolRef(BaseModel):
    """Reference index item for AST code & test symbols."""
    node_id: str
    symbol_name: str
    symbol_type: str         # "class" | "function" | "method" | "route" | "mcp_tool" | "cli" | "test"
    file_path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    signature: Optional[str] = None
    community_id: Optional[int] = None
    docstring: Optional[str] = None
    mtime: Optional[float] = None
    sha256: Optional[str] = None


class CrossLinkEdge(BaseModel):
    """Bidirectional cross-domain relationship."""
    source_id: str
    target_id: str
    relation: str            # "documents_symbol" | "documented_in" | "specifies_endpoint" | "verifies_symbol" | "verifies_requirement" | "conceptually_related_to"
    confidence: str          # "EXTRACTED" | "INFERRED"
    confidence_score: float  # 1.0 for EXTRACTED, 0.60-0.90 for INFERRED
    evidence: str            # e.g., "Explicit symbol backtick mention in heading"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CodeDocDrift(BaseModel):
    """Detected misalignment between documentation and code."""
    symbol_id: str
    doc_chunk_id: str
    drift_type: str          # "code_newer_than_doc" | "signature_mismatch" | "removed_symbol_in_doc"
    severity: str            # "high" | "medium" | "low"
    detail: str
    source_file: str
    doc_path: str


class KBTraceResult(BaseModel):
    """Unified inspection output for any target query."""
    query: str
    code_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    doc_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    tests: List[Dict[str, Any]] = Field(default_factory=list)
    callers: List[str] = Field(default_factory=list)
    callees: List[str] = Field(default_factory=list)
    coverage_status: str     # "documented" | "inferred" | "undocumented"
    drift: List[CodeDocDrift] = Field(default_factory=list)


class KBCoverageReport(BaseModel):
    """Repository-wide documentation & test traceability analytics."""
    project_id: str
    branch: str = "main"
    total_symbols: int = 0
    documented_symbols: int = 0
    documented_pct: float = 0.0
    inferred_symbols: int = 0
    inferred_pct: float = 0.0
    undocumented_symbols: int = 0
    undocumented_pct: float = 0.0
    api_coverage_pct: float = 0.0
    mcp_coverage_pct: float = 0.0
    test_linked_symbols: int = 0
    test_linked_pct: float = 0.0
    total_doc_chunks: int = 0
    cross_domain_edges: int = 0
    cross_source_edges: int = 0
    cross_source_drifted_count: int = 0
    cross_source_drift_items: List[Dict[str, Any]] = Field(default_factory=list)
    orphan_docs: List[str] = Field(default_factory=list)
    undocumented_key_symbols: List[Dict[str, Any]] = Field(default_factory=list)
    drift_items: List[CodeDocDrift] = Field(default_factory=list)


class CodeDocLinker:
    """
    Hierarchical document chunker, symbol indexer, and multi-signal linking engine.
    """

    def __init__(
        self,
        workspace_path: Union[str, Path] = ".",
        device: str = "auto",
        ocr_device: str = "auto",
        ocr_engine: str = "auto",
        semantic_engine: str = "auto",
    ):
        self.workspace_path = Path(workspace_path).resolve()
        self.device = device
        self.ocr_device = ocr_device
        self.ocr_engine = ocr_engine
        self.semantic_engine = semantic_engine

    def chunk_markdown_document(
        self,
        doc_path: str,
        content: str,
        doc_node_id: Optional[str] = None,
        max_chunk_chars: int = 3000,
    ) -> List[DocChunk]:
        """
        Structure-aware Markdown chunker:
        - Splits by H1, H2, H3, H4 breadcrumb headers.
        - Preserves code fences (``` ... ``` or ~~~ ... ~~~) and Markdown tables (| ... |) without splitting.
        - Sub-chunks large sections (> max_chunk_chars) only at paragraph boundaries outside fences/tables.
        - Calculates lines, tokens, and deterministic SHA256 hashes for idempotent deduplication.
        """
        if not content or not content.strip():
            return []

        clean_doc_path = str(doc_path).replace("\\", "/")
        fallback_title = Path(clean_doc_path).stem.replace("_", " ").title()
        doc_id = doc_node_id or f"doc:{clean_doc_path}"

        lines = content.splitlines(keepends=True)

        # First pass: identify line intervals that are protected (code blocks and tables)
        # 1-based indexing for lines
        protected_lines: Set[int] = set()
        table_lines: Set[int] = set()

        in_fence = False
        fence_marker = ""

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Code fence toggle
            if stripped.startswith("```") or stripped.startswith("~~~"):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                    protected_lines.add(idx)
                else:
                    if marker == fence_marker:
                        in_fence = False
                        fence_marker = ""
                        protected_lines.add(idx)
                    else:
                        protected_lines.add(idx)
            elif in_fence:
                protected_lines.add(idx)

            # Table line check
            if not in_fence and stripped.startswith("|") and stripped.endswith("|"):
                table_lines.add(idx)

        # Second pass: group into heading-bounded sections
        heading_re = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?$")
        breadcrumb_stack: List[Tuple[int, str]] = []

        class Section:
            def __init__(self, breadcrumb: str, level: int, start_line: int):
                self.breadcrumb = breadcrumb
                self.level = level
                self.start_line = start_line
                self.end_line = start_line
                self.lines: List[Tuple[int, str]] = []

        sections: List[Section] = []
        current_section = Section(fallback_title, 1, 1)

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            if idx not in protected_lines and idx not in table_lines:
                match = heading_re.match(stripped)
                if match:
                    depth = len(match.group(1))
                    title = match.group(2).strip()

                    # Pop headers deeper than or equal to current depth
                    while breadcrumb_stack and breadcrumb_stack[-1][0] >= depth:
                        breadcrumb_stack.pop()
                    breadcrumb_stack.append((depth, title))

                    new_breadcrumb = " > ".join(t for _, t in breadcrumb_stack)

                    # Flush current section if it has content
                    if current_section.lines:
                        sections.append(current_section)

                    current_section = Section(new_breadcrumb, depth, idx)

            current_section.lines.append((idx, line))
            current_section.end_line = idx

        if current_section.lines:
            sections.append(current_section)

        # Third pass: produce DocChunks, sub-chunking sections exceeding max_chunk_chars
        chunks: List[DocChunk] = []

        for sec in sections:
            sec_text = "".join(l for _, l in sec.lines)
            if len(sec_text) <= max_chunk_chars:
                # Direct chunk
                start_l = sec.lines[0][0]
                end_l = sec.lines[-1][0]
                chunk_hash = hashlib.sha256(sec_text.encode("utf-8")).hexdigest()
                cid_seed = f"{clean_doc_path}:{start_l}:{chunk_hash[:12]}"
                chunk_id = f"chunk_{hashlib.sha256(cid_seed.encode('utf-8')).hexdigest()[:16]}"
                tokens = max(1, len(sec_text.split()))

                chunks.append(
                    DocChunk(
                        chunk_id=chunk_id,
                        doc_node_id=doc_id,
                        doc_path=clean_doc_path,
                        heading_breadcrumb=sec.breadcrumb,
                        level=sec.level,
                        start_line=start_l,
                        end_line=end_l,
                        content=sec_text,
                        sha256=chunk_hash,
                        tokens=tokens,
                        sector="semantic",
                        metadata={
                            "doc_path": clean_doc_path,
                            "heading_breadcrumb": sec.breadcrumb,
                            "level": sec.level,
                            "start_line": start_l,
                            "end_line": end_l,
                            "tokens": tokens,
                            "sha256": chunk_hash,
                        },
                    )
                )
            else:
                # Sub-chunk at safe paragraph boundaries outside fences & tables
                parts: List[List[Tuple[int, str]]] = []
                cur_part: List[Tuple[int, str]] = []
                cur_part_chars = 0

                for lno, ltext in sec.lines:
                    cur_part.append((lno, ltext))
                    cur_part_chars += len(ltext)

                    # Safe split boundary: blank line outside code fence & table
                    is_safe_split = (
                        lno not in protected_lines
                        and lno not in table_lines
                        and ltext.strip() == ""
                    )

                    if is_safe_split and cur_part_chars >= max_chunk_chars:
                        parts.append(cur_part)
                        cur_part = []
                        cur_part_chars = 0

                if cur_part:
                    parts.append(cur_part)

                for p_idx, part_lines in enumerate(parts, start=1):
                    p_text = "".join(l for _, l in part_lines)
                    p_start = part_lines[0][0]
                    p_end = part_lines[-1][0]
                    p_breadcrumb = f"{sec.breadcrumb} (Part {p_idx})" if len(parts) > 1 else sec.breadcrumb
                    p_hash = hashlib.sha256(p_text.encode("utf-8")).hexdigest()
                    cid_seed = f"{clean_doc_path}:{p_start}:{p_hash[:12]}"
                    p_chunk_id = f"chunk_{hashlib.sha256(cid_seed.encode('utf-8')).hexdigest()[:16]}"
                    p_tokens = max(1, len(p_text.split()))

                    chunks.append(
                        DocChunk(
                            chunk_id=p_chunk_id,
                            doc_node_id=doc_id,
                            doc_path=clean_doc_path,
                            heading_breadcrumb=p_breadcrumb,
                            level=sec.level,
                            start_line=p_start,
                            end_line=p_end,
                            content=p_text,
                            sha256=p_hash,
                            tokens=p_tokens,
                            sector="semantic",
                            metadata={
                                "doc_path": clean_doc_path,
                                "heading_breadcrumb": p_breadcrumb,
                                "level": sec.level,
                                "start_line": p_start,
                                "end_line": p_end,
                                "tokens": p_tokens,
                                "sha256": p_hash,
                            },
                        )
                    )

        return chunks

    def build_symbol_index(
        self,
        graph_data: Union[GraphDelta, Dict[str, Any]],
    ) -> Dict[str, List[CodeSymbolRef]]:
        """
        Multi-key index over AST code and test nodes:
        - Exact symbol names
        - Class.method qualified names
        - Snake_case and camelCase normalized keys
        - Relative file paths
        - FastAPI routes and FastMCP tool names
        - CLI subcommands
        """
        raw_nodes: List[Dict[str, Any]] = []
        if isinstance(graph_data, GraphDelta):
            for n in graph_data.nodes:
                raw_nodes.append(n.to_dict() if hasattr(n, "to_dict") else n.model_dump())
        elif isinstance(graph_data, dict):
            raw_nodes = graph_data.get("nodes", [])

        raw_node_ids = {n.get("id") for n in raw_nodes if n.get("id")}

        # Map existing graph nodes for lookup: (rel_path, label) -> node_id
        node_id_map: Dict[Tuple[str, str], str] = {}
        for n in raw_nodes:
            src_f = (n.get("source_file") or "").replace("\\", "/")
            lbl = n.get("label") or ""
            nid = n.get("id") or ""
            clean_lbl = lbl.rstrip("()").lstrip(".")
            for l_key in (lbl, clean_lbl, clean_lbl.split(".")[-1]):
                if not l_key:
                    continue
                if src_f:
                    node_id_map[(src_f, l_key)] = nid
                    node_id_map[(src_f, f"{l_key}()")] = nid
                    node_id_map[(src_f, f".{l_key}()")] = nid
                node_id_map[("", l_key)] = nid
                node_id_map[("", f"{l_key}()")] = nid
                node_id_map[("", f".{l_key}()")] = nid
            if nid:
                node_id_map[(src_f, nid)] = nid
                node_id_map[("", nid)] = nid

        symbols: List[CodeSymbolRef] = []

        # Scan workspace Python files for rich AST signatures, docstrings, routes, and CLI
        for root, _, files in os.walk(self.workspace_path):
            rel_root = os.path.relpath(root, self.workspace_path).replace("\\", "/")
            if any(part.startswith(".") or part in ("venv", ".venv", "node_modules", "__pycache__", "dist", "build", "tools", "orchestrator", ".agents", ".agent") for part in rel_root.split("/")):
                continue

            for f in files:
                if not f.endswith(".py"):
                    continue

                abs_file = Path(root) / f
                rel_file = os.path.relpath(abs_file, self.workspace_path).replace("\\", "/")
                if any(part in ("tools", "orchestrator", ".agents", ".agent", "node_modules", "venv", ".venv") for part in rel_file.split("/")):
                    continue

                try:
                    file_bytes = abs_file.read_bytes()
                    file_sha = hashlib.sha256(file_bytes).hexdigest()
                    file_mtime = abs_file.stat().st_mtime
                    content = file_bytes.decode("utf-8", errors="replace")
                    tree = ast.parse(content, filename=str(abs_file))
                except Exception as e:
                    logger.debug("AST parsing failed on %s: %s", rel_file, e)
                    continue

                is_test_file = rel_file.startswith("tests/") or f.startswith("test_")

                # Extract route prefix if router = APIRouter(prefix=...)
                file_router_prefix = ""
                for n_ast in ast.walk(tree):
                    if isinstance(n_ast, ast.Assign):
                        for target in n_ast.targets:
                            if isinstance(target, ast.Name) and target.id == "router":
                                if isinstance(n_ast.value, ast.Call):
                                    for kw in n_ast.value.keywords:
                                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                                            file_router_prefix = str(kw.value.value).rstrip("/")

                if not file_router_prefix:
                    if rel_file.endswith("routes/memory.py"):
                        file_router_prefix = "/memory"
                    elif rel_file.endswith("routes/mcp_registry.py"):
                        file_router_prefix = "/memory/mcp"
                    elif rel_file.endswith("routes/codegraph.py"):
                        file_router_prefix = "/codegraph"
                    elif rel_file.endswith("routes/auth.py"):
                        file_router_prefix = "/auth"
                    elif rel_file.endswith("routes/pm.py"):
                        file_router_prefix = "/pm"

                file_slug = Path(rel_file).stem.lower().replace("-", "_")

                # Walk AST to extract classes, functions, methods, routes, MCP tools, and CLI subcommands
                for node in ast.iter_child_nodes(tree):
                    # Classes
                    if isinstance(node, ast.ClassDef):
                        cls_name = node.name
                        cls_slug = cls_name.lower().replace("-", "_")
                        cls_type = "test" if (is_test_file or cls_name.startswith("Test")) else "class"
                        exp_cls_id = f"{file_slug}_{cls_slug}"
                        cls_nid = (
                            exp_cls_id if exp_cls_id in raw_node_ids
                            else (node_id_map.get((rel_file, cls_name))
                            or node_id_map.get(("", cls_name))
                            or f"cls_{cls_name}")
                        )

                        symbols.append(
                            CodeSymbolRef(
                                node_id=cls_nid,
                                symbol_name=cls_name,
                                symbol_type=cls_type,
                                file_path=rel_file,
                                start_line=node.lineno,
                                end_line=getattr(node, "end_lineno", node.lineno),
                                signature=f"class {cls_name}",
                                docstring=ast.get_docstring(node),
                                mtime=file_mtime,
                                sha256=file_sha,
                            )
                        )

                        # Methods inside class
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                meth_name = item.name
                                meth_slug = meth_name.lower().replace("-", "_")
                                qual_name = f"{cls_name}.{meth_name}"
                                meth_type = "test" if (is_test_file or meth_name.startswith("test_")) else "method"
                                exp_meth_id = f"{file_slug}_{cls_slug}_{meth_slug}"
                                meth_nid = (
                                    exp_meth_id if exp_meth_id in raw_node_ids
                                    else (node_id_map.get((rel_file, qual_name))
                                    or node_id_map.get((rel_file, meth_name))
                                    or node_id_map.get((rel_file, f".{meth_name}()"))
                                    or node_id_map.get((rel_file, f"{meth_name}()"))
                                    or node_id_map.get(("", qual_name))
                                    or node_id_map.get(("", meth_name))
                                    or node_id_map.get(("", f".{meth_name}()"))
                                    or f"fn_{cls_name}_{meth_name}")
                                )
                                meth_sig = self._format_ast_signature(item)

                                symbols.append(
                                    CodeSymbolRef(
                                        node_id=meth_nid,
                                        symbol_name=qual_name,
                                        symbol_type=meth_type,
                                        file_path=rel_file,
                                        start_line=item.lineno,
                                        end_line=getattr(item, "end_lineno", item.lineno),
                                        signature=meth_sig,
                                        docstring=ast.get_docstring(item),
                                        mtime=file_mtime,
                                        sha256=file_sha,
                                    )
                                )

                    # Top-level functions
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fn_name = node.name
                        fn_slug = fn_name.lower().replace("-", "_")
                        fn_type = "test" if (is_test_file or fn_name.startswith("test_")) else "function"
                        exp_fn_id = f"{file_slug}_{fn_slug}"
                        fn_nid = (
                            exp_fn_id if exp_fn_id in raw_node_ids
                            else (node_id_map.get((rel_file, fn_name))
                            or node_id_map.get((rel_file, f"{fn_name}()"))
                            or node_id_map.get(("", fn_name))
                            or node_id_map.get(("", f"{fn_name}()"))
                            or f"fn_{fn_name}")
                        )
                        fn_sig = self._format_ast_signature(node)

                        # Check for FastAPI route decorators
                        route_info = self._extract_route_decorator(node)
                        if route_info:
                            route_method, raw_route_path = route_info
                            full_route_path = f"{file_router_prefix}{raw_route_path}" if file_router_prefix and not raw_route_path.startswith(file_router_prefix) else raw_route_path
                            if not full_route_path.startswith("/"):
                                full_route_path = "/" + full_route_path
                            route_nid = (
                                node_id_map.get((rel_file, full_route_path))
                                or node_id_map.get(("", full_route_path))
                                or f"route_{route_method}_{full_route_path.strip('/').replace('/', '_')}"
                            )
                            symbols.append(
                                CodeSymbolRef(
                                    node_id=route_nid,
                                    symbol_name=f"{route_method} {full_route_path}",
                                    symbol_type="route",
                                    file_path=rel_file,
                                    start_line=node.lineno,
                                    end_line=getattr(node, "end_lineno", node.lineno),
                                    signature=fn_sig,
                                    docstring=ast.get_docstring(node),
                                    mtime=file_mtime,
                                    sha256=file_sha,
                                )
                            )

                        # Check for FastMCP tool decorators
                        mcp_name = self._get_mcp_tool_name(node)
                        if mcp_name:
                            mcp_nid = (
                                node_id_map.get((rel_file, mcp_name))
                                or node_id_map.get(("", mcp_name))
                                or f"mcp_{mcp_name}"
                            )
                            symbols.append(
                                CodeSymbolRef(
                                    node_id=mcp_nid,
                                    symbol_name=mcp_name,
                                    symbol_type="mcp_tool",
                                    file_path=rel_file,
                                    start_line=node.lineno,
                                    end_line=getattr(node, "end_lineno", node.lineno),
                                    signature=fn_sig,
                                    docstring=ast.get_docstring(node),
                                    mtime=file_mtime,
                                    sha256=file_sha,
                                )
                            )

                        symbols.append(
                            CodeSymbolRef(
                                node_id=fn_nid,
                                symbol_name=fn_name,
                                symbol_type=fn_type,
                                file_path=rel_file,
                                start_line=node.lineno,
                                end_line=getattr(node, "end_lineno", node.lineno),
                                signature=fn_sig,
                                docstring=ast.get_docstring(node),
                                mtime=file_mtime,
                                sha256=file_sha,
                            )
                        )

                    # CLI Command detection (e.g. parser.add_parser("build-kb"))
                    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                        cli_cmd = self._extract_cli_command(node.value)
                        if cli_cmd:
                            symbols.append(
                                CodeSymbolRef(
                                    node_id=f"cli_{cli_cmd.replace('-', '_')}",
                                    symbol_name=cli_cmd,
                                    symbol_type="cli",
                                    file_path=rel_file,
                                    start_line=node.lineno,
                                    end_line=getattr(node, "end_lineno", node.lineno),
                                    signature=f"projectbrain {cli_cmd}",
                                    docstring=f"CLI subcommand: {cli_cmd}",
                                    mtime=file_mtime,
                                    sha256=file_sha,
                                )
                            )

        # Build multi-key dictionary index
        index: Dict[str, List[CodeSymbolRef]] = {}

        def add_index_key(key: str, sym: CodeSymbolRef):
            k = key.strip()
            if not k:
                return
            if k not in index:
                index[k] = []
            if sym not in index[k]:
                index[k].append(sym)

        for s in symbols:
            name = s.symbol_name

            # 1. Exact symbol name
            add_index_key(name, s)

            # 2. Lowercase name
            add_index_key(name.lower(), s)

            # 3. File path keys
            add_index_key(s.file_path, s)
            add_index_key(Path(s.file_path).name, s)
            add_index_key(f"{s.file_path}::{name}", s)

            # 4. Qualified variations (e.g. Class.method or method alone)
            if "." in name:
                cls_part, meth_part = name.split(".", 1)
                add_index_key(meth_part, s)
                add_index_key(f"{cls_part}.{meth_part}", s)

            # 5. Route variations (e.g. "GET /graphify/diff" and "/graphify/diff")
            if s.symbol_type == "route":
                parts = name.split(" ", 1)
                if len(parts) == 2:
                    add_index_key(parts[1], s)
                    add_index_key(parts[1].strip("/"), s)

            # 6. Snake_case and CamelCase normalizations
            snake = self._to_snake_case(name)
            if snake != name:
                add_index_key(snake, s)

            camel = self._to_camel_case(name)
            if camel != name:
                add_index_key(camel, s)

            # 7. FastMCP tool prefixes
            if s.symbol_type == "mcp_tool" and name.startswith("projectbrain_"):
                add_index_key(name.replace("projectbrain_", ""), s)

        self.last_symbols = list(symbols)
        return index

    def link_chunks_to_code(
        self,
        chunks: List[DocChunk],
        symbol_index: Dict[str, List[CodeSymbolRef]],
        graph_data: Union[GraphDelta, Dict[str, Any]],
        device: Optional[str] = None,
        semantic_engine: Optional[str] = None,
        semantic_top_k: int = 5,
        min_similarity: float = 0.60,
    ) -> Tuple[List[CrossLinkEdge], List[CodeDocDrift]]:
        """
        Emit EXTRACTED (1.0) and INFERRED (0.60-0.90) edges connecting doc chunks to code symbols.
        Detects drift when source file hash/mtime is newer or symbol signature mismatches.
        """
        edges: List[CrossLinkEdge] = []
        drift_items: List[CodeDocDrift] = []

        seen_edges: Set[Tuple[str, str, str]] = set()

        def add_edge(edge: CrossLinkEdge):
            k = (edge.source_id, edge.target_id, edge.relation)
            if k not in seen_edges:
                seen_edges.add(k)
                edges.append(edge)

        # Regex patterns for explicit code references in markdown
        backtick_re = re.compile(r"`([^`\n]+)`")
        func_call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
        route_re = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+([/\w\-]+)")

        for chunk in chunks:
            # 1. Explicit backtick matches
            backticked_candidates = backtick_re.findall(chunk.content)
            backticked_candidates.extend(backtick_re.findall(chunk.heading_breadcrumb))

            for raw_cand in backticked_candidates:
                cand = raw_cand.strip()
                clean_name = re.sub(r"\(.*?\)", "", cand).strip()

                matched_symbols: List[CodeSymbolRef] = []
                if cand in symbol_index:
                    matched_symbols = symbol_index[cand]
                elif clean_name in symbol_index:
                    matched_symbols = symbol_index[clean_name]

                if matched_symbols:
                    for sym in matched_symbols:
                        # Bidirectional EXTRACTED edges
                        add_edge(
                            CrossLinkEdge(
                                source_id=chunk.chunk_id,
                                target_id=sym.node_id,
                                relation="documents_symbol",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Explicit backtick reference `{cand}` in {chunk.heading_breadcrumb}",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )
                        add_edge(
                            CrossLinkEdge(
                                source_id=sym.node_id,
                                target_id=chunk.chunk_id,
                                relation="documented_in",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Documented in {chunk.doc_path} ({chunk.heading_breadcrumb})",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )
                        if sym.symbol_type in ("route", "mcp_tool"):
                            add_edge(
                                CrossLinkEdge(
                                    source_id=chunk.chunk_id,
                                    target_id=sym.node_id,
                                    relation="specifies_endpoint",
                                    confidence="EXTRACTED",
                                    confidence_score=1.0,
                                    evidence=f"Explicit endpoint specification `{cand}` in {chunk.heading_breadcrumb}",
                                    metadata={
                                        "doc_path": chunk.doc_path,
                                        "symbol_name": sym.symbol_name,
                                        "symbol_type": sym.symbol_type,
                                        "source_file": sym.file_path,
                                        "line": sym.start_line,
                                        "heading": chunk.heading_breadcrumb,
                                    },
                                )
                            )

                        # Code newer than doc check
                        try:
                            doc_abs = self.workspace_path / chunk.doc_path
                            if doc_abs.exists() and sym.mtime is not None:
                                doc_mtime = doc_abs.stat().st_mtime
                                if sym.mtime > (doc_mtime + 60):
                                    drift_items.append(
                                        CodeDocDrift(
                                            symbol_id=sym.node_id,
                                            doc_chunk_id=chunk.chunk_id,
                                            drift_type="code_newer_than_doc",
                                            severity="low",
                                            detail=f"Source file {sym.file_path} modified at {sym.mtime} is newer than doc {chunk.doc_path} at {doc_mtime}",
                                            source_file=sym.file_path,
                                            doc_path=chunk.doc_path,
                                        )
                                    )
                        except Exception:
                            pass
                else:
                    # Check for removed_symbol_in_doc (orphan code reference)
                    is_code_like = (
                        cand.startswith("src/")
                        or cand.endswith(".py")
                        or ("." in cand and re.match(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$", cand))
                        or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)$", cand)
                        or (len(cand) >= 5 and re.match(r"^[A-Z][a-zA-Z0-9]+$", cand) and cand not in ("Markdown", "Python", "FastAPI", "SQLite", "PostgreSQL", "Docker", "Mermaid", "GraphStore"))
                    )
                    if is_code_like and not cand.startswith(("http", "json.", "os.", "sys.", "typing.")):
                        drift_items.append(
                            CodeDocDrift(
                                symbol_id=cand,
                                doc_chunk_id=chunk.chunk_id,
                                drift_type="removed_symbol_in_doc",
                                severity="medium",
                                detail=f"Doc chunk references `{cand}` which does not exist in codebase AST index",
                                source_file="",
                                doc_path=chunk.doc_path,
                            )
                        )

            # 2. Check function calls for signature_mismatch
            for fn_name, raw_args in func_call_re.findall(chunk.content):
                if fn_name in symbol_index and fn_name not in GENERIC_STOP_WORDS:
                    for sym in symbol_index[fn_name]:
                        if sym.signature and sym.symbol_type in ("function", "method"):
                            doc_arg_tokens = [a.strip() for a in raw_args.split(",") if a.strip()]
                            doc_kw_names = []
                            for a in doc_arg_tokens:
                                if "=" in a:
                                    param_name = a.split("=")[0].strip()
                                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", param_name):
                                        doc_kw_names.append(param_name)
                                elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a) and a not in ("True", "False", "None"):
                                    doc_kw_names.append(a)

                            ast_sig_params = self._extract_param_names_from_signature(sym.signature)
                            mismatches = [p for p in doc_kw_names if p not in ast_sig_params and p not in ("self", "cls")]
                            if mismatches:
                                drift_items.append(
                                    CodeDocDrift(
                                        symbol_id=sym.node_id,
                                        doc_chunk_id=chunk.chunk_id,
                                        drift_type="signature_mismatch",
                                        severity="high",
                                        detail=f"Signature mismatch for `{sym.symbol_name}`: doc references argument(s) {mismatches} but AST signature is {sym.signature}",
                                        source_file=sym.file_path,
                                        doc_path=chunk.doc_path,
                                    )
                                )

            # 3. Explicit HTTP routes in chunk text
            for method, route_path in route_re.findall(chunk.content):
                full_route = f"{method} {route_path}"
                matched = symbol_index.get(full_route) or symbol_index.get(route_path) or symbol_index.get(route_path.strip("/"))
                if matched:
                    for sym in matched:
                        add_edge(
                            CrossLinkEdge(
                                source_id=chunk.chunk_id,
                                target_id=sym.node_id,
                                relation="specifies_endpoint",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Explicit route mention `{full_route}` in doc chunk",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )
                        add_edge(
                            CrossLinkEdge(
                                source_id=chunk.chunk_id,
                                target_id=sym.node_id,
                                relation="documents_symbol",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Documents route `{full_route}` in doc chunk",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )
                        add_edge(
                            CrossLinkEdge(
                                source_id=sym.node_id,
                                target_id=chunk.chunk_id,
                                relation="documented_in",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Documented in {chunk.doc_path}",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )

            # Direct endpoint path mentions (e.g. `POST /graphify/sync` or `/graphify/sync` in table)
            for raw_path in re.findall(r"(?:/|`/)((?:graphify|sources|kb|memory|codegraph|auth|pm)[a-zA-Z0-9_\-/]*)", chunk.content):
                r_cand = "/" + raw_path.strip("` \n\t")
                matched = symbol_index.get(r_cand) or symbol_index.get(r_cand.strip("/"))
                if matched:
                    for sym in matched:
                        add_edge(
                            CrossLinkEdge(
                                source_id=chunk.chunk_id,
                                target_id=sym.node_id,
                                relation="specifies_endpoint",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Explicit route path `{r_cand}` in doc chunk",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )
                        add_edge(
                            CrossLinkEdge(
                                source_id=chunk.chunk_id,
                                target_id=sym.node_id,
                                relation="documents_symbol",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Documents route path `{r_cand}`",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )
                        add_edge(
                            CrossLinkEdge(
                                source_id=sym.node_id,
                                target_id=chunk.chunk_id,
                                relation="documented_in",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Documented in {chunk.doc_path}",
                                metadata={
                                    "doc_path": chunk.doc_path,
                                    "symbol_name": sym.symbol_name,
                                    "symbol_type": sym.symbol_type,
                                    "source_file": sym.file_path,
                                    "line": sym.start_line,
                                    "heading": chunk.heading_breadcrumb,
                                },
                            )
                        )

            # 4. High-precision INFERRED lexical token links (conceptually_related_to, 0.60-0.90)
            prose_text = re.sub(r"```[\s\S]*?```", "", chunk.content)
            prose_words = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", prose_text))

            for word in prose_words:
                if word in GENERIC_STOP_WORDS:
                    continue
                if word in symbol_index:
                    for sym in symbol_index[word]:
                        if (chunk.chunk_id, sym.node_id, "documents_symbol") in seen_edges:
                            continue
                        score = 0.85 if word in chunk.heading_breadcrumb else 0.70
                        add_edge(
                            CrossLinkEdge(
                                source_id=chunk.chunk_id,
                                target_id=sym.node_id,
                                relation="conceptually_related_to",
                                confidence="INFERRED",
                                confidence_score=score,
                                evidence=f"Prose conceptual overlap with `{word}` in {chunk.heading_breadcrumb}",
                                metadata={"doc_path": chunk.doc_path, "symbol_name": sym.symbol_name},
                            )
                        )

        # 4b. GPU / Vectorized BLAS semantic similarity pass for INFERRED conceptually_related_to links
        eff_device = device if device is not None else self.device
        eff_semantic_engine = (
            semantic_engine
            if semantic_engine is not None
            else getattr(self, "semantic_engine", "auto")
        )
        self._link_semantic_similarity(
            chunks=chunks,
            symbol_index=symbol_index,
            add_edge=add_edge,
            seen_edges=seen_edges,
            device=eff_device,
            semantic_engine=eff_semantic_engine,
            top_k=semantic_top_k,
            min_similarity=min_similarity,
        )

        # 5. Hierarchical Class-to-Method and Enclosing Scope Inferred Linkage
        class_syms_by_nid = {s.node_id: s for s in getattr(self, "last_symbols", []) if s.symbol_type in ("class", "test")}
        methods_by_class: Dict[str, List[CodeSymbolRef]] = {}
        for s in getattr(self, "last_symbols", []):
            if s.symbol_type in ("method", "test") and "." in s.symbol_name:
                c_name = s.symbol_name.split(".", 1)[0]
                methods_by_class.setdefault(c_name, []).append(s)

        for edge in list(edges):
            if edge.relation == "documents_symbol" and edge.target_id in class_syms_by_nid:
                c_sym = class_syms_by_nid[edge.target_id]
                for m in methods_by_class.get(c_sym.symbol_name, []):
                    if (edge.source_id, m.node_id, "documents_symbol") not in seen_edges and (edge.source_id, m.node_id, "conceptually_related_to") not in seen_edges:
                        add_edge(
                            CrossLinkEdge(
                                source_id=edge.source_id,
                                target_id=m.node_id,
                                relation="conceptually_related_to",
                                confidence="INFERRED",
                                confidence_score=0.80,
                                evidence=f"Method of documented class `{c_sym.symbol_name}`",
                                metadata={
                                    "doc_path": edge.metadata.get("doc_path", ""),
                                    "symbol_name": m.symbol_name,
                                    "symbol_type": m.symbol_type,
                                    "source_file": m.file_path,
                                    "line": m.start_line,
                                },
                            )
                        )
                        add_edge(
                            CrossLinkEdge(
                                source_id=m.node_id,
                                target_id=edge.source_id,
                                relation="conceptually_related_to",
                                confidence="INFERRED",
                                confidence_score=0.80,
                                evidence=f"Documented via enclosing class `{c_sym.symbol_name}`",
                                metadata={
                                    "doc_path": edge.metadata.get("doc_path", ""),
                                    "symbol_name": m.symbol_name,
                                    "symbol_type": m.symbol_type,
                                    "source_file": m.file_path,
                                    "line": m.start_line,
                                },
                            )
                        )

        return edges, drift_items

    @staticmethod
    def _normalize_bipartite_topk(raw_matches: Any, n_queries: int) -> List[List[Tuple[int, float]]]:
        if raw_matches is None:
            return [[] for _ in range(n_queries)]
        if isinstance(raw_matches, tuple) and len(raw_matches) == 2:
            indices, scores = raw_matches
            return [
                [(int(ti), float(sc)) for ti, sc in zip(row_i, row_s) if int(ti) >= 0]
                for row_i, row_s in zip(indices, scores)
            ]
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
    def _vectorized_bipartite_tfidf_topk(
        query_texts: List[str],
        target_texts: List[str],
        top_k: int = 5,
        min_similarity: float = 0.62,
    ) -> List[List[Tuple[int, float]]]:
        """Fast vectorized NumPy BLAS TF-IDF cosine similarity between doc chunks and code symbols."""
        nq, nt = len(query_texts), len(target_texts)
        if nq == 0 or nt == 0:
            return [[] for _ in range(nq)]
        try:
            import numpy as np
        except ImportError:
            return [[] for _ in range(nq)]

        token_re = re.compile(r"[a-zA-Z0-9_]{3,}")
        all_docs = query_texts + target_texts
        doc_tokens: List[List[str]] = []
        df: Dict[str, int] = {}
        for txt in all_docs:
            toks = [
                t.lower()
                for t in token_re.findall(txt)
                if t.lower() not in GENERIC_STOP_WORDS
            ]
            doc_tokens.append(toks)
            for t in set(toks):
                df[t] = df.get(t, 0) + 1

        if not df:
            return [[] for _ in range(nq)]

        sorted_terms = sorted(df.keys())
        vocab = {tok: idx for idx, tok in enumerate(sorted_terms)}
        total_docs = float(len(all_docs))
        idf = np.array(
            [np.log((1.0 + total_docs) / (1.0 + df[tok])) + 1.0 for tok in sorted_terms],
            dtype=np.float32,
        )

        def _encode(start: int, count: int) -> Any:
            mat = np.zeros((count, len(vocab)), dtype=np.float32)
            for row_i in range(count):
                for tok in doc_tokens[start + row_i]:
                    mat[row_i, vocab[tok]] += 1.0
            mat *= idf[None, :]
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms = np.where(norms > 0, norms, 1.0)
            mat /= norms
            return mat

        q_mat = _encode(0, nq)
        t_mat = _encode(nq, nt)
        sim = q_mat @ t_mat.T

        k_lim = min(top_k, nt)
        results: List[List[Tuple[int, float]]] = []
        for i in range(nq):
            row = sim[i]
            if k_lim <= 0:
                results.append([])
                continue
            cand_idx = np.argpartition(row, -k_lim)[-k_lim:]
            cand_idx = cand_idx[np.argsort(row[cand_idx])[::-1]]
            results.append(
                [(int(j), float(row[j])) for j in cand_idx if float(row[j]) >= min_similarity]
            )
        return results

    def _link_semantic_similarity(
        self,
        chunks: List[DocChunk],
        symbol_index: Dict[str, List[CodeSymbolRef]],
        add_edge: Any,
        seen_edges: Set[Tuple[str, str, str]],
        device: str = "auto",
        semantic_engine: str = "auto",
        top_k: int = 5,
        min_similarity: float = 0.60,
    ) -> None:
        """
        Accelerate INFERRED `conceptually_related_to` linking across large chunk/symbol sets
        using GraphGPUAccelerator (`embed_texts` + `cosine_similarity_topk`) with fast
        vectorized NumPy BLAS TF-IDF fallback.
        """
        if not chunks or not symbol_index:
            return

        eff_engine = (semantic_engine or "auto").strip().lower()
        if eff_engine == "off":
            return

        unique_symbols: List[CodeSymbolRef] = []
        seen_sym_ids: Set[str] = set()
        for sym_list in symbol_index.values():
            for sym in sym_list:
                if sym.node_id not in seen_sym_ids:
                    seen_sym_ids.add(sym.node_id)
                    unique_symbols.append(sym)

        if not unique_symbols:
            return

        chunk_texts = [f"{c.heading_breadcrumb} {c.content[:400]}".strip() for c in chunks]
        sym_texts = [
            " ".join(
                p
                for p in (
                    s.symbol_name,
                    s.symbol_type,
                    s.file_path,
                    s.signature or "",
                    s.docstring or "",
                )
                if p
            )
            for s in unique_symbols
        ]

        matches: Optional[List[List[Tuple[int, float]]]] = None
        used_gpu_embeddings = False
        backend_tag = "numpy-blas-tfidf"
        if device != "cpu" or eff_engine not in ("auto", "tfidf", "numpy", "blas"):
            try:
                from projectbrain.graphify_engine.gpu_accelerator import GraphGPUAccelerator  # type: ignore

                if hasattr(GraphGPUAccelerator, "embed_texts") and hasattr(GraphGPUAccelerator, "cosine_similarity_topk"):
                    n_chunks = len(chunk_texts)
                    joint_texts = chunk_texts + sym_texts
                    try:
                        joint_res = GraphGPUAccelerator.embed_texts(
                            joint_texts,
                            engine=semantic_engine,
                            device=device,
                        )
                    except TypeError:
                        joint_res = GraphGPUAccelerator.embed_texts(joint_texts)

                    emb_meta: Dict[str, Any] = {}
                    if isinstance(joint_res, tuple) and len(joint_res) == 2 and isinstance(joint_res[1], dict):
                        joint_vecs, emb_meta = joint_res
                    else:
                        joint_vecs = joint_res

                    if emb_meta.get("disabled"):
                        return

                    q_vecs = joint_vecs[:n_chunks]
                    t_vecs = joint_vecs[n_chunks:]

                    try:
                        raw_sim = GraphGPUAccelerator.cosine_similarity_topk(
                            q_vecs,
                            t_vecs,
                            top_k=min(top_k, len(unique_symbols)),
                            min_score=min_similarity,
                            device=device,
                        )
                    except TypeError:
                        try:
                            raw_sim = GraphGPUAccelerator.cosine_similarity_topk(
                                q_vecs,
                                t_vecs,
                                top_k=min(top_k, len(unique_symbols)),
                                threshold=min_similarity,
                            )
                        except TypeError:
                            raw_sim = GraphGPUAccelerator.cosine_similarity_topk(
                                q_vecs,
                                t_vecs,
                                top_k=min(top_k, len(unique_symbols)),
                            )

                    sim_meta: Dict[str, Any] = {}
                    if isinstance(raw_sim, tuple) and len(raw_sim) == 2 and isinstance(raw_sim[1], dict):
                        raw_pairs, sim_meta = raw_sim
                    else:
                        raw_pairs = raw_sim
                    matches = self._normalize_bipartite_topk(raw_pairs, len(chunks))
                    used_gpu_embeddings = True
                    backend_tag = str(sim_meta.get("backend_used") or emb_meta.get("engine_used") or "gpu-embeddings")
            except ImportError as exc:
                logger.debug("GraphGPUAccelerator unavailable (%s); using BLAS TF-IDF linker.", exc)
            except Exception as exc:
                logger.debug("GraphGPUAccelerator semantic linking failed (%s); falling back to BLAS.", exc)

        if matches is None:
            matches = self._vectorized_bipartite_tfidf_topk(
                chunk_texts,
                sym_texts,
                top_k=min(top_k, len(unique_symbols)),
                min_similarity=max(0.62, min_similarity),
            )
            backend_tag = "numpy-blas-tfidf"
        for c_idx, neighs in enumerate(matches):
            chunk = chunks[c_idx]
            for s_idx, raw_score in neighs:
                if not (0 <= s_idx < len(unique_symbols)):
                    continue
                if raw_score < min_similarity:
                    continue
                sym = unique_symbols[s_idx]
                if (chunk.chunk_id, sym.node_id, "documents_symbol") in seen_edges:
                    continue
                if (chunk.chunk_id, sym.node_id, "conceptually_related_to") in seen_edges:
                    continue
                clamped_score = round(min(0.90, max(0.60, float(raw_score))), 4)
                add_edge(
                    CrossLinkEdge(
                        source_id=chunk.chunk_id,
                        target_id=sym.node_id,
                        relation="conceptually_related_to",
                        confidence="INFERRED",
                        confidence_score=clamped_score,
                        evidence=f"Semantic similarity ({backend_tag}, score={clamped_score:.2f}) in {chunk.heading_breadcrumb}",
                        metadata={
                            "doc_path": chunk.doc_path,
                            "symbol_name": sym.symbol_name,
                            "symbol_type": sym.symbol_type,
                            "source_file": sym.file_path,
                            "backend": backend_tag,
                        },
                    )
                )

    def link_tests_to_code(
        self,
        graph_data: Union[GraphDelta, Dict[str, Any]],
        chunks: List[DocChunk],
    ) -> List[CrossLinkEdge]:
        """
        Emit verifies_symbol and verifies_requirement edges from test AST nodes.
        """
        test_edges: List[CrossLinkEdge] = []
        seen_edges: Set[Tuple[str, str, str]] = set()

        raw_nodes: List[Dict[str, Any]] = []
        raw_edges: List[Dict[str, Any]] = []

        if isinstance(graph_data, GraphDelta):
            raw_nodes = [n.to_dict() if hasattr(n, "to_dict") else n.model_dump() for n in graph_data.nodes]
            raw_edges = [e.to_dict() if hasattr(e, "to_dict") else e.model_dump() for e in graph_data.edges]
        elif isinstance(graph_data, dict):
            raw_nodes = graph_data.get("nodes", [])
            raw_edges = graph_data.get("edges", [])

        # Build symbol lookup by id and label
        code_nodes_by_label: Dict[str, List[Dict[str, Any]]] = {}
        code_nodes_by_id: Dict[str, Dict[str, Any]] = {}
        for n in raw_nodes:
            lbl = n.get("label") or ""
            nid = n.get("id") or ""
            if nid:
                code_nodes_by_id[nid] = n
            if lbl:
                code_nodes_by_label.setdefault(lbl, []).append(n)

        # Map requirements (AC-*) to doc chunks
        ac_to_chunks: Dict[str, List[DocChunk]] = {}
        ac_re = re.compile(r"\b(AC-[A-Z0-9\-]+)\b")
        for chunk in chunks:
            matches = set(ac_re.findall(chunk.content) + ac_re.findall(chunk.heading_breadcrumb))
            for ac in matches:
                ac_to_chunks.setdefault(ac, []).append(chunk)

        # Scan test nodes
        test_nodes = [
            n for n in raw_nodes
            if (n.get("source_file") or "").startswith("tests/")
            or (n.get("label") or "").startswith("test_")
        ]

        for tnode in test_nodes:
            tnid = tnode.get("id") or ""
            tlabel = tnode.get("label") or ""
            tfile = tnode.get("source_file") or ""

            # 1. Map test calls to target code nodes from graph edges
            for edge in raw_edges:
                if edge.get("source") == tnid and edge.get("relation") in ("calls", "imports"):
                    target_id = edge.get("target")
                    if target_id and target_id in code_nodes_by_id:
                        k = (tnid, target_id, "verifies_symbol")
                        if k not in seen_edges:
                            seen_edges.add(k)
                            test_edges.append(
                                CrossLinkEdge(
                                    source_id=tnid,
                                    target_id=target_id,
                                    relation="verifies_symbol",
                                    confidence="EXTRACTED",
                                    confidence_score=1.0,
                                    evidence=f"Test {tlabel} invokes target node {target_id}",
                                    metadata={"test_file": tfile},
                                )
                            )

            # 2. Test name heuristic: test_chunk_markdown_document -> chunk_markdown_document
            clean_test_target = tlabel.replace("test_", "")
            if clean_test_target in code_nodes_by_label:
                for cnode in code_nodes_by_label[clean_test_target]:
                    target_id = cnode.get("id")
                    if target_id and (tnid, target_id, "verifies_symbol") not in seen_edges:
                        seen_edges.add((tnid, target_id, "verifies_symbol"))
                        test_edges.append(
                            CrossLinkEdge(
                                source_id=tnid,
                                target_id=target_id,
                                relation="verifies_symbol",
                                confidence="EXTRACTED",
                                confidence_score=1.0,
                                evidence=f"Test function {tlabel} directly targets symbol {clean_test_target}",
                                metadata={"test_file": tfile},
                            )
                        )

            # 3. Requirement verification (verifies_requirement)
            tmeta = tnode.get("metadata") or {}
            docstr = tmeta.get("docstring") or ""
            ac_matches = set(ac_re.findall(docstr) + ac_re.findall(tlabel))
            for ac in ac_matches:
                if ac in ac_to_chunks:
                    for chunk in ac_to_chunks[ac]:
                        k = (tnid, chunk.chunk_id, "verifies_requirement")
                        if k not in seen_edges:
                            seen_edges.add(k)
                            test_edges.append(
                                CrossLinkEdge(
                                    source_id=tnid,
                                    target_id=chunk.chunk_id,
                                    relation="verifies_requirement",
                                    confidence="EXTRACTED",
                                    confidence_score=1.0,
                                    evidence=f"Test {tlabel} verifies requirement {ac} documented in {chunk.doc_path}",
                                    metadata={"requirement": ac, "test_file": tfile},
                                )
                            )

        return test_edges

    # Helper methods for AST inspection
    @staticmethod
    def _format_ast_signature(node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> str:
        params = []
        for a in node.args.posonlyargs:
            params.append(a.arg)
        if node.args.posonlyargs:
            params.append("/")
        for a in node.args.args:
            params.append(a.arg)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        elif node.args.kwonlyargs:
            params.append("*")
        for a in node.args.kwonlyargs:
            params.append(a.arg)
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
        return f"({', '.join(params)})"

    @staticmethod
    def _extract_param_names_from_signature(sig: str) -> List[str]:
        cleaned = sig.strip("() ")
        names = []
        for part in cleaned.split(","):
            part = part.strip()
            if not part or part in ("/", "*"):
                continue
            name = part.split("=")[0].split(":")[0].strip().lstrip("*")
            if name:
                names.append(name)
        return names

    @staticmethod
    def _extract_route_decorator(node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Optional[Tuple[str, str]]:
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                attr = dec.func.attr.upper()
                if attr in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                        return attr, dec.args[0].value
        return None

    @staticmethod
    def _get_mcp_tool_name(node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Optional[str]:
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr == "tool":
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            return str(kw.value.value)
                    if dec.args and isinstance(dec.args[0], ast.Constant):
                        return str(dec.args[0].value)
                    return node.name
            elif isinstance(dec, ast.Attribute) and dec.attr == "tool":
                return node.name
        return None

    @classmethod
    def _is_mcp_tool(cls, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> bool:
        return cls._get_mcp_tool_name(node) is not None

    @staticmethod
    def _extract_cli_command(call_node: ast.Call) -> Optional[str]:
        if isinstance(call_node.func, ast.Attribute) and call_node.func.attr == "add_parser":
            if call_node.args and isinstance(call_node.args[0], ast.Constant) and isinstance(call_node.args[0].value, str):
                return call_node.args[0].value
        return None

    @staticmethod
    def _to_snake_case(name: str) -> str:
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    @staticmethod
    def _to_camel_case(name: str) -> str:
        parts = name.split("_")
        return parts[0] + "".join(p.capitalize() for p in parts[1:])


class UnifiedKBBuilder:
    """
    High-level orchestrator executing end-to-end KB synthesis, chunking, linking,
    cognitive memory persistence, and traceability inspection.
    """

    def __init__(
        self,
        workspace_path: Union[str, Path] = ".",
        registry: Optional[Any] = None,
        device: str = "auto",
        ocr_device: str = "auto",
        ocr_engine: str = "auto",
        semantic_engine: str = "auto",
    ):
        self.workspace_path = Path(workspace_path).resolve()
        self.device = device
        self.ocr_device = ocr_device
        self.ocr_engine = ocr_engine
        self.semantic_engine = semantic_engine
        self.linker = CodeDocLinker(
            workspace_path=self.workspace_path,
            device=device,
            ocr_device=ocr_device,
            ocr_engine=ocr_engine,
            semantic_engine=semantic_engine,
        )
        self.store: Optional[GraphStore] = None
        self.last_drift_items: List[CodeDocDrift] = []
        if registry is not None:
            self.registry = registry
        else:
            try:
                from projectbrain.plugins.registry import get_default_registry
                self.registry = get_default_registry()
            except Exception:
                self.registry = None

    @staticmethod
    def _merge_delta_dict(target: GraphDelta, raw_delta: Optional[Dict[str, Any]], project_id: str) -> None:
        if not isinstance(raw_delta, dict):
            return
        seen_nodes = {n.id for n in target.nodes}
        for n in raw_delta.get("nodes") or []:
            node_obj = n if isinstance(n, GraphNode) else GraphNode(**{**n, "project_id": n.get("project_id") or project_id})
            if node_obj.id not in seen_nodes:
                target.nodes.append(node_obj)
                seen_nodes.add(node_obj.id)
        seen_edges = {(e.source, e.target, e.relation) for e in target.edges}
        for e in raw_delta.get("edges") or []:
            edge_obj = e if isinstance(e, GraphEdge) else GraphEdge(**{**e, "project_id": e.get("project_id") or project_id})
            ek = (edge_obj.source, edge_obj.target, edge_obj.relation)
            if ek not in seen_edges:
                target.edges.append(edge_obj)
                seen_edges.add(ek)

    @staticmethod
    def _resolve_exporters(active_registry: Optional[Any], project_id: str) -> Dict[str, Any]:
        exporters_by_fmt: Dict[str, Any] = {}
        if active_registry is None or not hasattr(active_registry, "resolve_all"):
            return exporters_by_fmt
        try:
            for exp in active_registry.resolve_all("doc_exporter", project_id=project_id):
                fmt = (
                    getattr(exp, "export_format", None)
                    or exp.metadata.plugin_id.rsplit(".", 1)[-1]
                ).lower()
                if fmt not in exporters_by_fmt:
                    exporters_by_fmt[fmt] = exp
        except Exception as e:
            logger.debug("doc_exporter plugin resolution skipped: %s", e)
        return exporters_by_fmt

    async def build_unified_kb(
        self,
        project_id: str = "project-brain",
        branch: str = "main",
        persist_memory: bool = True,
        output_dir: Optional[str] = None,
        generate_office_exports: bool = True,
        store: Optional[GraphStore] = None,
        db_path: Optional[str] = None,
        registry: Optional[Any] = None,
        device: str = "auto",
        ocr_device: str = "auto",
        ocr_engine: str = "auto",
        workers: Optional[int] = None,
        semantic_engine: str = "auto",
    ) -> Dict[str, Any]:
        """
        End-to-end execution:
        1. Extract AST workspace graph with Leiden clustering (via etl_parser plugins).
        2. Synthesize Markdown wiki and multimodal Office exports via doc_exporter plugins.
        3. Ingest and hierarchically chunk all project docs (docs/**, wiki/**, README.md).
        4. Build symbol index and emit bidirectional cross-linking edges via knowledge_linker plugins.
        5. Persist chunks to HSG cognitive memory and create waypoints between connected chunks.
        6. Enrich GraphNode.metadata in GraphStore and persist manifest.
        """
        eff_device = self.device if (device == "auto" and self.device != "auto") else device
        eff_ocr_device = self.ocr_device if (ocr_device == "auto" and self.ocr_device != "auto") else ocr_device
        eff_ocr_engine = self.ocr_engine if (ocr_engine == "auto" and self.ocr_engine != "auto") else ocr_engine
        eff_semantic_engine = (
            getattr(self, "semantic_engine", "auto")
            if (semantic_engine == "auto" and getattr(self, "semantic_engine", "auto") != "auto")
            else semantic_engine
        )
        active_registry = registry if registry is not None else self.registry

        # 1. Resolve storage backends and database isolation
        if store is not None:
            self.store = store
        else:
            self.store = get_graph_store(
                db_path=db_path,
                project_id=project_id,
                registry=active_registry,
            )

        self.store.ensure_root_schema()
        if not self.store.is_root_branch(branch):
            self.store.ensure_branch_schema(project_id, branch)

        if db_path:
            env.database_url = f"sqlite:///{db_path}"
            db.conn = None
            db.connect()

        base_out_dir = Path(output_dir or "docs/kb")
        wiki_dir = base_out_dir / "wiki"
        exports_dir = base_out_dir / "exports"

        # 2. Extract AST workspace graph (code and tests) via ETLParserPlugin
        etl_parsers: List[Any] = []
        if active_registry is not None and hasattr(active_registry, "resolve_all"):
            try:
                etl_parsers = active_registry.resolve_all("etl_parser", project_id=project_id)
            except Exception as e:
                logger.debug("etl_parser plugin resolution skipped: %s", e)

        ast_plugin = None
        for p in etl_parsers:
            pid = getattr(getattr(p, "metadata", None), "plugin_id", "")
            if pid == "etl.multimodal_doc":
                continue
            if pid == "etl.graphify_ast" or (
                hasattr(p, "can_parse") and p.can_parse(str(self.workspace_path), format_hint="workspace")
            ):
                ast_plugin = p
                break

        extractor = GraphifyExtractor(
            cache_dir=ast_plugin.config.get("cache_dir") if ast_plugin and isinstance(getattr(ast_plugin, "config", None), dict) else None,
            device=eff_device,
            workers=workers,
            semantic_engine=eff_semantic_engine,
        )
        if ast_plugin is not None and (
            getattr(getattr(ast_plugin, "metadata", None), "plugin_id", "") != "etl.graphify_ast"
            or "parse" in getattr(ast_plugin, "__dict__", {})
            or type(ast_plugin).__name__ != "GraphifyASTETLPlugin"
        ):
            parsed_ast = await ast_plugin.parse(
                str(self.workspace_path),
                source_name=str(self.workspace_path),
                project_id=project_id,
                branch=branch,
                metadata={"include_docs": False, "device": eff_device, "workers": workers},
            )
            graph_delta = GraphDelta(project_id=project_id, branch=branch)
            self._merge_delta_dict(graph_delta, parsed_ast.get("graph_delta"), project_id)
        elif ast_plugin is not None or active_registry is None:
            use_cache = bool(ast_plugin.config.get("use_cache", True)) if ast_plugin else True
            graph_delta = extractor.extract_workspace(
                workspace_path=self.workspace_path,
                project_id=project_id,
                branch=branch,
                include_docs=False,
                use_cache=use_cache,
                device=eff_device,
                workers=workers,
                semantic_engine=eff_semantic_engine,
            )
        else:
            graph_delta = GraphDelta(project_id=project_id, branch=branch)

        # 3. Initial Doc Exports via DocExporterPlugin (with safe fallback)
        exporters_by_fmt = self._resolve_exporters(active_registry, project_id)
        export_ctx = {
            "workspace_path": str(self.workspace_path),
            "project_id": project_id,
            "branch": branch,
        }

        synthesizer = None
        if not exporters_by_fmt and active_registry is None:
            try:
                from projectbrain.graphify_engine.doc_synthesizer import ProjectDocSynthesizer
                synthesizer = ProjectDocSynthesizer(workspace_path=self.workspace_path)
            except ImportError:
                synthesizer = None

        try:
            if "wiki" in exporters_by_fmt:
                exporters_by_fmt["wiki"].export(graph_delta, str(wiki_dir), context=export_ctx)
            elif synthesizer is not None:
                synthesizer.synthesize_wiki(graph_delta, output_dir=wiki_dir)

            if generate_office_exports:
                if "docx" in exporters_by_fmt:
                    exporters_by_fmt["docx"].export(
                        graph_delta,
                        str(exports_dir / "ProjectBrain_Architecture_Spec.docx"),
                        context=export_ctx,
                    )
                elif synthesizer is not None:
                    synthesizer.export_docx_specification(
                        graph_delta,
                        output_path=exports_dir / "ProjectBrain_Architecture_Spec.docx",
                    )

                if "pptx" in exporters_by_fmt:
                    exporters_by_fmt["pptx"].export(
                        graph_delta,
                        str(exports_dir / "ProjectBrain_Architecture_Deck.pptx"),
                        context=export_ctx,
                    )
                elif synthesizer is not None:
                    synthesizer.export_pptx_architecture_deck(
                        graph_delta,
                        output_path=exports_dir / "ProjectBrain_Architecture_Deck.pptx",
                    )
        except Exception as e:
            logger.warning("DocExporterPlugin initial export warning: %s", e)

        # 4. Ingest and hierarchically chunk all project documentation + domain ETL parsers
        doc_pipeline = DocumentPipeline(extractor=extractor)
        ocr_options = doc_pipeline._build_ocr_options(
            ocr_engine=None if eff_ocr_engine == "auto" else eff_ocr_engine,
            ocr_device=None if eff_ocr_device == "auto" else eff_ocr_device,
        )
        all_chunks: List[DocChunk] = []

        doc_extensions = {".md", ".markdown"} | OFFICE_EXTENSIONS | PAPER_EXTENSIONS
        extra_etl_parsers = [
            p
            for p in etl_parsers
            if getattr(getattr(p, "metadata", None), "plugin_id", "")
            not in ("etl.graphify_ast", "etl.multimodal_doc")
        ]

        for root, _, files in os.walk(self.workspace_path):
            rel_root = os.path.relpath(root, self.workspace_path).replace("\\", "/")
            if "docs/kb/exports" in rel_root or any(p.startswith(".") or p in ("node_modules", "venv", ".venv", "tools", "orchestrator", ".agents", ".agent") for p in rel_root.split("/")):
                continue

            for f in files:
                abs_file = Path(root) / f
                rel_file = os.path.relpath(abs_file, self.workspace_path).replace("\\", "/")
                ext = Path(f).suffix.lower()

                if extra_etl_parsers and not rel_root.startswith("docs/kb"):
                    preview = ""
                    if ext not in OFFICE_EXTENSIONS and ext != ".pdf":
                        try:
                            preview = abs_file.read_text(encoding="utf-8", errors="replace")[:512]
                        except Exception:
                            preview = ""
                    for ep in extra_etl_parsers:
                        try:
                            if ep.can_parse(rel_file, content_preview=preview):
                                ep_res = await ep.parse(
                                    str(abs_file),
                                    source_name=rel_file,
                                    project_id=project_id,
                                    branch=branch,
                                )
                                self._merge_delta_dict(graph_delta, ep_res.get("graph_delta"), project_id)
                        except Exception as ep_exc:
                            logger.debug("Extra ETL parser %s skipped %s: %s", getattr(getattr(ep, "metadata", None), "plugin_id", "?"), rel_file, ep_exc)

                if ext not in doc_extensions:
                    continue

                try:
                    if ext in (".md", ".markdown"):
                        content = abs_file.read_text(encoding="utf-8", errors="replace")
                    else:
                        raw_bytes = abs_file.read_bytes()
                        content = (
                            await doc_pipeline._extract_content(abs_file, raw_bytes, ocr_options=ocr_options)
                            if ocr_options is not None
                            else await doc_pipeline._extract_content(abs_file, raw_bytes)
                        )

                    chunks = self.linker.chunk_markdown_document(
                        doc_path=rel_file,
                        content=content,
                        doc_node_id=f"doc:{rel_file}",
                    )
                    all_chunks.extend(chunks)
                except Exception as e:
                    logger.debug("Failed to ingest doc %s: %s", rel_file, e)

        # 5. Build symbol index and emit cross-linking edges & drift via KnowledgeLinkerPlugin
        linker_plugins: List[Any] = []
        if active_registry is not None and hasattr(active_registry, "resolve_all"):
            try:
                linker_plugins = active_registry.resolve_all("knowledge_linker", project_id=project_id)
            except Exception as e:
                logger.debug("knowledge_linker plugin resolution skipped: %s", e)

        code_doc_plugin = next(
            (lp for lp in linker_plugins if getattr(getattr(lp, "metadata", None), "plugin_id", "") == "linker.code_doc"),
            None,
        )

        cross_edges: List[CrossLinkEdge] = []
        drift_items: List[CodeDocDrift] = []
        test_edges: List[CrossLinkEdge] = []

        if code_doc_plugin is not None or active_registry is None:
            if code_doc_plugin is not None and (
                "link" in getattr(code_doc_plugin, "__dict__", {})
                or type(code_doc_plugin).__name__ != "CodeDocLinkerPlugin"
            ):
                custom_res = code_doc_plugin.link(
                    project_id=project_id,
                    branch=branch,
                    graph_data=graph_delta,
                    chunks=all_chunks,
                    context={"workspace_path": str(self.workspace_path)},
                )
                for e_dict in custom_res.get("edges") or []:
                    cross_edges.append(e_dict if isinstance(e_dict, CrossLinkEdge) else CrossLinkEdge(**e_dict))
                for d_dict in custom_res.get("drift_items") or []:
                    drift_items.append(d_dict if isinstance(d_dict, CodeDocDrift) else CodeDocDrift(**d_dict))
            else:
                symbol_index = self.linker.build_symbol_index(graph_delta)
                cross_edges, drift_items = self.linker.link_chunks_to_code(
                    all_chunks,
                    symbol_index,
                    graph_delta,
                    device=eff_device,
                    semantic_engine=eff_semantic_engine,
                )
                test_edges = self.linker.link_tests_to_code(graph_delta, all_chunks)

        for lp in linker_plugins:
            lpid = getattr(getattr(lp, "metadata", None), "plugin_id", "")
            if lpid == "linker.code_doc":
                continue
            try:
                lp_res = lp.link(
                    project_id=project_id,
                    branch=branch,
                    graph_data=graph_delta,
                    chunks=all_chunks,
                    context={"workspace_path": str(self.workspace_path)},
                )
                if isinstance(lp_res, dict):
                    self._merge_delta_dict(graph_delta, lp_res.get("graph_delta"), project_id)
                    for e_dict in lp_res.get("edges") or []:
                        if isinstance(e_dict, CrossLinkEdge):
                            cross_edges.append(e_dict)
                        elif isinstance(e_dict, dict) and {"source_id", "target_id", "relation", "confidence", "confidence_score", "evidence"} <= set(e_dict.keys()):
                            cross_edges.append(CrossLinkEdge(**e_dict))
            except Exception as lp_exc:
                logger.debug("KnowledgeLinkerPlugin %s warning: %s", lpid, lp_exc)

        all_link_edges = cross_edges + test_edges
        self.last_drift_items = list(drift_items)

        # 6. Post-link trace matrix export via DocExporterPlugin
        if generate_office_exports and ("xlsx" in exporters_by_fmt or synthesizer is not None):
            try:
                matrix_path = exports_dir / "ProjectBrain_Traceability_Matrix.xlsx"
                xlsx_linker_index = {
                    "links": [e.model_dump() for e in all_link_edges],
                    "chunks": [c.model_dump() for c in all_chunks],
                    "coverage": {},
                }
                if "xlsx" in exporters_by_fmt:
                    exporters_by_fmt["xlsx"].export(
                        graph_delta,
                        str(matrix_path),
                        linker_index=xlsx_linker_index,
                        context=export_ctx,
                    )
                elif synthesizer is not None:
                    synthesizer.export_xlsx_traceability_matrix(
                        graph_data=graph_delta,
                        output_path=matrix_path,
                        linker_index=xlsx_linker_index,
                    )
            except Exception as e:
                logger.warning("Traceability matrix export warning: %s", e)

        # 7. HSG Cognitive Memory & Waypoints Associative Graph Persistence
        chunk_memory_map: Dict[str, str] = {}
        if persist_memory:
            for chunk in all_chunks:
                mem_id: Optional[str] = None
                try:
                    existing = db.fetchone(
                        "SELECT id, meta FROM memories WHERE meta LIKE ? LIMIT 1",
                        (f'%"{chunk.chunk_id}"%',),
                    )
                    if existing:
                        mem_id = existing["id"]
                except Exception as e:
                    logger.debug("Existing memory check error: %s", e)

                if not mem_id:
                    linked_code_ids = [
                        e.target_id
                        for e in cross_edges
                        if e.source_id == chunk.chunk_id and e.relation in ("documents_symbol", "specifies_endpoint", "conceptually_related_to")
                    ]
                    try:
                        mem_res = await add_hsg_memory(
                            content=chunk.content,
                            tags=f"doc,{chunk.doc_path}",
                            metadata={
                                "chunk_id": chunk.chunk_id,
                                "doc_path": chunk.doc_path,
                                "heading": chunk.heading_breadcrumb,
                                "sha256": chunk.sha256,
                                "doc_node_id": chunk.doc_node_id,
                                "linked_code_node_ids": linked_code_ids,
                            },
                        )
                        mem_id = mem_res.get("id")
                    except Exception as e:
                        logger.debug("add_hsg_memory error for chunk %s: %s", chunk.chunk_id, e)

                if mem_id:
                    chunk_memory_map[chunk.chunk_id] = mem_id

            # Waypoints: create directed associative edges connecting memories of related chunks
            now_ms = int(time.time() * 1000)

            sym_to_chunks: Dict[str, Set[str]] = {}
            for edge in cross_edges:
                if edge.relation in ("documents_symbol", "conceptually_related_to"):
                    sym_to_chunks.setdefault(edge.target_id, set()).add(edge.source_id)

            for edge in graph_delta.edges:
                if edge.relation in ("calls", "imports"):
                    src_sym_chunks = sym_to_chunks.get(edge.source, set())
                    tgt_sym_chunks = sym_to_chunks.get(edge.target, set())

                    for c1 in src_sym_chunks:
                        for c2 in tgt_sym_chunks:
                            if c1 != c2:
                                m1 = chunk_memory_map.get(c1)
                                m2 = chunk_memory_map.get(c2)
                                if m1 and m2:
                                    try:
                                        db.execute(
                                            "INSERT OR REPLACE INTO waypoints (src_id, dst_id, dst_sector, user_id, weight, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                            (m1, m2, "semantic", "system", 1.0, now_ms, now_ms),
                                        )
                                    except Exception as e:
                                        logger.debug("Failed to insert waypoint %s -> %s: %s", m1, m2, e)

        # 8. Enrich GraphNode.metadata in GraphStore
        doc_by_sym: Dict[str, List[str]] = {}
        inf_by_sym: Dict[str, List[str]] = {}
        for edge in cross_edges:
            if edge.relation == "documents_symbol":
                doc_by_sym.setdefault(edge.target_id, []).append(edge.source_id)
            elif edge.relation == "conceptually_related_to":
                inf_by_sym.setdefault(edge.target_id, []).append(edge.source_id)

        test_by_sym: Dict[str, List[str]] = {}
        for edge in test_edges:
            if edge.relation == "verifies_symbol":
                test_by_sym.setdefault(edge.target_id, []).append(edge.source_id)

        drift_by_sym: Dict[str, List[str]] = {}
        for d in drift_items:
            drift_by_sym.setdefault(d.symbol_id, []).append(d.detail)

        # Add route, mcp_tool, and cli symbols into graph_delta.nodes so they are tracked in graph store and coverage
        existing_node_ids = {n.id for n in graph_delta.nodes}
        for s in getattr(self.linker, "last_symbols", []):
            if s.node_id not in existing_node_ids and s.symbol_type in ("route", "mcp_tool", "cli"):
                graph_delta.nodes.append(
                    GraphNode(
                        id=s.node_id,
                        project_id=project_id,
                        label=s.symbol_name,
                        file_type="code",
                        source_file=s.file_path,
                        source_location=f"L{s.start_line}-L{s.end_line}",
                        metadata={
                            "kind": s.symbol_type,
                            "symbol_type": s.symbol_type,
                            "signature": s.signature,
                            "docstring": s.docstring,
                            "route": s.symbol_type == "route",
                            "mcp": s.symbol_type == "mcp_tool",
                            "cli": s.symbol_type == "cli",
                        },
                    )
                )
                existing_node_ids.add(s.node_id)

        # Update metadata on code nodes
        for node in graph_delta.nodes:
            if node.file_type == "code":
                docs = doc_by_sym.get(node.id, [])
                infs = inf_by_sym.get(node.id, [])
                tests = test_by_sym.get(node.id, [])
                drifts = drift_by_sym.get(node.id, [])
                mems = [chunk_memory_map[cid] for cid in docs if cid in chunk_memory_map]

                cov = "documented" if docs else ("inferred" if infs else "undocumented")

                node.metadata.update({
                    "documented_by": docs,
                    "inferred_by": infs,
                    "memory_chunk_ids": mems,
                    "verified_by_tests": tests,
                    "doc_coverage": cov,
                    "drift_alerts": drifts,
                })

        # Add DocChunks as document nodes into graph_delta
        for chunk in all_chunks:
            linked_syms = [
                e.target_id
                for e in cross_edges
                if e.source_id == chunk.chunk_id and e.relation == "documents_symbol"
            ]
            doc_node = GraphNode(
                id=chunk.chunk_id,
                project_id=project_id,
                label=chunk.heading_breadcrumb,
                file_type="document",
                source_file=chunk.doc_path,
                source_location=f"L{chunk.start_line}-L{chunk.end_line}",
                metadata={
                    "chunk_id": chunk.chunk_id,
                    "heading_breadcrumb": chunk.heading_breadcrumb,
                    "memory_id": chunk_memory_map.get(chunk.chunk_id),
                    "documents_symbols": linked_syms,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "sha256": chunk.sha256,
                    "tokens": chunk.tokens,
                    "snippet": chunk.content[:300],
                    "content": chunk.content,
                },
            )
            graph_delta.nodes.append(doc_node)

        # Add cross-domain edges into graph_delta
        for edge in all_link_edges:
            graph_delta.edges.append(
                GraphEdge(
                    project_id=project_id,
                    source=edge.source_id,
                    target=edge.target_id,
                    relation=edge.relation,
                    confidence=edge.confidence,
                    confidence_score=edge.confidence_score,
                    source_file=edge.metadata.get("doc_path") or edge.metadata.get("test_file"),
                    weight=edge.confidence_score,
                )
            )

        # Persist into GraphStore
        self.store.sync_branch_delta(
            project_id=project_id,
            branch_name=branch,
            delta=graph_delta,
            partial=False,
        )

        # 9. Compute repository coverage report & write manifest
        coverage_report = self.get_kb_coverage_report(project_id=project_id, branch=branch)
        coverage_report.drift_items = list(drift_items)
        coverage_report.orphan_docs = list(set([d.symbol_id for d in drift_items if d.drift_type == "removed_symbol_in_doc"]))

        # Export full traceability matrix workbook with populated real coverage stats
        if generate_office_exports and ("xlsx" in exporters_by_fmt or synthesizer is not None):
            try:
                matrix_path = exports_dir / "ProjectBrain_Traceability_Matrix.xlsx"
                final_linker_index = {
                    "links": [e.model_dump() for e in all_link_edges],
                    "chunks": [c.model_dump() for c in all_chunks],
                    "coverage": coverage_report.model_dump(),
                }
                if "xlsx" in exporters_by_fmt:
                    exporters_by_fmt["xlsx"].export(
                        graph_delta,
                        str(matrix_path),
                        linker_index=final_linker_index,
                        context=export_ctx,
                    )
                elif synthesizer is not None:
                    synthesizer.export_xlsx_traceability_matrix(
                        graph_data=graph_delta,
                        output_path=matrix_path,
                        linker_index=final_linker_index,
                    )
            except Exception as e:
                logger.warning("Traceability matrix export warning: %s", e)

        # Invoke any additional custom doc_exporter plugins beyond the standard 4 formats
        for fmt, exp_plugin in exporters_by_fmt.items():
            if fmt in ("wiki", "docx", "pptx", "xlsx"):
                continue
            try:
                custom_out = exports_dir / f"ProjectBrain_Export.{fmt}"
                exp_plugin.export(
                    graph_delta,
                    str(custom_out),
                    linker_index={
                        "links": [e.model_dump() for e in all_link_edges],
                        "chunks": [c.model_dump() for c in all_chunks],
                        "coverage": coverage_report.model_dump(),
                    },
                    context=export_ctx,
                )
            except Exception as e:
                logger.warning("Custom DocExporterPlugin (%s) warning: %s", fmt, e)

        base_out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = base_out_dir / "kb_manifest.json"
        manifest_data = {
            "project_id": project_id,
            "branch": branch,
            "build_timestamp": int(time.time()),
            "stats": coverage_report.model_dump(),
            "drift_count": len(drift_items),
            "artifacts": {
                "wiki_dir": str(wiki_dir),
                "exports_dir": str(exports_dir),
                "manifest_path": str(manifest_path),
            },
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        return {
            "status": "success",
            "project_id": project_id,
            "branch": branch,
            "total_chunks": len(all_chunks),
            "total_cross_edges": len(all_link_edges),
            "drift_count": len(drift_items),
            "cross_source_edges": coverage_report.cross_source_edges,
            "cross_source_drifted_count": coverage_report.cross_source_drifted_count,
            "coverage": coverage_report.model_dump(),
            "manifest_path": str(manifest_path),
        }

    def trace_code_and_docs(
        self,
        project_id: str,
        target: str,
        branch: str = "main",
    ) -> KBTraceResult:
        """
        Given ANY symbol, file path, route, or doc heading, returns unified code+doc+test view.
        """
        if self.store is None:
            db_path = (
                os.getenv("OM_DB_PATH")
                or os.getenv("PB_DB_PATH")
                or (str(self.workspace_path / ".projectbrain" / "kb" / "projectbrain_kb.db")
                    if (self.workspace_path / ".projectbrain" / "kb" / "projectbrain_kb.db").exists()
                    else None)
            )
            self.store = get_graph_store(db_path=db_path)

        subgraph = self.store.get_resolved_subgraph(project_id, branch, limit=100000)
        nodes = subgraph.get("nodes", [])
        edges = subgraph.get("edges", [])

        target_clean = target.strip()
        target_lower = target_clean.lower()

        # Identify target node(s)
        matched_code_nodes: List[Dict[str, Any]] = []
        matched_doc_nodes: List[Dict[str, Any]] = []
        matched_test_nodes: List[Dict[str, Any]] = []

        for n in nodes:
            nid = n.get("id") or ""
            lbl = n.get("label") or ""
            src = n.get("source_file") or ""
            ftype = n.get("file_type") or "code"
            meta = n.get("metadata") or {}

            # Direct match criteria
            is_match = (
                nid == target_clean
                or lbl == target_clean
                or lbl.lower() == target_lower
                or src == target_clean
                or src.endswith(target_clean)
                or target_clean in lbl
                or (ftype == "document" and (
                    target_clean in meta.get("heading_breadcrumb", "")
                    or target_clean in meta.get("content", "")
                    or target_clean in meta.get("snippet", "")
                ))
            )

            if is_match:
                if ftype == "document":
                    matched_doc_nodes.append(n)
                elif src.startswith("tests/") or lbl.startswith("test_"):
                    matched_test_nodes.append(n)
                else:
                    matched_code_nodes.append(n)

        # Collect linked nodes across edges
        matched_code_ids = {n["id"] for n in matched_code_nodes}
        matched_doc_ids = {n["id"] for n in matched_doc_nodes}

        callers: List[str] = []
        callees: List[str] = []

        for e in edges:
            src = e.get("source")
            tgt = e.get("target")
            rel = e.get("relation")

            # Document links
            if src in matched_code_ids and rel in ("documented_in", "specified_by"):
                for n in nodes:
                    if n.get("id") == tgt and n not in matched_doc_nodes:
                        matched_doc_nodes.append(n)
            elif tgt in matched_code_ids and rel in ("documents_symbol", "conceptually_related_to", "specifies_endpoint"):
                for n in nodes:
                    if n.get("id") == src and n not in matched_doc_nodes:
                        matched_doc_nodes.append(n)

            # Test links
            if tgt in matched_code_ids and rel == "verifies_symbol":
                for n in nodes:
                    if n.get("id") == src and n not in matched_test_nodes:
                        matched_test_nodes.append(n)
            elif tgt in matched_doc_ids and rel == "verifies_requirement":
                for n in nodes:
                    if n.get("id") == src and n not in matched_test_nodes:
                        matched_test_nodes.append(n)

            # Call graph links
            if tgt in matched_code_ids and rel in ("calls", "imports"):
                callers.append(src)
            elif src in matched_code_ids and rel in ("calls", "imports"):
                callees.append(tgt)

        # Determine coverage status
        coverage_status = "undocumented"
        for n in matched_code_nodes:
            cov = (n.get("metadata") or {}).get("doc_coverage")
            if cov == "documented":
                coverage_status = "documented"
                break
            elif cov == "inferred" and coverage_status != "documented":
                coverage_status = "inferred"

        # Check drift
        drift_list: List[CodeDocDrift] = []
        for n in matched_code_nodes:
            meta = n.get("metadata") or {}
            for d_str in meta.get("drift_alerts", []):
                drift_list.append(
                    CodeDocDrift(
                        symbol_id=n.get("id", ""),
                        doc_chunk_id="",
                        drift_type="signature_mismatch",
                        severity="high",
                        detail=d_str,
                        source_file=n.get("source_file", ""),
                        doc_path="",
                    )
                )

        # Also pull from self.last_drift_items if matching
        for d in self.last_drift_items:
            if d.symbol_id in matched_code_ids or d.doc_chunk_id in matched_doc_ids or target_clean in d.symbol_id:
                if d not in drift_list:
                    drift_list.append(d)

        # Format doc chunks nicely
        formatted_doc_chunks = []
        for d in matched_doc_nodes:
            meta = d.get("metadata") or {}
            formatted_doc_chunks.append({
                "chunk_id": d.get("id", ""),
                "heading_breadcrumb": meta.get("heading_breadcrumb", d.get("label", "")),
                "doc_path": d.get("source_file", ""),
                "start_line": meta.get("start_line", 0),
                "end_line": meta.get("end_line", 0),
                "memory_id": meta.get("memory_id", ""),
                "snippet": meta.get("snippet", ""),
                "content": meta.get("content", ""),
            })

        return KBTraceResult(
            query=target,
            code_nodes=matched_code_nodes,
            doc_chunks=formatted_doc_chunks,
            tests=matched_test_nodes,
            callers=list(set(callers)),
            callees=list(set(callees)),
            coverage_status=coverage_status,
            drift=drift_list,
        )

    def get_kb_coverage_report(
        self,
        project_id: str,
        branch: str = "main",
    ) -> KBCoverageReport:
        """
        Compute repository-wide documentation, test linkage, and drift analytics.
        """
        if self.store is None:
            db_path = (
                os.getenv("OM_DB_PATH")
                or os.getenv("PB_DB_PATH")
                or (str(self.workspace_path / ".projectbrain" / "kb" / "projectbrain_kb.db")
                    if (self.workspace_path / ".projectbrain" / "kb" / "projectbrain_kb.db").exists()
                    else None)
            )
            self.store = get_graph_store(db_path=db_path)

        subgraph = self.store.get_resolved_subgraph(project_id, branch, limit=100000)
        nodes = subgraph.get("nodes", [])
        edges = subgraph.get("edges", [])

        code_nodes = [
            n for n in nodes
            if n.get("file_type") == "code"
            and not (n.get("source_file") or "").startswith("tests/")
            and not (n.get("label") or "").startswith("test_")
            and not (n.get("label") or "").endswith(".py")
            and (n.get("metadata") or {}).get("kind") not in ("module", "file", "rationale")
            and not (n.get("id") or "").startswith(("import_", "rationale_"))
            and "_rationale_" not in (n.get("id") or "")
        ]

        doc_nodes = [n for n in nodes if n.get("file_type") == "document"]

        total_symbols = len(code_nodes)
        documented_count = 0
        inferred_count = 0
        test_linked_count = 0

        total_routes = 0
        doc_routes = 0
        total_mcps = 0
        doc_mcps = 0

        undocumented_key_symbols: List[Dict[str, Any]] = []

        for n in code_nodes:
            meta = n.get("metadata") or {}
            cov = meta.get("doc_coverage") or "undocumented"
            tests = meta.get("verified_by_tests") or []

            is_route = (meta.get("kind") == "route") or (meta.get("symbol_type") == "route") or bool(meta.get("route")) or (n.get("id") or "").startswith("route_")
            is_mcp = (meta.get("kind") == "mcp_tool") or (meta.get("symbol_type") == "mcp_tool") or bool(meta.get("mcp"))

            if is_route:
                total_routes += 1
            if is_mcp:
                total_mcps += 1

            if cov == "documented":
                documented_count += 1
                if is_route:
                    doc_routes += 1
                if is_mcp:
                    doc_mcps += 1
            elif cov == "inferred":
                inferred_count += 1
            else:
                undocumented_key_symbols.append({
                    "id": n.get("id"),
                    "label": n.get("label"),
                    "source_file": n.get("source_file"),
                })

            if tests:
                test_linked_count += 1

        undocumented_count = max(0, total_symbols - documented_count - inferred_count)

        doc_pct = round((documented_count / total_symbols * 100), 2) if total_symbols > 0 else 0.0
        inf_pct = round((inferred_count / total_symbols * 100), 2) if total_symbols > 0 else 0.0
        undoc_pct = round((undocumented_count / total_symbols * 100), 2) if total_symbols > 0 else 0.0
        api_pct = round((doc_routes / total_routes * 100), 2) if total_routes > 0 else 0.0
        mcp_pct = round((doc_mcps / total_mcps * 100), 2) if total_mcps > 0 else 0.0
        test_pct = round((test_linked_count / total_symbols * 100), 2) if total_symbols > 0 else 0.0

        cross_domain_edges = sum(
            1 for e in edges
            if e.get("relation") in ("documents_symbol", "documented_in", "specifies_endpoint", "verifies_symbol", "verifies_requirement", "conceptually_related_to")
        )

        orphan_docs = list(set([d.symbol_id for d in self.last_drift_items if d.drift_type == "removed_symbol_in_doc"]))

        cross_source_edges = 0
        cross_source_drifted_count = 0
        cross_source_drift_items: List[Dict[str, Any]] = []
        try:
            from projectbrain.core.tenant_manager import tenant_manager

            cs_rels = tenant_manager.list_cross_source_relations(project_id)
            non_rejected = [r for r in cs_rels if r.get("status") != "rejected"]
            cross_source_edges = len(non_rejected)
            for r in non_rejected:
                if r.get("status") == "drifted":
                    c_meta = r.get("contract_metadata") or {}
                    cross_source_drift_items.append(
                        {
                            "id": r.get("id"),
                            "project_id": r.get("project_id"),
                            "src_source_id": r.get("src_source_id"),
                            "src_branch": r.get("src_branch"),
                            "dst_source_id": r.get("dst_source_id"),
                            "dst_branch": r.get("dst_branch"),
                            "relation_type": r.get("relation_type"),
                            "contract_key": r.get("contract_key"),
                            "status": r.get("status"),
                            "drift_reason": c_meta.get("drift_reason") or "Provider contract drifted",
                            "src_file_path": r.get("src_file_path"),
                            "dst_doc_key": r.get("dst_doc_key"),
                            "updated_at": r.get("updated_at"),
                        }
                    )
            cross_source_drifted_count = len(cross_source_drift_items)
        except Exception as exc:
            logger.debug("Cross-source coverage lookup skipped for %s: %s", project_id, exc)

        return KBCoverageReport(
            project_id=project_id,
            branch=branch,
            total_symbols=total_symbols,
            documented_symbols=documented_count,
            documented_pct=doc_pct,
            inferred_symbols=inferred_count,
            inferred_pct=inf_pct,
            undocumented_symbols=undocumented_count,
            undocumented_pct=undoc_pct,
            api_coverage_pct=api_pct,
            mcp_coverage_pct=mcp_pct,
            test_linked_symbols=test_linked_count,
            test_linked_pct=test_pct,
            total_doc_chunks=len(doc_nodes),
            cross_domain_edges=cross_domain_edges,
            cross_source_edges=cross_source_edges,
            cross_source_drifted_count=cross_source_drifted_count,
            cross_source_drift_items=cross_source_drift_items,
            orphan_docs=orphan_docs,
            undocumented_key_symbols=undocumented_key_symbols[:20],
            drift_items=list(self.last_drift_items),
        )
