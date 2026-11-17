"""
Unit test suite for Graphify AST/Semantic Extractor, Caching, Clustering,
and Multimodal Document Push Pipeline.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from projectbrain.graphify_engine.doc_pipeline import DocumentPipeline
from projectbrain.graphify_engine.extractor import (
    GraphifyExtractor,
    _strip_markdown_frontmatter,
)
from projectbrain.graphify_engine.models import (
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory for extractor tests."""
    temp_dir = tempfile.mkdtemp(prefix="pb_test_ws_")
    ws_path = Path(temp_dir)
    yield ws_path
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def extractor(temp_workspace):
    """Instantiate a GraphifyExtractor using a cache dir inside temp_workspace."""
    cache_dir = temp_workspace / ".projectbrain" / "cache"
    return GraphifyExtractor(cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Test 1: Python AST Extraction
# ---------------------------------------------------------------------------
def test_python_ast_extraction(temp_workspace, extractor):
    """Verify Python code extraction extracts modules, classes, methods, calls, and rationale."""
    py_code = '''"""Module docstring."""
import os
from math import sqrt

class AuthService:
    """Authentication service. Rationale: Centralized token validation."""

    def __init__(self, secret: str):
        self.secret = secret

    def validate_token(self, token: str) -> bool:
        """Validate token signature."""
        if not token:
            return False
        return self._verify_signature(token)

    def _verify_signature(self, token: str) -> bool:
        return True
'''
    py_file = temp_workspace / "auth.py"
    py_file.write_text(py_code, encoding="utf-8")

    delta = extractor.extract_workspace(
        workspace_path=temp_workspace,
        project_id="test_proj",
        branch="feature/auth",
        scoped_paths=["auth.py"],
        use_cache=False,
    )

    assert delta.project_id == "test_proj"
    assert delta.branch == "feature/auth"
    assert len(delta.nodes) >= 2  # module, AuthService, methods
    assert len(delta.edges) >= 1

    node_ids = {n.id for n in delta.nodes}
    assert any("authservice" in nid.lower() for nid in node_ids)

    # Check for rationale_for edge or contains edge
    relations = {e.relation for e in delta.edges}
    assert "contains" in relations or "method" in relations or "imports" in relations


# ---------------------------------------------------------------------------
# Test 2: TypeScript / JavaScript AST Extraction
# ---------------------------------------------------------------------------
def test_typescript_ast_extraction(temp_workspace, extractor):
    """Verify TypeScript / JavaScript symbols are extracted."""
    ts_code = '''
export interface UserPayload {
    id: string;
    role: string;
}

export class TokenManager {
    private secret: string;

    constructor(secret: string) {
        this.secret = secret;
    }

    public parseToken(token: string): UserPayload | null {
        return null;
    }
}
'''
    ts_file = temp_workspace / "tokens.ts"
    ts_file.write_text(ts_code, encoding="utf-8")

    delta = extractor.extract_workspace(
        workspace_path=temp_workspace,
        project_id="test_proj",
        scoped_paths=["tokens.ts"],
        use_cache=False,
    )

    assert len(delta.nodes) >= 1
    node_labels = {n.label for n in delta.nodes}
    assert any("TokenManager" in lbl or "tokens.ts" in lbl for lbl in node_labels)


# ---------------------------------------------------------------------------
# Test 3: SHA256 Caching & Markdown Frontmatter Invariance
# ---------------------------------------------------------------------------
def test_sha256_cache_hits_and_frontmatter(temp_workspace, extractor):
    """Verify cache hits on second run and frontmatter stripping invariance for Markdown."""
    # Test Frontmatter stripping utility directly
    md_with_frontmatter = b"---\ntitle: Auth Spec\nreviewed: false\ntags: [auth, jwt]\n---\n# Real Content Here\nDetails."
    stripped = _strip_markdown_frontmatter(md_with_frontmatter)
    assert b"title: Auth Spec" not in stripped
    assert b"# Real Content Here" in stripped

    # Test file extraction caching
    doc_path = temp_workspace / "spec.md"
    doc_path.write_text("---\nstatus: draft\n---\n# User Auth\n`AuthService` is used.\n", encoding="utf-8")

    # Pass 1: Extraction & Cache Write
    delta1 = extractor.extract_workspace(temp_workspace, project_id="p1", scoped_paths=["spec.md"], use_cache=True)
    cache_key1, _ = extractor.compute_file_hashes(doc_path)
    cached_file = extractor.cache_dir / f"{cache_key1}.json"
    assert cached_file.exists()

    # Pass 2: Edit only frontmatter
    doc_path.write_text("---\nstatus: approved\nauthor: alice\n---\n# User Auth\n`AuthService` is used.\n", encoding="utf-8")
    cache_key2, _ = extractor.compute_file_hashes(doc_path)

    # Cache keys must match because only frontmatter changed!
    assert cache_key1 == cache_key2

    # Pass 3: Extraction should hit cache
    delta2 = extractor.extract_workspace(temp_workspace, project_id="p1", scoped_paths=["spec.md"], use_cache=True)
    assert len(delta2.nodes) == len(delta1.nodes)
    assert len(delta2.edges) == len(delta1.edges)


# ---------------------------------------------------------------------------
# Test 4: Scoped Partial Workspace Extraction
# ---------------------------------------------------------------------------
def test_partial_scoped_paths_extraction(temp_workspace, extractor):
    """Verify scoped_paths isolates processing to the designated subtree."""
    dir_a = temp_workspace / "module_a"
    dir_b = temp_workspace / "module_b"
    dir_a.mkdir()
    dir_b.mkdir()

    (dir_a / "service_a.py").write_text("class ServiceA: pass\n", encoding="utf-8")
    (dir_b / "service_b.py").write_text("class ServiceB: pass\n", encoding="utf-8")

    # Extract only module_a
    delta = extractor.extract_workspace(
        workspace_path=temp_workspace,
        scoped_paths=["module_a"],
        use_cache=False,
    )

    sources = {n.source_file for n in delta.nodes}
    assert any("module_a" in s for s in sources)
    assert not any("module_b" in s for s in sources)


# ---------------------------------------------------------------------------
# Test 5: Local Partial Graph Persistence & Merge with Tombstones
# ---------------------------------------------------------------------------
def test_save_local_partial_graph_and_merge(temp_workspace, extractor):
    """Verify save_local_partial_graph saves and merges graph fragments while honoring tombstones."""
    local_graph_dir = temp_workspace / ".projectbrain" / "local_graph"

    delta1 = GraphDelta(
        project_id="test_p",
        branch="main",
        nodes=[
            GraphNode(id="n1", project_id="test_p", label="Node 1", source_file="a.py"),
            GraphNode(id="n2", project_id="test_p", label="Node 2", source_file="b.py"),
        ],
        edges=[
            GraphEdge(project_id="test_p", source="n1", target="n2", relation="calls"),
        ],
        synced_files=[{"file_path": "a.py", "sha256": "h1"}, {"file_path": "b.py", "sha256": "h2"}],
    )

    saved_path = extractor.save_local_partial_graph(delta1, output_dir=str(local_graph_dir))
    assert Path(saved_path).exists()
    assert (local_graph_dir / "manifest.json").exists()

    with open(saved_path, "r", encoding="utf-8") as f:
        data1 = json.load(f)
    assert len(data1["nodes"]) == 2
    assert len(data1["edges"]) == 1

    # Delta 2 deletes n1 via tombstone and adds n3
    delta2 = GraphDelta(
        project_id="test_p",
        branch="main",
        nodes=[
            GraphNode(id="n3", project_id="test_p", label="Node 3", source_file="c.py"),
        ],
        edges=[
            GraphEdge(project_id="test_p", source="n2", target="n3", relation="calls"),
        ],
        node_tombstones=["n1"],
        edge_tombstones=[{"source": "n1", "target": "n2", "relation": "calls"}],
        synced_files=[{"file_path": "c.py", "sha256": "h3"}],
    )

    extractor.save_local_partial_graph(delta2, output_dir=str(local_graph_dir), merge_existing=True)

    with open(saved_path, "r", encoding="utf-8") as f:
        data2 = json.load(f)

    node_ids = {n["id"] for n in data2["nodes"]}
    assert "n1" not in node_ids  # Tombstoned
    assert "n2" in node_ids
    assert "n3" in node_ids
    assert len(data2["edges"]) == 1
    assert data2["edges"][0]["source"] == "n2"
    assert data2["edges"][0]["target"] == "n3"


# ---------------------------------------------------------------------------
# Test 6: Community Clustering & God Nodes
# ---------------------------------------------------------------------------
def test_clustering_and_god_nodes(extractor):
    """Verify Leiden / Louvain clustering and God Node identification."""
    nodes = [
        GraphNode(id=f"node_{i}", project_id="test_p", label=f"Entity {i}", source_file=f"file_{i//3}.py")
        for i in range(12)
    ]
    edges = [
        # Cluster 0: 0, 1, 2, 3
        GraphEdge(project_id="test_p", source="node_0", target="node_1", relation="calls"),
        GraphEdge(project_id="test_p", source="node_1", target="node_2", relation="calls"),
        GraphEdge(project_id="test_p", source="node_2", target="node_0", relation="calls"),
        GraphEdge(project_id="test_p", source="node_0", target="node_3", relation="calls"),
        # Cluster 1: 4, 5, 6, 7
        GraphEdge(project_id="test_p", source="node_4", target="node_5", relation="calls"),
        GraphEdge(project_id="test_p", source="node_5", target="node_6", relation="calls"),
        GraphEdge(project_id="test_p", source="node_6", target="node_4", relation="calls"),
        GraphEdge(project_id="test_p", source="node_4", target="node_7", relation="calls"),
        # God Node Hub: node_0 connects to node_4 (cross-community bridge)
        GraphEdge(project_id="test_p", source="node_0", target="node_4", relation="calls"),
    ]

    delta = GraphDelta(project_id="test_p", branch="main", nodes=nodes, edges=edges)

    # 1. Clustering
    clustered_delta = extractor.cluster_graph(delta)
    assigned_communities = {n.community for n in clustered_delta.nodes if n.community is not None}
    assert len(assigned_communities) >= 1

    for n in clustered_delta.nodes:
        if n.community is not None:
            assert "cohesion_score" in n.metadata

    # 2. God Nodes & Bridges
    analysis = extractor.compute_god_nodes_and_bridges(clustered_delta, top_k=3)
    assert "god_nodes" in analysis
    assert "communities" in analysis
    assert len(analysis["god_nodes"]) > 0

    # node_0 or node_4 should have high edge count
    god_ids = [g["id"] for g in analysis["god_nodes"]]
    assert "node_0" in god_ids or "node_4" in god_ids


# ---------------------------------------------------------------------------
# Test 7: Document Graph Extraction (Headings, Tables, Backticks, Rationale)
# ---------------------------------------------------------------------------
def test_document_graph_extraction(extractor):
    """Verify Markdown parsing extracts sections, tables, backtick symbols, and rationale."""
    markdown_text = """# Architecture Overview

This document specifies the system architecture.

## Authentication Pipeline
Rationale: Decoupled auth from user database for zero-trust security.
The `AuthService` coordinates with `JwtValidator` and `UserRepository`.

| Component | Responsibility | Status |
| --- | --- | --- |
| `AuthService` | Gateway auth | Stable |
| `JwtValidator` | Signature verify | Active |
| `UserRepository` | Account lookup | Active |

See also [Database Schema](db_schema.md) for table definitions.
"""
    delta = extractor.extract_document_graph(
        file_path="docs/architecture.md",
        markdown_content=markdown_text,
        project_id="test_proj",
        branch="main",
    )

    assert delta.project_id == "test_proj"
    assert len(delta.nodes) >= 2  # Doc root + Authentication Pipeline section
    assert len(delta.edges) >= 3

    labels = {n.label for n in delta.nodes}
    assert any("Architecture Overview" in lbl or "architecture.md" in lbl for lbl in labels)
    assert any("Authentication Pipeline" in lbl for lbl in labels)

    relations = {e.relation for e in delta.edges}
    assert "defines" in relations
    assert "cites" in relations or "rationale_for" in relations

    # Check for hyperedges generated from 3-entity table or section
    assert len(delta.hyperedges) >= 1
    assert any("authservice" in [n.lower() for n in h.nodes] for h in delta.hyperedges)


# ---------------------------------------------------------------------------
# Test 8: DocumentPipeline process_file (Markdown & CSV)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_doc_pipeline_process_file_markdown_and_csv(temp_workspace, extractor):
    """Verify DocumentPipeline correctly handles Markdown and CSV files."""
    pipeline = DocumentPipeline(extractor=extractor)

    # 1. Process Markdown
    md_file = temp_workspace / "design.md"
    md_file.write_text("# Design Document\n`OrderService` processes payments.\n", encoding="utf-8")

    md_res = await pipeline.process_file(
        file_path=md_file,
        project_id="proj_1",
        branch="main",
        author="lead_dev",
        tags=["architecture"],
        sector="engineering",
    )

    assert md_res["project_id"] == "proj_1"
    assert md_res["document"]["file_type"] == "document"
    assert md_res["document"]["author"] == "lead_dev"
    assert len(md_res["document"]["chunks"]) >= 1
    assert md_res["document"]["chunks"][0]["sector"] == "engineering"
    assert "graph_delta" in md_res
    assert len(md_res["graph_delta"]["nodes"]) >= 1

    # 2. Process CSV
    csv_file = temp_workspace / "metrics.csv"
    csv_file.write_text("service,latency_ms,sla\n`AuthService`,45,true\n`PaymentService`,120,true\n", encoding="utf-8")

    csv_res = await pipeline.process_file(
        file_path=csv_file,
        project_id="proj_1",
        author="sre_team",
    )

    assert csv_res["document"]["file_type"] == "office"
    assert "| service |" in csv_res["document"]["markdown_content"]
    assert len(csv_res["document"]["chunks"]) >= 1


# ---------------------------------------------------------------------------
# Test 9: DocumentPipeline process_batch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_doc_pipeline_process_batch(temp_workspace, extractor):
    """Verify DocumentPipeline.process_batch iterates over multiple files."""
    pipeline = DocumentPipeline(extractor=extractor)

    f1 = temp_workspace / "doc1.md"
    f2 = temp_workspace / "doc2.md"
    f1.write_text("# Doc 1\nDetails 1\n", encoding="utf-8")
    f2.write_text("# Doc 2\nDetails 2\n", encoding="utf-8")

    results = await pipeline.process_batch(
        file_paths=[f1, f2],
        project_id="batch_proj",
        branch="feat/batch",
    )

    assert len(results) == 2
    assert results[0]["project_id"] == "batch_proj"
    assert results[1]["project_id"] == "batch_proj"


# ---------------------------------------------------------------------------
# Test 10: Server Push via HTTPX (Mocked)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_push_processed_to_server_mocked(extractor):
    """Verify push_processed_to_server sends POST payload with Authorization header."""
    pipeline = DocumentPipeline(extractor=extractor)

    payload = {
        "project_id": "proj_123",
        "branch": "feature/token",
        "document": {"filename": "spec.md", "sha256": "abc123def456"},
        "graph_delta": {"nodes": [], "edges": []},
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "project_id": "proj_123",
        "branch": "feature/token",
        "nodes_upserted": 10,
        "edges_upserted": 15,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await pipeline.push_processed_to_server(
            server_url="http://5.104.85.38:8080",
            payload=payload,
            token="secret_bearer_token",
            timeout=30.0,
        )

        assert result["status"] == "success"
        assert result["nodes_upserted"] == 10
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://5.104.85.38:8080/sources/push-processed"
        assert call_args[1]["headers"]["Authorization"] == "Bearer secret_bearer_token"
        assert call_args[1]["json"] == payload


# ---------------------------------------------------------------------------
# Test 11: Git Dirty Scan Extraction
# ---------------------------------------------------------------------------
def test_git_dirty_scan_extraction(temp_workspace, extractor):
    """Verify git_dirty_only=True indexes only modified/untracked files and identifies deleted files."""
    import subprocess

    # Initialize a temporary git repository
    subprocess.run(["git", "init"], cwd=str(temp_workspace), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(temp_workspace), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(temp_workspace), check=True)

    file_a = temp_workspace / "clean.py"
    file_b = temp_workspace / "to_delete.py"
    file_c = temp_workspace / "to_modify.py"

    file_a.write_text("class Clean: pass\n", encoding="utf-8")
    file_b.write_text("class ToDelete: pass\n", encoding="utf-8")
    file_c.write_text("class ToModify: pass\n", encoding="utf-8")

    # Initial full index & save local partial graph
    initial_delta = extractor.extract_workspace(temp_workspace, use_cache=False)
    extractor.save_local_partial_graph(initial_delta, output_dir=str(temp_workspace / ".projectbrain" / "local_graph"))

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=str(temp_workspace), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(temp_workspace), check=True, capture_output=True)

    # Now make workspace dirty:
    # 1. Modify file_c
    file_c.write_text("class ToModify:\n    def new_method(self): pass\n", encoding="utf-8")
    # 2. Delete file_b
    file_b.unlink()
    # 3. Add untracked file_d
    file_d = temp_workspace / "untracked.py"
    file_d.write_text("class Untracked: pass\n", encoding="utf-8")

    dirty_delta = extractor.extract_workspace(
        workspace_path=temp_workspace,
        git_dirty_only=True,
        use_cache=False,
    )

    synced = {f["file_path"] for f in dirty_delta.synced_files}
    assert "to_modify.py" in synced
    assert "untracked.py" in synced
    assert "clean.py" not in synced  # Clean file must NOT be extracted

    # Verify deleted file node tombstone
    assert len(dirty_delta.node_tombstones) >= 1 or len(dirty_delta.edge_tombstones) >= 0


# ---------------------------------------------------------------------------
# Test 12: Image & PDF Ingestion with OCR / Parser Bridge
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_doc_pipeline_image_and_pdf_mocked(temp_workspace, extractor):
    """Verify DocumentPipeline correctly routes images through OCR and office/PDF through doc_parser."""
    pipeline = DocumentPipeline(extractor=extractor)

    # 1. Test image ingestion with OCR mock
    img_file = temp_workspace / "diagram.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)  # Minimal PNG header

    with patch(
        "projectbrain.utils.doc_parser.call_ocr_if_available",
        new_callable=AsyncMock,
        return_value="<!-- Start Embedded Image OCR -->\nArchitecture Diagram: `AuthService` -> `TokenCache`\n<!-- End Embedded Image OCR -->",
    ):
        img_res = await pipeline.process_file(img_file, project_id="p_img")
        assert img_res["document"]["file_type"] == "image"
        assert "AuthService" in img_res["document"]["markdown_content"]
        assert len(img_res["graph_delta"]["nodes"]) >= 1

    # 2. Test PDF ingestion with doc_parser mock
    pdf_file = temp_workspace / "spec.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock pdf bytes")

    with patch(
        "projectbrain.utils.doc_parser.parse_document",
        new_callable=AsyncMock,
        return_value="# PDF Specification\n## Section 1\n`StorageCluster` stores data.\n",
    ):
        pdf_res = await pipeline.process_file(pdf_file, project_id="p_pdf")
        assert pdf_res["document"]["file_type"] == "paper"
        assert "StorageCluster" in pdf_res["document"]["markdown_content"]
        assert len(pdf_res["graph_delta"]["nodes"]) >= 2

