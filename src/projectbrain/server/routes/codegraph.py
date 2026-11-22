from fastapi import APIRouter, HTTPException, Query, Request, File, UploadFile, Form
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Tuple
import sqlite3
import time
import json
import logging
from pathlib import Path
from ...core.db import db
from ...core.tenant_manager import (
    _effective_root_branch,
    is_multi_db_enabled,
    sanitize_branch_schema_for_project,
    sanitize_project_db_name,
    tenant_context,
    tenant_manager,
)

logger = logging.getLogger("server.routes.codegraph")

router = APIRouter(tags=["codegraph"])


def parse_project_and_branch(project_id: str, default_branch: str = "main") -> Tuple[str, str]:
    """Parse composite project identifier (e.g. 'ecommerce:feature-x') into (project, branch)."""
    if not project_id:
        return ("default", default_branch)
    if ":" in project_id:
        parts = project_id.split(":", 1)
        return (parts[0], parts[1] or default_branch)
    return (project_id, default_branch)


def _get_store():
    import os
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

class SyncRequest(BaseModel):
    project_id: str
    project_name: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    author: Optional[str] = None
    project_path: Optional[str] = None

class CreateProjectRequest(BaseModel):
    id: str
    name: str
    description: Optional[str] = None

def get_local_db_path() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent / ".codegraph" / "codegraph.db"


def _replace_codegraph_tables(
    project_id_key: str,
    proj: str,
    branch: str,
    project_name: str,
    description: str,
    ts: int,
    client_ip: str,
    author: Optional[str],
    project_path: Optional[str],
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Persist legacy codegraph project metadata in Master DB and nodes/edges in Project DB."""
    proj_meta = tenant_manager.ensure_project_database(proj, name=project_name)
    eff_root = str(proj_meta.get("root_branch") or _effective_root_branch())
    tenant_manager.ensure_branch_schema(
        proj,
        branch,
        base_branch=eff_root,
        author=author or "system",
        has_graph=True,
        has_doc=False,
        record_lineage=(branch != eff_root),
    )

    db.connect()
    db.execute("DELETE FROM projects WHERE id = ?", (project_id_key,))
    db.execute(
        "INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id_key,
            project_name,
            description,
            ts,
            ts,
            client_ip,
            author or "anonymous",
            project_path,
        ),
    )

    with tenant_context(proj, branch):
        db.execute("DELETE FROM project_edges WHERE project_id = ?", (project_id_key,))
        db.execute("DELETE FROM project_nodes WHERE project_id = ?", (project_id_key,))

        if nodes or edges:
            if is_multi_db_enabled():
                t_conn, t_is_pg, _, _ = tenant_manager.get_tenant_connection(proj, eff_root)
            else:
                db.connect()
                t_conn, t_is_pg = db.conn, db.is_postgres

            node_tuples = [
                (
                    project_id_key,
                    n.get("id"),
                    n.get("kind"),
                    n.get("name"),
                    n.get("qualified_name"),
                    n.get("file_path"),
                    n.get("language"),
                    n.get("start_line", 0),
                    n.get("end_line", 0),
                    n.get("docstring"),
                    n.get("signature"),
                    n.get("updated_at", ts),
                )
                for n in nodes
            ]
            edge_tuples = [
                (
                    project_id_key,
                    e.get("id"),
                    e.get("source"),
                    e.get("target"),
                    e.get("kind"),
                    json.dumps(e.get("metadata"))
                    if isinstance(e.get("metadata"), (dict, list))
                    else e.get("metadata"),
                    e.get("line"),
                    e.get("col"),
                )
                for e in edges
            ]

            if t_is_pg:
                from psycopg2.extras import execute_values

                with t_conn.cursor() as cur:
                    if node_tuples:
                        execute_values(
                            cur,
                            """
                            INSERT INTO project_nodes
                            (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
                            VALUES %s
                            """,
                            node_tuples,
                        )
                    if edge_tuples:
                        execute_values(
                            cur,
                            """
                            INSERT INTO project_edges
                            (project_id, id, source, target, kind, metadata, line, col)
                            VALUES %s
                            """,
                            edge_tuples,
                        )
                try:
                    if not getattr(t_conn, "autocommit", False):
                        t_conn.commit()
                except Exception:
                    pass
            else:
                if node_tuples:
                    t_conn.executemany(
                        """
                        INSERT INTO project_nodes
                        (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        node_tuples,
                    )
                if edge_tuples:
                    t_conn.executemany(
                        """
                        INSERT INTO project_edges
                        (project_id, id, source, target, kind, metadata, line, col)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        edge_tuples,
                    )
                try:
                    t_conn.commit()
                except Exception:
                    pass

    db.commit()
    return proj_meta


@router.get("/projects")
async def get_projects():
    db.connect()
    try:
        rows = db.fetchall(
            "SELECT id, name, description, created_at, updated_at, sync_ip, sync_author, project_path "
            "FROM projects ORDER BY name ASC;"
        )

        projects = []
        seen_ids = set()
        for r in rows:
            p = dict(r)
            projects.append(p)
            seen_ids.add(p["id"])

        # Load master catalog projects and branch schemas
        master_proj_map: Dict[str, Dict[str, Any]] = {}
        branches_by_proj: Dict[str, List[Dict[str, Any]]] = {}
        try:
            mp_rows = tenant_manager._master_fetchall(
                "SELECT project_id, name, description, db_name, root_branch, root_schema, created_at, updated_at "
                "FROM master_projects ORDER BY project_id ASC"
            )
            for mp in mp_rows:
                pid = str(mp.get("project_id") or "")
                if pid:
                    master_proj_map[pid] = dict(mp)

            mb_rows = tenant_manager._master_fetchall(
                "SELECT project_id, branch_name, schema_name, base_branch, is_root, has_graph_schema, has_doc_schema, updated_at "
                "FROM master_branches ORDER BY is_root DESC, created_at ASC"
            )
            for mb in mb_rows:
                pid = str(mb.get("project_id") or "")
                if not pid:
                    continue
                branches_by_proj.setdefault(pid, []).append(
                    {
                        "branch_name": str(mb.get("branch_name") or ""),
                        "schema_name": str(mb.get("schema_name") or ""),
                        "base_branch": str(mb.get("base_branch") or "main"),
                        "is_root": bool(mb.get("is_root", False)),
                        "has_graph_schema": bool(mb.get("has_graph_schema", False)),
                        "has_doc_schema": bool(mb.get("has_doc_schema", False)),
                        "updated_at": mb.get("updated_at"),
                    }
                )
        except Exception as me:
            logger.debug(f"Master catalog merge skipped: {me}")

        # Merge with GraphStore projects
        try:
            store = _get_store()
            try:
                store_projects = store.list_projects()
            finally:
                store.close()
            now_ts = int(time.time())
            for sp in store_projects:
                if sp not in seen_ids:
                    projects.append(
                        {
                            "id": sp,
                            "name": sp,
                            "description": f"Graphify project {sp}",
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "sync_ip": "127.0.0.1",
                            "sync_author": "system",
                            "project_path": None,
                        }
                    )
                    seen_ids.add(sp)
        except Exception as ge:
            logger.debug(f"GraphStore project merge skipped: {ge}")

        # Merge master_projects entries missing from projects / GraphStore
        now_ts = int(time.time())
        for pid, mp in master_proj_map.items():
            if pid not in seen_ids:
                projects.append(
                    {
                        "id": pid,
                        "name": mp.get("name") or pid,
                        "description": mp.get("description") or f"Project {pid}",
                        "created_at": mp.get("created_at") or now_ts,
                        "updated_at": mp.get("updated_at") or now_ts,
                        "sync_ip": "127.0.0.1",
                        "sync_author": "system",
                        "project_path": None,
                    }
                )
                seen_ids.add(pid)

        eff_root = _effective_root_branch()
        for p in projects:
            proj_key, _ = parse_project_and_branch(p["id"], default_branch=eff_root)
            mp = master_proj_map.get(proj_key)
            p["db_name"] = (
                str(mp["db_name"])
                if mp and mp.get("db_name")
                else sanitize_project_db_name(proj_key)
            )
            p["root_schema"] = (
                str(mp["root_schema"]) if mp and mp.get("root_schema") else "pb_root"
            )
            p["branches"] = branches_by_proj.get(proj_key, [])

        return {"projects": projects}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load projects: {str(e)}")


@router.post("/projects")
async def create_project(req: CreateProjectRequest):
    import os
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(status_code=403, detail="Operation not permitted: Server is running in Read-Only mode.")
    db.connect()
    ts = int(time.time())
    try:
        eff_default_root = _effective_root_branch()
        proj, branch = parse_project_and_branch(req.id, default_branch=eff_default_root)
        desc = req.description or f"Project {req.name}"
        proj_meta = tenant_manager.ensure_project_database(
            proj, name=req.name, description=desc
        )
        eff_root = str(proj_meta.get("root_branch") or eff_default_root)
        has_explicit_branch = ":" in req.id and branch != eff_root
        br_meta = tenant_manager.ensure_branch_schema(
            proj,
            branch if has_explicit_branch else eff_root,
            base_branch=eff_root,
            author="system",
            has_graph=True,
            has_doc=True,
            record_lineage=has_explicit_branch,
        )
        schema_name = str(
            br_meta.get("schema_name")
            or sanitize_branch_schema_for_project(branch, eff_root)
        )

        try:
            store = _get_store()
            try:
                if has_explicit_branch and hasattr(store, "ensure_branch_schema"):
                    store.ensure_branch_schema(
                        proj, branch, author="system", base_branch=eff_root
                    )
                elif hasattr(store, "_get_project_store"):
                    delegate, _ = store._get_project_store(proj)
                    delegate.ensure_root_schema()
                elif hasattr(store, "ensure_root_schema"):
                    store.ensure_root_schema()
            finally:
                store.close()
        except Exception as ge:
            logger.debug(f"GraphStore project init skipped: {ge}")

        db.execute(
            "INSERT INTO projects (id, name, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET name=excluded.name, description=excluded.description, updated_at=excluded.updated_at",
            (req.id, req.name, desc, ts, ts),
        )
        db.commit()
        return {
            "status": "success",
            "message": f"Project {req.id} created successfully.",
            "project_id": req.id,
            "db_name": proj_meta["db_name"],
            "schema_name": schema_name,
        }
    except Exception as e:
        if db.conn:
            try:
                db.conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")


@router.delete("/projects/{project_id:path}")
async def delete_project(project_id: str):
    import os
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(status_code=403, detail="Operation not permitted: Server is running in Read-Only mode.")

    eff_default_root = _effective_root_branch()
    proj, branch = parse_project_and_branch(project_id, default_branch=eff_default_root)
    proj_root = eff_default_root
    try:
        row = tenant_manager._master_fetchone(
            "SELECT root_branch FROM master_projects WHERE project_id = ?",
            (proj,),
        )
        if row and row.get("root_branch"):
            proj_root = str(row["root_branch"])
    except Exception:
        pass

    delete_branch_only = ":" in project_id and branch != proj_root

    # Clean up GraphStore
    try:
        store = _get_store()
        try:
            if delete_branch_only:
                store.delete_branch(proj, branch)
            else:
                store.delete_project(proj)
        finally:
            store.close()
    except Exception as ge:
        logger.debug(f"GraphStore delete project cleanup: {ge}")

    db.connect()
    try:
        if delete_branch_only:
            try:
                tenant_manager.drop_branch_schema(proj, branch)
            except Exception:
                pass
            db.execute("DELETE FROM projects WHERE id = ?;", (project_id,))
        else:
            try:
                with tenant_context(proj, proj_root):
                    db.execute("DELETE FROM project_nodes WHERE project_id = ?;", (project_id,))
                    db.execute("DELETE FROM project_edges WHERE project_id = ?;", (project_id,))
                    if proj != project_id:
                        db.execute("DELETE FROM project_nodes WHERE project_id = ?;", (proj,))
                        db.execute("DELETE FROM project_edges WHERE project_id = ?;", (proj,))
            except Exception as te:
                logger.debug(f"Tenant codegraph cleanup skipped for {proj}: {te}")

            try:
                tenant_manager.drop_project_database(proj)
            except Exception as de:
                logger.debug(f"Master project database drop warning for {proj}: {de}")

            db.execute(
                "DELETE FROM projects WHERE id = ? OR id = ? OR id LIKE ?;",
                (project_id, proj, f"{proj}:%"),
            )

        db.commit()
        return {"status": "success", "message": f"Project {project_id} deleted successfully."}
    except Exception as e:
        if db.conn:
            try:
                db.conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {str(e)}")

@router.get("/diff")
async def diff_projects(base_project_id: str, target_project_id: str):
    # Try GraphStore diff first
    try:
        from ...graphify_engine.store import get_graph_store
        store = _get_store()
        try:
            base_proj, base_br = parse_project_and_branch(base_project_id)
            target_proj, target_br = parse_project_and_branch(target_project_id)
            if base_proj == target_proj:
                base_stats = store.get_stats(base_proj, base_br)
                target_stats = store.get_stats(target_proj, target_br)
                base_node_cnt = base_stats.get("node_count", 0) or base_stats.get("nodes_count", 0)
                target_node_cnt = target_stats.get("node_count", 0) or target_stats.get("nodes_count", 0)
                if base_node_cnt > 0 or target_node_cnt > 0:
                    diff_res = store.diff_branches(
                        project_id=base_proj,
                        base_branch=base_br,
                        target_branch=target_br
                    )
                    added = diff_res.new_nodes
                    modified = diff_res.modified_nodes
                    deleted = [{"id": did} for did in diff_res.deleted_node_ids]
                    return {
                        "base_project_id": base_project_id,
                        "target_project_id": target_project_id,
                        "added": added,
                        "deleted": deleted,
                        "modified": modified
                    }
        finally:
            store.close()
    except Exception as ge:
        logger.debug(f"GraphStore diff fallback: {ge}")

    db.connect()
    try:
        eff_root = _effective_root_branch()
        base_proj, base_br = parse_project_and_branch(base_project_id, default_branch=eff_root)
        target_proj, target_br = parse_project_and_branch(target_project_id, default_branch=eff_root)

        with tenant_context(base_proj, base_br):
            base_rows = db.fetchall(
                "SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature "
                "FROM project_nodes WHERE project_id = ?",
                (base_project_id,),
            )

        with tenant_context(target_proj, target_br):
            target_rows = db.fetchall(
                "SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature "
                "FROM project_nodes WHERE project_id = ?",
                (target_project_id,),
            )

        base_nodes = [dict(r) for r in base_rows]
        target_nodes = [dict(r) for r in target_rows]
        
        # Index nodes by (kind, qualified_name, file_path)
        base_map = {(n["kind"], n["qualified_name"], n["file_path"]): n for n in base_nodes}
        target_map = {(n["kind"], n["qualified_name"], n["file_path"]): n for n in target_nodes}
        
        added = []
        deleted = []
        modified = []
        
        for key, target_node in target_map.items():
            if key not in base_map:
                added.append(target_node)
            else:
                base_node = base_map[key]
                changes = []
                if base_node.get("signature") != target_node.get("signature"):
                    changes.append("signature")
                if base_node.get("docstring") != target_node.get("docstring"):
                    changes.append("docstring")
                if base_node.get("start_line") != target_node.get("start_line") or base_node.get("end_line") != target_node.get("end_line"):
                    changes.append("lines")
                    
                if changes:
                    modified.append({
                        "node": target_node,
                        "changes": changes,
                        "base": {
                            "signature": base_node.get("signature"),
                            "docstring": base_node.get("docstring"),
                            "start_line": base_node.get("start_line"),
                            "end_line": base_node.get("end_line")
                        }
                    })
                    
        for key, base_node in base_map.items():
            if key not in target_map:
                deleted.append(base_node)
                
        return {
            "base_project_id": base_project_id,
            "target_project_id": target_project_id,
            "added": added,
            "deleted": deleted,
            "modified": modified
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to diff projects: {str(e)}")

@router.get("/status")
async def get_codegraph_status(project_id: Optional[str] = None):
    if project_id:
        proj_id, branch = parse_project_and_branch(project_id)
        # Try GraphStore first
        try:
            store = _get_store()
            try:
                stats = store.get_stats(proj_id, branch)
            finally:
                store.close()
            node_cnt = stats.get("node_count", 0) or stats.get("nodes_count", 0)
            edge_cnt = stats.get("edge_count", 0) or stats.get("edges_count", 0)
            if node_cnt > 0 or edge_cnt > 0:
                return {
                    "status": "ready",
                    "source": "server",
                    "project_id": project_id,
                    "branch": branch,
                    "nodes_count": node_cnt,
                    "edges_count": edge_cnt,
                    "hyperedges_count": stats.get("hyperedge_count", 0) or stats.get("hyperedges_count", 0),
                }
        except Exception as ge:
            logger.debug(f"GraphStore status fallback: {ge}")

        db.connect()
        try:
            proj_id, branch = parse_project_and_branch(project_id, default_branch=_effective_root_branch())
            with tenant_context(proj_id, branch):
                n_row = db.fetchone(
                    "SELECT COUNT(*) AS cnt FROM project_nodes WHERE project_id = ?;",
                    (project_id,),
                )
                e_row = db.fetchone(
                    "SELECT COUNT(*) AS cnt FROM project_edges WHERE project_id = ?;",
                    (project_id,),
                )
            nodes_count = int((n_row or {}).get("cnt", 0))
            edges_count = int((e_row or {}).get("cnt", 0))
            return {
                "status": "ready",
                "source": "server",
                "project_id": project_id,
                "nodes_count": nodes_count,
                "edges_count": edges_count
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to query server project status: {str(e)}")
            
    # Local fallback
    db_path = get_local_db_path()
    if not db_path.exists():
        return {
            "status": "not_initialized",
            "message": "Local codegraph index not found, and no project_id specified."
        }
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM nodes;")
        nodes_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM edges;")
        edges_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM files;")
        files_count = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "status": "ready",
            "source": "local",
            "nodes_count": nodes_count,
            "edges_count": edges_count,
            "files_count": files_count
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to read local codegraph database: {str(e)}"
        }

@router.get("/data")
async def get_codegraph_data(
    project_id: Optional[str] = None,
    query: Optional[str] = None,
    kinds: Optional[str] = None,
    limit: int = Query(default=300, le=1000)
):
    if project_id:
        proj_id, branch = parse_project_and_branch(project_id)
        # Try GraphStore first
        try:
            from ...graphify_engine.store import get_graph_store
            store = _get_store()
            try:
                visjs = store.to_legacy_visjs(
                    project_id=proj_id,
                    branch_name=branch,
                    query=query,
                    kinds=kinds,
                    limit=limit,
                )
            finally:
                store.close()
            if visjs.get("nodes"):
                return visjs
        except Exception as ge:
            logger.debug(f"GraphStore to_legacy_visjs fallback: {ge}")

        db.connect()
        try:
            proj_id, branch = parse_project_and_branch(project_id, default_branch=_effective_root_branch())
            if "%" in proj_id:
                ctx = tenant_context(None)
            elif "%" in branch:
                ctx = tenant_context(proj_id, "%")
            else:
                ctx = tenant_context(proj_id, branch)

            is_like = "%" in project_id
            where_clauses = ["project_id LIKE ?" if is_like else "project_id = ?"]
            params: List[Any] = [project_id]

            if query:
                where_clauses.append("(name LIKE ? OR qualified_name LIKE ? OR file_path LIKE ?)")
                q_param = f"%{query}%"
                params.extend([q_param, q_param, q_param])

            if kinds and kinds != "all":
                kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
                if kind_list:
                    placeholders = ",".join(["?"] * len(kind_list))
                    where_clauses.append(f"kind IN ({placeholders})")
                    params.extend(kind_list)

            where_sql = f"WHERE {' AND '.join(where_clauses)}"
            nodes_query = f"""
                SELECT id, kind, name, qualified_name, file_path, language,
                       start_line, end_line, docstring, signature
                FROM project_nodes
                {where_sql}
                LIMIT ?
            """

            with ctx:
                rows = db.fetchall(nodes_query, tuple(params + [limit]))
                nodes = [dict(r) for r in rows]
                node_ids = {n["id"] for n in nodes}

                if not nodes:
                    return {"nodes": [], "edges": []}

                placeholders = ",".join(["?"] * len(node_ids))
                edge_op = "LIKE" if is_like else "="
                edges_query = f"""
                    SELECT id, source, target, kind, metadata, line, col
                    FROM project_edges
                    WHERE project_id {edge_op} ?
                      AND (source IN ({placeholders}) OR target IN ({placeholders}))
                    LIMIT 2000
                """
                edge_params = tuple([project_id] + list(node_ids) + list(node_ids))
                edge_rows = db.fetchall(edges_query, edge_params)

                edges = []
                all_referenced_node_ids = set()
                for r in edge_rows:
                    edge_data = dict(r)
                    if isinstance(edge_data.get("metadata"), str) and edge_data["metadata"]:
                        try:
                            edge_data["metadata"] = json.loads(edge_data["metadata"])
                        except Exception:
                            pass
                    edges.append(edge_data)
                    all_referenced_node_ids.add(edge_data["source"])
                    all_referenced_node_ids.add(edge_data["target"])

                missing_node_ids = all_referenced_node_ids - node_ids
                if missing_node_ids:
                    missing_placeholders = ",".join(["?"] * len(missing_node_ids))
                    missing_query = f"""
                        SELECT id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, docstring, signature
                        FROM project_nodes
                        WHERE project_id {edge_op} ?
                          AND id IN ({missing_placeholders})
                    """
                    missing_rows = db.fetchall(
                        missing_query, tuple([project_id] + list(missing_node_ids))
                    )
                    for r in missing_rows:
                        nodes.append(dict(r))

            return {
                "nodes": nodes,
                "edges": edges
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to query server project nodes: {str(e)}")

    # Local fallback
    db_path = get_local_db_path()
    if not db_path.exists():
        raise HTTPException(
            status_code=404, 
            detail="Local codegraph database not found, and no project_id was provided."
        )
        
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        where_clauses = []
        params = []
        
        if query:
            where_clauses.append("(name LIKE ? OR qualified_name LIKE ? OR file_path LIKE ?)")
            q_param = f"%{query}%"
            params.extend([q_param, q_param, q_param])
            
        if kinds and kinds != "all":
            kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
            if kind_list:
                where_clauses.append(f"kind IN ({','.join(['?'] * len(kind_list))})")
                params.extend(kind_list)
                
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        
        nodes_query = f"""
            SELECT id, kind, name, qualified_name, file_path, language, 
                   start_line, end_line, docstring, signature 
            FROM nodes
            {where_sql}
            LIMIT ?
        """
        cursor.execute(nodes_query, params + [limit])
        node_rows = cursor.fetchall()
        
        nodes = []
        node_ids = set()
        for r in node_rows:
            node_data = dict(r)
            nodes.append(node_data)
            node_ids.add(node_data["id"])
            
        if not nodes:
            conn.close()
            return {"nodes": [], "edges": []}
            
        placeholders = ",".join(["?"] * len(node_ids))
        edges_query = f"""
            SELECT id, source, target, kind, metadata, line, col
            FROM edges
            WHERE source IN ({placeholders}) OR target IN ({placeholders})
            LIMIT 200
        """
        cursor.execute(edges_query, list(node_ids) + list(node_ids))
        edge_rows = cursor.fetchall()
        
        edges = []
        all_referenced_node_ids = set()
        for r in edge_rows:
            d = dict(r)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except:
                    pass
            edges.append(d)
            all_referenced_node_ids.add(d["source"])
            all_referenced_node_ids.add(d["target"])
            
        # Fetch any missing referenced nodes (parents/connects) locally
        missing_node_ids = all_referenced_node_ids - node_ids
        if missing_node_ids:
            missing_placeholders = ",".join(["?"] * len(missing_node_ids))
            missing_query = f"""
                SELECT id, kind, name, qualified_name, file_path, language, 
                       start_line, end_line, docstring, signature 
                FROM nodes
                WHERE id IN ({missing_placeholders})
            """
            cursor.execute(missing_query, list(missing_node_ids))
            missing_rows = cursor.fetchall()
            for r in missing_rows:
                nodes.append(dict(r))
                
        conn.close()
        return {
            "nodes": nodes,
            "edges": edges
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to query local codegraph: {str(e)}"
        )

@router.post("/sync")
async def sync_codegraph_data(req: SyncRequest, request: Request = None):
    import os
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(status_code=403, detail="Operation not permitted: Server is running in Read-Only mode.")
    if not req.nodes:
        raise HTTPException(
            status_code=400,
            detail="Cannot sync empty codegraph data. Ensure that you have run 'codegraph init' and indexed files locally before syncing."
        )

    db.connect()
    ts = int(time.time())
    client_ip = "127.0.0.1"
    if request and request.client:
        client_ip = request.client.host

    
    try:
        proj, branch = parse_project_and_branch(req.project_id, default_branch=_effective_root_branch())
        proj_meta = _replace_codegraph_tables(
            project_id_key=req.project_id,
            proj=proj,
            branch=branch,
            project_name=req.project_name,
            description=f"Synced project {req.project_name}",
            ts=ts,
            client_ip=client_ip,
            author=req.author,
            project_path=req.project_path,
            nodes=req.nodes,
            edges=req.edges,
        )

        # Bridge into GraphStore
        try:
            from ...graphify_engine.store import get_graph_store
            from ...graphify_engine.models import GraphDelta, GraphNode, GraphEdge
            proj, branch = parse_project_and_branch(req.project_id)
            g_nodes = [
                GraphNode(
                    id=n.get("id"),
                    project_id=proj,
                    label=n.get("name") or n.get("qualified_name") or n.get("id"),
                    file_type="code",
                    source_file=n.get("file_path") or "unknown",
                    source_location=f"{n.get('start_line', 0)}:{n.get('end_line', 0)}",
                    metadata={
                        "kind": n.get("kind"),
                        "name": n.get("name"),
                        "qualified_name": n.get("qualified_name"),
                        "signature": n.get("signature"),
                        "docstring": n.get("docstring"),
                        "language": n.get("language")
                    }
                )
                for n in req.nodes
            ]
            g_edges = [
                GraphEdge(
                    project_id=proj,
                    source=e.get("source"),
                    target=e.get("target"),
                    relation=e.get("kind") or "calls",
                    confidence="EXTRACTED",
                    source_file=e.get("source_file"),
                    metadata=e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
                )
                for e in req.edges
            ]
            delta = GraphDelta(project_id=proj, branch=branch, nodes=g_nodes, edges=g_edges)
            store = _get_store()
            try:
                store.sync_branch_delta(
                    project_id=proj,
                    branch_name=branch,
                    delta=delta,
                    partial=False,
                    author=req.author or "anonymous"
                )
            finally:
                store.close()
        except Exception as ge:
            logger.debug(f"GraphStore bridge sync failed: {ge}")
        
        return {
            "status": "success",
            "message": f"Successfully synced project {req.project_id}",
            "db_name": proj_meta["db_name"],
            "nodes_synced": len(req.nodes),
            "edges_synced": len(req.edges)
        }
    except Exception as e:
        if db.conn:
            try:
                db.conn.rollback()
            except:
                pass
        raise HTTPException(
            status_code=500,
            detail=f"Database synchronization failed: {str(e)}"
        )

@router.post("/upload-db")
async def upload_codegraph_db(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    project_name: Optional[str] = Form(None),
    author: Optional[str] = Form("anonymous"),
    project_path: Optional[str] = Form(None),
    request: Request = None
):
    import os
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(status_code=403, detail="Operation not permitted: Server is running in Read-Only mode.")
    import tempfile
    import os
    import shutil

    safe_name = Path(file.filename or "uploaded_file.db").name
    max_mb = float(os.getenv("PB_MAX_UPLOAD_MB", "50"))
    max_bytes = int(max_mb * 1024 * 1024)

    content_len = request.headers.get("content-length") if request else None
    if content_len and int(content_len) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Payload Too Large: Maximum allowed upload is {max_mb}MB")

    # Create a temporary file to save the uploaded SQLite database
    fd, temp_path = tempfile.mkstemp(suffix=".db")
    try:
        total_read = 0
        with os.fdopen(fd, "wb") as tmp:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > max_bytes:
                    raise HTTPException(status_code=413, detail=f"Payload Too Large: Maximum allowed upload is {max_mb}MB")
                tmp.write(chunk)

        # Connect to the uploaded SQLite file
        conn = sqlite3.connect(temp_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Read nodes
        cursor.execute("SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at FROM nodes;")
        nodes = [dict(row) for row in cursor.fetchall()]

        # Read edges
        cursor.execute("SELECT id, source, target, kind, metadata, line, col FROM edges;")
        edges = [dict(row) for row in cursor.fetchall()]

        conn.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read and parse uploaded SQLite database: {str(e)}"
        )
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

    # Now reuse the existing sync database logic to save it
    db.connect()
    ts = int(time.time())
    client_ip = "127.0.0.1"
    if request and request.client:
        client_ip = request.client.host

    name = project_name or project_id

    try:
        proj, branch = parse_project_and_branch(project_id, default_branch=_effective_root_branch())
        proj_meta = _replace_codegraph_tables(
            project_id_key=project_id,
            proj=proj,
            branch=branch,
            project_name=name,
            description=f"Uploaded db project {name}",
            ts=ts,
            client_ip=client_ip,
            author=author,
            project_path=project_path,
            nodes=nodes,
            edges=edges,
        )

        # Bridge into GraphStore
        try:
            from ...graphify_engine.store import get_graph_store
            from ...graphify_engine.models import GraphDelta, GraphNode, GraphEdge
            proj, branch = parse_project_and_branch(project_id)
            g_nodes = [
                GraphNode(
                    id=n.get("id"),
                    project_id=proj,
                    label=n.get("name") or n.get("qualified_name") or n.get("id"),
                    file_type="code",
                    source_file=n.get("file_path") or "unknown",
                    source_location=f"{n.get('start_line', 0)}:{n.get('end_line', 0)}",
                    metadata={
                        "kind": n.get("kind"),
                        "name": n.get("name"),
                        "qualified_name": n.get("qualified_name"),
                        "signature": n.get("signature"),
                        "docstring": n.get("docstring"),
                        "language": n.get("language")
                    }
                )
                for n in nodes
            ]
            g_edges = [
                GraphEdge(
                    project_id=proj,
                    source=e.get("source"),
                    target=e.get("target"),
                    relation=e.get("kind") or "calls",
                    confidence="EXTRACTED",
                    source_file=e.get("source_file"),
                    metadata=e.get("metadata") if isinstance(e.get("metadata"), dict) else {}
                )
                for e in edges
            ]
            delta = GraphDelta(project_id=proj, branch=branch, nodes=g_nodes, edges=g_edges)
            store = _get_store()
            try:
                store.sync_branch_delta(
                    project_id=proj,
                    branch_name=branch,
                    delta=delta,
                    partial=False,
                    author=author or "anonymous"
                )
            finally:
                store.close()
        except Exception as ge:
            logger.debug(f"GraphStore bridge upload failed: {ge}")
        
        return {
            "status": "success",
            "message": f"Successfully imported uploaded codegraph database for project {project_id}",
            "db_name": proj_meta["db_name"],
            "nodes_synced": len(nodes),
            "edges_synced": len(edges)
        }
    except Exception as e:
        if db.conn:
            try:
                db.conn.rollback()
            except:
                pass
        raise HTTPException(
            status_code=500,
            detail=f"Database synchronization via upload failed: {str(e)}"
        )
