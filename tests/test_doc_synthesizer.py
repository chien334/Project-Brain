"""
Unit and integration tests for ProjectDocSynthesizer (TASK-10A / AC-KB-01..AC-KB-02).
Zero-hallucination Markdown Wiki and Multimodal Office Suite (.docx, .xlsx, .pptx) generation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import docx
import openpyxl
import pytest
from pptx import Presentation

from projectbrain.graphify_engine.doc_synthesizer import (
    OfficeExportResult,
    ProjectDocSynthesizer,
    SynthesisResult,
    WikiPage,
    WorkspaceASTIntrospector,
)
from projectbrain.graphify_engine.models import (
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)


@pytest.fixture
def sample_graph_delta() -> GraphDelta:
    """Fixture providing a realistic GraphDelta with nodes, edges, communities, and hyperedges."""
    nodes = [
        GraphNode(
            id="node_store",
            project_id="test_proj",
            label="PostgresSchemaGraphStore",
            file_type="code",
            source_file="src/projectbrain/graphify_engine/store.py",
            source_location="src/projectbrain/graphify_engine/store.py:1750",
            community=0,
            metadata={"doc_coverage": "documented"},
        ),
        GraphNode(
            id="node_resolver",
            project_id="test_proj",
            label="HybridResolver",
            file_type="code",
            source_file="src/projectbrain/graphify_engine/store.py",
            source_location="src/projectbrain/graphify_engine/store.py:1",
            community=0,
            metadata={"doc_coverage": "documented"},
        ),
        GraphNode(
            id="node_extractor",
            project_id="test_proj",
            label="GraphifyExtractor",
            file_type="code",
            source_file="src/projectbrain/graphify_engine/extractor.py",
            source_location="src/projectbrain/graphify_engine/extractor.py:25",
            community=0,
            metadata={"doc_coverage": "documented"},
        ),
        GraphNode(
            id="node_hsg",
            project_id="test_proj",
            label="add_hsg_memory",
            file_type="code",
            source_file="src/projectbrain/memory/hsg.py",
            source_location="src/projectbrain/memory/hsg.py:1",
            community=1,
            metadata={"doc_coverage": "documented"},
        ),
        GraphNode(
            id="node_query",
            project_id="test_proj",
            label="hsg_query",
            file_type="code",
            source_file="src/projectbrain/memory/hsg.py",
            source_location="src/projectbrain/memory/hsg.py:45",
            community=1,
            metadata={"doc_coverage": "inferred"},
        ),
        GraphNode(
            id="node_route_sync",
            project_id="test_proj",
            label="POST /graphify/sync",
            file_type="code",
            source_file="src/projectbrain/server/routes/graphify.py",
            source_location="src/projectbrain/server/routes/graphify.py:171",
            community=2,
            metadata={"doc_coverage": "documented"},
        ),
        GraphNode(
            id="node_mcp_query",
            project_id="test_proj",
            label="projectbrain_query",
            file_type="code",
            source_file="src/projectbrain/ai/mcp.py",
            source_location="src/projectbrain/ai/mcp.py:84",
            community=2,
            metadata={"doc_coverage": "documented"},
        ),
    ]

    edges = [
        GraphEdge(
            project_id="test_proj",
            source="node_resolver",
            target="node_store",
            relation="calls",
            confidence="EXTRACTED",
            confidence_score=1.0,
            weight=2.0,
        ),
        GraphEdge(
            project_id="test_proj",
            source="node_extractor",
            target="node_store",
            relation="populates",
            confidence="EXTRACTED",
            confidence_score=1.0,
            weight=1.5,
        ),
        GraphEdge(
            project_id="test_proj",
            source="node_query",
            target="node_hsg",
            relation="reads",
            confidence="EXTRACTED",
            confidence_score=1.0,
            weight=1.0,
        ),
        # Cross-community bridge: community 2 -> community 0
        GraphEdge(
            project_id="test_proj",
            source="node_route_sync",
            target="node_resolver",
            relation="calls",
            confidence="EXTRACTED",
            confidence_score=1.0,
            weight=3.0,
        ),
        # Cross-community bridge: community 2 -> community 1
        GraphEdge(
            project_id="test_proj",
            source="node_mcp_query",
            target="node_query",
            relation="calls",
            confidence="INFERRED",
            confidence_score=0.85,
            weight=2.5,
        ),
    ]

    hyperedges = [
        GraphHyperedge(
            id="hyp_sync_pipeline",
            project_id="test_proj",
            label="Sync Pipeline Subsystem",
            nodes=["node_route_sync", "node_resolver", "node_store"],
            relation="orchestrates",
            confidence="EXTRACTED",
            confidence_score=1.0,
        )
    ]

    return GraphDelta(
        project_id="test_proj",
        branch="main",
        nodes=nodes,
        edges=edges,
        hyperedges=hyperedges,
    )


@pytest.fixture
def sample_graph_dict() -> Dict[str, Any]:
    """Fixture providing raw dict representation of graph data."""
    return {
        "nodes": [
            {
                "id": "dict_node_a",
                "label": "AlphaEngine",
                "file_type": "code",
                "source_file": "src/projectbrain/alpha.py",
                "source_location": "src/projectbrain/alpha.py:10",
                "community": 0,
            },
            {
                "id": "dict_node_b",
                "label": "BetaEngine",
                "file_type": "code",
                "source_file": "src/projectbrain/beta.py",
                "source_location": "src/projectbrain/beta.py:20",
                "community": 1,
            },
        ],
        "edges": [
            {
                "source": "dict_node_a",
                "target": "dict_node_b",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
            }
        ],
        "hyperedges": [],
    }


# ===========================================================================
# 1. Test DTO Data Models
# ===========================================================================
def test_dto_models():
    """Verify WikiPage, OfficeExportResult, and SynthesisResult model constraints."""
    wp = WikiPage(
        slug="arch_test",
        title="Architecture Test",
        relative_path="wiki/arch.md",
        content="# Arch\nContent",
        category="overview",
        metadata={"subsystems": 3},
    )
    assert wp.slug == "arch_test"
    assert wp.category == "overview"

    oer = OfficeExportResult(
        file_path="/tmp/test.docx",
        export_type="docx",
        file_size_bytes=1024,
        sections_count=5,
        metadata={"tables": 2},
    )
    assert oer.export_type == "docx"
    assert oer.file_size_bytes == 1024

    sr = SynthesisResult(
        wiki_pages=[wp],
        office_exports=[oer],
        stats={"total": 1},
    )
    assert len(sr.wiki_pages) == 1
    assert len(sr.office_exports) == 1


# ===========================================================================
# 2. Test AST Workspace Introspector
# ===========================================================================
def test_ast_introspector_on_workspace():
    """Verify AST introspection accurately parses routes, MCP tools, and models from codebase."""
    workspace_root = Path(__file__).resolve().parent.parent
    introspector = WorkspaceASTIntrospector(workspace_root)
    data = introspector.introspect()

    routes = data.get("routes", [])
    mcp_tools = data.get("mcp_tools", [])
    cli_commands = data.get("cli_commands", [])
    pydantic_models = data.get("pydantic_models", [])
    sql_tables = data.get("sql_tables", [])

    assert len(routes) > 0, "AST should discover FastAPI routes"
    assert len(mcp_tools) > 0, "AST should discover FastMCP tools"
    assert len(pydantic_models) > 0, "AST should discover Pydantic models"
    assert len(sql_tables) > 0, "AST should discover SQL schemas"

    # Verify known endpoints exist
    route_paths = [r["path"] for r in routes]
    assert any("/sync" in p for p in route_paths)

    # Verify known MCP tools exist
    mcp_names = [t["name"] for t in mcp_tools]
    assert "projectbrain_query" in mcp_names


# ===========================================================================
# 3. Test synthesize_wiki (AC-KB-01)
# ===========================================================================
def test_synthesize_wiki_creates_all_files(tmp_path: Path, sample_graph_delta: GraphDelta):
    """
    Verify synthesize_wiki generates complete Markdown wiki without hallucination:
    - index.md
    - architecture_overview.md
    - api_and_mcp_reference.md
    - database_and_schemas.md
    - communities/community_{id}_{slug}.md
    - ../GRAPH_REPORT.md
    """
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    wiki_output_dir = tmp_path / "wiki"
    pages = synthesizer.synthesize_wiki(
        sample_graph_delta,
        output_dir=wiki_output_dir,
        top_god_nodes=10,
        top_bridges=10,
    )

    assert len(pages) >= 6, "Must generate index, arch, api, db, graph report, and community pages"

    # Verify files exist on disk
    index_file = wiki_output_dir / "index.md"
    arch_file = wiki_output_dir / "architecture_overview.md"
    api_file = wiki_output_dir / "api_and_mcp_reference.md"
    db_file = wiki_output_dir / "database_and_schemas.md"
    report_file = tmp_path / "GRAPH_REPORT.md"

    assert index_file.exists(), "index.md must exist on disk"
    assert arch_file.exists(), "architecture_overview.md must exist on disk"
    assert api_file.exists(), "api_and_mcp_reference.md must exist on disk"
    assert db_file.exists(), "database_and_schemas.md must exist on disk"
    assert report_file.exists(), "GRAPH_REPORT.md must exist alongside wiki/ in parent folder"

    # Verify index.md content
    index_content = index_file.read_text(encoding="utf-8")
    assert "Project-Brain Knowledge Base Index" in index_content
    assert "PostgresSchemaGraphStore" in index_content
    assert "Leiden Communities Directory" in index_content

    # Verify architecture_overview.md content
    arch_content = arch_file.read_text(encoding="utf-8")
    assert "```mermaid" in arch_content
    assert "flowchart TD" in arch_content
    assert "PostgresSchemaGraphStore" in arch_content
    assert "HybridResolver" in arch_content

    # Verify api_and_mcp_reference.md content
    api_content = api_file.read_text(encoding="utf-8")
    assert "FastAPI REST Endpoints" in api_content
    assert "FastMCP Server Tools Catalog" in api_content
    assert "`projectbrain_query`" in api_content

    # Verify database_and_schemas.md content
    db_content = db_file.read_text(encoding="utf-8")
    assert "pb_root" in db_content
    assert "pb_br_" in db_content
    assert "memories" in db_content
    assert "waypoints" in db_content

    # Verify GRAPH_REPORT.md content
    report_content = report_file.read_text(encoding="utf-8")
    assert "Graph Structural Analysis & Topological Report" in report_content
    assert "Epistemological Confidence Breakdown" in report_content
    assert "EXTRACTED" in report_content
    assert "Surprising Cross-Community Bridges" in report_content

    # Verify community deep dive pages
    comm_dir = wiki_output_dir / "communities"
    assert comm_dir.exists()
    comm_files = list(comm_dir.glob("community_*.md"))
    assert len(comm_files) >= 2, "Must generate at least 2 community files for multiple clusters"
    for cf in comm_files:
        c_text = cf.read_text(encoding="utf-8")
        assert "Community Member Symbols" in c_text


def test_synthesize_wiki_with_dict_input(tmp_path: Path, sample_graph_dict: Dict[str, Any]):
    """Verify synthesize_wiki accepts raw dictionary data without crashing."""
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    wiki_output_dir = tmp_path / "wiki_dict"
    pages = synthesizer.synthesize_wiki(sample_graph_dict, output_dir=wiki_output_dir)

    assert len(pages) >= 5
    index_file = wiki_output_dir / "index.md"
    assert index_file.exists()
    assert "AlphaEngine" in index_file.read_text()


# ===========================================================================
# 4. Test export_docx_specification (AC-KB-02)
# ===========================================================================
def test_export_docx_specification(tmp_path: Path, sample_graph_delta: GraphDelta):
    """
    Verify export_docx_specification generates structured Word specification.
    Opens output file with docx.Document, asserting title, headings, and tables.
    """
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    docx_path = tmp_path / "exports" / "test_spec.docx"
    result = synthesizer.export_docx_specification(sample_graph_delta, output_path=docx_path)

    assert result.export_type == "docx"
    assert result.file_path == str(docx_path)
    assert docx_path.exists()
    assert result.file_size_bytes > 0
    assert result.sections_count >= 4

    # Open with python-docx
    doc = docx.Document(str(docx_path))
    assert len(doc.paragraphs) > 0
    assert len(doc.tables) >= 3

    # Check for core text
    full_text = " ".join([p.text for p in doc.paragraphs])
    assert "Project-Brain System Architecture Specification" in full_text
    assert "Executive Summary" in full_text


# ===========================================================================
# 5. Test export_xlsx_traceability_matrix (AC-KB-02)
# ===========================================================================
def test_export_xlsx_without_linker_index(tmp_path: Path, sample_graph_delta: GraphDelta):
    """
    Verify export_xlsx_traceability_matrix generates 4-sheet workbook:
    - Code_To_Doc_Links
    - Doc_Chunks_Inventory
    - API_And_MCP_Endpoints
    - Coverage_And_Gaps
    Asserts valid sheet names and non-zero table rows.
    """
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    xlsx_path = tmp_path / "exports" / "test_matrix.xlsx"
    result = synthesizer.export_xlsx_traceability_matrix(sample_graph_delta, output_path=xlsx_path)

    assert result.export_type == "xlsx"
    assert result.sections_count == 4
    assert xlsx_path.exists()
    assert result.file_size_bytes > 0

    # Open with openpyxl
    wb = openpyxl.load_workbook(str(xlsx_path))
    expected_sheets = ["Code_To_Doc_Links", "Doc_Chunks_Inventory", "API_And_MCP_Endpoints", "Coverage_And_Gaps"]
    assert wb.sheetnames == expected_sheets

    # Verify each sheet has header + data rows (> 1 row)
    for sheet_name in expected_sheets:
        ws = wb[sheet_name]
        assert ws.max_row > 1, f"Sheet {sheet_name} must have data rows"


def test_export_xlsx_with_linker_index(tmp_path: Path, sample_graph_delta: GraphDelta):
    """Verify export_xlsx_traceability_matrix populates provided linker_index data."""
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    mock_linker_index = {
        "links": [
            {
                "symbol_name": "TestStore",
                "symbol_type": "class",
                "source_file": "src/test_store.py",
                "line": 42,
                "doc_file": "wiki/test_store.md",
                "heading": "Test Store API",
                "relation": "documents_symbol",
                "confidence": "EXTRACTED",
            }
        ],
        "chunks": [
            {
                "chunk_id": "chk_001",
                "doc_path": "docs/test.md",
                "heading_breadcrumb": "Overview > Section A",
                "start_line": 1,
                "end_line": 50,
                "tokens": 120,
                "sector": "semantic",
            }
        ],
        "coverage": {
            "total_symbols": 50,
            "documented_symbols": 45,
            "documented_pct": 90.0,
            "inferred_symbols": 3,
            "inferred_pct": 6.0,
            "undocumented_symbols": 2,
            "undocumented_pct": 4.0,
            "api_coverage_pct": 100.0,
            "mcp_coverage_pct": 100.0,
        },
    }

    xlsx_path = tmp_path / "exports" / "test_matrix_linked.xlsx"
    synthesizer.export_xlsx_traceability_matrix(
        sample_graph_delta, output_path=xlsx_path, linker_index=mock_linker_index
    )

    wb = openpyxl.load_workbook(str(xlsx_path))
    ws_links = wb["Code_To_Doc_Links"]
    assert ws_links.cell(row=2, column=1).value == "TestStore"

    ws_chunks = wb["Doc_Chunks_Inventory"]
    assert ws_chunks.cell(row=2, column=1).value == "chk_001"

    ws_cov = wb["Coverage_And_Gaps"]
    assert ws_cov.max_row >= 5


# ===========================================================================
# 6. Test export_pptx_architecture_deck (AC-KB-02)
# ===========================================================================
def test_export_pptx_architecture_deck(tmp_path: Path, sample_graph_delta: GraphDelta):
    """
    Verify export_pptx_architecture_deck generates 7-slide presentation.
    Opens output file with pptx.Presentation and asserts slide count == 7.
    """
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    pptx_path = tmp_path / "exports" / "test_deck.pptx"
    result = synthesizer.export_pptx_architecture_deck(sample_graph_delta, output_path=pptx_path)

    assert result.export_type == "pptx"
    assert result.sections_count == 7
    assert pptx_path.exists()
    assert result.file_size_bytes > 0

    # Open with python-pptx
    prs = Presentation(str(pptx_path))
    assert len(prs.slides) == 7, "Presentation must contain exactly 7 architecture slides"

    # Verify Slide 1 text
    s1 = prs.slides[0]
    full_text_s1 = " ".join([shape.text for shape in s1.shapes if hasattr(shape, "text")])
    assert "Project-Brain System Architecture Deck" in full_text_s1


# ===========================================================================
# 7. Test synthesize_all Full Orchestration
# ===========================================================================
def test_synthesize_all(tmp_path: Path, sample_graph_delta: GraphDelta):
    """Verify synthesize_all orchestrates Markdown wiki + Word + Excel + PowerPoint exports."""
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    wiki_dir = tmp_path / "kb" / "wiki"
    exports_dir = tmp_path / "kb" / "exports"

    result = synthesizer.synthesize_all(
        sample_graph_delta,
        wiki_dir=wiki_dir,
        exports_dir=exports_dir,
    )

    assert isinstance(result, SynthesisResult)
    assert len(result.wiki_pages) >= 6
    assert len(result.office_exports) == 3

    # Check export types
    types = [e.export_type for e in result.office_exports]
    assert "docx" in types
    assert "xlsx" in types
    assert "pptx" in types

    # Check files exist
    assert (exports_dir / "ProjectBrain_Architecture_Spec.docx").exists()
    assert (exports_dir / "ProjectBrain_Traceability_Matrix.xlsx").exists()
    assert (exports_dir / "ProjectBrain_Architecture_Deck.pptx").exists()


# ===========================================================================
# 8. Test Edge Case: Empty GraphDelta
# ===========================================================================
def test_empty_graph_delta_graceful_handling(tmp_path: Path):
    """Verify synthesizer handles empty graph data gracefully without uncaught exceptions."""
    workspace_root = Path(__file__).resolve().parent.parent
    synthesizer = ProjectDocSynthesizer(workspace_path=workspace_root)

    empty_delta = GraphDelta(project_id="empty_proj", branch="main")
    wiki_dir = tmp_path / "empty_wiki"
    pages = synthesizer.synthesize_wiki(empty_delta, output_dir=wiki_dir)

    assert len(pages) >= 5
    assert (wiki_dir / "index.md").exists()
    assert (wiki_dir / "architecture_overview.md").exists()
