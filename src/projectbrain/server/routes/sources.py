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

    model_config = ConfigDict(extra="allow")


@router.post("/push-processed")
async def push_processed(req: PushDocumentRequest):
    _check_read_only()
    try:
        project_id = req.project_id or "default"
        branch = req.branch or "main"
        doc = req.document or {}

        filename = doc.get("filename", "unknown_doc")
        sha256 = doc.get("sha256", "")
        author = doc.get("author", "system")
        file_type = doc.get("file_type", "document")
        markdown_content = doc.get("markdown_content", "")
        chunks = doc.get("chunks")

        # Determine effective user_id
        if ":" in project_id:
            effective_user_id = project_id
        elif branch and branch != "main":
            effective_user_id = f"{project_id}:{branch}"
        else:
            effective_user_id = project_id

        # 1. Ingest chunks into HSG memory
        from ...memory.hsg import add_hsg_memory
        from ...utils.chunking import chunk_text

        if not chunks and markdown_content:
            raw_chunks = chunk_text(markdown_content, tgt=768, ovr=0.1)
            chunks = [{"content": c["text"], "chunk_id": f"p_{i + 1}"} for i, c in enumerate(raw_chunks)]

        memories_created = 0
        if chunks:
            for ch in chunks:
                chunk_text_content = ch.get("content") or ch.get("text") or ""
                if not chunk_text_content.strip():
                    continue
                chunk_tags = ch.get("tags") or ["document", file_type]
                tag_str = ",".join(chunk_tags) if isinstance(chunk_tags, list) else str(chunk_tags)
                chunk_meta = {
                    "source": "push-processed",
                    "filename": filename,
                    "sha256": sha256,
                    "author": author,
                    "file_type": file_type,
                    "chunk_id": ch.get("chunk_id"),
                    "project_id": project_id,
                    "branch": branch,
                }
                await add_hsg_memory(
                    content=chunk_text_content,
                    tags=tag_str,
                    metadata=chunk_meta,
                    user_id=effective_user_id,
                )
                memories_created += 1

        # 2. Ingest GraphDelta into GraphStore
        from ...graphify_engine.models import GraphDelta
        from ...graphify_engine.store import get_graph_store
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
                markdown_content=markdown_content,
                project_id=project_id,
                branch=branch,
                metadata={"author": author, "sha256": sha256, "file_type": file_type},
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
            "nodes_upserted": nodes_upserted,
            "edges_upserted": edges_upserted,
            "memories_created": memories_created,
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

        # 1. Parse document to markdown using our utility
        from ...utils.doc_parser import parse_document
        markdown_content = await parse_document(filename, file_bytes)

        if not markdown_content.strip():
            raise HTTPException(400, "Document content is empty or could not be parsed.")

        # 2. Add to memory under the project_id (which maps to user_id)
        from ...main import Memory
        mem = Memory()

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        if "document" not in tag_list:
            tag_list.append("document")

        meta = {
            "source": "upload",
            "filename": filename,
            "size": len(file_bytes),
            "project_id": project_id,
            "branch": branch,
        }
        if author:
            meta["author"] = author

        user_target = project_id
        if ":" not in user_target and branch and branch != "main":
            user_target = f"{project_id}:{branch}"

        res = await mem.add(
            content=f"Document: {filename}\n\n{markdown_content}",
            user_id=user_target,
            meta=meta,
            tags=tag_list
        )

        # 3. Extract and sync document graph into GraphStore branch overlay
        nodes_upserted = 0
        edges_upserted = 0
        graphify_synced = False
        try:
            from ...graphify_engine.extractor import GraphifyExtractor
            from ...graphify_engine.store import get_graph_store
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
                )
            finally:
                store.close()
            nodes_upserted = sync_res.get("nodes_upserted", len(delta.nodes))
            edges_upserted = sync_res.get("edges_upserted", len(delta.edges))
            graphify_synced = True
        except Exception as ge:
            graphify_synced = False
            logger.warning(f"Graphify extraction for uploaded file {filename} skipped or failed: {ge}")

        return {
            "status": "success",
            "filename": filename,
            "project_id": project_id,
            "branch": branch,
            "memory_id": res.get("root_memory_id") or res.get("id"),
            "tags": tag_list,
            "graphify_synced": graphify_synced,
            "nodes_upserted": nodes_upserted,
            "edges_upserted": edges_upserted,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to upload and parse document: {str(e)}")


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
