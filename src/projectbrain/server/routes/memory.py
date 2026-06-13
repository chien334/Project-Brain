import json
from fastapi import APIRouter, HTTPException, Body
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from ...main import Memory
mem = Memory()

router = APIRouter()

class AddMemoryRequest(BaseModel):
    content: str
    user_id: Optional[str] = None
    tags: Optional[List[str]] = []
    metadata: Optional[Dict[str, Any]] = {}
    author: Optional[str] = None

class SearchMemoryRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    limit: Optional[int] = 10
    filters: Optional[Dict[str, Any]] = {}
    author: Optional[str] = None

class DeleteAllRequest(BaseModel):
    user_id: Optional[str] = None

@router.post("/add")
async def add_memory(req: AddMemoryRequest):
    try:
        meta = req.metadata or {}
        if req.tags: meta["tags"] = req.tags
        if req.author: meta["author"] = req.author

        result = await mem.add(req.content, user_id=req.user_id, meta=meta, tags=req.tags)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search")
async def search_memory(req: SearchMemoryRequest):
    try:
        import logging
        logger = logging.getLogger("server")
        logger.info(f"User '{req.author or 'anonymous'}' searched: '{req.query}' on project '{req.user_id or 'all'}'")
        
        filters = req.filters or {}
        results = await mem.search(req.query, user_id=req.user_id, limit=req.limit, **filters)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_history(user_id: Optional[str] = None, limit: int = 20, offset: int = 0):
    try:
        results = mem.history(user_id, limit, offset)
        return {"history": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_stats(user_id: Optional[str] = None):
    try:
        from ...core.db import db
        if user_id:
            total_res = db.fetchone("SELECT count(*) as c FROM memories WHERE user_id=?", (user_id,))
            sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories WHERE user_id=? GROUP BY primary_sector", (user_id,))
            facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts WHERE user_id=?", (user_id,))
            tags_res = db.fetchall("SELECT tags FROM memories WHERE user_id=?", (user_id,))
        else:
            total_res = db.fetchone("SELECT count(*) as c FROM memories")
            sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories GROUP BY primary_sector")
            facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts")
            tags_res = db.fetchall("SELECT tags FROM memories")
            
        total_mems = total_res["c"] if total_res else 0
        total_facts = facts_res["c"] if facts_res else 0
        sectors = {r["primary_sector"]: r["c"] for r in sector_res}
        
        all_tags = set()
        for r in tags_res:
            if r.get("tags"):
                try:
                    t_list = json.loads(r["tags"])
                    if isinstance(t_list, list):
                        for t in t_list:
                            all_tags.add(t)
                except:
                    pass
                    
        return {
            "total_memories": total_mems,
            "total_temporal_facts": total_facts,
            "sectors": sectors,
            "tags": list(all_tags),
            "user_id": user_id or "all"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete_all")
async def delete_all_memories(req: Optional[DeleteAllRequest] = None):
    try:
        uid = req.user_id if req else None
        await mem.delete_all(user_id=uid)
        return {"success": True, "message": f"Deleted all memories for user {uid or 'all'}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{memory_id}")
async def get_single_memory(memory_id: str):
    try:
        m = await mem.get(memory_id)
        if not m:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"success": True, "data": m}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{memory_id}")
async def delete_single_memory(memory_id: str):
    try:
        await mem.delete(memory_id)
        return {"success": True, "message": f"Memory {memory_id} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{memory_id}/reinforce")
async def reinforce_memory(memory_id: str):
    try:
        m = await mem.get(memory_id)
        if not m:
            raise HTTPException(status_code=404, detail="Memory not found")
        from ...ops.dynamics import applyRetrievalTraceReinforcementToMemory
        import time
        from ...core.db import db
        new_sal = await applyRetrievalTraceReinforcementToMemory(memory_id, m.get("salience", 0) or 0)
        db.execute("UPDATE memories SET salience=?, last_seen_at=? WHERE id=?", (new_sal, int(time.time()*1000), memory_id))
        db.commit()
        return {"success": True, "new_salience": new_sal}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
