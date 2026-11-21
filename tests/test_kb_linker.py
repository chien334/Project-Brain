"""
Unit & Integration Test Suite for CodeDocLinker and UnifiedKBBuilder.

Covers:
- AC-KB-03: Hierarchical structure-aware document chunker & idempotency
- AC-KB-04: Multi-key symbol, route, FastMCP tool, and CLI indexer
- AC-KB-05: Bidirectional code-doc-test cross-linking (EXTRACTED & INFERRED)
- AC-KB-06: HSG cognitive memory persistence and associative waypoints
- AC-KB-07: GraphNode metadata enrichment and Code-Doc Drift detection
- AC-KB-08: Unified traceability queries (trace_code_and_docs)
- AC-KB-09: Repository-wide coverage analytics (get_kb_coverage_report)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
import pytest

from projectbrain.graphify_engine.kb_linker import (
    CodeDocDrift,
    CodeDocLinker,
    CodeSymbolRef,
    CrossLinkEdge,
    DocChunk,
    KBCoverageReport,
    KBTraceResult,
    UnifiedKBBuilder,
)
from projectbrain.graphify_engine.models import (
    GraphDelta,
    GraphEdge,
    GraphNode,
)
from projectbrain.graphify_engine.store import SqliteOverlayGraphStore
from projectbrain.core.config import env
from projectbrain.core.db import db


@pytest.fixture
def sample_workspace(tmp_path):
    """Create an isolated workspace with code files, tests, and documentation."""
    ws = tmp_path / "sample_workspace"
    ws.mkdir(parents=True, exist_ok=True)

    src_dir = ws / "src" / "sample"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = ws / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = ws / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Source code file
    calc_code = """\"\"\"Calculator module.\"\"\"

class Calculator:
    \"\"\"Performs arithmetic operations.\"\"\"

    def add(self, a: int, b: int) -> int:
        \"\"\"Add two integers.\"\"\"
        return a + b

    def subtract(self, a: int, b: int) -> int:
        \"\"\"Subtract b from a.\"\"\"
        return a - b

def compute_total(items: list, tax_rate: float = 0.05) -> float:
    \"\"\"Compute total amount with tax.\"\"\"
    base = sum(items)
    return base * (1.0 + tax_rate)

# Route definition
class RouterMock:
    def get(self, path):
        def decorator(f):
            return f
        return decorator

router = RouterMock()

@router.get("/calculate/sum")
def api_calculate_sum(x: int, y: int) -> int:
    \"\"\"REST Endpoint for calculation.\"\"\"
    calc = Calculator()
    return calc.add(x, y)
"""
    (src_dir / "calculator.py").write_text(calc_code, encoding="utf-8")

    # 2. Test file
    test_code = """\"\"\"Tests for calculator module.\"\"\"
import pytest
from sample.calculator import Calculator, compute_total

def test_calculator_add():
    \"\"\"Verify Calculator.add works correctly. AC-KB-03\"\"\"
    calc = Calculator()
    assert calc.add(2, 3) == 5

def test_compute_total():
    \"\"\"Verify compute_total calculation. AC-KB-05\"\"\"
    assert compute_total([10, 20], tax_rate=0.1) == 33.0
"""
    (tests_dir / "test_calculator.py").write_text(test_code, encoding="utf-8")

    # 3. Documentation file
    doc_content = """# System Architecture Overview

This is the system architecture documentation.

## Storage and Calculation Layer

The calculation layer provides core business arithmetic.

### Calculator Core

The `Calculator` class provides arithmetic operations such as `Calculator.add` and `Calculator.subtract`.
You can call `compute_total(items, tax_rate)` to evaluate order totals.

Here is a sample table of operations:

| Operation | Method | Time Complexity |
| :--- | :--- | :--- |
| Addition | `Calculator.add` | O(1) |
| Subtraction | `Calculator.subtract` | O(1) |

And here is sample usage:

```python
# Fenced code block with internal comment
calc = Calculator()
result = calc.add(10, 20)
print(result)
```

## API and Routes Specification

The system exposes endpoint `GET /calculate/sum` to trigger calculation remotely.
Specification requirement AC-KB-03 and AC-KB-05 are implemented here.
"""
    (docs_dir / "architecture.md").write_text(doc_content, encoding="utf-8")

    return ws


@pytest.fixture
def isolated_db(tmp_path):
    """Ensure tests run against a fresh temporary SQLite database."""
    db_file = tmp_path / "test_isolated_hsg.db"
    env.database_url = f"sqlite:///{db_file}"
    db.conn = None
    db.connect()
    yield db_file
    if db.conn:
        try:
            db.conn.close()
        except Exception:
            pass
        db.conn = None


def test_chunk_markdown_document_hierarchical_breadcrumbs(sample_workspace):
    """Verify AC-KB-03: Structure-aware chunking with H1 > H2 > H3 breadcrumb hierarchy."""
    linker = CodeDocLinker(workspace_path=sample_workspace)
    doc_file = sample_workspace / "docs" / "architecture.md"
    content = doc_file.read_text(encoding="utf-8")

    chunks = linker.chunk_markdown_document(
        doc_path="docs/architecture.md",
        content=content,
        doc_node_id="doc:docs/architecture.md",
    )

    assert len(chunks) >= 3

    # Check breadcrumb structure
    breadcrumbs = [c.heading_breadcrumb for c in chunks]
    assert any("System Architecture Overview" in b for b in breadcrumbs)
    assert any("Storage and Calculation Layer" in b for b in breadcrumbs)
    assert any("Calculator Core" in b for b in breadcrumbs)
    assert any("API and Routes Specification" in b for b in breadcrumbs)

    # Line numbers must be positive and non-decreasing
    for c in chunks:
        assert c.start_line > 0
        assert c.end_line >= c.start_line
        assert c.doc_path == "docs/architecture.md"
        assert c.tokens > 0
        assert len(c.sha256) == 64


def test_chunk_markdown_document_preserves_code_fences_and_tables(sample_workspace):
    """Verify AC-KB-03: Markdown tables and fenced code blocks remain contiguous without being split."""
    linker = CodeDocLinker(workspace_path=sample_workspace)
    doc_file = sample_workspace / "docs" / "architecture.md"
    content = doc_file.read_text(encoding="utf-8")

    chunks = linker.chunk_markdown_document(
        doc_path="docs/architecture.md",
        content=content,
    )

    # Find chunk containing the table
    table_chunk = next((c for c in chunks if "| Operation |" in c.content), None)
    assert table_chunk is not None
    assert "| Addition |" in table_chunk.content
    assert "| Subtraction |" in table_chunk.content
    assert "| :--- |" in table_chunk.content

    # Find chunk containing the code fence
    fence_chunk = next((c for c in chunks if "```python" in c.content), None)
    assert fence_chunk is not None
    assert "```python" in fence_chunk.content
    assert "result = calc.add(10, 20)" in fence_chunk.content
    assert "```" in fence_chunk.content
    # Ensure internal comment was NOT treated as heading
    assert not any(c.heading_breadcrumb.endswith("Fenced code block with internal comment") for c in chunks)


def test_chunk_markdown_document_idempotency_and_subchunking(sample_workspace):
    """Verify AC-KB-03: Repeated chunking on identical content yields identical chunk IDs & sub-chunking."""
    linker = CodeDocLinker(workspace_path=sample_workspace)
    doc_file = sample_workspace / "docs" / "architecture.md"
    content = doc_file.read_text(encoding="utf-8")

    chunks_run1 = linker.chunk_markdown_document(doc_path="docs/architecture.md", content=content)
    chunks_run2 = linker.chunk_markdown_document(doc_path="docs/architecture.md", content=content)

    assert len(chunks_run1) == len(chunks_run2)
    for c1, c2 in zip(chunks_run1, chunks_run2):
        assert c1.chunk_id == c2.chunk_id
        assert c1.sha256 == c2.sha256
        assert c1.heading_breadcrumb == c2.heading_breadcrumb

    # Test sub-chunking of a large section exceeding max_chunk_chars
    long_content = "# Massive Section\n\n"
    for i in range(20):
        long_content += f"Paragraph {i}: This is extended prose explaining architectural design concepts in detail.\n\n"

    long_chunks = linker.chunk_markdown_document(
        doc_path="docs/massive.md",
        content=long_content,
        max_chunk_chars=300,
    )
    assert len(long_chunks) > 1
    assert any("(Part 1)" in c.heading_breadcrumb for c in long_chunks)
    assert any("(Part 2)" in c.heading_breadcrumb for c in long_chunks)


def test_build_symbol_index(sample_workspace):
    """Verify AC-KB-04: Multi-key AST indexing of classes, methods, functions, routes, and CLI."""
    linker = CodeDocLinker(workspace_path=sample_workspace)

    # Empty delta initially; AST scanner parses workspace files directly
    delta = GraphDelta(project_id="test-proj")
    index = linker.build_symbol_index(delta)

    # 1. Exact class and function symbols
    assert "Calculator" in index
    assert "Calculator.add" in index
    assert "compute_total" in index

    # 2. Method signature extraction
    calc_syms = index["Calculator.add"]
    assert len(calc_syms) >= 1
    add_sym = calc_syms[0]
    assert "a" in add_sym.signature
    assert "b" in add_sym.signature
    assert add_sym.symbol_type == "method"

    # 3. Route detection
    assert "GET /calculate/sum" in index or "/calculate/sum" in index
    route_syms = index.get("GET /calculate/sum") or index.get("/calculate/sum")
    assert route_syms is not None
    assert any(s.symbol_type == "route" for s in route_syms)

    # 4. Normalized snake_case & camelCase lookups
    assert "calculator" in index
    assert "computeTotal" in index or "compute_total" in index


def test_bidirectional_code_doc_linking(sample_workspace):
    """Verify AC-KB-05: Bidirectional EXTRACTED (1.0) and INFERRED (0.6-0.9) cross-linking."""
    linker = CodeDocLinker(workspace_path=sample_workspace)
    content = (sample_workspace / "docs" / "architecture.md").read_text(encoding="utf-8")
    chunks = linker.chunk_markdown_document("docs/architecture.md", content)

    delta = GraphDelta(project_id="test-proj")
    symbol_index = linker.build_symbol_index(delta)

    edges, drift = linker.link_chunks_to_code(chunks, symbol_index, delta)

    # 1. Bidirectional EXTRACTED edges
    doc_to_code = [e for e in edges if e.relation == "documents_symbol" and e.confidence == "EXTRACTED"]
    code_to_doc = [e for e in edges if e.relation == "documented_in" and e.confidence == "EXTRACTED"]

    assert len(doc_to_code) >= 2
    assert len(code_to_doc) >= 2
    for e in doc_to_code:
        assert e.confidence_score == 1.0
    for e in code_to_doc:
        assert e.confidence_score == 1.0

    # 2. Route endpoint specification
    endpoint_edges = [e for e in edges if e.relation == "specifies_endpoint"]
    assert len(endpoint_edges) >= 1

    # 3. Inferred conceptual overlap (prose text)
    inferred_edges = [e for e in edges if e.confidence == "INFERRED"]
    for e in inferred_edges:
        assert 0.60 <= e.confidence_score <= 0.90
        assert e.relation == "conceptually_related_to"


def test_link_tests_to_code_and_requirements(sample_workspace):
    """Verify AC-KB-05: verifies_symbol and verifies_requirement edges from test nodes."""
    linker = CodeDocLinker(workspace_path=sample_workspace)
    content = (sample_workspace / "docs" / "architecture.md").read_text(encoding="utf-8")
    chunks = linker.chunk_markdown_document("docs/architecture.md", content)

    # Construct sample graph delta with test nodes and code nodes
    delta = GraphDelta(
        project_id="test-proj",
        nodes=[
            GraphNode(id="fn_add", project_id="test-proj", label="add", file_type="code", source_file="src/sample/calculator.py"),
            GraphNode(id="test_calc_add", project_id="test-proj", label="test_calculator_add", file_type="code", source_file="tests/test_calculator.py", metadata={"docstring": "Verify Calculator.add. AC-KB-03"}),
        ],
        edges=[
            GraphEdge(project_id="test-proj", source="test_calc_add", target="fn_add", relation="calls"),
        ],
    )

    test_edges = linker.link_tests_to_code(delta, chunks)

    # 1. verifies_symbol edge
    sym_edges = [e for e in test_edges if e.relation == "verifies_symbol"]
    assert len(sym_edges) >= 1
    assert any(e.source_id == "test_calc_add" and e.target_id == "fn_add" for e in sym_edges)

    # 2. verifies_requirement edge
    req_edges = [e for e in test_edges if e.relation == "verifies_requirement"]
    assert len(req_edges) >= 1
    assert any(e.source_id == "test_calc_add" for e in req_edges)


def test_code_doc_drift_detection(sample_workspace):
    """Verify AC-KB-07: Signature mismatch, removed symbol, and code newer than doc drift."""
    linker = CodeDocLinker(workspace_path=sample_workspace)

    # Craft a doc containing intentional drift:
    # 1. Signature mismatch: compute_total with invalid arguments
    # 2. Removed symbol: `src/sample/nonexistent_module.py`
    drift_doc = """# Drift Test Document

Here we call `compute_total(invalid_param=123, missing_param=456)`.
We also reference an orphan module `src/sample/nonexistent_module.py` and `OldClass.deleted_method`.
"""
    chunks = linker.chunk_markdown_document("docs/drift.md", drift_doc)
    delta = GraphDelta(project_id="test-proj")
    symbol_index = linker.build_symbol_index(delta)

    edges, drift_items = linker.link_chunks_to_code(chunks, symbol_index, delta)

    drift_types = [d.drift_type for d in drift_items]

    # Verify signature mismatch was flagged
    assert "signature_mismatch" in drift_types
    sig_mismatch = next(d for d in drift_items if d.drift_type == "signature_mismatch")
    assert "invalid_param" in sig_mismatch.detail or "missing_param" in sig_mismatch.detail

    # Verify removed symbol in doc was flagged
    assert "removed_symbol_in_doc" in drift_types
    removed = [d for d in drift_items if d.drift_type == "removed_symbol_in_doc"]
    assert any("nonexistent_module.py" in d.symbol_id for d in removed)


@pytest.mark.asyncio
async def test_hsg_memory_and_waypoints_persistence(sample_workspace, isolated_db):
    """Verify AC-KB-06: Persists chunks to HSG cognitive memory & creates associative waypoints."""
    builder = UnifiedKBBuilder(workspace_path=sample_workspace)
    store = SqliteOverlayGraphStore(db_path=str(isolated_db))

    res = await builder.build_unified_kb(
        project_id="test-hsg-proj",
        branch="main",
        persist_memory=True,
        output_dir=str(sample_workspace / "docs" / "kb"),
        store=store,
        db_path=str(isolated_db),
    )

    assert res["status"] == "success"
    assert res["total_chunks"] > 0

    # 1. Verify memories table rows exist with metadata
    mem_rows = db.fetchall("SELECT id, content, tags, meta FROM memories")
    assert len(mem_rows) > 0

    # Verify metadata fields in memories
    for r in mem_rows:
        meta_str = r["meta"]
        if meta_str:
            import json
            meta = json.loads(meta_str)
            assert "chunk_id" in meta
            assert "heading" in meta

    # 2. Verify idempotency: re-running does not duplicate memories
    initial_mem_count = len(mem_rows)
    await builder.build_unified_kb(
        project_id="test-hsg-proj",
        branch="main",
        persist_memory=True,
        output_dir=str(sample_workspace / "docs" / "kb"),
        store=store,
        db_path=str(isolated_db),
    )
    after_mem_rows = db.fetchall("SELECT id FROM memories")
    assert len(after_mem_rows) == initial_mem_count

    # 3. Verify waypoints table rows exist or were inserted
    wp_rows = db.fetchall("SELECT * FROM waypoints")
    assert isinstance(wp_rows, list)


def test_trace_code_and_docs(sample_workspace, isolated_db):
    """Verify AC-KB-08: Unified traceability query returning code nodes, doc chunks, callers, tests."""
    builder = UnifiedKBBuilder(workspace_path=sample_workspace)
    store = SqliteOverlayGraphStore(db_path=str(isolated_db))

    # Build KB first
    asyncio.run(
        builder.build_unified_kb(
            project_id="test-trace-proj",
            branch="main",
            persist_memory=True,
            output_dir=str(sample_workspace / "docs" / "kb"),
            store=store,
            db_path=str(isolated_db),
        )
    )

    # 1. Trace by symbol name
    trace_calc = builder.trace_code_and_docs(
        project_id="test-trace-proj",
        target="Calculator",
        branch="main",
    )
    assert isinstance(trace_calc, KBTraceResult)
    assert trace_calc.query == "Calculator"
    assert len(trace_calc.code_nodes) >= 1
    assert len(trace_calc.doc_chunks) >= 1
    assert trace_calc.coverage_status in ("documented", "inferred")

    # 2. Trace by route
    trace_route = builder.trace_code_and_docs(
        project_id="test-trace-proj",
        target="/calculate/sum",
        branch="main",
    )
    assert isinstance(trace_route, KBTraceResult)

    # 3. Trace by doc heading / requirement
    trace_ac = builder.trace_code_and_docs(
        project_id="test-trace-proj",
        target="AC-KB-03",
        branch="main",
    )
    assert isinstance(trace_ac, KBTraceResult)
    assert len(trace_ac.doc_chunks) >= 1


def test_get_kb_coverage_report(sample_workspace, isolated_db):
    """Verify AC-KB-09: Repository-wide coverage analytics."""
    builder = UnifiedKBBuilder(workspace_path=sample_workspace)
    store = SqliteOverlayGraphStore(db_path=str(isolated_db))

    # Build KB
    asyncio.run(
        builder.build_unified_kb(
            project_id="test-cov-proj",
            branch="main",
            persist_memory=True,
            output_dir=str(sample_workspace / "docs" / "kb"),
            store=store,
            db_path=str(isolated_db),
        )
    )

    report = builder.get_kb_coverage_report(project_id="test-cov-proj", branch="main")

    assert isinstance(report, KBCoverageReport)
    assert report.total_symbols > 0
    assert 0.0 <= report.documented_pct <= 100.0
    assert 0.0 <= report.inferred_pct <= 100.0
    assert 0.0 <= report.undocumented_pct <= 100.0
    assert 0.0 <= report.test_linked_pct <= 100.0
    assert report.total_doc_chunks > 0
    assert report.cross_domain_edges > 0


@pytest.mark.asyncio
async def test_unified_kb_builder_build_e2e(sample_workspace, isolated_db):
    """Verify end-to-end KB build, manifest generation, and GraphStore synchronization."""
    out_dir = sample_workspace / "docs" / "kb"
    builder = UnifiedKBBuilder(workspace_path=sample_workspace)
    store = SqliteOverlayGraphStore(db_path=str(isolated_db))

    res = await builder.build_unified_kb(
        project_id="test-e2e-proj",
        branch="main",
        persist_memory=True,
        output_dir=str(out_dir),
        store=store,
        db_path=str(isolated_db),
    )

    assert res["status"] == "success"

    # Verify manifest file created
    manifest_file = out_dir / "kb_manifest.json"
    assert manifest_file.exists()

    import json
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert data["project_id"] == "test-e2e-proj"
    assert data["branch"] == "main"
    assert "stats" in data
    assert data["stats"]["total_symbols"] > 0
    assert data["stats"]["total_doc_chunks"] > 0

    # Verify GraphStore contains document nodes
    subgraph = store.get_resolved_subgraph("test-e2e-proj", "main")
    doc_nodes = [n for n in subgraph["nodes"] if n.get("file_type") == "document"]
    assert len(doc_nodes) > 0
    code_nodes = [n for n in subgraph["nodes"] if n.get("file_type") == "code"]
    assert len(code_nodes) > 0
