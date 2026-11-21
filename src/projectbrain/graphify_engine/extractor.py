"""
Graphify AST & Semantic Document Extractor Engine.

Integrates with `graphify` (v0.3.28) for zero-token deterministic Tree-sitter AST
extraction across 22 programming languages, Python AST fallback, Markdown & document
semantic entity/rationale parsing, SHA256 content+path caching, Leiden/Louvain community
clustering, architectural God-node & cross-community bridge analysis, and local partial graph persistence.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

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

    def __init__(self, cache_dir: Optional[Union[str, Path]] = None):
        self.cache_dir = Path(cache_dir or ".projectbrain/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
        """Atomically persist extraction delta into self.cache_dir."""
        entry = self.cache_dir / f"{cache_key}.json"
        tmp = entry.with_suffix(".tmp")
        try:
            payload = delta.to_dict() if hasattr(delta, "to_dict") else delta.model_dump()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, entry)
        except Exception as e:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            logger.debug("Failed to write cache entry %s: %s", entry, e)

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
    ) -> GraphDelta:
        """
        Extract code AST and semantic document entities across a workspace.

        Supports:
        - Full workspace scanning.
        - Partial scoped extraction via `scoped_paths`.
        - Incremental git dirty scan (`git_dirty_only=True`).
        - Git-diff auto-delta against `base_branch` (`base_branch...HEAD` + uncommitted changes).
        - SHA256 content+path caching.
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

        # 2. Filter & Process Files
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        hyperedges: List[GraphHyperedge] = []
        synced_files: List[Dict[str, Any]] = []

        for fpath in target_files:
            if not fpath.is_file():
                continue
            try:
                rel_path = str(fpath.relative_to(ws_root)).replace("\\", "/")
            except ValueError:
                rel_path = str(fpath).replace("\\", "/")

            if any(part in ("tools", "orchestrator", ".agents", "node_modules", ".venv", "venv", "__pycache__") for part in rel_path.split("/")):
                continue

            ext = fpath.suffix.lower()
            is_code = ext in CODE_EXTENSIONS
            is_doc = include_docs and (ext in DOC_EXTENSIONS)

            if not (is_code or is_doc):
                continue

            cache_key, content_sha256 = self.compute_file_hashes(fpath)
            synced_files.append({"file_path": rel_path, "sha256": content_sha256})

            # Check cache
            cached_delta: Optional[GraphDelta] = None
            if use_cache:
                cached_delta = self.load_cached_delta(cache_key, project_id, branch)

            if cached_delta is not None:
                nodes.extend(cached_delta.nodes)
                edges.extend(cached_delta.edges)
                hyperedges.extend(cached_delta.hyperedges)
                continue

            # Cache miss: execute extraction
            file_delta: GraphDelta
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

            # Persist to cache
            if use_cache:
                self.save_cached_delta(cache_key, file_delta)

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

        return GraphDelta(
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
    # Graph Clustering & Cohesion Analysis
    # -----------------------------------------------------------------------
    def cluster_graph(self, delta: GraphDelta, method: str = "leiden") -> GraphDelta:
        """
        Apply community detection (Leiden via graspologic with Louvain fallback)
        to the GraphDelta, splitting oversized communities (>25% total nodes, min 10),
        calculating cohesion scores, and assigning community IDs to nodes.
        """
        if not delta.nodes:
            return delta

        G = nx.Graph()
        for n in delta.nodes:
            G.add_node(n.id, label=n.label, file_type=n.file_type, source_file=n.source_file)
        for e in delta.edges:
            G.add_edge(e.source, e.target, relation=e.relation, weight=e.weight)

        communities: Dict[int, List[str]] = {}
        try:
            import graphify.cluster as g_cluster  # type: ignore

            communities = g_cluster.cluster(G)
            scores = g_cluster.score_all(G, communities)
        except Exception as e:
            logger.debug("graphify.cluster error: %s. Using internal Louvain.", e)
            try:
                raw_comm = nx.community.louvain_communities(G, seed=42)
                communities = {i: sorted(list(c)) for i, c in enumerate(raw_comm)}
            except Exception:
                communities = {0: sorted(list(G.nodes()))}
            # Compute internal cohesion score
            scores = {}
            for cid, members in communities.items():
                n_count = len(members)
                if n_count <= 1:
                    scores[cid] = 1.0
                else:
                    sub = G.subgraph(members)
                    actual = sub.number_of_edges()
                    possible = n_count * (n_count - 1) / 2
                    scores[cid] = round(actual / possible, 2) if possible > 0 else 0.0

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

        return delta

    # -----------------------------------------------------------------------
    # God Nodes & Surprising Connections
    # -----------------------------------------------------------------------
    def compute_god_nodes_and_bridges(
        self,
        delta: GraphDelta,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """
        Isolate architectural core God nodes (degree centrality, excluding file hubs)
        and cross-community bridge edges (surprising connections).
        """
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

        try:
            import graphify.analyze as g_analyze  # type: ignore

            god_nodes_res = g_analyze.god_nodes(G, top_n=top_k)
        except Exception:
            pass

        # Fallback for god nodes if graphify returned empty or failed
        if not god_nodes_res and G.number_of_nodes() > 0:
            deg = dict(G.degree())
            sorted_deg = sorted(deg.items(), key=lambda x: x[1], reverse=True)
            for nid, d in sorted_deg:
                data = G.nodes[nid]
                if data.get("file_type") == "document" and data.get("label", "").endswith(".md"):
                    continue
                god_nodes_res.append({"id": nid, "label": data.get("label", nid), "edges": d})
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
            scores = {cid: 1.0 for cid in comm_map}

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
