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
        import logging
        logger = logging.getLogger("server")
        logger.info(f"[get_history] Raw user_id={repr(user_id)} limit={limit} offset={offset}")
        results = mem.history(user_id, limit, offset)
        logger.info(f"[get_history] Result size={len(results)}")
        return {"history": results}
    except Exception as e:
        import logging
        logging.getLogger("server").error(f"[get_history] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
async def get_stats(user_id: Optional[str] = None):
    try:
        import logging
        logger = logging.getLogger("server")
        logger.info(f"[get_stats] Raw user_id={repr(user_id)}")
        from ...core.db import db
        if user_id:
            if "%" in user_id or "_" in user_id:
                logger.info(f"[get_stats] Using LIKE query for user_id={repr(user_id)}")
                total_res = db.fetchone("SELECT count(*) as c FROM memories WHERE user_id LIKE ?", (user_id,))
                sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories WHERE user_id LIKE ? GROUP BY primary_sector", (user_id,))
                facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts WHERE user_id LIKE ?", (user_id,))
                tags_res = db.fetchall("SELECT tags FROM memories WHERE user_id LIKE ?", (user_id,))
            else:
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

@router.get("/diff")
async def diff_memories(base_project_id: str, target_project_id: str):
    try:
        from ...core.db import db
        db.connect()
        
        # Fetch base memories
        base_rows = db.fetchall(
            "SELECT id, content, primary_sector, tags, meta, created_at FROM memories WHERE user_id = ?",
            (base_project_id,)
        )
        # Fetch target memories
        target_rows = db.fetchall(
            "SELECT id, content, primary_sector, tags, meta, created_at FROM memories WHERE user_id = ?",
            (target_project_id,)
        )
        
        # Helper to parse meta
        def parse_meta(row):
            meta_str = row.get("meta") or "{}"
            try:
                if isinstance(meta_str, dict):
                    return meta_str
                return json.loads(meta_str)
            except Exception:
                return {}
        
        base_map = {}
        for r in base_rows:
            meta = parse_meta(r)
            file_path = meta.get("file_path")
            sec_idx = meta.get("section_index")
            if file_path is not None and sec_idx is not None:
                key = (file_path, sec_idx)
            else:
                key = r["id"]
            base_map[key] = (r, meta)
            
        target_map = {}
        for r in target_rows:
            meta = parse_meta(r)
            file_path = meta.get("file_path")
            sec_idx = meta.get("section_index")
            if file_path is not None and sec_idx is not None:
                key = (file_path, sec_idx)
            else:
                key = r["id"]
            target_map[key] = (r, meta)
            
        added = []
        deleted = []
        modified = []
        
        for key, (r_target, meta_target) in target_map.items():
            if key not in base_map:
                added.append(r_target)
            else:
                r_base, meta_base = base_map[key]
                if r_base["content"].strip() != r_target["content"].strip():
                    modified.append({
                        "base": r_base,
                        "target": r_target,
                        "file_path": meta_target.get("file_path"),
                        "section_index": meta_target.get("section_index")
                    })
                    
        for key, (r_base, meta_base) in base_map.items():
            if key not in target_map:
                deleted.append(r_base)
                
        return {
            "base_project_id": base_project_id,
            "target_project_id": target_project_id,
            "added": added,
            "deleted": deleted,
            "modified": modified
        }
    except Exception as e:
        import logging
        logging.getLogger("server").error(f"[diff_memories] Error: {e}")
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

class ActiveProjectRequest(BaseModel):
    project_id: str

@router.post("/active-project")
def set_active_project(req: ActiveProjectRequest):
    try:
        from ...core.config import env
        env.active_project = req.project_id
        return {"success": True, "active_project": env.active_project}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/active-project")
def get_active_project():
    try:
        from ...core.config import env
        return {"active_project": env.active_project or "default"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

