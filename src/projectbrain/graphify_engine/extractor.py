"""
Graphify AST & Semantic Document Extractor Engine.

Integrates with `graphify` (v0.3.28) for zero-token deterministic Tree-sitter AST
extraction across 22 programming languages, Python AST fallback, Markdown & document
semantic entity/rationale parsing, SHA256 content+path caching, Leiden/Louvain community
clustering, architectural God-node & cross-community bridge analysis, and local partial graph persistence.
"""

from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

logger = logging.getLogger(__name__)

# Guard external graphify.extract calls in case Tree-sitter reuses module-level C Parser state.
_TREE_SITTER_LOCK = threading.Lock()

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
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
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
    text = content.decode("utf-8", errors="replace")
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
    # Code AST Extraction (graphify + Python AST Fallback)
    # -----------------------------------------------------------------------
    def _extract_code_ast(
        self,
        file_path: Path,
        rel_path: str,
        project_id: str,
        branch: str,
    ) -> GraphDelta:
        """Extract code symbols using `graphify.extract.extract` with Python AST fallback."""
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []

        # 1. Try graphify tree-sitter extraction
        extracted_ok = False
        try:
            import graphify.extract as g_extract  # type: ignore

            with _TREE_SITTER_LOCK:
                raw = g_extract.extract([file_path])
            if raw and (raw.get("nodes") or raw.get("edges")):
                for n in raw.get("nodes", []):
                    nid = str(n.get("id") or "")
                    if not nid:
                        continue
                    nodes.append(
                        GraphNode(
                            id=nid,
                            project_id=project_id,
                            label=str(n.get("label") or nid),
                            file_type="code",
                            source_file=rel_path,
                            source_location=n.get("source_location"),
                            metadata={
                                k: v
                                for k, v in n.items()
                                if k not in ("id", "label", "file_type", "source_file", "source_location", "community")
                            },
                        )
                    )
                for e in raw.get("edges", []):
                    src = str(e.get("source") or "")
                    tgt = str(e.get("target") or "")
                    if not src or not tgt:
                        continue
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=src,
                            target=tgt,
                            relation=str(e.get("relation") or "calls"),
                            confidence=str(e.get("confidence") or "EXTRACTED"),
                            confidence_score=float(e.get("confidence_score", 1.0)),
                            source_file=rel_path,
                            source_location=e.get("source_location"),
                            weight=float(e.get("weight", 1.0)),
                        )
                    )
                extracted_ok = True
        except Exception as err:
            logger.debug("graphify.extract failed on %s: %s. Falling back to internal AST.", rel_path, err)

        # 2. Python AST Fallback if graphify produced nothing or errored on Python
        if not extracted_ok and file_path.suffix.lower() in (".py", ".pyw"):
            nodes, edges = self._python_ast_fallback(file_path, rel_path, project_id)

        return GraphDelta(
            project_id=project_id,
            branch=branch,
            nodes=nodes,
            edges=edges,
            hyperedges=[],
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
            content = file_path.read_text(encoding="utf-8", errors="replace")
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
                        text_content = fpath.read_text(encoding="utf-8", errors="replace")
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

            with concurrent.futures.ThreadPoolExecutor(max_workers=resolved_workers) as executor:
                for idx, file_delta in executor.map(_extract_single_file, uncached_tasks):
                    per_file_deltas[idx] = file_delta
        else:
            for task in uncached_tasks:
                idx, file_delta = _extract_single_file(task)
                per_file_deltas[idx] = file_delta

        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        hyperedges: List[GraphHyperedge] = []
        for file_delta in per_file_deltas:
            if file_delta is not None:
                nodes.extend(file_delta.nodes)
                edges.extend(file_delta.edges)
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
