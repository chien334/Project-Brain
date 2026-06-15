from fastapi import APIRouter, HTTPException, Query, Request, File, UploadFile, Form
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import sqlite3
import time
import json
from pathlib import Path
from ...core.db import db

router = APIRouter(tags=["codegraph"])

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

@router.get("/projects")
async def get_projects():
    db.connect()
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT id, name, description, created_at, updated_at, sync_ip, sync_author, project_path FROM projects ORDER BY name ASC;")
        rows = cursor.fetchall()
        
        projects = []
        for r in rows:
            if db.is_postgres:
                projects.append({
                    "id": r[0],
                    "name": r[1],
                    "description": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                    "sync_ip": r[5],
                    "sync_author": r[6],
                    "project_path": r[7]
                })
            else:
                projects.append(dict(r))
                
        cursor.close()
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
        cursor = db.conn.cursor()
        if db.is_postgres:
            cursor.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, updated_at=EXCLUDED.updated_at",
                (req.id, req.name, req.description or f"Project {req.name}", ts, ts)
            )
        else:
            cursor.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET name=excluded.name, description=excluded.description, updated_at=excluded.updated_at",
                (req.id, req.name, req.description or f"Project {req.name}", ts, ts)
            )
        db.conn.commit()
        cursor.close()
        return {"status": "success", "message": f"Project {req.id} created successfully."}
    except Exception as e:
        if db.conn:
            try: db.conn.rollback()
            except: pass
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")

@router.get("/diff")
async def diff_projects(base_project_id: str, target_project_id: str):
    db.connect()
    try:
        cursor = db.conn.cursor()
        
        # Fetch base nodes
        if db.is_postgres:
            cursor.execute("""
                SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature 
                FROM project_nodes WHERE project_id = %s
            """, (base_project_id,))
        else:
            cursor.execute("""
                SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature 
                FROM project_nodes WHERE project_id = ?
            """, (base_project_id,))
        base_rows = cursor.fetchall()
        
        # Fetch target nodes
        if db.is_postgres:
            cursor.execute("""
                SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature 
                FROM project_nodes WHERE project_id = %s
            """, (target_project_id,))
        else:
            cursor.execute("""
                SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature 
                FROM project_nodes WHERE project_id = ?
            """, (target_project_id,))
        target_rows = cursor.fetchall()
        cursor.close()
        
        # Helper to convert sqlite/postgres rows to dict
        def row_to_dict(r):
            if db.is_postgres:
                return {
                    "id": r[0], "kind": r[1], "name": r[2], "qualified_name": r[3],
                    "file_path": r[4], "language": r[5], "start_line": r[6], "end_line": r[7],
                    "docstring": r[8], "signature": r[9]
                }
            return dict(r)
            
        base_nodes = [row_to_dict(r) for r in base_rows]
        target_nodes = [row_to_dict(r) for r in target_rows]
        
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
        db.connect()
        try:
            cursor = db.conn.cursor()
            
            # Count nodes
            if db.is_postgres:
                cursor.execute("SELECT COUNT(*) FROM project_nodes WHERE project_id = %s;", (project_id,))
                nodes_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM project_edges WHERE project_id = %s;", (project_id,))
                edges_count = cursor.fetchone()[0]
            else:
                cursor.execute("SELECT COUNT(*) FROM project_nodes WHERE project_id = ?;", (project_id,))
                nodes_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM project_edges WHERE project_id = ?;", (project_id,))
                edges_count = cursor.fetchone()[0]
                
            cursor.close()
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
        db.connect()
        try:
            cursor = db.conn.cursor()
            
            # Build node query for Server Db
            if "%" in project_id or "_" in project_id:
                where_clauses = ["project_id LIKE " + ("%s" if db.is_postgres else "?")]
            else:
                where_clauses = ["project_id = " + ("%s" if db.is_postgres else "?")]
            params = [project_id]
            
            if query:
                where_clauses.append("(name LIKE " + ("%s" if db.is_postgres else "?") + 
                                     " OR qualified_name LIKE " + ("%s" if db.is_postgres else "?") + 
                                     " OR file_path LIKE " + ("%s" if db.is_postgres else "?") + ")")
                q_param = f"%{query}%"
                params.extend([q_param, q_param, q_param])
                
            if kinds and kinds != "all":
                kind_list = [k.strip() for k in kinds.split(",") if k.strip()]
                if kind_list:
                    placeholders = ",".join(["%s" if db.is_postgres else "?"] * len(kind_list))
                    where_clauses.append(f"kind IN ({placeholders})")
                    params.extend(kind_list)
                    
            where_sql = f"WHERE {' AND '.join(where_clauses)}"
            
            nodes_query = f"""
                SELECT id, kind, name, qualified_name, file_path, language, 
                       start_line, end_line, docstring, signature 
                FROM project_nodes
                {where_sql}
                LIMIT {("%s" if db.is_postgres else "?")}
            """
            
            cursor.execute(nodes_query, params + [limit])
            rows = cursor.fetchall()
            
            nodes = []
            node_ids = set()
            for r in rows:
                if db.is_postgres:
                    node_data = {
                        "id": r[0], "kind": r[1], "name": r[2], "qualified_name": r[3],
                        "file_path": r[4], "language": r[5], "start_line": r[6], "end_line": r[7],
                        "docstring": r[8], "signature": r[9]
                    }
                else:
                    node_data = dict(r)
                nodes.append(node_data)
                node_ids.add(node_data["id"])
                
            if not nodes:
                cursor.close()
                return {"nodes": [], "edges": []}
                
            # Query edges connecting these nodes
            placeholders = ",".join(["%s" if db.is_postgres else "?"] * len(node_ids))
            edge_op = "LIKE" if ("%" in project_id or "_" in project_id) else "="
            edges_query = f"""
                SELECT id, source, target, kind, metadata, line, col
                FROM project_edges
                WHERE project_id {edge_op} {("%s" if db.is_postgres else "?")} 
                  AND source IN ({placeholders}) 
                  AND target IN ({placeholders})
            """
            
            edge_params = [project_id] + list(node_ids) + list(node_ids)
            cursor.execute(edges_query, edge_params)
            rows = cursor.fetchall()
            
            edges = []
            for r in rows:
                if db.is_postgres:
                    meta_raw = r[4]
                    meta = json.loads(meta_raw) if meta_raw else None
                    edges.append({
                        "id": r[0], "source": r[1], "target": r[2], "kind": r[3],
                        "metadata": meta, "line": r[5], "col": r[6]
                    })
                else:
                    d = dict(r)
                    if d.get("metadata"):
                        try:
                            d["metadata"] = json.loads(d["metadata"])
                        except:
                            pass
                    edges.append(d)
                    
            cursor.close()
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
            WHERE source IN ({placeholders}) AND target IN ({placeholders})
        """
        cursor.execute(edges_query, list(node_ids) + list(node_ids))
        edge_rows = cursor.fetchall()
        
        edges = []
        for r in edge_rows:
            d = dict(r)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except:
                    pass
            edges.append(d)
            
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
        cursor = db.conn.cursor()
        
        # Dialect-safe delete and insert pattern
        if db.is_postgres:
            # Delete old nodes, edges and project info
            cursor.execute("DELETE FROM project_edges WHERE project_id = %s", (req.project_id,))
            cursor.execute("DELETE FROM project_nodes WHERE project_id = %s", (req.project_id,))
            cursor.execute("DELETE FROM projects WHERE id = %s", (req.project_id,))
            
            # Insert project
            cursor.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (req.project_id, req.project_name, f"Synced project {req.project_name}", ts, ts, client_ip, req.author or "anonymous", req.project_path)
            )
            
            # Batch insert nodes
            if req.nodes:
                node_tuples = [
                    (
                        req.project_id, n.get("id"), n.get("kind"), n.get("name"), n.get("qualified_name"),
                        n.get("file_path"), n.get("language"), n.get("start_line", 0), n.get("end_line", 0),
                        n.get("docstring"), n.get("signature"), n.get("updated_at", ts)
                    )
                    for n in req.nodes
                ]
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    """
                    INSERT INTO project_nodes 
                    (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
                    VALUES %s
                    """,
                    node_tuples
                )
                
            # Batch insert edges
            if req.edges:
                edge_tuples = [
                    (
                        req.project_id, e.get("id"), e.get("source"), e.get("target"), e.get("kind"),
                        json.dumps(e.get("metadata")) if isinstance(e.get("metadata"), (dict, list)) else e.get("metadata"),
                        e.get("line"), e.get("col")
                    )
                    for e in req.edges
                ]
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    """
                    INSERT INTO project_edges 
                    (project_id, id, source, target, kind, metadata, line, col)
                    VALUES %s
                    """,
                    edge_tuples
                )
        else:
            # SQLite batch insert
            cursor.execute("DELETE FROM project_edges WHERE project_id = ?", (req.project_id,))
            cursor.execute("DELETE FROM project_nodes WHERE project_id = ?", (req.project_id,))
            cursor.execute("DELETE FROM projects WHERE id = ?", (req.project_id,))
            
            cursor.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (req.project_id, req.project_name, f"Synced project {req.project_name}", ts, ts, client_ip, req.author or "anonymous", req.project_path)
            )
            
            if req.nodes:
                node_tuples = [
                    (
                        req.project_id, n.get("id"), n.get("kind"), n.get("name"), n.get("qualified_name"),
                        n.get("file_path"), n.get("language"), n.get("start_line", 0), n.get("end_line", 0),
                        n.get("docstring"), n.get("signature"), n.get("updated_at", ts)
                    )
                    for n in req.nodes
                ]
                cursor.executemany(
                    """
                    INSERT INTO project_nodes 
                    (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    node_tuples
                )
                
            if req.edges:
                edge_tuples = [
                    (
                        req.project_id, e.get("id"), e.get("source"), e.get("target"), e.get("kind"),
                        json.dumps(e.get("metadata")) if isinstance(e.get("metadata"), (dict, list)) else e.get("metadata"),
                        e.get("line"), e.get("col")
                    )
                    for e in req.edges
                ]
                cursor.executemany(
                    """
                    INSERT INTO project_edges 
                    (project_id, id, source, target, kind, metadata, line, col)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    edge_tuples
                )
                
        db.conn.commit()
        cursor.close()
        
        return {
            "status": "success",
            "message": f"Successfully synced project {req.project_id}",
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

    # Create a temporary file to save the uploaded SQLite database
    fd, temp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as tmp:
            shutil.copyfileobj(file.file, tmp)

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
        db_cursor = db.conn.cursor()
        
        if db.is_postgres:
            db_cursor.execute("DELETE FROM project_edges WHERE project_id = %s", (project_id,))
            db_cursor.execute("DELETE FROM project_nodes WHERE project_id = %s", (project_id,))
            db_cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            
            db_cursor.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (project_id, name, f"Uploaded db project {name}", ts, ts, client_ip, author or "anonymous", project_path)
            )
            
            if nodes:
                node_tuples = [
                    (
                        project_id, n.get("id"), n.get("kind"), n.get("name"), n.get("qualified_name"),
                        n.get("file_path"), n.get("language"), n.get("start_line", 0), n.get("end_line", 0),
                        n.get("docstring"), n.get("signature"), n.get("updated_at", ts)
                    )
                    for n in nodes
                ]
                from psycopg2.extras import execute_values
                execute_values(
                    db_cursor,
                    """
                    INSERT INTO project_nodes 
                    (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
                    VALUES %s
                    """,
                    node_tuples
                )
                
            if edges:
                edge_tuples = [
                    (
                        project_id, e.get("id"), e.get("source"), e.get("target"), e.get("kind"),
                        json.dumps(e.get("metadata")) if isinstance(e.get("metadata"), (dict, list)) else e.get("metadata"),
                        e.get("line"), e.get("col")
                    )
                    for e in edges
                ]
                from psycopg2.extras import execute_values
                execute_values(
                    db_cursor,
                    """
                    INSERT INTO project_edges 
                    (project_id, id, source, target, kind, metadata, line, col)
                    VALUES %s
                    """,
                    edge_tuples
                )
        else:
            db_cursor.execute("DELETE FROM project_edges WHERE project_id = ?", (project_id,))
            db_cursor.execute("DELETE FROM project_nodes WHERE project_id = ?", (project_id,))
            db_cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            
            db_cursor.execute(
                "INSERT INTO projects (id, name, description, created_at, updated_at, sync_ip, sync_author, project_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, name, f"Uploaded db project {name}", ts, ts, client_ip, author or "anonymous", project_path)
            )
            
            if nodes:
                node_tuples = [
                    (
                        project_id, n.get("id"), n.get("kind"), n.get("name"), n.get("qualified_name"),
                        n.get("file_path"), n.get("language"), n.get("start_line", 0), n.get("end_line", 0),
                        n.get("docstring"), n.get("signature"), n.get("updated_at", ts)
                    )
                    for n in nodes
                ]
                db_cursor.executemany(
                    """
                    INSERT INTO project_nodes 
                    (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    node_tuples
                )
                
            if edges:
                edge_tuples = [
                    (
                        project_id, e.get("id"), e.get("source"), e.get("target"), e.get("kind"),
                        json.dumps(e.get("metadata")) if isinstance(e.get("metadata"), (dict, list)) else e.get("metadata"),
                        e.get("line"), e.get("col")
                    )
                    for e in edges
                ]
                db_cursor.executemany(
                    """
                    INSERT INTO project_edges 
                    (project_id, id, source, target, kind, metadata, line, col)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    edge_tuples
                )
                
        db.conn.commit()
        db_cursor.close()
        
        return {
            "status": "success",
            "message": f"Successfully imported uploaded codegraph database for project {project_id}",
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
