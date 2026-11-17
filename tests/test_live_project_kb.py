"""
Live End-to-End Verification Test Suite for Project-Brain Knowledge Base.
Validates docs/kb/ artifacts, .projectbrain/kb/projectbrain_kb.db, office exports,
wiki structure, HSG cognitive memory, and bidirectional traceability.
"""

import json
import os
import sqlite3
from pathlib import Path
import pytest

from projectbrain.graphify_engine.kb_linker import UnifiedKBBuilder
from projectbrain.graphify_engine.store import get_graph_store

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
KB_OUTPUT_DIR = WORKSPACE_DIR / "docs" / "kb"
EXPORTS_DIR = KB_OUTPUT_DIR / "exports"
WIKI_DIR = KB_OUTPUT_DIR / "wiki"
DB_PATH = WORKSPACE_DIR / ".projectbrain" / "kb" / "projectbrain_kb.db"


def test_live_kb_artifacts_exist_and_non_empty():
    """Verify all expected KB markdown and manifest artifacts exist and are non-empty."""
    assert KB_OUTPUT_DIR.exists(), f"Directory missing: {KB_OUTPUT_DIR}"
    assert (KB_OUTPUT_DIR / "GRAPH_REPORT.md").is_file()
    assert (KB_OUTPUT_DIR / "GRAPH_REPORT.md").stat().st_size > 1000

    manifest_path = KB_OUTPUT_DIR / "kb_manifest.json"
    assert manifest_path.is_file()
    assert manifest_path.stat().st_size > 10000

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest.get("project_id") == "project-brain"
    assert manifest.get("branch") == "main"
    assert "stats" in manifest
    stats = manifest["stats"]
    assert stats.get("total_symbols", 0) > 500
    assert stats.get("total_doc_chunks", 0) > 100
    assert stats.get("cross_domain_edges", 0) > 1000


def test_live_kb_wiki_pages_structure():
    """Verify wiki core pages and Leiden community breakdown files."""
    assert WIKI_DIR.exists()
    for fname in ["index.md", "architecture_overview.md", "api_and_mcp_reference.md", "database_and_schemas.md"]:
        wiki_file = WIKI_DIR / fname
        assert wiki_file.is_file(), f"Missing wiki page: {wiki_file}"
        assert wiki_file.stat().st_size > 500, f"Wiki page too small: {wiki_file}"

    communities_dir = WIKI_DIR / "communities"
    assert communities_dir.is_dir()
    community_files = list(communities_dir.glob("community_*.md"))
    assert len(community_files) >= 10, f"Expected >= 10 community files, found {len(community_files)}"


def test_live_kb_office_artifacts_integrity():
    """Verify DOCX, PPTX, and all 4 XLSX sheets exist and contain valid structured content."""
    docx_file = EXPORTS_DIR / "ProjectBrain_Architecture_Spec.docx"
    pptx_file = EXPORTS_DIR / "ProjectBrain_Architecture_Deck.pptx"
    xlsx_file = EXPORTS_DIR / "ProjectBrain_Traceability_Matrix.xlsx"

    assert docx_file.is_file()
    assert docx_file.stat().st_size > 10000

    assert pptx_file.is_file()
    assert pptx_file.stat().st_size > 10000

    assert xlsx_file.is_file()
    assert xlsx_file.stat().st_size > 50000

    # Inspect DOCX
    import docx
    doc = docx.Document(str(docx_file))
    assert len(doc.paragraphs) >= 5
    assert len(doc.tables) >= 2

    # Inspect PPTX
    import pptx
    prs = pptx.Presentation(str(pptx_file))
    assert len(prs.slides) >= 5

    # Inspect XLSX 4 sheets
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx_file))
    expected_sheets = [
        "Code_To_Doc_Links",
        "Doc_Chunks_Inventory",
        "API_And_MCP_Endpoints",
        "Coverage_And_Gaps",
    ]
    for sname in expected_sheets:
        assert sname in wb.sheetnames, f"Sheet {sname} missing from Excel export"
        ws = wb[sname]
        assert ws.max_row >= 5, f"Sheet {sname} has fewer than 5 rows ({ws.max_row})"


def test_live_kb_sqlite_database_state():
    """Verify SQLite database contains root nodes, edges, memories, and waypoints."""
    assert DB_PATH.is_file(), f"Database missing at {DB_PATH}"
    assert DB_PATH.stat().st_size > 1000000, f"Database too small: {DB_PATH.stat().st_size}"

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    c.execute("SELECT count(*) FROM root_nodes")
    node_count = c.fetchone()[0]
    assert node_count >= 500, f"Expected >= 500 nodes, got {node_count}"

    c.execute("SELECT count(*) FROM root_edges")
    edge_count = c.fetchone()[0]
    assert edge_count >= 1000, f"Expected >= 1000 edges, got {edge_count}"

    c.execute("SELECT count(*) FROM memories")
    mem_count = c.fetchone()[0]
    assert mem_count >= 100, f"Expected >= 100 memories, got {mem_count}"

    c.execute("SELECT count(*) FROM waypoints")
    waypoint_count = c.fetchone()[0]
    assert waypoint_count >= 100, f"Expected >= 100 waypoints, got {waypoint_count}"

    conn.close()


def test_live_kb_coverage_thresholds():
    """Assert live KB coverage strictly meets >85% code-to-doc and 100% API/MCP coverage."""
    manifest_path = KB_OUTPUT_DIR / "kb_manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    cov = manifest.get("stats", {})
    documented_pct = cov.get("documented_pct", 0.0)
    inferred_pct = cov.get("inferred_pct", 0.0)
    total_coverage = documented_pct + inferred_pct

    assert total_coverage >= 85.0, f"Code-to-doc coverage too low: {total_coverage}% < 85%"
    assert cov.get("api_coverage_pct") == 100.0, f"API coverage not 100%: {cov.get('api_coverage_pct')}"
    assert cov.get("mcp_coverage_pct") == 100.0, f"MCP coverage not 100%: {cov.get('mcp_coverage_pct')}"
    assert cov.get("cross_domain_edges", 0) >= 5000, "Expected >= 5000 cross domain edges"


@pytest.mark.parametrize(
    "symbol",
    [
        "PostgresSchemaGraphStore",
        "SqliteOverlayGraphStore",
        "GraphifyExtractor",
        "DocumentPipeline",
        "HybridResolver",
        "ProjectDocSynthesizer",
        "CodeDocLinker",
        "UnifiedKBBuilder",
        "add_hsg_memory",
    ],
)
def test_live_kb_trace_architectural_symbols(symbol: str):
    """Verify trace_code_and_docs returns code nodes, doc chunks, and tests for core symbols."""
    builder = UnifiedKBBuilder(workspace_path=str(WORKSPACE_DIR))
    builder.store = get_graph_store(db_path=str(DB_PATH), root_branch="main")

    res = builder.trace_code_and_docs(
        project_id="project-brain",
        target=symbol,
        branch="main",
    )

    assert len(res.code_nodes) >= 1, f"No code node matched for {symbol}"
    assert len(res.doc_chunks) >= 1, f"No doc chunks linked for {symbol}"
    assert res.coverage_status in ("documented", "inferred")


@pytest.mark.parametrize(
    "endpoint",
    [
        "/graphify/sync",
        "/sources/push-processed",
        "/kb/build",
        "projectbrain_build_complete_kb",
        "projectbrain_hybrid_query",
    ],
)
def test_live_kb_trace_endpoints_and_mcp_tools(endpoint: str):
    """Verify trace_code_and_docs returns documented status and doc chunks for routes & MCP tools."""
    builder = UnifiedKBBuilder(workspace_path=str(WORKSPACE_DIR))
    builder.store = get_graph_store(db_path=str(DB_PATH), root_branch="main")

    res = builder.trace_code_and_docs(
        project_id="project-brain",
        target=endpoint,
        branch="main",
    )

    assert len(res.code_nodes) >= 1, f"No code node matched for {endpoint}"
    assert len(res.doc_chunks) >= 1, f"No doc chunks linked for {endpoint}"
    assert res.coverage_status == "documented"
