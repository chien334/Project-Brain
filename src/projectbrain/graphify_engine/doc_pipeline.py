"""
Multimodal Document Ingestion & Server Push Pipeline.

Processes local documents (.pdf, .docx, .xlsx, .csv, .pptx, .md, .txt, .rst, images),
extracts structured Markdown/tables (with OCR fallback), chunks content, extracts
semantic GraphDelta entities and rationale relations via GraphifyExtractor, and
dispatches server pushes to POST /sources/push-processed via HTTPX.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.utils.chunking import chunk_text

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


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".xls", ".pptx", ".csv"}
PAPER_EXTENSIONS = {".pdf"}
DOC_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".adoc"}


def _classify_document_type(ext: str) -> str:
    """Classify file extension into semantic category."""
    ext = ext.lower()
    if ext in PAPER_EXTENSIONS:
        return "paper"
    if ext in OFFICE_EXTENSIONS:
        return "office"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "document"


def _format_csv_to_markdown(raw_bytes: bytes) -> str:
    """Format CSV bytes into a Markdown table."""
    text = raw_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        return "_Empty CSV Document_\n"

    col_count = max(len(r) for r in rows)

    def clean_cell(val: str) -> str:
        return val.replace("|", "\\|").replace("\n", " ").strip()

    lines = []
    # Header
    header = [clean_cell(rows[0][i]) if i < len(rows[0]) else "" for i in range(col_count)]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")

    for r in rows[1:]:
        row_cells = [clean_cell(r[i]) if i < len(r) else "" for i in range(col_count)]
        lines.append("| " + " | ".join(row_cells) + " |")

    return "\n".join(lines) + "\n"


class DocumentPipeline:
    """
    Multimodal Document Processing and Server Push Pipeline.

    Parses PDF, Word (DOCX), Excel (XLSX/CSV), PowerPoint (PPTX), Markdown,
    text, and image documents. Integrates with `doc_parser` for text and table
    extraction, runs OCR where needed, generates semantic text chunks, builds
    epistemological GraphDelta representations, and pushes payloads to the server.
    """

    def __init__(self, extractor: Optional[GraphifyExtractor] = None):
        self.extractor = extractor or GraphifyExtractor()

    async def _extract_content(self, path: Path, raw_bytes: bytes) -> str:
        """Extract Markdown text and tables from document bytes using doc_parser or internal handlers."""
        ext = path.suffix.lower()

        # 1. Plain Markdown & Text files
        if ext in DOC_TEXT_EXTENSIONS:
            return raw_bytes.decode("utf-8", errors="replace")

        # 2. CSV files
        if ext == ".csv":
            return _format_csv_to_markdown(raw_bytes)

        # 3. Images: Run OCR if available, fallback to markdown image tag
        if ext in IMAGE_EXTENSIONS:
            mime_type, _ = mimetypes.guess_type(path.name)
            mime_type = mime_type or "image/png"
            try:
                from projectbrain.utils.doc_parser import call_ocr_if_available

                ocr_res = await call_ocr_if_available(raw_bytes, mime_type)
                if ocr_res and ocr_res.strip() and ocr_res.strip() != "![Image]":
                    return f"# Image: {path.name}\n\n{ocr_res.strip()}\n"
            except Exception as e:
                logger.debug("Image OCR extraction failed for %s: %s", path.name, e)
            return f"# Image: {path.name}\n\n![{path.name}]({path.name})\n"

        # 4. Office & PDF Documents: Route through doc_parser
        try:
            from projectbrain.utils.doc_parser import parse_document

            return await parse_document(path.name, raw_bytes)
        except Exception as e:
            logger.debug("doc_parser.parse_document failed for %s: %s. Using text fallback.", path.name, e)
            return raw_bytes.decode("utf-8", errors="replace")

    async def process_file(
        self,
        file_path: Union[str, Path],
        project_id: str = "default",
        branch: str = "main",
        author: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sector: str = "semantic",
    ) -> Dict[str, Any]:
        """
        Process a single document file.

        - Extracts structured Markdown and tables.
        - Chunks content into token-sized segments.
        - Constructs GraphDelta with document nodes, section headers, rationale, and citations.
        - Returns server-ready push payload.
        """
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {file_path}")

        raw_bytes = path.read_bytes()
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        ext = path.suffix.lower()
        file_type = _classify_document_type(ext)

        # Extract Markdown text representation
        markdown_content = await self._extract_content(path, raw_bytes)

        # Generate semantic chunks
        raw_chunks = chunk_text(markdown_content, tgt=768, ovr=0.1)
        stem = path.stem
        chunks = []
        for idx, c in enumerate(raw_chunks):
            chunks.append(
                {
                    "chunk_id": f"{stem}_p{idx + 1}",
                    "content": c["text"],
                    "start": c.get("start", 0),
                    "end": c.get("end", 0),
                    "tokens": c.get("tokens", 0),
                    "sector": sector,
                    "tags": tags or [],
                }
            )

        # Build GraphDelta via extractor
        doc_metadata = {
            "author": author or "system",
            "sha256": file_sha256,
            "file_type": file_type,
            "tags": tags or [],
            "sector": sector,
        }
        graph_delta = self.extractor.extract_document_graph(
            file_path=str(file_path),
            markdown_content=markdown_content,
            project_id=project_id,
            branch=branch,
            metadata=doc_metadata,
        )

        delta_dict = (
            graph_delta.to_dict()
            if hasattr(graph_delta, "to_dict")
            else graph_delta.model_dump()
        )

        return {
            "project_id": project_id,
            "branch": branch,
            "document": {
                "filename": str(file_path),
                "sha256": file_sha256,
                "author": author or "system",
                "file_type": file_type,
                "markdown_content": markdown_content,
                "chunks": chunks,
            },
            "graph_delta": delta_dict,
        }

    async def process_batch(
        self,
        file_paths: List[Union[str, Path]],
        project_id: str = "default",
        branch: str = "main",
        author: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sector: str = "semantic",
    ) -> List[Dict[str, Any]]:
        """Process a collection of document files, returning a list of payload dicts."""
        results: List[Dict[str, Any]] = []
        for fp in file_paths:
            try:
                res = await self.process_file(
                    file_path=fp,
                    project_id=project_id,
                    branch=branch,
                    author=author,
                    tags=tags,
                    sector=sector,
                )
                results.append(res)
            except Exception as e:
                logger.error("Failed processing file %s: %s", fp, e)
        return results

    async def push_processed_to_server(
        self,
        server_url: str,
        payload: Dict[str, Any],
        token: Optional[str] = None,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """
        Push pre-processed document text chunks and graph entity delta to
        POST /sources/push-processed on the Project-Brain remote server.
        """
        base = server_url.rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = f"http://{base}"
        endpoint = f"{base}/sources/push-processed"

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
