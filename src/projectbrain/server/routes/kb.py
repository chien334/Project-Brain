"""Knowledge Base REST API Routes for Unified Code-Doc Knowledge Base Engine.

Provides endpoints for complete KB synthesis (/kb/build), cross-layer traceability (/kb/trace),
repository-wide coverage analytics (/kb/coverage), and multimodal artifact exports (/kb/export).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...graphify_engine.doc_synthesizer import ProjectDocSynthesizer
from ...graphify_engine.kb_linker import UnifiedKBBuilder
from ...graphify_engine.store import GraphStore, get_graph_store

logger = logging.getLogger("server.routes.kb")

router = APIRouter(prefix="/kb", tags=["kb"])


def _get_store() -> GraphStore:
    """Retrieve GraphStore instance configured with shared database path if SQLite."""
    db_path = (
        os.getenv("PB_GRAPH_DB_PATH")
        or os.getenv("OM_GRAPH_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or os.getenv("OM_DB_PATH")
    )
    if not db_path:
        try:
            from ...core.config import env
            db_url = getattr(env, "database_url", "")
            if db_url.startswith("sqlite:///"):
                db_path = db_url.replace("sqlite:///", "")
        except Exception:
            pass
    return get_graph_store(db_path=db_path)


def _get_db_path() -> Optional[str]:
    """Retrieve raw database path for SQLite isolation."""
    db_path = (
        os.getenv("PB_GRAPH_DB_PATH")
        or os.getenv("OM_GRAPH_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or os.getenv("OM_DB_PATH")
    )
    if not db_path:
        try:
            from ...core.config import env
            db_url = getattr(env, "database_url", "")
            if db_url.startswith("sqlite:///"):
                db_path = db_url.replace("sqlite:///", "")
        except Exception:
            pass
    return db_path


def _check_read_only() -> None:
    """Raise 403 Forbidden if server is configured in Read-Only mode."""
    if (
        os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes")
        or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes")
    ):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted: Server is running in Read-Only mode.",
        )


def _validate_safe_path(
    path_str: Optional[str],
    field_name: str = "workspace_path",
    allow_none: bool = False,
) -> Optional[Path]:
    """Validate that path resolves strictly inside workspace root (cwd) to prevent path traversal (SEC-02)."""
    if path_str is None:
        if allow_none:
            return None
        return Path.cwd().resolve()

    clean_str = str(path_str).strip()
    if clean_str in ("", "."):
        return Path.cwd().resolve()

    try:
        resolved = Path(clean_str).resolve()
        cwd = Path.cwd().resolve()
        resolved.relative_to(cwd)
        return resolved
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or out-of-workspace {field_name}: path traversal detected",
        )


class KBBuildRequest(BaseModel):
    project_id: str = "project-brain"
    branch: str = "main"
    workspace_path: str = "."
    persist_memory: bool = True
    output_dir: Optional[str] = None
    generate_office_exports: bool = True


class KBExportRequest(BaseModel):
    project_id: str = "project-brain"
    branch: str = "main"
    workspace_path: str = "."
    output_dir: str = "docs/kb"
    formats: List[str] = Field(default_factory=lambda: ["wiki", "docx", "xlsx", "pptx"])


@router.post("/build")
async def build_kb(req: KBBuildRequest):
    """Trigger full end-to-end Knowledge Base synthesis, chunking, linking, and HSG memory ingestion."""
    _check_read_only()
    resolved_workspace = _validate_safe_path(req.workspace_path, "workspace_path")
    resolved_output = _validate_safe_path(req.output_dir, "output_dir", allow_none=True)

    store = _get_store()
    db_path = _get_db_path()
    try:
        builder = UnifiedKBBuilder(workspace_path=resolved_workspace)
        res = await builder.build_unified_kb(
            project_id=req.project_id,
            branch=req.branch,
            persist_memory=req.persist_memory,
            output_dir=str(resolved_output) if resolved_output else None,
            generate_office_exports=req.generate_office_exports,
            store=store,
            db_path=db_path,
        )
        return res
    finally:
        store.close()


@router.get("/trace")
async def trace_code_and_docs(
    project_id: str = "project-brain",
    target: str = Query(..., description="Target symbol name, route, file path, or doc heading"),
    branch: str = "main",
    workspace_path: str = ".",
):
    """Inspect cross-layer traceability connecting AST code nodes, documentation chunks, and verified tests."""
    resolved_workspace = _validate_safe_path(workspace_path, "workspace_path")
    store = _get_store()
    try:
        builder = UnifiedKBBuilder(workspace_path=resolved_workspace)
        builder.store = store
        res = builder.trace_code_and_docs(
            project_id=project_id,
            target=target,
            branch=branch,
        )
        return res.model_dump()
    finally:
        store.close()


@router.get("/coverage")
async def get_kb_coverage(
    project_id: str = "project-brain",
    branch: str = "main",
    workspace_path: str = ".",
):
    """Fetch repository-wide documentation and test coverage metrics, gap statistics, and drift alerts."""
    resolved_workspace = _validate_safe_path(workspace_path, "workspace_path")
    store = _get_store()
    try:
        builder = UnifiedKBBuilder(workspace_path=resolved_workspace)
        builder.store = store
        res = builder.get_kb_coverage_report(
            project_id=project_id,
            branch=branch,
        )
        return res.model_dump()
    finally:
        store.close()


@router.post("/export")
async def export_kb_artifacts(req: KBExportRequest):
    """Generate on-demand Markdown Wiki and multimodal Office suite artifacts from current GraphStore."""
    _check_read_only()
    resolved_workspace = _validate_safe_path(req.workspace_path, "workspace_path")
    resolved_output = _validate_safe_path(req.output_dir, "output_dir")

    store = _get_store()
    try:
        subgraph = store.get_resolved_subgraph(req.project_id, req.branch, limit=100000)
    finally:
        store.close()

    synthesizer = ProjectDocSynthesizer(workspace_path=resolved_workspace)
    resolved_output.mkdir(parents=True, exist_ok=True)

    wiki_dir = resolved_output / "wiki" if "wiki" not in resolved_output.parts else resolved_output
    exports_dir = resolved_output / "exports" if "exports" not in resolved_output.parts else resolved_output

    artifacts: List[Dict[str, Any]] = []
    target_formats = [f.lower().strip() for f in req.formats] if req.formats else ["wiki", "docx", "xlsx", "pptx"]

    if "wiki" in target_formats:
        wiki_pages = synthesizer.synthesize_wiki(subgraph, output_dir=wiki_dir)
        artifacts.append({"format": "wiki", "count": len(wiki_pages), "dir": str(wiki_dir)})

    if "docx" in target_formats:
        exports_dir.mkdir(parents=True, exist_ok=True)
        docx_file = exports_dir / "ProjectBrain_Architecture_Spec.docx"
        r = synthesizer.export_docx_specification(subgraph, output_path=docx_file)
        artifacts.append(r.model_dump())

    if "xlsx" in target_formats:
        exports_dir.mkdir(parents=True, exist_ok=True)
        xlsx_file = exports_dir / "ProjectBrain_Traceability_Matrix.xlsx"
        linker_index = {
            "links": [
                e
                for e in subgraph.get("edges", [])
                if e.get("relation")
                in (
                    "documents_symbol",
                    "documented_in",
                    "conceptually_related_to",
                    "verifies_symbol",
                    "verifies_requirement",
                    "specifies_endpoint",
                )
            ],
            "chunks": [n for n in subgraph.get("nodes", []) if n.get("file_type") == "document"],
            "coverage": {},
        }
        r = synthesizer.export_xlsx_traceability_matrix(subgraph, output_path=xlsx_file, linker_index=linker_index)
        artifacts.append(r.model_dump())

    if "pptx" in target_formats:
        exports_dir.mkdir(parents=True, exist_ok=True)
        pptx_file = exports_dir / "ProjectBrain_Architecture_Deck.pptx"
        r = synthesizer.export_pptx_architecture_deck(subgraph, output_path=pptx_file)
        artifacts.append(r.model_dump())

    return {
        "status": "success",
        "project_id": req.project_id,
        "branch": req.branch,
        "output_dir": str(resolved_output),
        "formats": target_formats,
        "artifacts": artifacts,
    }
