import time
import math
import asyncio
import hashlib
import json
import re
import random
import numpy as np
import uuid
from collections import OrderedDict, deque
from typing import List, Dict, Any, Optional, Set, Tuple

from ..core.db import q, db, transaction
from ..core.config import env
from ..core.constants import SECTOR_CONFIGS
from ..core.tenant_manager import (
    _effective_root_branch,
    get_current_source,
    get_current_tenant,
    is_multi_db_enabled,
    parse_tenant_from_user_id,
    parse_tenant_spec,
    tenant_context,
    tenant_manager,
)
from ..core.vector_store import vector_store as store
from ..utils.text import canonical_token_set, canonical_tokens_from_text, stable_text_fallback_hash
from ..utils.chunking import chunk_text
from ..utils.keyword import keyword_filter_memories, compute_keyword_overlap
from ..utils.vectors import buf_to_vec, vec_to_buf, cos_sim
from .embed import embed_multi_sector, embed_for_sector, embed_multi_sector, calc_mean_vec
from .decay import inc_q, dec_q, on_query_hit, calc_recency_score as calc_recency_score_decay, pick_tier
from ..ops.dynamics import (
    calculateCrossSectorResonanceScore,
    applyRetrievalTraceReinforcementToMemory,
    applyRetrievalTraceReinforcementToMemory,
    propagateAssociativeReinforcementToLinkedNodes
)
from .user_summary import update_user_summary
SCORING_WEIGHTS = {
    "similarity": 0.35,
    "overlap": 0.20,
    "waypoint": 0.15,
    "recency": 0.10,
    "tag_match": 0.20,
}

HYBRID_PARAMS = {
    "tau": 3.0,
    "beta": 2.0,
    "eta": 0.1,
    "gamma": 0.2,
    "alpha_reinforce": 0.08,
    "t_days": 7.0,
    "t_max_days": 60.0,
    "tau_hours": 1.0,
    "epsilon": 1e-8,
}

REINFORCEMENT = {
    "salience_boost": 0.1,
    "waypoint_boost": 0.05,
    "max_salience": 1.0,
    "max_waypoint_weight": 1.0,
    "prune_threshold": 0.05,
}

SECTOR_RELATIONSHIPS = {
    "semantic": {"procedural": 0.8, "episodic": 0.6, "reflective": 0.7, "emotional": 0.4},
    "procedural": {"semantic": 0.8, "episodic": 0.6, "reflective": 0.6, "emotional": 0.3},
    "episodic": {"reflective": 0.8, "semantic": 0.6, "procedural": 0.6, "emotional": 0.7},
    "reflective": {"episodic": 0.8, "semantic": 0.7, "procedural": 0.6, "emotional": 0.6},
    "emotional": {"episodic": 0.7, "reflective": 0.6, "semantic": 0.4, "procedural": 0.3},
}

async def embed_query_for_all_sectors(query: str, sectors: List[str]) -> Dict[str, List[float]]:
    res = {}
    for s in sectors:
        res[s] = await embed_for_sector(query, s)
    return res

def has_temporal_markers(text: str) -> bool:
    pats = [
        r"\b(today|yesterday|tomorrow|this\s+week|last\s+week|this\s+morning)\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b20\d{2}[/-]?(0[1-9]|1[0-2])[/-]?(0[1-9]|[12]\d|3[01])\b",
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}",
        r"\bwhat\s+(did|have)\s+(i|we)\s+(do|done)\b",
    ]
    return any(re.search(p, text, re.I) for p in pats)

async def compute_tag_match_score(mid: str, q_toks: Set[str]) -> float:
    mem = q.get_mem(mid)
    if not mem or not mem["tags"]: return 0.0
    try:
        tags = json.loads(mem["tags"])
        if not isinstance(tags, list): return 0.0
        matches = 0
        for tag in tags:
            tl = str(tag).lower()
            if tl in q_toks: matches += 2
            else:
                for tok in q_toks:
                    if tl in tok or tok in tl: matches += 1
        return min(1.0, matches / max(1, len(tags) * 2))
    except:
        return 0.0

def compress_vec_for_storage(vec: List[float], target_dim: int) -> List[float]:
    if len(vec) <= target_dim: return vec
    comp = [0.0] * target_dim
    bucketsz = len(vec) / target_dim
    for i in range(target_dim):
        start = int(i * bucketsz)
        end = int((i + 1) * bucketsz)
        s = 0.0
        c = 0
        for j in range(start, min(end, len(vec))):
            s += vec[j]
            c += 1
        comp[i] = s / c if c > 0 else 0.0
    n = math.sqrt(sum(x*x for x in comp))
    if n > 0:
        for i in range(target_dim): comp[i] /= n
    return comp

def classify_content(content: str, metadata: Any = None) -> Dict[str, Any]:
    meta_sec = metadata.get("sector") if isinstance(metadata, dict) else None
    if meta_sec and meta_sec in SECTOR_CONFIGS:
        return {"primary": meta_sec, "additional": [], "confidence": 1.0}

    scores = {k: 0.0 for k in SECTOR_CONFIGS}
    for sec, cfg in SECTOR_CONFIGS.items():
        score = 0
        for pat in cfg["patterns"]:
            matches = pat.findall(content)
            if matches:
                score += len(matches) * cfg["weight"]
        scores[sec] = score

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary, p_score = sorted_scores[0]
    thresh = max(1.0, p_score * 0.3)

    additional = [s for s, sc in sorted_scores[1:] if sc > 0 and sc >= thresh]

    second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0
    confidence = min(1.0, p_score / (p_score + second_score + 1)) if p_score > 0 else 0.2

    return {
        "primary": primary if p_score > 0 else "semantic",
        "additional": additional,
        "confidence": confidence
    }

def calc_decay(sec: str, init_sal: float, days_since: float, seg_idx: Optional[int] = None, max_seg: Optional[int] = None) -> float:
    cfg = SECTOR_CONFIGS.get(sec)
    if not cfg: return init_sal
    lam = cfg["decay_lambda"]
    if seg_idx is not None and max_seg is not None and max_seg > 0:
        seg_ratio = math.sqrt(seg_idx / max_seg)
        lam = lam * (1.0 - seg_ratio)

    decayed = init_sal * math.exp(-lam * days_since)
    reinf = HYBRID_PARAMS["alpha_reinforce"] * (1 - math.exp(-lam * days_since))
    return max(0.0, min(1.0, decayed + reinf))

def calc_recency_score(last_seen: int) -> float:
    days = (time.time()*1000 - last_seen) / 86400000.0
    t = HYBRID_PARAMS["t_days"]
    tmax = HYBRID_PARAMS["t_max_days"]
    return math.exp(-days / t) * (1 - days / tmax)

def boosted_sim(s: float) -> float:
    return 1 - math.exp(-HYBRID_PARAMS["tau"] * s)

def compute_simhash(text: str) -> str:
    tokens = canonical_token_set(text)
    if not tokens:
        return stable_text_fallback_hash(text)
    vec = [0] * 64
    for t in tokens:
        h = int.from_bytes(hashlib.blake2b(t.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(64):
            if h & (1 << i):
                vec[i] += 1
            else:
                vec[i] -= 1

    res_hash = ""
    for i in range(0, 64, 4):
        nibble = 0
        if vec[i] > 0: nibble += 8
        if vec[i+1] > 0: nibble += 4
        if vec[i+2] > 0: nibble += 2
        if vec[i+3] > 0: nibble += 1
        res_hash += format(nibble, 'x')
    return res_hash

def hamming_dist(h1: str, h2: str) -> int:
    dist = 0
    for i in range(len(h1)):
        x = int(h1[i], 16) ^ int(h2[i], 16)
        if x & 8: dist += 1
        if x & 4: dist += 1
        if x & 2: dist += 1
        if x & 1: dist += 1
    return dist

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def extract_essence(raw: str, sec: str, max_len: int) -> str:
    if not env.use_summary_only or len(raw) <= max_len: return raw
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if len(s.strip()) > 10]
    if not sents: return raw[:max_len]

    scored = []
    for idx, s in enumerate(sents):
        sc = 0
        if idx == 0: sc += 10
        if idx == 1: sc += 5
        #if re.match(r"^#+\s", s) or re.match(r"^[A-Z][A-Z\s]+:", s): sc += 8
        if re.match(r"^[A-Z][a-z]+:", s): sc += 6
        if re.search(r"\d{4}-\d{2}-\d{2}", s): sc += 7
        if re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d+", s, re.I): sc += 5
        if re.search(r"\$\d+|\d+\s*(miles|dollars|years|months|km)", s): sc += 4
        if re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", s): sc += 3
        if re.search(r"\b(bought|purchased|serviced|visited|went|got|received|paid|earned|learned|discovered|found|saw|met|completed|finished|fixed|implemented|created|updated|added|removed|resolved)\b", s, re.I): sc += 4
        if re.search(r"\b(who|what|when|where|why|how)\b", s, re.I): sc += 2
        if len(s) < 80: sc += 2
        if re.search(r"\b(I|my|me)\b", s): sc += 1
        scored.append({"text": s, "score": sc, "idx": idx})

    scored.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    curr_len = 0
    first = next((x for x in scored if x["idx"] == 0), None)
    if first and len(first["text"]) < max_len:
        selected.append(first)
        curr_len += len(first["text"])

    for item in scored:
        if item["idx"] == 0: continue
        if curr_len + len(item["text"]) + 2 <= max_len:
            selected.append(item)
            curr_len += len(item["text"]) + 2

    selected.sort(key=lambda x: x["idx"])
    return " ".join(x["text"] for x in selected)

def compute_token_overlap(q_toks: Set[str], mem_toks: Set[str]) -> float:
    if not q_toks: return 0.0
    ov = len(q_toks.intersection(mem_toks))
    return ov / len(q_toks)

def compute_hybrid_score(sim: float, tok_ov: float, wp_wt: float, rec_sc: float, kw_score: float = 0, tag_match: float = 0) -> float:
    s_p = boosted_sim(sim)
    raw = (SCORING_WEIGHTS["similarity"] * s_p +
           SCORING_WEIGHTS["overlap"] * tok_ov +
           SCORING_WEIGHTS["waypoint"] * wp_wt +
           SCORING_WEIGHTS["recency"] * rec_sc +
           SCORING_WEIGHTS["tag_match"] * tag_match +
           kw_score)
    return sigmoid(raw)

async def create_single_waypoint(new_id: str, new_mean: List[float], ts: int, user_id: str = "anonymous"):
    mems = q.all_mem_by_user(user_id, 1000, 0) if user_id else q.all_mem(1000, 0)
    best = None
    best_sim = -1.0

    import numpy as np
    nm = np.array(new_mean, dtype=np.float32)

    for mem in mems:
        if mem["id"] == new_id or not mem["mean_vec"]: continue
        ex_mean = np.array(buf_to_vec(mem["mean_vec"]), dtype=np.float32)
        sim = cos_sim(nm, ex_mean)
        if sim > best_sim:
            best_sim = sim
            best = mem["id"]

    if best:
        db.execute("INSERT OR REPLACE INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)", (new_id, best, user_id, float(best_sim), ts, ts))
    else:
        db.execute("INSERT OR REPLACE INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)", (new_id, new_id, user_id, 1.0, ts, ts))
    db.commit()

async def create_cross_sector_waypoints(prim_id: str, prim_sec: str, add_secs: List[str], user_id: Optional[str] = None):
    now = int(time.time() * 1000)
    wt = 0.5
    for sec in add_secs:
        uid = user_id or "anonymous"
        db.execute("INSERT OR REPLACE INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                   (prim_id, f"{prim_id}:{sec}", uid, wt, now, now))
        db.execute("INSERT OR REPLACE INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                   (f"{prim_id}:{sec}", prim_id, uid, wt, now, now))
    db.commit()

async def create_inter_mem_waypoints(new_id: str, prim_sec: str, new_vec: List[float], ts: int, user_id: Optional[str] = None):
    thresh = 0.75
    wt = 0.5
    active_t = get_current_tenant()
    if active_t is None and user_id:
        p_id, p_src, br_name = parse_tenant_spec(user_id, default_branch=_effective_root_branch())
        ctx = tenant_context(p_id, br_name, source_id=_ctx_source_arg(p_src))
    else:
        from contextlib import nullcontext
        ctx = nullcontext()

    with ctx:
        vecs = await store.getVectorsBySector(prim_sec)

        nm = np.array(new_vec, dtype=np.float32)

        for vr in vecs:
            vr_id = vr["id"] if isinstance(vr, dict) else vr.id
            if vr_id == new_id: continue
            vr_vec = vr["vector"] if isinstance(vr, dict) else vr.vector
            ex_vec = np.array(vr_vec, dtype=np.float32)
            if ex_vec.shape != nm.shape:
                continue
            sim = cos_sim(nm, ex_vec)

            if sim >= thresh:
                uid = user_id or "anonymous"
                db.execute("INSERT OR REPLACE INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                           (new_id, vr_id, uid, wt, ts, ts))
                db.execute("INSERT OR REPLACE INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                           (vr_id, new_id, uid, wt, ts, ts))
        db.commit()

async def create_contextual_waypoints(mem_id: str, rel_ids: List[str], base_wt: float = 0.3, user_id: Optional[str] = None):
    now = int(time.time() * 1000)
    uid = user_id or "anonymous"

    for rel_id in rel_ids:
        if mem_id == rel_id: continue
        existing = db.fetchone("SELECT * FROM waypoints WHERE src_id=? AND dst_id=?", (mem_id, rel_id))

        if existing:
            new_wt = min(1.0, float(existing["weight"]) + 0.1)
            db.execute("UPDATE waypoints SET weight=?, updated_at=? WHERE src_id=? AND dst_id=?", (new_wt, now, mem_id, rel_id))
        else:
            db.execute("INSERT INTO waypoints(src_id,dst_id,user_id,weight,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                       (mem_id, rel_id, uid, base_wt, now, now))
    db.commit()

async def reinforce_waypoints(trav_path: List[str]):
    now = int(time.time() * 1000)
    for i in range(len(trav_path) - 1):
        src_id = trav_path[i]
        dst_id = trav_path[i+1]

        wp = db.fetchone("SELECT * FROM waypoints WHERE src_id=? AND dst_id=?", (src_id, dst_id))
        if wp:
            new_wt = min(REINFORCEMENT["max_waypoint_weight"], float(wp["weight"]) + REINFORCEMENT["waypoint_boost"])
            db.execute("UPDATE waypoints SET weight=?, updated_at=? WHERE src_id=? AND dst_id=?", (new_wt, now, src_id, dst_id))

    db.commit()

async def prune_weak_waypoints() -> int:
    thresh = REINFORCEMENT["prune_threshold"]
    db.execute("DELETE FROM waypoints WHERE weight < ?", (thresh,))
    db.commit()
    return 0

async def calc_multi_vec_fusion_score(mid: str, qe: Dict[str, List[float]], w: Dict[str, float]) -> float:
    vecs = await store.getVectorsById(mid)
    s = 0.0
    tot = 0.0

    wm = {
         "semantic": w.get("semantic_dimension_weight", 0),
         "emotional": w.get("emotional_dimension_weight", 0),
         "procedural": w.get("procedural_dimension_weight", 0),
         "episodic": w.get("temporal_dimension_weight", 0),
         "reflective": w.get("reflective_dimension_weight", 0),
    }

    for v in vecs:
        qv = qe.get(v.sector)
        if not qv: continue
        sim = cos_sim(v.vector, qv)
        wgt = wm.get(v.sector, 0.5)
        s += sim * wgt
        tot += wgt

    return s / tot if tot > 0 else 0.0

def _ctx_source_arg(source_id: Optional[str]) -> Optional[str]:
    """Convert normalized source_id into `tenant_context(..., source_id=...)` argument."""
    if source_id is None:
        return None
    cleaned = str(source_id).strip()
    if not cleaned or cleaned == "__default__":
        return None
    return cleaned


def _parse_meta_dict(raw_meta: Any) -> Dict[str, Any]:
    if isinstance(raw_meta, dict):
        return raw_meta
    if isinstance(raw_meta, str):
        try:
            parsed = json.loads(raw_meta)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_tags_list(raw_tags: Any) -> List[str]:
    if isinstance(raw_tags, list):
        return [str(t) for t in raw_tags if t is not None]
    if isinstance(raw_tags, str):
        cleaned = raw_tags.strip()
        if not cleaned:
            return []
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return [str(t) for t in parsed if t is not None]
        except Exception:
            pass
        return [t.strip() for t in cleaned.split(",") if t.strip()]
    return []


def _resolve_project_root_branch(project_id: str) -> str:
    try:
        if tenant_manager._master_table_exists("master_projects"):
            row = tenant_manager._master_fetchone(
                "SELECT root_branch FROM master_projects WHERE project_id = ?",
                (project_id,),
            )
            if row and row.get("root_branch"):
                return str(row["root_branch"]).strip() or _effective_root_branch()
    except Exception:
        pass
    return _effective_root_branch()


def _get_source_branch_lineage(
    project_id: str,
    branch: str,
    source_id: Optional[str],
    max_depth: int = 8,
) -> List[str]:
    from ..core.db import get_memory_branch_lineage

    if source_id in (None, "__default__", "__shared__"):
        return get_memory_branch_lineage(project_id, branch, max_depth=max_depth)

    br = (branch or "").strip()
    if not br:
        return []
    lineage: List[str] = [br]
    visited = {br}
    cur = br
    while len(lineage) <= max_depth:
        try:
            if not tenant_manager._master_table_exists("master_source_branches"):
                break
            row = tenant_manager._master_fetchone(
                "SELECT base_branch FROM master_source_branches "
                "WHERE project_id=? AND source_id=? AND branch_name=?",
                (project_id, source_id, cur),
            )
        except Exception:
            break
        parent = str(row["base_branch"]).strip() if row and row.get("base_branch") else None
        if not parent or parent == cur or parent in visited:
            break
        lineage.append(parent)
        visited.add(parent)
        cur = parent
    return lineage


def _maybe_record_overlay_tombstone(user_id: Optional[str], metadata: Any) -> None:
    if not user_id or ":" not in user_id or not isinstance(metadata, dict):
        return
    file_path = metadata.get("file_path")
    doc_key = metadata.get("doc_key")
    if not file_path and not doc_key:
        return
    proj, _, br = parse_tenant_spec(user_id, default_branch=_effective_root_branch())
    if not proj or not br:
        return
    from ..core.db import get_memory_branch_lineage, record_memory_tombstone
    if len(get_memory_branch_lineage(proj, br)) > 1:
        if file_path and str(file_path).strip():
            record_memory_tombstone(proj, br, "file", str(file_path))
        if doc_key and str(doc_key).strip():
            record_memory_tombstone(proj, br, "doc", str(doc_key))


async def delete_branch_target_memories(project_id: str, branch: str, target_type: str, target_key: str) -> int:
    from ..core.db import _normalize_overlay_key
    uid = f"{project_id}:{branch}"
    norm_key = _normalize_overlay_key(target_type, target_key)
    if not norm_key:
        return 0

    eff_root = _effective_root_branch()
    with tenant_context(project_id, branch):
        if branch in ("main", eff_root):
            rows = db.fetchall(
                "SELECT id, meta FROM memories WHERE user_id IN (?, ?)",
                (uid, project_id),
            )
        else:
            rows = db.fetchall("SELECT id, meta FROM memories WHERE user_id=?", (uid,))
        deleted = 0
        for row in rows:
            meta = _parse_meta_dict(row.get("meta"))
            if not isinstance(meta, dict):
                continue

            matched = False
            if target_type == "file":
                for field in ("file_path", "filename"):
                    val = meta.get(field)
                    if val and _normalize_overlay_key("file", str(val)) == norm_key:
                        matched = True
                        break
            elif target_type == "doc":
                for field in ("doc_key", "filename"):
                    val = meta.get(field)
                    if val and _normalize_overlay_key("doc", str(val)) == norm_key:
                        matched = True
                        break
            else:
                for field in (target_type, "filename"):
                    val = meta.get(field)
                    if val and _normalize_overlay_key(target_type, str(val)) == norm_key:
                        matched = True
                        break

            if matched:
                mid = row["id"]
                await store.deleteVectors(mid)
                q.del_mem(mid)
                deleted += 1

    invalidate_source_query_cache(project_id)
    return deleted


async def add_hsg_memory(
    content: str,
    tags: Optional[str] = None,
    metadata: Any = None,
    user_id: Optional[str] = None,
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    uid = user_id or "anonymous"
    eff_root = _effective_root_branch()
    active_tenant = get_current_tenant()
    active_source = get_current_source()
    parsed_proj, parsed_src, parsed_br = parse_tenant_spec(uid, default_branch=eff_root)

    if (
        active_tenant is not None
        and ":" not in uid
        and "@" not in uid
        and uid in (active_tenant[0], "anonymous", "default")
    ):
        proj, br = active_tenant
    else:
        proj, br = parsed_proj, parsed_br

    meta_dict = metadata if isinstance(metadata, dict) else {}
    if source_id is not None and str(source_id).strip():
        raw_src = str(source_id).strip()
    elif parsed_src is not None:
        raw_src = parsed_src
    elif meta_dict.get("source_id") and str(meta_dict.get("source_id")).strip():
        raw_src = str(meta_dict["source_id"]).strip()
    elif meta_dict.get("scope") == "project_shared":
        raw_src = "__shared__"
    else:
        raw_src = active_source

    ctx_src = _ctx_source_arg(raw_src)
    mem_src_col = (str(raw_src).strip() or "__default__") if raw_src else "__default__"
    mem_scope_col = (
        "project_shared"
        if (mem_src_col == "__shared__" or meta_dict.get("scope") == "project_shared")
        else "source"
    )
    mem_doc_role = str(meta_dict.get("doc_role") or "general").strip() or "general"

    with tenant_context(proj, br, source_id=ctx_src):
        simhash = compute_simhash(content)
        existing = db.fetchone("SELECT * FROM memories WHERE simhash=? AND user_id=? ORDER BY salience DESC LIMIT 1", (simhash, uid))

        if existing and hamming_dist(simhash, existing["simhash"]) <= 3:
            now = int(time.time()*1000)
            boost = min(1.0, (existing["salience"] or 0) + 0.15)
            db.execute("UPDATE memories SET last_seen_at=?, salience=?, updated_at=? WHERE id=?", (now, boost, now, existing["id"]))
            db.commit()
            tenant_manager.remember_memory_id(existing["id"], proj, br, source_id=ctx_src)
            _maybe_record_overlay_tombstone(user_id, metadata)
            invalidate_source_query_cache(proj, ctx_src)
            return {
                "id": existing["id"],
                "primary_sector": existing["primary_sector"],
                "sectors": [existing["primary_sector"]],
                "deduplicated": True,
                "source_id": existing.get("source_id") or mem_src_col,
                "scope": existing.get("scope") or mem_scope_col,
                "branch": br,
            }

        mid = str(uuid.uuid4())
        now = int(time.time()*1000)
        if user_id:
            u = db.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
            if not u:
                db.execute("INSERT OR IGNORE INTO users(user_id,summary,reflection_count,created_at,updated_at) VALUES (?,?,?,?,?)",
                           (user_id, "User profile initializing...", 0, now, now))
                db.commit()

        chunks = chunk_text(content)
        use_chunks = len(chunks) > 1
        cls = classify_content(content, metadata)
        all_secs = [cls["primary"]] + cls["additional"]
        try:
            max_seg_res = db.fetchone("SELECT coalesce(max(segment), 0) as max_seg FROM memories")
            cur_seg = max_seg_res["max_seg"]
            cnt_res = db.fetchone("SELECT count(*) as c FROM memories WHERE segment=?", (cur_seg,))
            if cnt_res["c"] >= env.seg_size:
                cur_seg += 1
                print(f"[HSG] Rotated to segment {cur_seg}")

            stored = extract_essence(content, cls["primary"], env.summary_max_length)
            sec_cfg = SECTOR_CONFIGS[cls["primary"]]
            init_sal = max(0.0, min(1.0, 0.4 + 0.1 * len(cls["additional"])))
            q.ins_mem(
                id=mid,
                user_id=user_id or "anonymous",
                segment=cur_seg,
                content=stored,
                simhash=simhash,
                primary_sector=cls["primary"],
                tags=tags,
                meta=json.dumps(metadata or {}),
                created_at=now,
                updated_at=now,
                last_seen_at=now,
                salience=init_sal,
                decay_lambda=sec_cfg["decay_lambda"],
                version=1,
                mean_dim=None,
                mean_vec=None,
                compressed_vec=None,
                feedback_score=0
            )
            if not is_multi_db_enabled() and db.conn is not None:
                tenant_manager._ensure_source_columns_on_conn(
                    db.conn, db.is_postgres, tables=("memories",)
                )
            try:
                db.execute(
                    "UPDATE memories SET source_id=?, scope=?, doc_role=? WHERE id=?",
                    (mem_src_col, mem_scope_col, mem_doc_role, mid),
                )
                db.commit()
            except Exception:
                pass
            tenant_manager.remember_memory_id(mid, proj, br, source_id=ctx_src)

            emb_res = await embed_multi_sector(mid, content, all_secs, chunks if use_chunks else None)
            for r in emb_res:
                 await store.storeVector(mid, r["sector"], r["vector"], r["dim"], user_id or "anonymous")

            mean_vec = calc_mean_vec(emb_res, all_secs)
            mean_buf = vec_to_buf(mean_vec)
            db.execute("UPDATE memories SET mean_dim=?, mean_vec=? WHERE id=?", (len(mean_vec), mean_buf, mid))

            if len(mean_vec) > 128:
                comp = compress_vec_for_storage(mean_vec, 128)
                db.execute("UPDATE memories SET compressed_vec=? WHERE id=?", (vec_to_buf(comp), mid))

            await create_single_waypoint(mid, mean_vec, now, user_id)
            if user_id:
                await update_user_summary(user_id)
            _maybe_record_overlay_tombstone(user_id, metadata)
            invalidate_source_query_cache(proj, ctx_src)
            return {
                "id": mid,
                "content": content,
                "primary_sector": cls["primary"],
                "sectors": all_secs,
                "chunks": len(chunks),
                "salience": init_sal,
                "source_id": mem_src_col,
                "scope": mem_scope_col,
                "branch": br,
            }
        except Exception as e:
            raise e

MAX_CACHE_SIZE = 512
cache: OrderedDict = OrderedDict()
TTL = 60000
_SOURCE_CACHE_EPOCH = 0

_ACTIVE_RELATION_STATUSES = frozenset({"active", "pinned", "drifted"})
_CONTRACT_STOP_TOKENS = frozenset({
    "api", "v1", "v2", "v3", "get", "post", "put", "delete", "patch", "head",
    "options", "schema", "event", "spec", "infra", "deploy", "http", "https",
})


def invalidate_query_cache() -> None:
    cache.clear()


def invalidate_source_query_cache(
    project_id: Optional[str] = None,
    source_id: Optional[str] = None,
) -> int:
    """Bump monotonic source cache epoch and clear cached HSG results."""
    global _SOURCE_CACHE_EPOCH
    _SOURCE_CACHE_EPOCH += 1
    invalidate_query_cache()
    return _SOURCE_CACHE_EPOCH


def _contract_key_overlaps_query(
    contract_key: str, query_text: str, q_tokens: Optional[Set[str]] = None
) -> bool:
    """Return True if meaningful query tokens overlap the relation's contract_key."""
    ck = (contract_key or "").strip()
    if not ck:
        return False
    q_low = (query_text or "").strip().lower()
    if not q_low:
        return False
    ck_low = ck.lower()
    if ck_low in q_low:
        return True

    ck_raw = {t.lower() for t in re.findall(r"[A-Za-z0-9]{2,}", ck)}
    ck_can = canonical_token_set(ck)
    ck_terms = (ck_raw | ck_can) - _CONTRACT_STOP_TOKENS
    if not ck_terms:
        ck_terms = ck_raw | ck_can

    q_raw = {t.lower() for t in re.findall(r"[A-Za-z0-9]{2,}", q_low)}
    q_eff_tokens = (q_tokens if q_tokens is not None else canonical_token_set(q_low)) | q_raw
    q_terms = q_eff_tokens - _CONTRACT_STOP_TOKENS
    return bool(ck_terms & q_terms)


def _load_shared_binding_map(project_id: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Load `master_shared_doc_bindings` for `project_id` indexed by `{doc_key: {target_source_id: row}}`."""
    binding_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
    try:
        rows = tenant_manager.list_shared_doc_bindings(project_id)
        for r in rows:
            dkey = str(r.get("doc_key") or "").strip()
            tsrc = str(r.get("target_source_id") or "*").strip() or "*"
            if dkey:
                binding_map.setdefault(dkey, {})[tsrc] = r
    except Exception:
        pass
    return binding_map


def _is_shared_doc_allowed_for_source(
    doc_key: Optional[str],
    doc_version: Optional[str],
    target_source_id: Optional[str],
    binding_map: Dict[str, Dict[str, Dict[str, Any]]],
) -> bool:
    """Check `master_shared_doc_bindings` (`status='active'`, `target_source_id IN ('*', target_source_id)`)."""
    dkey = str(doc_key or "").strip()
    if not dkey:
        return True
    key_entries = binding_map.get(dkey)
    if not key_entries:
        return True

    eff_target = (target_source_id or "").strip()
    spec = key_entries.get(eff_target) if eff_target else None
    wild = key_entries.get("*")
    chosen = spec if spec is not None else wild
    if chosen is None:
        return False
    if str(chosen.get("status") or "").strip().lower() != "active":
        return False
    pinned = chosen.get("pinned_version")
    if pinned and doc_version and str(doc_version).strip() != str(pinned).strip():
        return False
    return True


def collect_cross_source_relations_bfs(
    project_id: Optional[str],
    source_id: Optional[str] = None,
    branch: Optional[str] = None,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
    expand_relations: bool = True,
    include_shared_docs: bool = True,
    include_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run bounded BFS (up to 3 hops) over `cross_source_relations` for summary and expansion."""
    if not expand_relations or not project_id:
        return {"relations": [], "sibling_edges": {}}

    try:
        eff_depth = min(3, max(0, int(relation_depth)))
    except (TypeError, ValueError):
        eff_depth = 1
    if eff_depth <= 0:
        return {"relations": [], "sibling_edges": {}}

    eff_root = _resolve_project_root_branch(project_id)
    eff_branch = (branch or eff_root).strip() or eff_root
    rel_type_set: Optional[Set[str]] = (
        {str(rt).strip() for rt in relation_types if str(rt).strip()}
        if relation_types is not None
        else None
    )
    inc_src_set: Optional[Set[str]] = (
        {str(s).strip() for s in include_sources if str(s).strip()}
        if include_sources is not None
        else None
    )

    try:
        raw_rels = tenant_manager.list_cross_source_relations(project_id)
    except Exception:
        return {"relations": [], "sibling_edges": {}}

    active_rels = [
        r
        for r in raw_rels
        if str(r.get("status") or "").strip().lower() in _ACTIVE_RELATION_STATUSES
        and (
            rel_type_set is None
            or str(r.get("relation_type") or "").strip() in rel_type_set
        )
    ]
    if not active_rels:
        return {"relations": [], "sibling_edges": {}}

    clean_src = str(source_id).strip() if source_id is not None and str(source_id).strip() else None

    def _format_rel_summary(rel: Dict[str, Any], from_src: str, to_src: str, hop: int) -> Dict[str, Any]:
        return {
            "id": rel.get("id"),
            "project_id": rel.get("project_id") or project_id,
            "src_source_id": rel.get("src_source_id"),
            "src_branch": rel.get("src_branch") or eff_root,
            "dst_source_id": rel.get("dst_source_id"),
            "dst_branch": rel.get("dst_branch") or eff_root,
            "relation_type": rel.get("relation_type"),
            "contract_key": rel.get("contract_key") or "",
            "status": rel.get("status") or "active",
            "confidence": rel.get("confidence"),
            "confidence_score": float(
                rel.get("confidence_score") if rel.get("confidence_score") is not None else 1.0
            ),
            "src_node_id": rel.get("src_node_id") or "",
            "dst_node_id": rel.get("dst_node_id") or "",
            "src_file_path": rel.get("src_file_path"),
            "dst_doc_key": rel.get("dst_doc_key"),
            "contract_metadata": rel.get("contract_metadata") or {},
            "is_bidirectional": bool(rel.get("is_bidirectional")),
            "from_source": from_src,
            "to_source": to_src,
            "hop": hop,
        }

    traversed_relations: List[Dict[str, Any]] = []
    seen_rel_keys: Set[Tuple[str, str, str, str]] = set()
    sibling_edges: Dict[Tuple[str, str], List[Tuple[int, str, Dict[str, Any]]]] = {}

    if clean_src and clean_src not in ("__default__", "__shared__"):
        source_min_hop: Dict[str, int] = {clean_src: 0}
        queue: deque[Tuple[str, str, int]] = deque([(clean_src, eff_branch, 0)])

        while queue:
            curr_src, _curr_br, curr_hop = queue.popleft()
            if curr_hop >= eff_depth:
                continue
            next_hop = curr_hop + 1

            for rel in active_rels:
                s_src = str(rel.get("src_source_id") or "").strip()
                d_src = str(rel.get("dst_source_id") or "").strip()
                if s_src == curr_src:
                    nbr_src = d_src
                    nbr_br = str(rel.get("dst_branch") or eff_root).strip() or eff_root
                elif d_src == curr_src:
                    nbr_src = s_src
                    nbr_br = str(rel.get("src_branch") or eff_root).strip() or eff_root
                else:
                    continue

                if not nbr_src or nbr_src == curr_src:
                    continue
                if nbr_src == "__shared__" and not include_shared_docs:
                    continue
                prev_hop = source_min_hop.get(nbr_src)
                if prev_hop is not None and prev_hop < next_hop:
                    continue

                if prev_hop is None:
                    source_min_hop[nbr_src] = next_hop
                    if nbr_src != "__shared__":
                        queue.append((nbr_src, nbr_br, next_hop))

                summary = _format_rel_summary(rel, curr_src, nbr_src, next_hop)
                r_key = (
                    str(summary.get("id") or summary.get("relation_type") or ""),
                    curr_src,
                    nbr_src,
                    str(summary.get("contract_key") or ""),
                )
                if r_key not in seen_rel_keys:
                    seen_rel_keys.add(r_key)
                    if inc_src_set is None or nbr_src in inc_src_set or nbr_src == "__shared__":
                        traversed_relations.append(summary)

                if nbr_src not in ("__shared__", "__default__"):
                    if inc_src_set is None or nbr_src in inc_src_set:
                        sibling_edges.setdefault((nbr_src, nbr_br), []).append(
                            (next_hop, curr_src, summary)
                        )
    elif clean_src is None:
        for rel in active_rels:
            s_src = str(rel.get("src_source_id") or "").strip()
            d_src = str(rel.get("dst_source_id") or "").strip()
            if not include_shared_docs and (s_src == "__shared__" or d_src == "__shared__"):
                continue
            if inc_src_set is not None and s_src not in inc_src_set and d_src not in inc_src_set:
                continue
            traversed_relations.append(_format_rel_summary(rel, s_src, d_src, 1))

    return {
        "relations": traversed_relations,
        "sibling_edges": sibling_edges,
    }


def resolve_cross_source_relations_summary(
    user_id_or_project: Optional[str],
    source_id: Optional[str] = None,
    branch: Optional[str] = None,
    relation_depth: int = 1,
    relation_types: Optional[List[str]] = None,
    expand_relations: bool = True,
    include_shared_docs: bool = True,
    include_sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Resolve `cross_source_relations` summary list for REST API responses."""
    raw = (user_id_or_project or "").strip()
    if not raw or raw in ("%", "*", "all") or "%" in raw:
        return []
    eff_root = _effective_root_branch()
    proj, parsed_src, parsed_br = parse_tenant_spec(raw, default_branch=branch or eff_root)
    eff_src = str(source_id).strip() if source_id is not None and str(source_id).strip() else parsed_src
    eff_br = branch if (branch and branch != "main") or ":" not in raw else parsed_br
    bfs_res = collect_cross_source_relations_bfs(
        project_id=proj,
        source_id=eff_src,
        branch=eff_br,
        relation_depth=relation_depth,
        relation_types=relation_types,
        expand_relations=expand_relations,
        include_shared_docs=include_shared_docs,
        include_sources=include_sources,
    )
    return bfs_res["relations"]

async def expand_via_waypoints(ids: List[str], max_exp: int = 10):
    exp = []
    vis = set(ids)
    q_arr = [{"id": i, "weight": 1.0, "path": [i]} for i in ids]
    cnt = 0

    while q_arr and cnt < max_exp:
        cur = q_arr.pop(0)
        neighs = db.fetchall("SELECT dst_id, weight FROM waypoints WHERE src_id=? ORDER BY weight DESC", (cur["id"],))
        for n in neighs:
            dst = n["dst_id"]
            if dst in vis: continue
            wt = min(1.0, max(0.0, float(n["weight"])))
            exp_wt = cur["weight"] * wt * 0.8
            if exp_wt < 0.1: continue

            item = {"id": dst, "weight": exp_wt, "path": cur["path"] + [dst]}
            exp.append(item)
            vis.add(dst)
            q_arr.append(item)
            cnt += 1
    return exp

def _memory_matches_source_target(
    mem: Dict[str, Any],
    query_proj: str,
    target_src: str,
    target_branch: str,
    eff_root: str,
) -> bool:
    """Verify a memory row belongs to `(query_proj, target_src, target_branch)`."""
    m_uid = mem.get("user_id") or ""
    m_meta = _parse_meta_dict(mem.get("meta"))
    m_proj, m_src_uid, m_br_uid = parse_tenant_spec(m_uid, default_branch=eff_root)

    if m_proj != query_proj:
        if str(m_meta.get("project_id") or "").strip() != query_proj:
            return False

    col_src = str(mem.get("source_id") or "").strip()
    meta_src = str(m_meta.get("source_id") or "").strip()
    if m_src_uid:
        actual_src = m_src_uid
    elif col_src and col_src != "__default__":
        actual_src = col_src
    elif meta_src and meta_src != "__default__":
        actual_src = meta_src
    else:
        actual_src = "__default__"

    col_scope = str(mem.get("scope") or "").strip()
    meta_scope = str(m_meta.get("scope") or "").strip()
    actual_scope = (
        col_scope
        if (col_scope and col_scope != "source")
        else (meta_scope or col_scope or "source")
    )

    if target_src == "__shared__":
        if actual_src != "__shared__" and actual_scope != "project_shared":
            return False
    else:
        if actual_scope == "project_shared" or actual_src == "__shared__":
            return False
        if actual_src != target_src:
            return False

    actual_br = str(m_meta.get("branch") or "").strip() or m_br_uid
    if target_branch and actual_br != target_branch:
        return False
    return True


async def _score_memory_candidate(
    mid: str,
    m: Dict[str, Any],
    qe: Dict[str, List[float]],
    qc: Dict[str, Any],
    qtk: Set[str],
    w: Dict[str, float],
    sr: Dict[str, List[Dict[str, Any]]],
    exp: List[Dict[str, Any]],
    kw_scores: Dict[str, float],
    source_id_val: str,
    branch_val: str,
    scope_val: str,
    via_relation: Optional[Dict[str, Any]],
    weight_multiplier: float = 1.0,
    additive_boost: float = 0.0,
    debug: bool = False,
) -> Dict[str, Any]:
    """Compute hybrid score and build hit dict with multi-source provenance."""
    mvf = await calc_multi_vec_fusion_score(mid, qe, w)
    tag_match = await compute_tag_match_score(mid, qtk)
    csr = await calculateCrossSectorResonanceScore(m["primary_sector"], qc["primary"], mvf)

    best_sim = csr
    for _s, rlist in sr.items():
        for r in rlist:
            if r["id"] == mid and r["similarity"] > best_sim:
                best_sim = r["similarity"]
    mem_sec = m["primary_sector"]
    q_sec = qc["primary"]
    penalty = 1.0
    if mem_sec != q_sec:
        penalty = SECTOR_RELATIONSHIPS.get(q_sec, {}).get(mem_sec, 0.3)

    adj = best_sim * penalty

    em = next((e for e in exp if e["id"] == mid), None)
    ww = min(1.0, max(0.0, em["weight"] if em else 0.0))

    days = (time.time() * 1000 - m["last_seen_at"]) / 86400000.0
    sal = calc_decay(m["primary_sector"], m["salience"], days)
    mtk = canonical_token_set(m["content"])
    tok_ov = compute_token_overlap(qtk, mtk)
    rec_sc = calc_recency_score_decay(m["last_seen_at"])

    base_fs = compute_hybrid_score(adj, tok_ov, ww, rec_sc, kw_scores.get(mid, 0), tag_match)
    final_score = base_fs * weight_multiplier + additive_boost

    item: Dict[str, Any] = {
        "id": mid,
        "user_id": m["user_id"],
        "content": m["content"],
        "score": final_score,
        "primary_sector": m["primary_sector"],
        "path": em["path"] if em else [mid],
        "salience": sal,
        "last_seen_at": m["last_seen_at"],
        "tags": _parse_tags_list(m.get("tags")),
        "metadata": _parse_meta_dict(m.get("meta")),
        "source_id": source_id_val,
        "branch": branch_val,
        "scope": scope_val,
        "via_relation": dict(via_relation) if via_relation is not None else None,
    }
    if debug:
        item["_debug"] = {
            "base_score": base_fs,
            "weight_multiplier": weight_multiplier,
            "additive_boost": additive_boost,
            "sim_adj": adj,
            "tok_ov": tok_ov,
            "recency": rec_sc,
            "waypoint": ww,
            "tag": tag_match,
            "penalty": penalty,
        }
    return item


async def _query_tenant_memory_bucket(
    qt: str,
    k: int,
    ss: List[str],
    qe: Dict[str, List[float]],
    qc: Dict[str, Any],
    qtk: Set[str],
    w: Dict[str, float],
    f_dict: Dict[str, Any],
    query_proj: str,
    target_src: str,
    target_branch: str,
    bucket_scope: str,
    weight_multiplier: float,
    additive_boost: float,
    via_relation: Optional[Dict[str, Any]],
    eff_root: str,
    shared_bindings: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
    target_source_for_bindings: Optional[str] = None,
) -> List[Tuple[Dict[str, Any], Tuple[str, str, Optional[str]]]]:
    """Query a single `(project_id, target_src, target_branch)` memory bucket safely."""
    ctx_src = _ctx_source_arg(target_src)
    if is_multi_db_enabled() and not tenant_manager.has_branch(
        query_proj, target_branch, source_id=ctx_src
    ):
        return []

    if is_multi_db_enabled():
        vec_filter: Dict[str, Any] = {}
        vec_k = max(k * 5, 30) if target_src == "__shared__" else k * 3
    else:
        vec_filter = {"user_id": f"{query_proj}%"}
        vec_k = max(k * 6, 40)

    debug_flag = bool(f_dict.get("debug"))
    min_sal = f_dict.get("minSalience")
    items_out: List[Tuple[Dict[str, Any], Tuple[str, str, Optional[str]]]] = []

    with tenant_context(query_proj, target_branch, source_id=ctx_src):
        sr_bucket: Dict[str, List[Dict[str, Any]]] = {}
        all_sims: List[float] = []
        ids: Set[str] = set()
        for s in ss:
            res = await store.search(qe[s], s, vec_k, vec_filter)
            sr_bucket[s] = res
            for r in res:
                all_sims.append(r["similarity"])
                ids.add(r["id"])

        if not ids:
            return []

        avg_top = sum(all_sims) / len(all_sims) if all_sims else 0.0
        high_conf = avg_top >= 0.55

        exp: List[Dict[str, Any]] = []
        if not high_conf and ids:
            b_exp = await expand_via_waypoints(list(ids), k * 2)
            for e in b_exp:
                exp.append(e)
                ids.add(e["id"])

        kw_scores: Dict[str, float] = {}
        mem_map: Dict[str, Any] = {}
        for mid in ids:
            mem = q.get_mem(mid)
            if not mem:
                continue
            if min_sal and mem["salience"] < min_sal:
                continue
            if not _memory_matches_source_target(
                mem, query_proj, target_src, target_branch, eff_root
            ):
                continue
            if target_src == "__shared__" and shared_bindings is not None:
                m_meta = _parse_meta_dict(mem.get("meta"))
                if not _is_shared_doc_allowed_for_source(
                    m_meta.get("doc_key"),
                    m_meta.get("doc_version"),
                    target_source_for_bindings,
                    shared_bindings,
                ):
                    continue
            mem_map[mid] = mem
            kw_scores[mid] = compute_keyword_overlap(qt, mem["content"]) * 0.15

        for mid, m in mem_map.items():
            item = await _score_memory_candidate(
                mid=mid,
                m=m,
                qe=qe,
                qc=qc,
                qtk=qtk,
                w=w,
                sr=sr_bucket,
                exp=exp,
                kw_scores=kw_scores,
                source_id_val=target_src,
                branch_val=target_branch,
                scope_val=bucket_scope,
                via_relation=via_relation,
                weight_multiplier=weight_multiplier,
                additive_boost=additive_boost,
                debug=debug_flag,
            )
            items_out.append((item, (query_proj, target_branch, ctx_src)))

    return items_out


async def hsg_query(qt: str, k: int = 10, f: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    from contextlib import nullcontext

    start_q = time.time()
    inc_q()
    try:
        f_dict = f or {}
        inherit_base = bool(f_dict.get("inherit_base", True))
        raw_uid = f_dict.get("user_id")
        raw_explicit_src = f_dict.get("source_id")
        explicit_src = (
            str(raw_explicit_src).strip()
            if raw_explicit_src is not None and str(raw_explicit_src).strip()
            else None
        )
        raw_explicit_branch = f_dict.get("branch")
        explicit_branch = (
            str(raw_explicit_branch).strip()
            if raw_explicit_branch is not None and str(raw_explicit_branch).strip()
            else None
        )
        include_shared_docs = bool(f_dict.get("include_shared_docs", True))
        raw_rel_depth = f_dict.get("relation_depth")
        relation_depth = (
            1
            if ("relation_depth" not in f_dict or raw_rel_depth is None)
            else max(0, int(raw_rel_depth))
        )
        raw_rel_types = f_dict.get("relation_types")
        relation_types: Optional[List[str]] = (
            [str(x).strip() for x in raw_rel_types if str(x).strip()]
            if raw_rel_types is not None
            else None
        )
        raw_inc_sources = f_dict.get("include_sources")
        include_sources: Optional[List[str]] = (
            [str(x).strip() for x in raw_inc_sources if str(x).strip()]
            if raw_inc_sources is not None
            else None
        )

        eff_root = _effective_root_branch()
        active_tenant = get_current_tenant()
        active_source = get_current_source()

        lineage_branches: List[str] = []
        lineage_uids: List[str] = []
        uid_depth: Dict[str, int] = {}
        uid_to_branch: Dict[str, str] = {}
        branch_tombstones: Dict[str, Dict[str, Set[str]]] = {}
        query_proj: Optional[str] = None
        single_branch: Optional[str] = None
        wildcard_branches: List[str] = []
        eff_src: Optional[str] = explicit_src

        if raw_uid and isinstance(raw_uid, str) and "%" in raw_uid:
            if ":" in raw_uid:
                p_part, b_part = raw_uid.split(":", 1)
                p_clean, wc_src, _ = parse_tenant_spec(p_part, default_branch=eff_root)
                if eff_src is None:
                    eff_src = wc_src if wc_src is not None else active_source
                if "%" not in p_clean:
                    query_proj = p_clean
                    reg_branches = tenant_manager.list_project_branches(
                        p_clean, source_id=_ctx_source_arg(eff_src)
                    )
                    if reg_branches:
                        if b_part in ("%", ""):
                            wildcard_branches = reg_branches
                        else:
                            b_rx = re.compile(
                                "^"
                                + "".join(
                                    ".*" if c == "%" else "." if c == "_" else re.escape(c)
                                    for c in b_part
                                )
                                + "$"
                            )
                            matched_br = [
                                b
                                for b in reg_branches
                                if b_rx.match(b) or b_rx.match(f"{p_clean}:{b}")
                            ]
                            wildcard_branches = matched_br if matched_br else reg_branches
            else:
                if eff_src is None:
                    eff_src = active_source
        elif raw_uid and isinstance(raw_uid, str):
            if (
                active_tenant is not None
                and ":" not in raw_uid
                and "@" not in raw_uid
                and raw_uid in (active_tenant[0], "anonymous", "default")
            ):
                query_proj, single_branch = active_tenant
                parsed_src = None
            else:
                query_proj, parsed_src, single_branch = parse_tenant_spec(
                    raw_uid, default_branch=explicit_branch or eff_root
                )
                if ":" not in raw_uid and explicit_branch:
                    single_branch = explicit_branch
            if eff_src is None:
                eff_src = parsed_src if parsed_src is not None else active_source

            if inherit_base and query_proj and single_branch:
                if eff_src is None and ":" in raw_uid:
                    from ..core.db import get_memory_branch_lineage, get_memory_tombstones

                    lineage_branches = get_memory_branch_lineage(query_proj, single_branch)
                    if len(lineage_branches) > 1:
                        for idx, b_name in enumerate(lineage_branches):
                            cand_uid = f"{query_proj}:{b_name}"
                            if cand_uid not in uid_depth:
                                uid_depth[cand_uid] = idx
                                uid_to_branch[cand_uid] = b_name
                                lineage_uids.append(cand_uid)
                        oldest_idx = len(lineage_branches) - 1
                        root_br = getattr(env, "root_branch", None) or "main"
                        if lineage_branches[-1] == root_br or lineage_branches[-1] == "main":
                            if query_proj not in uid_depth:
                                uid_depth[query_proj] = oldest_idx
                                uid_to_branch[query_proj] = lineage_branches[-1]
                                lineage_uids.append(query_proj)
                        branch_tombstones = {
                            b: get_memory_tombstones(query_proj, b)
                            for b in lineage_branches
                        }
                elif eff_src not in (None, "__default__", "__shared__"):
                    from ..core.db import get_memory_tombstones

                    src_lineage = _get_source_branch_lineage(
                        query_proj, single_branch, eff_src
                    )
                    if len(src_lineage) > 1:
                        lineage_branches = src_lineage
                        for idx, b_name in enumerate(lineage_branches):
                            cand_uid = f"{query_proj}@{eff_src}:{b_name}"
                            if cand_uid not in uid_depth:
                                uid_depth[cand_uid] = idx
                                uid_to_branch[cand_uid] = b_name
                                lineage_uids.append(cand_uid)
                        branch_tombstones = {}
                        for b_name in lineage_branches:
                            with tenant_context(query_proj, b_name, source_id=eff_src):
                                branch_tombstones[b_name] = get_memory_tombstones(
                                    query_proj, b_name
                                )
        elif active_tenant is not None:
            query_proj, single_branch = active_tenant
            if eff_src is None:
                eff_src = active_source
        else:
            if explicit_branch:
                single_branch = explicit_branch
            if eff_src is None:
                eff_src = active_source

        tomb_sig = (
            [
                (
                    b,
                    sorted(branch_tombstones[b].get("file", set())),
                    sorted(branch_tombstones[b].get("doc", set())),
                )
                for b in lineage_branches
            ]
            if len(lineage_branches) > 1
            else []
        )
        cache_key = (
            f"{qt}:{k}:{json.dumps(f_dict, sort_keys=True, default=str)}:"
            f"{eff_src or ''}:{int(include_shared_docs)}:{relation_depth}:"
            f"{json.dumps(sorted(relation_types or []))}:"
            f"{json.dumps(sorted(include_sources or []))}:"
            f"{_SOURCE_CACHE_EPOCH}:"
            f"{json.dumps(lineage_branches)}:{json.dumps(tomb_sig)}:"
            f"{active_tenant if raw_uid is None else ''}:{active_source or ''}"
        )
        if cache_key in cache:
            entry = cache[cache_key]
            if time.time() * 1000 - entry["t"] < TTL:
                cache.move_to_end(cache_key)
                return entry["r"]
            cache.pop(cache_key, None)

        qc = classify_content(qt)
        qtk = canonical_token_set(qt)

        ss = f_dict.get("sectors") or list(SECTOR_CONFIGS.keys())
        if not ss:
            ss = ["semantic"]

        qe = await embed_query_for_all_sectors(qt, ss)

        w = {
            "semantic_dimension_weight": 1.2 if qc["primary"] == "semantic" else 0.8,
            "emotional_dimension_weight": 1.5 if qc["primary"] == "emotional" else 0.6,
            "procedural_dimension_weight": 1.3 if qc["primary"] == "procedural" else 0.7,
            "temporal_dimension_weight": 1.4 if qc["primary"] == "episodic" else 0.7,
            "reflective_dimension_weight": 1.1 if qc["primary"] == "reflective" else 0.5,
        }

        cum_tomb_files: Dict[int, Set[str]] = {}
        cum_tomb_docs: Dict[int, Set[str]] = {}
        if len(lineage_branches) > 1:
            for d in range(1, len(lineage_branches)):
                f_set: Set[str] = set()
                d_set: Set[str] = set()
                for j in range(d):
                    b_tomb = branch_tombstones.get(lineage_branches[j], {})
                    f_set.update(b_tomb.get("file", set()))
                    d_set.update(b_tomb.get("doc", set()))
                cum_tomb_files[d] = f_set
                cum_tomb_docs[d] = d_set

        candidate_items: List[Tuple[Dict[str, Any], Optional[Tuple[str, str, Optional[str]]]]] = []
        debug_flag = bool(f_dict.get("debug"))

        # -----------------------------------------------------------------
        # 1. Primary Bucket (weight 1.0)
        # -----------------------------------------------------------------
        if eff_src is not None and query_proj and not wildcard_branches and len(lineage_branches) <= 1:
            prim_branch = single_branch or eff_root
            prim_scope = "project_shared" if eff_src == "__shared__" else "source"
            prim_hits = await _query_tenant_memory_bucket(
                qt=qt,
                k=k,
                ss=ss,
                qe=qe,
                qc=qc,
                qtk=qtk,
                w=w,
                f_dict=f_dict,
                query_proj=query_proj,
                target_src=eff_src,
                target_branch=prim_branch,
                bucket_scope=prim_scope,
                weight_multiplier=1.0,
                additive_boost=0.0,
                via_relation=None,
                eff_root=eff_root,
            )
            candidate_items.extend(prim_hits)
        elif eff_src is None and query_proj and include_sources and not wildcard_branches and len(lineage_branches) <= 1:
            shared_bindings_proj = _load_shared_binding_map(query_proj) if include_shared_docs else None
            for inc_s in include_sources:
                if inc_s == "__shared__":
                    if not include_shared_docs:
                        continue
                    sh_br = _resolve_project_root_branch(query_proj)
                    sh_hits = await _query_tenant_memory_bucket(
                        qt=qt,
                        k=k,
                        ss=ss,
                        qe=qe,
                        qc=qc,
                        qtk=qtk,
                        w=w,
                        f_dict=f_dict,
                        query_proj=query_proj,
                        target_src="__shared__",
                        target_branch=sh_br,
                        bucket_scope="project_shared",
                        weight_multiplier=0.92,
                        additive_boost=0.0,
                        via_relation=None,
                        eff_root=eff_root,
                        shared_bindings=shared_bindings_proj,
                        target_source_for_bindings=None,
                    )
                    candidate_items.extend(sh_hits)
                else:
                    inc_hits = await _query_tenant_memory_bucket(
                        qt=qt,
                        k=k,
                        ss=ss,
                        qe=qe,
                        qc=qc,
                        qtk=qtk,
                        w=w,
                        f_dict=f_dict,
                        query_proj=query_proj,
                        target_src=inc_s,
                        target_branch=single_branch or eff_root,
                        bucket_scope="source",
                        weight_multiplier=1.0,
                        additive_boost=0.0,
                        via_relation=None,
                        eff_root=eff_root,
                    )
                    candidate_items.extend(inc_hits)
        else:
            # Legacy single-source / multi-level lineage / wildcard path
            eff_ctx_src = _ctx_source_arg(eff_src)
            sr = {}
            hit_branch: Dict[str, str] = {}
            hit_proj: Dict[str, str] = {}
            hit_ctx_src: Dict[str, Optional[str]] = {}
            hit_best_sim: Dict[str, float] = {}

            for s in ss:
                qv = qe[s]
                if len(lineage_branches) > 1 and query_proj:
                    merged_by_id: Dict[str, Dict[str, Any]] = {}
                    if eff_ctx_src is not None:
                        for cand_b in lineage_branches:
                            if is_multi_db_enabled() and not tenant_manager.has_branch(
                                query_proj, cand_b, source_id=eff_ctx_src
                            ):
                                continue
                            with tenant_context(query_proj, cand_b, source_id=eff_ctx_src):
                                vf = {} if is_multi_db_enabled() else {"user_id": f"{query_proj}%"}
                                cand_res = await store.search(qv, s, k * 3, vf)
                            for r in cand_res:
                                rid = r["id"]
                                sim_val = float(r.get("similarity") or 0.0)
                                if rid not in merged_by_id or sim_val > merged_by_id[rid]["similarity"]:
                                    merged_by_id[rid] = r
                                if rid not in hit_best_sim or sim_val >= hit_best_sim[rid]:
                                    hit_best_sim[rid] = sim_val
                                    hit_proj[rid] = query_proj
                                    hit_branch[rid] = cand_b
                                    hit_ctx_src[rid] = eff_ctx_src
                    else:
                        for cand_uid in lineage_uids:
                            cand_b = uid_to_branch.get(cand_uid, single_branch or eff_root)
                            with tenant_context(query_proj, cand_b):
                                cand_res = await store.search(qv, s, k * 3, {"user_id": cand_uid})
                            for r in cand_res:
                                rid = r["id"]
                                sim_val = float(r.get("similarity") or 0.0)
                                if rid not in merged_by_id or sim_val > merged_by_id[rid]["similarity"]:
                                    merged_by_id[rid] = r
                                if rid not in hit_best_sim or sim_val >= hit_best_sim[rid]:
                                    hit_best_sim[rid] = sim_val
                                    hit_proj[rid] = query_proj
                                    hit_branch[rid] = cand_b
                                    hit_ctx_src[rid] = None
                    sr[s] = list(merged_by_id.values())
                elif wildcard_branches and query_proj:
                    merged_wc: Dict[str, Dict[str, Any]] = {}
                    for cand_b in wildcard_branches:
                        with tenant_context(query_proj, cand_b, source_id=eff_ctx_src):
                            vf = (
                                {}
                                if (eff_ctx_src is not None and is_multi_db_enabled())
                                else {"user_id": raw_uid}
                            )
                            cand_res = await store.search(qv, s, k * 3, vf)
                        for r in cand_res:
                            rid = r["id"]
                            sim_val = float(r.get("similarity") or 0.0)
                            if rid not in merged_wc or sim_val > merged_wc[rid]["similarity"]:
                                merged_wc[rid] = r
                            if rid not in hit_best_sim or sim_val >= hit_best_sim[rid]:
                                hit_best_sim[rid] = sim_val
                                hit_proj[rid] = query_proj
                                hit_branch[rid] = cand_b
                                hit_ctx_src[rid] = eff_ctx_src
                    sr[s] = list(merged_wc.values())
                elif query_proj and single_branch:
                    with tenant_context(query_proj, single_branch, source_id=eff_ctx_src):
                        res = await store.search(qv, s, k * 3, {"user_id": raw_uid})
                    for r in res:
                        rid = r["id"]
                        hit_proj.setdefault(rid, query_proj)
                        hit_branch.setdefault(rid, single_branch)
                        hit_ctx_src.setdefault(rid, eff_ctx_src)
                    sr[s] = res
                else:
                    with tenant_context(None):
                        res = await store.search(qv, s, k * 3, {"user_id": raw_uid})
                    sr[s] = res

            all_sims = []
            ids: Set[str] = set()
            for _s, res in sr.items():
                for r in res:
                    all_sims.append(r["similarity"])
                    ids.add(r["id"])

            avg_top = sum(all_sims) / len(all_sims) if all_sims else 0
            high_conf = avg_top >= 0.55

            exp: List[Dict[str, Any]] = []
            if not high_conf and ids:
                grouped_ids: Dict[Optional[Tuple[str, str, Optional[str]]], List[str]] = {}
                for mid in ids:
                    if mid in hit_proj and mid in hit_branch:
                        key: Optional[Tuple[str, str, Optional[str]]] = (
                            hit_proj[mid],
                            hit_branch[mid],
                            hit_ctx_src.get(mid, eff_ctx_src),
                        )
                    elif query_proj and single_branch:
                        key = (query_proj, single_branch, eff_ctx_src)
                    else:
                        hint = tenant_manager.lookup_memory_hint(mid)
                        key = (
                            (hint[0], hint[1], tenant_manager.lookup_memory_source_hint(mid))
                            if hint
                            else None
                        )
                    grouped_ids.setdefault(key, []).append(mid)

                for t_triple, b_ids in grouped_ids.items():
                    ctx = (
                        tenant_context(t_triple[0], t_triple[1], source_id=t_triple[2])
                        if t_triple
                        else tenant_context(None)
                    )
                    with ctx:
                        b_exp = await expand_via_waypoints(b_ids, k * 2)
                    for e in b_exp:
                        exp.append(e)
                        ids.add(e["id"])
                        if t_triple:
                            hit_proj.setdefault(e["id"], t_triple[0])
                            hit_branch.setdefault(e["id"], t_triple[1])
                            hit_ctx_src.setdefault(e["id"], t_triple[2])

            kw_scores: Dict[str, float] = {}
            user_id_regex = None
            if raw_uid and len(lineage_branches) <= 1:
                uid_pattern = raw_uid
                if "%" in uid_pattern:
                    regex_pat = "^" + "".join(
                        ".*" if c == "%" else
                        "." if c == "_" else
                        re.escape(c)
                        for c in uid_pattern
                    ) + "$"
                    user_id_regex = re.compile(regex_pat)

            mem_map: Dict[str, Any] = {}
            overridden_files_by_depth: Dict[int, Set[str]] = {}
            overridden_docs_by_depth: Dict[int, Set[str]] = {}
            if len(lineage_branches) > 1:
                from ..core.db import _normalize_overlay_key

            for mid in ids:
                m_proj = hit_proj.get(mid)
                m_br = hit_branch.get(mid)
                m_src_ctx = hit_ctx_src.get(mid, eff_ctx_src)
                if not (m_proj and m_br):
                    hint = tenant_manager.lookup_memory_hint(mid)
                    if hint:
                        m_proj, m_br = hint
                        m_src_ctx = tenant_manager.lookup_memory_source_hint(mid)
                    elif query_proj and single_branch and len(lineage_branches) <= 1 and not wildcard_branches:
                        m_proj, m_br = query_proj, single_branch

                if m_proj and m_br:
                    with tenant_context(m_proj, m_br, source_id=m_src_ctx):
                        mem = q.get_mem(mid)
                    if mem:
                        hit_proj[mid] = m_proj
                        hit_branch[mid] = m_br
                        hit_ctx_src[mid] = m_src_ctx
                else:
                    with tenant_context(None):
                        mem = q.get_mem(mid)
                    if mem:
                        hint = tenant_manager.lookup_memory_hint(mid)
                        if hint:
                            hit_proj[mid], hit_branch[mid] = hint
                            hit_ctx_src[mid] = tenant_manager.lookup_memory_source_hint(mid)
                        elif mem.get("user_id"):
                            p_inferred, s_inferred, b_inferred = parse_tenant_spec(
                                mem["user_id"], default_branch=eff_root
                            )
                            hit_proj[mid], hit_branch[mid] = p_inferred, b_inferred
                            hit_ctx_src[mid] = _ctx_source_arg(s_inferred)

                if mem:
                    mem_map[mid] = mem
                    overlap = compute_keyword_overlap(qt, mem["content"])
                    kw_scores[mid] = overlap * 0.15
                    if len(lineage_branches) > 1:
                        d_mem: Optional[int] = None
                        if eff_ctx_src is not None and hit_branch.get(mid) in lineage_branches:
                            d_mem = lineage_branches.index(hit_branch[mid])
                        elif mem.get("user_id") in uid_depth:
                            d_mem = uid_depth[mem["user_id"]]
                        if d_mem is not None:
                            m_meta = _parse_meta_dict(mem.get("meta"))
                            if m_meta.get("file_path"):
                                overridden_files_by_depth.setdefault(d_mem, set()).add(
                                    _normalize_overlay_key("file", str(m_meta["file_path"]))
                                )
                            elif m_meta.get("filename") and not m_meta.get("doc_key"):
                                overridden_files_by_depth.setdefault(d_mem, set()).add(
                                    _normalize_overlay_key("file", str(m_meta["filename"]))
                                )
                            if m_meta.get("doc_key"):
                                overridden_docs_by_depth.setdefault(d_mem, set()).add(
                                    _normalize_overlay_key("doc", str(m_meta["doc_key"]))
                                )
                            elif m_meta.get("filename") and not m_meta.get("file_path"):
                                overridden_docs_by_depth.setdefault(d_mem, set()).add(
                                    _normalize_overlay_key("doc", str(m_meta["filename"]))
                                )

            for mid in ids:
                m = mem_map.get(mid)
                if not m:
                    continue
                if f_dict.get("minSalience") and m["salience"] < f_dict["minSalience"]:
                    continue
                if raw_uid:
                    if len(lineage_branches) > 1:
                        if eff_ctx_src is not None:
                            cand_b_mem = hit_branch.get(mid, "")
                            if cand_b_mem not in lineage_branches or not _memory_matches_source_target(
                                m, query_proj or "default", eff_src or "__default__", cand_b_mem, eff_root
                            ):
                                continue
                            d_src = lineage_branches.index(cand_b_mem)
                        else:
                            if m["user_id"] not in uid_depth:
                                continue
                            d_src = uid_depth[m["user_id"]]
                        if d_src > 0:
                            m_meta = _parse_meta_dict(m.get("meta"))
                            f_key = (
                                _normalize_overlay_key("file", str(m_meta["file_path"]))
                                if m_meta.get("file_path")
                                else (
                                    _normalize_overlay_key("file", str(m_meta["filename"]))
                                    if m_meta.get("filename")
                                    else ""
                                )
                            )
                            d_key = (
                                _normalize_overlay_key("doc", str(m_meta["doc_key"]))
                                if m_meta.get("doc_key")
                                else (
                                    _normalize_overlay_key("doc", str(m_meta["filename"]))
                                    if m_meta.get("filename")
                                    else ""
                                )
                            )
                            shadowed = False
                            if f_key and f_key in cum_tomb_files.get(d_src, set()):
                                shadowed = True
                            if not shadowed and d_key and d_key in cum_tomb_docs.get(d_src, set()):
                                shadowed = True
                            if not shadowed:
                                for closer_d in range(d_src):
                                    if f_key and f_key in overridden_files_by_depth.get(closer_d, set()):
                                        shadowed = True
                                        break
                                    if d_key and d_key in overridden_docs_by_depth.get(closer_d, set()):
                                        shadowed = True
                                        break
                            if shadowed:
                                continue
                    elif user_id_regex:
                        if eff_src is not None:
                            cand_b_wc = hit_branch.get(mid, "")
                            if not (
                                user_id_regex.match(m["user_id"] or "")
                                or _memory_matches_source_target(
                                    m, query_proj or "default", eff_src, cand_b_wc, eff_root
                                )
                            ):
                                continue
                        elif not user_id_regex.match(m["user_id"] or ""):
                            continue
                    elif m["user_id"] != raw_uid:
                        continue

                score_ctx = (
                    tenant_context(
                        hit_proj[mid], hit_branch[mid], source_id=hit_ctx_src.get(mid)
                    )
                    if (mid in hit_proj and mid in hit_branch)
                    else nullcontext()
                )
                with score_ctx:
                    m_branch_val = hit_branch.get(mid) or single_branch or eff_root
                    m_src_val = (
                        eff_src
                        or str(m.get("source_id") or "").strip()
                        or "__default__"
                    )
                    m_scope_val = (
                        "project_shared"
                        if (m_src_val == "__shared__" or m.get("scope") == "project_shared")
                        else "source"
                    )
                    item = await _score_memory_candidate(
                        mid=mid,
                        m=m,
                        qe=qe,
                        qc=qc,
                        qtk=qtk,
                        w=w,
                        sr=sr,
                        exp=exp,
                        kw_scores=kw_scores,
                        source_id_val=m_src_val,
                        branch_val=m_branch_val,
                        scope_val=m_scope_val,
                        via_relation=None,
                        weight_multiplier=1.0,
                        additive_boost=0.0,
                        debug=debug_flag,
                    )
                ctx_triple = (
                    (hit_proj[mid], hit_branch[mid], hit_ctx_src.get(mid))
                    if (mid in hit_proj and mid in hit_branch)
                    else None
                )
                candidate_items.append((item, ctx_triple))

        # -----------------------------------------------------------------
        # 2. Shared Project Docs Bucket (weight 0.92)
        # -----------------------------------------------------------------
        if include_shared_docs and query_proj and eff_src not in (None, "__shared__"):
            shared_br = _resolve_project_root_branch(query_proj)
            shared_bindings = _load_shared_binding_map(query_proj)
            shared_hits = await _query_tenant_memory_bucket(
                qt=qt,
                k=k,
                ss=ss,
                qe=qe,
                qc=qc,
                qtk=qtk,
                w=w,
                f_dict=f_dict,
                query_proj=query_proj,
                target_src="__shared__",
                target_branch=shared_br,
                bucket_scope="project_shared",
                weight_multiplier=0.92,
                additive_boost=0.0,
                via_relation=None,
                eff_root=eff_root,
                shared_bindings=shared_bindings,
                target_source_for_bindings=eff_src,
            )
            candidate_items.extend(shared_hits)

        # -----------------------------------------------------------------
        # 3. Sibling Relationship Expansion Bucket
        # -----------------------------------------------------------------
        if (
            relation_depth > 0
            and query_proj
            and eff_src not in (None, "__default__", "__shared__")
        ):
            bfs_res = collect_cross_source_relations_bfs(
                project_id=query_proj,
                source_id=eff_src,
                branch=single_branch or eff_root,
                relation_depth=relation_depth,
                relation_types=relation_types,
                expand_relations=True,
                include_shared_docs=include_shared_docs,
                include_sources=include_sources,
            )
            for (nbr_source, nbr_branch), edge_candidates in bfs_res["sibling_edges"].items():
                best_rank: Optional[Tuple[int, int, float]] = None
                best_choice: Optional[Tuple[int, str, Dict[str, Any], bool, float]] = None
                for hop, from_src, rel_dict in edge_candidates:
                    ck = str(rel_dict.get("contract_key") or "")
                    ov = _contract_key_overlaps_query(ck, qt, qtk)
                    conf_val = rel_dict.get("confidence_score")
                    conf_sc = max(
                        0.0,
                        min(1.0, float(conf_val if conf_val is not None else 1.0)),
                    )
                    rank_key = (1 if ov else 0, -hop, conf_sc)
                    if best_rank is None or rank_key > best_rank:
                        best_rank = rank_key
                        best_choice = (hop, from_src, rel_dict, ov, conf_sc)

                if best_choice is None:
                    continue
                b_hop, b_from, b_rel, b_ov, b_conf = best_choice
                attenuation = (0.72 ** b_hop) * b_conf
                contract_boost = 0.10 if b_ov else 0.0
                via_rel = {
                    "relation_type": b_rel.get("relation_type"),
                    "contract_key": b_rel.get("contract_key") or "",
                    "from_source": b_from,
                    "to_source": nbr_source,
                    "hop": b_hop,
                    "status": b_rel.get("status") or "active",
                }
                sib_hits = await _query_tenant_memory_bucket(
                    qt=qt,
                    k=k,
                    ss=ss,
                    qe=qe,
                    qc=qc,
                    qtk=qtk,
                    w=w,
                    f_dict=f_dict,
                    query_proj=query_proj,
                    target_src=nbr_source,
                    target_branch=nbr_branch,
                    bucket_scope="related_source",
                    weight_multiplier=attenuation,
                    additive_boost=contract_boost,
                    via_relation=via_rel,
                    eff_root=eff_root,
                )
                candidate_items.extend(sib_hits)

        # Merge & deduplicate by memory ID keeping highest score
        dedup_map: Dict[str, Tuple[Dict[str, Any], Optional[Tuple[str, str, Optional[str]]]]] = {}
        for item, ctx_triple in candidate_items:
            mid = item["id"]
            prev = dedup_map.get(mid)
            if prev is None or item["score"] > prev[0]["score"]:
                dedup_map[mid] = (item, ctx_triple)

        sorted_pairs = sorted(
            dedup_map.values(), key=lambda pair: pair[0]["score"], reverse=True
        )[:k]
        top: List[Dict[str, Any]] = [pair[0] for pair in sorted_pairs]

        for r, ctx_triple in sorted_pairs:
            rmid = r["id"]
            if ctx_triple is not None:
                r_proj, r_br, r_ctx_src = ctx_triple
            else:
                hint = tenant_manager.lookup_memory_hint(rmid)
                if hint:
                    r_proj, r_br = hint
                    r_ctx_src = tenant_manager.lookup_memory_source_hint(rmid)
                elif r.get("user_id"):
                    r_proj, p_src, r_br = parse_tenant_spec(
                        r["user_id"], default_branch=eff_root
                    )
                    r_ctx_src = _ctx_source_arg(p_src)
                else:
                    r_proj, r_br, r_ctx_src = None, None, None

            reinf_ctx = (
                tenant_context(r_proj, r_br, source_id=r_ctx_src)
                if (r_proj and r_br)
                else nullcontext()
            )
            with reinf_ctx:
                rsal = await applyRetrievalTraceReinforcementToMemory(rmid, r["salience"])
                now = int(time.time() * 1000)
                db.execute("UPDATE memories SET salience=?, last_seen_at=? WHERE id=?", (rsal, now, rmid))
                if len(r["path"]) > 1:
                    wps_rows = db.fetchall("SELECT dst_id, weight FROM waypoints WHERE src_id=?", (rmid,))
                    wps = [{"target_id": row["dst_id"], "weight": row["weight"]} for row in wps_rows]

                    pru = await propagateAssociativeReinforcementToLinkedNodes(rmid, rsal, wps)
                    for u in pru:
                        linked_mem = q.get_mem(u["node_id"])
                        if linked_mem:
                            time_diff = (now - linked_mem["last_seen_at"]) / 86400000.0
                            decay_fact = math.exp(-0.02 * time_diff)
                            ctx_boost = HYBRID_PARAMS["gamma"] * (rsal - (linked_mem["salience"] or 0)) * decay_fact
                            new_sal = max(0.0, min(1.0, (linked_mem["salience"] or 0) + ctx_boost))
                            db.execute("UPDATE memories SET salience=?, last_seen_at=? WHERE id=?", (new_sal, now, u["node_id"]))

                await on_query_hit(rmid, r["primary_sector"], lambda t: embed_for_sector(t, r["primary_sector"]))
                db.commit()

        cache[cache_key] = {"r": top, "t": time.time() * 1000}
        cache.move_to_end(cache_key)
        while len(cache) > MAX_CACHE_SIZE:
            cache.popitem(last=False)
        return top

    finally:
        dec_q()
