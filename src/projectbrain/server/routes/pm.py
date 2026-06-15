from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from ...main import Memory
from ...ai.gemini import GeminiAdapter

router = APIRouter(tags=["pm"])
mem = Memory()

class PMSearchRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    limit: Optional[int] = 10
    author: Optional[str] = None

class PMDocRequest(BaseModel):
    prompt: str
    doc_type: str = "status_report" # status_report, prd, user_story, roadmap
    user_id: Optional[str] = None
    author: Optional[str] = None

@router.post("/search")
async def pm_search(req: PMSearchRequest):
    try:
        import logging
        logger = logging.getLogger("server")
        logger.info(f"User '{req.author or 'anonymous'}' triggered PM RAG search: '{req.query}' on project '{req.user_id or 'all'}'")

        # 1. Search ProjectBrain
        mems = await mem.search(req.query, user_id=req.user_id, limit=req.limit)
        
        if not mems:
            return {
                "results": "No relevant memories found to answer the question.",
                "source_memories": []
            }
            
        # 2. Build RAG prompt for Gemini
        context = "\n".join([f"- [{m.get('primary_sector')} | Tag: {m.get('tags')}] {m.get('content')}" for m in mems])
        
        prompt = f"""You are a smart AI assistant supporting a PM (Project Manager). 
Please answer the PM's question based on the project memories (context) found below.
If the information in the context is insufficient, answer based on what is available and clearly state the gaps.

Question: {req.query}

Project memories found:
{context}
"""
        # Call Gemini
        adapter = GeminiAdapter()
        answer = await adapter.chat([
            {"role": "user", "content": prompt}
        ])
        
        return {
            "results": answer,
            "source_memories": mems
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
@router.post("/generate-doc")
async def pm_generate_doc(req: PMDocRequest):
    try:
        import logging
        logger = logging.getLogger("server")
        logger.info(f"User '{req.author or 'anonymous'}' triggered PM document generation (Type: {req.doc_type}) for prompt: '{req.prompt}' on project '{req.user_id or 'all'}'")
 
        # 1. Search memories related to the prompt
        mems = await mem.search(req.prompt, user_id=req.user_id, limit=20)
        
        # 2. Build RAG prompt for document generation
        context = "\n".join([f"- [{m.get('primary_sector')}] {m.get('content')}" for m in mems])
        
        doc_instructions = {
            "status_report": "Project Status Report including completed tasks, in-progress work, and identified risks.",
            "prd": "Product Requirement Document (PRD) including overview, objectives, user stories, functional and technical requirements.",
            "user_story": "Detailed list of User Stories accompanied by acceptance criteria (Acceptance Criteria).",
            "roadmap": "Product Roadmap broken down by milestones and major features."
        }
        
        instruction = doc_instructions.get(req.doc_type, doc_instructions["status_report"])
        
        prompt = f"""You are an outstanding Product Manager expert. 
Your task is to draft a document: {instruction}
Please base your writing on the actual facts collected from the project memories below, and write the document professionally with a clear Markdown structure.

PM Request: {req.prompt}

Actual project memories collected:
{context}
"""
        # Call Gemini
        adapter = GeminiAdapter()
        document = await adapter.chat([
            {"role": "user", "content": prompt}
        ])
        
        return {
            "document": document,
            "source_memories": mems
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
