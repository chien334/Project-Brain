"""
sources routes - ingest data from external sources via HTTP

POST /sources/{source}/ingest
  body: { creds: {...}, filters: {...}, user_id?: string }

POST /sources/webhook/{source}
  generic webhook endpoint for source-specific payloads
"""
from fastapi import APIRouter, Request, HTTPException, File, UploadFile, Form
from typing import Optional, Dict, Any
from pydantic import BaseModel

router = APIRouter(prefix="/sources", tags=["sources"])

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    project_id: str = Form("default"),
    tags: Optional[str] = Form(""),
    author: Optional[str] = Form(None)
):
    try:
        file_bytes = await file.read()
        filename = file.filename
        
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
            "project_id": project_id
        }
        if author:
            meta["author"] = author
            
        res = await mem.add(
            content=f"Document: {filename}\n\n{markdown_content}",
            user_id=project_id,
            meta=meta,
            tags=tag_list
        )
        
        return {
            "status": "success",
            "filename": filename,
            "project_id": project_id,
            "memory_id": res.get("root_memory_id") or res.get("id"),
            "tags": tag_list
        }
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
    from ..connectors import (
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
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("/webhook/github")
async def github_webhook(request: Request):
    from ..ops.ingest import ingest_document

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
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("/webhook/notion")
async def notion_webhook(request: Request):
    from ..ops.ingest import ingest_document
    import json

    payload = await request.json()

    try:
        content = json.dumps(payload, indent=2)
        result = await ingest_document("text", content, meta={"source": "notion_webhook"})
        return {"ok": True, "memory_id": result.get("root_memory_id")}
    except Exception as e:
        raise HTTPException(500, str(e))
