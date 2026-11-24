import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel

from ...ai.gemini import GeminiAdapter  # Kept for backward compatibility
from ...ai.runtime_settings import (
    get_public_ai_settings_async,
    lookup_project_graph_matches,
    run_pm_doc_generation,
    run_rag_chat,
    sanitize_secret,
    test_ai_provider,
    update_ai_settings,
)
from ...main import Memory
from ...memory.hsg import resolve_cross_source_relations_summary
from . import auth

router = APIRouter(tags=["pm"])
mem = Memory()
logger = logging.getLogger("server")


class PMSearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    limit: Optional[int] = 10
    author: Optional[str] = None
    source_id: Optional[str] = None
    include_shared_docs: bool = True
    relation_depth: int = 1
    relation_types: Optional[List[str]] = None


class PMDocRequest(BaseModel):
    prompt: str
    doc_type: str = "status_report"  # status_report, prd, user_story, roadmap
    user_id: Optional[str] = None
    author: Optional[str] = None
    source_id: Optional[str] = None
    include_shared_docs: bool = True
    relation_depth: int = 1
    relation_types: Optional[List[str]] = None


class AISettingsUpdateRequest(BaseModel):
    provider: Optional[str] = None
    fallback_to_local_rag: Optional[bool] = None
    emb_kind: Optional[str] = None
    gemini_key: Optional[str] = None
    clear_gemini_key: Optional[bool] = None
    gemini_model: Optional[str] = None
    gemini_base_url: Optional[str] = None
    openai_key: Optional[str] = None
    clear_openai_key: Optional[bool] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_vision_model: Optional[str] = None
    minimax_key: Optional[str] = None
    clear_minimax_key: Optional[bool] = None
    minimax_base_url: Optional[str] = None
    minimax_model: Optional[str] = None
    local_ai_base_url: Optional[str] = None
    local_ai_model: Optional[str] = None
    local_ai_api_key: Optional[str] = None
    clear_local_ai_key: Optional[bool] = None


class AISettingsTestRequest(AISettingsUpdateRequest):
    pass


def _dump_request_dict(model_obj: Optional[BaseModel]) -> Dict[str, Any]:
    if model_obj is None:
        return {}
    if hasattr(model_obj, "model_dump"):
        return model_obj.model_dump(exclude_unset=True)
    return model_obj.dict(exclude_unset=True)


def _enforce_admin_if_authenticated(request: Optional[Request]) -> Optional[Dict[str, Any]]:
    account = auth.get_authenticated_account(request)
    if account and account.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Permission denied: Admin role required.")
    if auth._is_rest_auth_required() and not account:
        raise HTTPException(
            status_code=401,
            detail="Authentication required: Valid admin token required.",
        )
    return account


@router.get("/ai-settings")
async def get_ai_settings_endpoint(
    probe_ollama: bool = Query(default=True),
):
    return await get_public_ai_settings_async(probe_ollama=probe_ollama)


@router.put("/ai-settings")
async def put_ai_settings_endpoint(
    req: AISettingsUpdateRequest,
    request: Request = None,
    probe_ollama: bool = Query(default=False),
):
    account = _enforce_admin_if_authenticated(request)
    payload = _dump_request_dict(req)
    try:
        _, audit_summary = update_ai_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    audit_fn = getattr(auth, "_record_audit", None) or getattr(auth, "_log_audit", None)
    if callable(audit_fn):
        audit_fn(
            "ai_settings_updated",
            "success",
            actor_username=(account or {}).get("username"),
            request=request,
            details=audit_summary,
        )

    public_settings = await get_public_ai_settings_async(probe_ollama=probe_ollama)
    return {"status": "success", "settings": public_settings, **public_settings}


@router.post("/ai-settings/test")
async def test_ai_settings_endpoint(
    req: Optional[AISettingsTestRequest] = Body(default=None),
    request: Request = None,
):
    _enforce_admin_if_authenticated(request)
    overrides = _dump_request_dict(req)
    return await test_ai_provider(overrides=overrides or None)


@router.post("/search")
async def pm_search(req: PMSearchRequest):
    try:
        logger.info(
            f"User '{req.author or 'anonymous'}' triggered PM RAG search: "
            f"'{req.query}' on project '{req.user_id or 'all'}'"
        )

        # 1. Search cognitive memories + Graphify code/doc symbols across source & related sources
        mems = await mem.search(
            req.query,
            user_id=req.user_id,
            limit=req.limit or 10,
            source_id=req.source_id,
            include_shared_docs=req.include_shared_docs,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
        )
        graph_nodes = lookup_project_graph_matches(
            req.query,
            req.user_id,
            source_id=req.source_id,
            include_shared_docs=req.include_shared_docs,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
        )
        cross_source_relations = resolve_cross_source_relations_summary(
            req.user_id,
            source_id=req.source_id,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
            include_shared_docs=req.include_shared_docs,
        )

        if not mems and not graph_nodes:
            return {
                "results": "No relevant memories or graph symbols found to answer the question.",
                "source_memories": [],
                "graph_matches": [],
                "cross_source_relations": cross_source_relations,
                "provider_used": "none",
                "model_used": "none",
                "provider_label": "No Matches",
                "fallback_reason": None,
            }

        # 2. Synthesize via active AI provider or zero-key Local RAG
        rag_res = await run_rag_chat(
            req.query,
            mems,
            graph_nodes,
            source_id=req.source_id,
            include_shared_docs=req.include_shared_docs,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
            cross_source_relations=cross_source_relations,
        )
        return {
            "results": rag_res["answer"],
            "source_memories": mems,
            "graph_matches": graph_nodes,
            "cross_source_relations": cross_source_relations,
            "provider_used": rag_res["provider_used"],
            "model_used": rag_res["model_used"],
            "provider_label": rag_res["provider_label"],
            "fallback_reason": rag_res["fallback_reason"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_secret(e))


@router.post("/generate-doc")
async def pm_generate_doc(req: PMDocRequest):
    try:
        logger.info(
            f"User '{req.author or 'anonymous'}' triggered PM document generation "
            f"(Type: {req.doc_type}) for prompt: '{req.prompt}' on project '{req.user_id or 'all'}'"
        )

        # 1. Search memories & graph symbols related to the prompt
        mems = await mem.search(
            req.prompt,
            user_id=req.user_id,
            limit=20,
            source_id=req.source_id,
            include_shared_docs=req.include_shared_docs,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
        )
        graph_nodes = lookup_project_graph_matches(
            req.prompt,
            req.user_id,
            limit=10,
            source_id=req.source_id,
            include_shared_docs=req.include_shared_docs,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
        )
        cross_source_relations = resolve_cross_source_relations_summary(
            req.user_id,
            source_id=req.source_id,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
            include_shared_docs=req.include_shared_docs,
        )

        # 2. Generate document via active AI provider or zero-key Local RAG
        doc_res = await run_pm_doc_generation(
            req.prompt,
            req.doc_type,
            mems,
            graph_nodes,
            source_id=req.source_id,
            include_shared_docs=req.include_shared_docs,
            relation_depth=req.relation_depth,
            relation_types=req.relation_types,
            cross_source_relations=cross_source_relations,
        )
        return {
            "document": doc_res["document"],
            "source_memories": mems,
            "graph_matches": graph_nodes,
            "cross_source_relations": cross_source_relations,
            "provider_used": doc_res["provider_used"],
            "model_used": doc_res["model_used"],
            "provider_label": doc_res["provider_label"],
            "fallback_reason": doc_res["fallback_reason"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=sanitize_secret(e))
