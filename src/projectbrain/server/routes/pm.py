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

class PMDocRequest(BaseModel):
    prompt: str
    doc_type: str = "status_report" # status_report, prd, user_story, roadmap
    user_id: Optional[str] = None

@router.post("/search")
async def pm_search(req: PMSearchRequest):
    try:
        # 1. Search ProjectBrain
        mems = await mem.search(req.query, user_id=req.user_id, limit=req.limit)
        
        if not mems:
            return {
                "results": "Không tìm thấy ký ức nào liên quan để trả lời câu hỏi.",
                "source_memories": []
            }
            
        # 2. Build RAG prompt for Gemini
        context = "\n".join([f"- [{m.get('primary_sector')} | Tag: {m.get('tags')}] {m.get('content')}" for m in mems])
        
        prompt = f"""Bạn là một trợ lý AI thông minh hỗ trợ PM (Quản trị dự án). 
Hãy trả lời câu hỏi của PM dựa trên các thông tin ký ức (context) được tìm thấy dưới đây.
Nếu thông tin trong context không đủ, hãy trả lời dựa trên những gì có sẵn và nêu rõ thiếu sót.

Câu hỏi: {req.query}

Thông tin ký ức tìm thấy:
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
        # 1. Search memories related to the prompt
        mems = await mem.search(req.prompt, user_id=req.user_id, limit=20)
        
        # 2. Build RAG prompt for document generation
        context = "\n".join([f"- [{m.get('primary_sector')}] {m.get('content')}" for m in mems])
        
        doc_instructions = {
            "status_report": "Báo cáo tiến độ dự án (Status Report) bao gồm các công việc hoàn thành, đang làm, và các rủi ro phát sinh.",
            "prd": "Tài liệu Yêu cầu Sản phẩm (PRD) bao gồm tổng quan, mục tiêu, user stories, yêu cầu chức năng và kỹ thuật.",
            "user_story": "Danh sách các User Stories chi tiết kèm theo tiêu chí nghiệm thu (Acceptance Criteria).",
            "roadmap": "Lộ trình phát triển sản phẩm (Roadmap) chia theo các mốc thời gian và tính năng chính."
        }
        
        instruction = doc_instructions.get(req.doc_type, doc_instructions["status_report"])
        
        prompt = f"""Bạn là một chuyên gia Product Manager xuất sắc. 
Nhiệm vụ của bạn là soạn thảo một tài liệu: {instruction}
Hãy dựa trên thông tin thực tế thu thập từ ký ức của dự án dưới đây để viết tài liệu này một cách chuyên nghiệp, cấu trúc rõ ràng bằng Markdown.

Yêu cầu PM: {req.prompt}

Thông tin ký ức thực tế thu thập được:
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
