"""
sources routes - ingest data from external sources via HTTP

POST /sources/{source}/ingest
  body: { creds: {...}, filters: {...}, user_id?: string }

POST /sources/webhook/{source}
  generic webhook endpoint for source-specific payloads
"""
from fastapi import APIRouter, Request, HTTPException, File, UploadFile, Form
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict
import logging
import os

logger = logging.getLogger("server.routes.sources")

router = APIRouter(prefix="/sources", tags=["sources"])


def _check_read_only() -> None:
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted: Server is running in Read-Only mode."
        )


def _get_store():
    from ...graphify_engine.store import get_graph_store
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


class PushDocumentRequest(BaseModel):
    project_id: str = "default"
    branch: str = "main"
    document: Dict[str, Any]
    graph_delta: Optional[Dict[str, Any]] = None
    doc_key: Optional[str] = None
    doc_version: Optional[str] = None
    base_version: Optional[str] = None
    base_branch: Optional[str] = None

    model_config = ConfigDict(extra="allow")


@router.post("/push-processed")
async def push_processed(req: PushDocumentRequest):
    _check_read_only()
    try:
        from ...graphify_engine.doc_versioning import ingest_document_versioned, resolve_doc_user_id

        project_id, branch, _ = resolve_doc_user_id(
            req.project_id or "default", req.branch or "main"
        )
        doc = req.document or {}

        filename = doc.get("filename", "unknown_doc")
        sha256 = doc.get("sha256", "")
        author = doc.get("author", "system")
        file_type = doc.get("file_type", "document")
        markdown_content = doc.get("markdown_content", "")
        chunks = doc.get("chunks")
        precomputed_units = doc.get("units")
        doc_key = doc.get("doc_key") or req.doc_key
        doc_version = doc.get("doc_version") or req.doc_version
        base_version = doc.get("base_version") or req.base_version
        base_branch = doc.get("base_branch") or req.base_branch

        # 1. Versioned ingestion into HSG memory (with unit reuse & superseding)

        doc_tags = doc.get("tags")
        if isinstance(doc_tags, str):
            doc_tags = [t.strip() for t in doc_tags.split(",") if t.strip()]
        elif not isinstance(doc_tags, list):
            doc_tags = ["document", file_type] if file_type else ["document"]

        ver_res = await ingest_document_versioned(
            filename=filename,
            markdown_content=markdown_content,
            chunks=chunks,
            precomputed_units=precomputed_units,
            project_id=project_id,
            branch=branch,
            author=author,
            tags=doc_tags,
            doc_key=doc_key,
            doc_version=doc_version,
            base_version=base_version,
            base_branch=base_branch,
        )
        memories_created = ver_res["memories_created"]
        effective_markdown = ver_res.get("markdown_content") or markdown_content

        # 2. Ingest GraphDelta into GraphStore
        from ...graphify_engine.models import GraphDelta
        from ...graphify_engine.extractor import GraphifyExtractor

        if req.graph_delta and isinstance(req.graph_delta, dict):
            delta = GraphDelta(
                project_id=project_id,
                branch=branch,
                nodes=req.graph_delta.get("nodes", []),
                edges=req.graph_delta.get("edges", []),
                hyperedges=req.graph_delta.get("hyperedges", []),
                node_tombstones=req.graph_delta.get("node_tombstones", []),
                edge_tombstones=req.graph_delta.get("edge_tombstones", []),
                hyperedge_tombstones=req.graph_delta.get("hyperedge_tombstones", []),
                synced_files=req.graph_delta.get("synced_files", []),
            )
        else:
            extractor = GraphifyExtractor()
            delta = extractor.extract_document_graph(
                file_path=filename,
                markdown_content=effective_markdown,
                project_id=project_id,
                branch=branch,
                metadata={
                    "author": author,
                    "sha256": sha256,
                    "file_type": file_type,
                    "doc_key": ver_res["doc_key"],
                    "doc_version": ver_res["doc_version"],
                },
            )

        store = _get_store()
        try:
            sync_res = store.sync_branch_delta(
                project_id=project_id,
                branch_name=branch,
                delta=delta,
                partial=True,
                synced_files=[filename],
                author=author,
                base_branch=ver_res.get("base_branch") or base_branch,
            )
            schema_used = sync_res.get("schema_name") or sync_res.get("schema_used") or ("root" if store.is_root_branch(branch) else f"branch_{branch}")
        finally:
            store.close()
        nodes_upserted = sync_res.get("nodes_upserted", len(delta.nodes))
        edges_upserted = sync_res.get("edges_upserted", len(delta.edges))

        return {
            "status": "success",
            "project_id": project_id,
            "branch": branch,
            "schema_used": schema_used,
            "db_name": sync_res.get("db_name") or ver_res.get("db_name"),
            "doc_schema": ver_res.get("doc_schema"),
            "nodes_upserted": nodes_upserted,
            "edges_upserted": edges_upserted,
            "memories_created": memories_created,
            "doc_key": ver_res["doc_key"],
            "doc_version": ver_res["doc_version"],
            "parent_version": ver_res["parent_version"],
            "base_branch": ver_res["base_branch"],
            "total_units": ver_res["total_units"],
            "reused_units": ver_res["reused_units"],
            "new_units": ver_res["new_units"],
            "deleted_units": ver_res["deleted_units"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to push processed document: {str(e)}")


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    project_id: str = Form("default"),
    branch: str = Form("main"),
    tags: Optional[str] = Form(""),
    author: Optional[str] = Form(None),
    doc_key: Optional[str] = Form(None),
    doc_version: Optional[str] = Form(None),
    base_version: Optional[str] = Form(None),
    base_branch: Optional[str] = Form(None),
    ocr_engine: Optional[str] = Form(None),
    ocr_device: Optional[str] = Form(None),
    local_ai_model: Optional[str] = Form(None),
    local_ai_url: Optional[str] = Form(None),
    request: Request = None,
):
    from pathlib import Path
    _check_read_only()

    safe_name = Path(file.filename or "uploaded_file").name
    max_mb = float(os.getenv("PB_MAX_UPLOAD_MB", "50"))
    max_bytes = int(max_mb * 1024 * 1024)

    content_len = request.headers.get("content-length") if request else None
    if content_len and int(content_len) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Payload Too Large: Maximum allowed upload is {max_mb}MB")

    try:
        chunk = await file.read(max_bytes + 1)
        if len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail=f"Payload Too Large: Maximum allowed upload is {max_mb}MB")
        file_bytes = chunk
        filename = safe_name

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        if "document" not in tag_list:
            tag_list.append("document")

        # 1. Versioned pre-OCR delta ingestion (avoids eager full-doc OCR on unchanged units)
        from ...graphify_engine.doc_versioning import ingest_document_versioned, resolve_doc_user_id
        from ...utils.doc_parser import ocr_options_context

        project_id, branch, _ = resolve_doc_user_id(project_id, branch)

        eff_ocr_engine = (
            ocr_engine
            if ocr_engine is not None
            else (request.query_params.get("ocr_engine") if request else None)
        )
        eff_ocr_device = (
            ocr_device
            if ocr_device is not None
            else (request.query_params.get("ocr_device") if request else None)
        )
        eff_local_ai_model = (
            local_ai_model
            if local_ai_model is not None
            else (request.query_params.get("local_ai_model") if request else None)
        )
        eff_local_ai_url = (
            local_ai_url
            if local_ai_url is not None
            else (request.query_params.get("local_ai_url") if request else None)
        )
        ocr_options: Dict[str, Any] = {}
        if eff_ocr_engine is not None:
            ocr_options["engine"] = eff_ocr_engine
        if eff_ocr_device is not None:
            ocr_options["device"] = eff_ocr_device
        if eff_local_ai_model is not None:
            ocr_options["local_ai_model"] = eff_local_ai_model
        if eff_local_ai_url is not None:
            ocr_options["local_ai_url"] = eff_local_ai_url

        try:
            with ocr_options_context(ocr_options or None):
                ver_res = await ingest_document_versioned(
                    filename=filename,
                    file_bytes=file_bytes,
                    project_id=project_id,
                    branch=branch,
                    author=author,
                    tags=tag_list,
                    doc_key=doc_key,
                    doc_version=doc_version,
                    base_version=base_version,
                    base_branch=base_branch,
                )
        except ValueError as ve:
            raise HTTPException(400, str(ve))

        markdown_content = ver_res.get("markdown_content", "")
        if not markdown_content.strip():
            raise HTTPException(400, "Document content is empty or could not be parsed.")

        meta = {
            "source": "upload",
            "filename": filename,
            "size": len(file_bytes),
            "project_id": project_id,
            "branch": branch,
            "doc_key": ver_res["doc_key"],
            "doc_version": ver_res["doc_version"],
        }
        if author:
            meta["author"] = author

        # 2. Extract and sync document graph into GraphStore branch overlay
        nodes_upserted = 0
        edges_upserted = 0
        graphify_synced = False
        sync_res: Dict[str, Any] = {}
        try:
            from ...graphify_engine.extractor import GraphifyExtractor
            extractor = GraphifyExtractor()
            delta = extractor.extract_document_graph(
                file_path=filename,
                markdown_content=markdown_content,
                project_id=project_id,
                branch=branch,
                metadata=meta,
            )
            store = _get_store()
            try:
                sync_res = store.sync_branch_delta(
                    project_id=project_id,
                    branch_name=branch,
                    delta=delta,
                    partial=True,
                    synced_files=[filename],
                    author=author or "system",
                    base_branch=ver_res.get("base_branch") or base_branch,
                )
            finally:
                store.close()
            nodes_upserted = sync_res.get("nodes_upserted", len(delta.nodes))
            edges_upserted = sync_res.get("edges_upserted", len(delta.edges))
            graphify_synced = True
        except Exception as ge:
            graphify_synced = False
            logger.warning(f"Graphify extraction for uploaded file {filename} skipped or failed: {ge}")

        memory_ids = ver_res.get("memory_ids") or []
        doc_schema = ver_res.get("doc_schema")
        schema_used = (
            sync_res.get("schema_name")
            or sync_res.get("schema_used")
            or doc_schema
        )
        return {
            "status": "success",
            "filename": filename,
            "project_id": project_id,
            "branch": branch,
            "schema_used": schema_used,
            "db_name": sync_res.get("db_name") or ver_res.get("db_name"),
            "doc_schema": doc_schema,
            "memory_id": memory_ids[0] if memory_ids else None,
            "tags": tag_list,
            "graphify_synced": graphify_synced,
            "nodes_upserted": nodes_upserted,
            "edges_upserted": edges_upserted,
            "doc_key": ver_res["doc_key"],
            "doc_version": ver_res["doc_version"],
            "parent_version": ver_res["parent_version"],
            "total_units": ver_res["total_units"],
            "reused_units": ver_res["reused_units"],
            "new_units": ver_res["new_units"],
            "deleted_units": ver_res["deleted_units"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to upload and parse document: {str(e)}")


@router.get("/documents")
async def get_documents(
    project_id: str = "default",
    branch: Optional[str] = None,
    status: str = "active",
):
    from ...graphify_engine.doc_versioning import list_documents
    try:
        docs = list_documents(project_id=project_id, branch=branch, status=status)
        return {
            "project_id": project_id,
            "branch": branch,
            "status": status,
            "documents": docs,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to list documents: {str(e)}")


@router.get("/documents/{doc_key}/versions")
async def get_document_versions(
    doc_key: str,
    project_id: str = "default",
    branch: Optional[str] = None,
):
    from ...graphify_engine.doc_versioning import list_document_versions
    try:
        versions = list_document_versions(project_id=project_id, doc_key=doc_key, branch=branch)
        return {
            "project_id": project_id,
            "doc_key": doc_key,
            "branch": branch,
            "versions": versions,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to list document versions: {str(e)}")


@router.get("/documents/{doc_key}/diff")
async def get_document_diff(
    doc_key: str,
    from_version: str,
    to_version: str,
    project_id: str = "default",
    branch: str = "main",
    from_branch: Optional[str] = None,
    to_branch: Optional[str] = None,
):
    from ...graphify_engine.doc_versioning import diff_document_versions
    try:
        return diff_document_versions(
            project_id=project_id,
            doc_key=doc_key,
            from_version=from_version,
            to_version=to_version,
            branch=branch,
            from_branch=from_branch,
            to_branch=to_branch,
        )
    except (KeyError, ValueError) as not_found_err:
        raise HTTPException(404, str(not_found_err))
    except Exception as e:
        raise HTTPException(500, f"Failed to diff document versions: {str(e)}")


class ingest_req(BaseModel):
    creds: Dict[str, Any] = {}
    filters: Dict[str, Any] = {}
    user_id: Optional[str] = None


@router.get("")
async def list_sources():
    return {
        "sources": ["github", "notion", "google_drive", "google_sheets", "google_slides", "onedrive", "web_crawler"],
        "usage": {
            "ingest": "POST /sources/{source}/ingest { creds: {}, filters: {}, user_id? }",
            "webhook": "POST /sources/webhook/{source} (source-specific payload)"
        }
    }


@router.post("/{source}/ingest")
async def ingest_source(source: str, req: ingest_req):
    _check_read_only()
    from ...connectors import (
        github_connector, notion_connector, google_drive_connector,
        google_sheets_connector, google_slides_connector,
        onedrive_connector, web_crawler_connector
    )

    source_map = {
        "github": github_connector,
        "notion": notion_connector,
        "google_drive": google_drive_connector,
        "google_sheets": google_sheets_connector,
        "google_slides": google_slides_connector,
        "onedrive": onedrive_connector,
        "web_crawler": web_crawler_connector,
    }

    if source not in source_map:
        raise HTTPException(400, f"unknown source: {source}. available: {list(source_map.keys())}")

    try:
        src = source_map[source](user_id=req.user_id)
        await src.connect(**req.creds)
        ids = await src.ingest_all(**req.filters)
        return {"ok": True, "ingested": len(ids), "memory_ids": ids}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/webhook/github")
async def github_webhook(request: Request):
    _check_read_only()
    from ...ops.ingest import ingest_document

    event_type = request.headers.get("x-github-event", "unknown")
    payload = await request.json()

    if not payload:
        raise HTTPException(400, "no payload")

    try:
        content = ""
        meta = {"source": "github_webhook", "event": event_type}

        if event_type == "push":
            commits = payload.get("commits", [])
            content = "\n\n".join([f"{c['message']}\n{c['url']}" for c in commits])
            meta["repo"] = payload.get("repository", {}).get("full_name")
            meta["ref"] = payload.get("ref")
        elif event_type == "issues":
            issue = payload.get("issue", {})
            content = f"[{payload.get('action')}] {issue.get('title')}\n{issue.get('body', '')}"
            meta["repo"] = payload.get("repository", {}).get("full_name")
            meta["issue_number"] = issue.get("number")
        elif event_type == "pull_request":
            pr = payload.get("pull_request", {})
            content = f"[{payload.get('action')}] PR: {pr.get('title')}\n{pr.get('body', '')}"
            meta["repo"] = payload.get("repository", {}).get("full_name")
            meta["pr_number"] = pr.get("number")
        else:
            import json
            content = json.dumps(payload, indent=2)

        if content:
            result = await ingest_document("text", content, meta=meta)
            return {"ok": True, "memory_id": result.get("root_memory_id"), "event": event_type}
        return {"ok": True, "skipped": True, "reason": "no content"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/webhook/notion")
async def notion_webhook(request: Request):
    _check_read_only()
    from ...ops.ingest import ingest_document
    import json

    payload = await request.json()

    try:
        content = json.dumps(payload, indent=2)
        result = await ingest_document("text", content, meta={"source": "notion_webhook"})
        return {"ok": True, "memory_id": result.get("root_memory_id")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
