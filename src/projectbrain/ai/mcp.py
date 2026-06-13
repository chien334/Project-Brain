import sys
import os
import json
import traceback
import datetime
import logging
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp")

from ..main import Memory
from ..core.config import env
from ..temporal_graph.store import insert_fact
from ..temporal_graph.query import query_facts_at_time
from ..ops.dynamics import applyRetrievalTraceReinforcementToMemory
from ..core.db import db

# Create FastMCP server instance
mcp_server = FastMCP("projectbrain-mcp")
mem = Memory()

@mcp_server.tool(name="projectbrain_query", description="Query ProjectBrain for contextual memories (HSG) and/or temporal facts")
async def projectbrain_query(
    query: str,
    type: str = "contextual",
    fact_pattern: dict = None,
    at: str = None,
    k: int = 10,
    user_id: str = None,
    sector: str = None,
    author: str = None
) -> str:
    """
    Query ProjectBrain for contextual memories (HSG) and/or temporal facts.
    
    Args:
        query: Free-form search text.
        type: Query type: 'contextual' for HSG semantic search (default), 'factual' for temporal fact queries, 'unified' for both.
        fact_pattern: Fact pattern for temporal queries. Used when type is 'factual' or 'unified'.
        at: ISO date string for point-in-time queries (default: now).
        k: Max results for HSG queries.
        user_id: Isolate results to specific user.
        sector: Restrict to sector (lexical, semantic, etc).
        author: Optional name of the user performing the search.
    """
    try:
        import getpass
        resolved_author = author or os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous-mcp"
        logger.info(f"MCP User '{resolved_author}' queried: '{query}' on project '{user_id or 'default'}'")

        # Normalize and validate qtype
        qtype = type
        if not qtype or not isinstance(qtype, str):
            qtype = "contextual"
        qtype = qtype.lower().strip()
        if qtype not in ["contextual", "factual", "unified"]:
            qtype = "contextual"

        limit = k
        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        
        at_date = datetime.datetime.fromisoformat(at) if at else datetime.datetime.now()
        at_ts = int(at_date.timestamp() * 1000)
        
        results = {"type": qtype, "query": query}
        
        # contextual (hsg) query
        if qtype in ["contextual", "unified"]:
            filters = {}
            if sector: filters["sector"] = sector
            
            contextual = await mem.search(query, user_id=uid, limit=limit, **filters)
            results["contextual"] = [{
                "source": "hsg",
                "id": m.get("id"),
                "score": round(m.get("score", 0), 4),
                "primary_sector": m.get("primary_sector"),
                "salience": round(m.get("salience", 0), 4),
                "content": m.get("content")
            } for m in contextual]
        
        # temporal fact query
        if qtype in ["factual", "unified"]:
            pattern = fact_pattern or {}
            facts = await query_facts_at_time(
                subject=pattern.get("subject"),
                predicate=pattern.get("predicate"),
                obj=pattern.get("object"),
                at_time=at_ts,
                min_confidence=0.0,
                user_id=uid
            )
            results["factual"] = [{
                "source": "temporal",
                "id": f["id"],
                "subject": f["subject"],
                "predicate": f["predicate"],
                "object": f["object"],
                "valid_from": f["valid_from"],
                "valid_to": f.get("valid_to"),
                "confidence": round(f["confidence"], 4),
                "content": f"{f['subject']} {f['predicate']} {f['object']}"
            } for f in facts]
        
        # build summary based on what was successfully queried
        has_contextual = "contextual" in results
        has_factual = "factual" in results
        
        if has_contextual and has_factual:
            ctx_count = len(results.get("contextual", []))
            fact_count = len(results.get("factual", []))
            summary = f"Found {ctx_count} contextual memories and {fact_count} temporal facts"
        elif has_contextual:
            count = len(results.get("contextual", []))
            summary = f"Found {count} contextual memories for '{query}'" if count > 0 else "No contextual memories matched."
        elif has_factual:
            count = len(results.get("factual", []))
            summary = f"Found {count} temporal facts" if count > 0 else "No temporal facts matched."
        else:
            summary = "No query results."

        return f"{summary}\n\n{json.dumps(results, default=str, indent=2)}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_store", description="Persist new content into ProjectBrain (HSG contextual memory and/or temporal facts)")
async def projectbrain_store(
    content: str,
    type: str = "contextual",
    facts: list = None,
    user_id: str = None,
    tags: list = None,
    metadata: dict = None,
    author: str = None
) -> str:
    """
    Persist new content into ProjectBrain (HSG contextual memory and/or temporal facts).
    
    Args:
        content: Raw memory text to store.
        type: Storage type: 'contextual' for HSG only (default), 'factual' for temporal facts only, 'both' for both systems.
        facts: Array of facts to store. Required when type is 'factual' or 'both'.
        user_id: User ID associated with the changes.
        tags: Custom tags for organizing memories.
        metadata: Custom metadata dictionary.
        author: Optional name of the user storing the memory.
    """
    try:
        import getpass
        # Normalize and validate stype
        stype = type
        if not stype or not isinstance(stype, str):
            stype = "contextual"
        stype = stype.lower().strip()
        if stype not in ["contextual", "factual", "both"]:
            stype = "contextual"

        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID") or "anonymous"
        
        # Read tags from args, apply configured tags if present
        actual_tags = tags or []
        env_tags_str = os.getenv("PB_DEFAULT_TAGS") or os.getenv("OM_DEFAULT_TAGS")
        if env_tags_str:
            env_tags = [t.strip() for t in env_tags_str.split(",") if t.strip()]
            for et in env_tags:
                if et not in actual_tags:
                    actual_tags.append(et)
        
        # Track edit origin metadata
        meta = metadata or {}
        meta["editor"] = "mcp-client"
        meta["editor_user"] = uid
        resolved_author = author or os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous-mcp"
        meta["author"] = resolved_author
        if "tags" not in meta:
            meta["tags"] = actual_tags

        facts_data = facts or []
        
        # validate facts requirement
        if stype in ["factual", "both"] and not facts_data:
            raise ValueError(f"Facts array is required when type is '{stype}'. Please provide at least one fact.")
        
        results = {"type": stype}
        
        # store contextual memory
        if stype in ["contextual", "both"]:
            res = await mem.add(content, user_id=uid, meta=meta, tags=actual_tags)
            if res:
                results["hsg"] = {
                    "id": res.get('root_memory_id') or res.get('id'),
                    "primary_sector": res.get('primary_sector')
                }
        
        # store temporal facts
        if stype in ["factual", "both"] and facts_data:
            temporal_results = []
            for fact in facts_data:
                valid_from_str = fact.get("valid_from")
                valid_from_dt = datetime.datetime.fromisoformat(valid_from_str) if valid_from_str else datetime.datetime.now()
                valid_from_ts = int(valid_from_dt.timestamp() * 1000)
                confidence = fact.get("confidence", 1.0)
                
                fact_id = await insert_fact(
                    subject=fact["subject"],
                    predicate=fact["predicate"],
                    subject_object=fact["object"],
                    valid_from=valid_from_ts,
                    confidence=confidence,
                    metadata=meta,
                    user_id=uid
                )
                
                temporal_results.append({
                    "id": fact_id,
                    "subject": fact["subject"],
                    "predicate": fact["predicate"],
                    "object": fact["object"],
                    "valid_from": valid_from_dt.isoformat(),
                    "confidence": confidence
                })
            results["temporal"] = temporal_results
        
        # build response message based on what was successfully stored
        has_hsg = "hsg" in results
        has_temporal = "temporal" in results
        
        if has_hsg and has_temporal:
            txt = f"Stored in both systems: HSG memory {results['hsg']['id']} + {len(results['temporal'])} temporal fact(s)"
        elif has_hsg:
            txt = f"Stored memory {results['hsg']['id']}"
        elif has_temporal:
            txt = f"Stored {len(results['temporal'])} temporal fact(s)"
        else:
            txt = "No data was stored (invalid or empty inputs)."
            
        if uid and (has_hsg or has_temporal):
            txt += f" [user={uid}]"
        
        return f"{txt}\n\n{json.dumps(results, default=str, indent=2)}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_get", description="Fetch a single memory by ID")
async def projectbrain_get(id: str) -> str:
    """Fetch a single memory by ID."""
    try:
        m = await mem.get(id)
        if not m:
            return f"Memory {id} not found"
        return json.dumps(dict(m), default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_delete", description="Delete a memory by ID")
async def projectbrain_delete(id: str, user_id: str = None) -> str:
    """Delete a memory by ID."""
    try:
        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        m = await mem.get(id)
        if not m:
            return f"Memory {id} not found"
        if uid and m.get("user_id") != uid:
            return f"Memory {id} not found for user {uid}"
        await mem.delete(id)
        return f"Memory {id} deleted"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_list", description="List recent memories")
async def projectbrain_list(limit: int = 20, user_id: str = None) -> str:
    """List recent memories."""
    try:
        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        res = mem.history(user_id=uid, limit=limit)
        return json.dumps([dict(r) for r in res], default=str, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_reinforce", description="Reinforce a memory's salience/importance by its ID")
async def projectbrain_reinforce(id: str) -> str:
    """Reinforce a memory's salience/importance by its ID."""
    try:
        m = await mem.get(id)
        if not m:
            return f"Memory {id} not found"
        new_sal = await applyRetrievalTraceReinforcementToMemory(id, m.get("salience", 0) or 0)
        db.execute("UPDATE memories SET salience=?, last_seen_at=? WHERE id=?", (new_sal, int(datetime.datetime.now().timestamp()*1000), id))
        db.commit()
        return f"Memory {id} reinforced. New salience: {new_sal:.4f}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_delete_all", description="Delete all memories for a user")
async def projectbrain_delete_all(user_id: str = None) -> str:
    """Delete all memories for a user."""
    try:
        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        await mem.delete_all(user_id=uid)
        return f"All memories deleted for user {uid or 'default'}"
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_stats", description="Retrieve cognitive memory engine statistics (total count, sectors, tags)")
async def projectbrain_stats(user_id: str = None) -> str:
    """Retrieve cognitive memory engine statistics (total count, sectors, tags)."""
    try:
        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        if uid:
            total_res = db.fetchone("SELECT count(*) as c FROM memories WHERE user_id=?", (uid,))
            sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories WHERE user_id=? GROUP BY primary_sector", (uid,))
            facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts WHERE user_id=?", (uid,))
        else:
            total_res = db.fetchone("SELECT count(*) as c FROM memories")
            sector_res = db.fetchall("SELECT primary_sector, count(*) as c FROM memories GROUP BY primary_sector")
            facts_res = db.fetchone("SELECT count(*) as c FROM temporal_facts")
        
        total_mems = total_res["c"] if total_res else 0
        total_facts = facts_res["c"] if facts_res else 0
        sectors = {r["primary_sector"]: r["c"] for r in sector_res}
        
        stats = {
            "total_memories": total_mems,
            "total_temporal_facts": total_facts,
            "sectors": sectors,
            "user_id": uid or "all"
        }
        return json.dumps(stats, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_ingest", description="Ingest data from an external connector (GitHub, Notion, Web Crawler, OneDrive, Google Drive/Sheets/Slides)")
async def projectbrain_ingest(source: str, creds: dict = None, filters: dict = None, user_id: str = None) -> str:
    """Ingest data from an external connector."""
    try:
        actual_creds = creds or {}
        actual_filters = filters or {}
        uid = user_id or os.getenv("PB_DEFAULT_USER_ID") or os.getenv("OM_DEFAULT_USER_ID") or os.getenv("PB_USER_ID") or os.getenv("OM_USER_ID")
        
        src = mem.source(source)
        if uid:
            src.user_id = uid
        
        await src.connect(**actual_creds)
        ids = await src.ingest_all(**actual_filters)
        return json.dumps({"ok": True, "count": len(ids), "memory_ids": ids}, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error: {str(e)}"

@mcp_server.tool(name="projectbrain_sync_codegraph", description="Synchronize local codegraph database structure (nodes/edges) to ProjectBrain by project ID and branch.")
async def projectbrain_sync_codegraph(project_id: str, project_path: str = None, branch: str = None, author: str = None) -> str:
    """
    Synchronize the local codegraph database (.codegraph/codegraph.db in current working directory or specified project path)
    to the ProjectBrain server under the specified project ID and branch.
    
    Args:
        project_id: Unique identifier for the project (e.g. 'projectbrain-py').
        project_path: Optional local path to the project directory containing .codegraph/
        branch: Optional branch name to associate with the synchronization. If not specified, tries to auto-detect the active git branch.
        author: Optional name of the user performing the sync.
    """
    import sqlite3
    import os
    import getpass
    import shutil
    import subprocess
    
    resolved_project_path = project_path
    if not resolved_project_path:
        # Check database for previously synced local project_path
        try:
            from ..core.db import db
            db.connect()
            cursor = db.conn.cursor()
            base_id = project_id.split(":", 1)[0] if ":" in project_id else project_id
            cursor.execute("SELECT project_path FROM projects WHERE id = ? OR id LIKE ? ORDER BY updated_at DESC LIMIT 1;", (project_id, f"{base_id}%"))
            row = cursor.fetchone()
            if row and row[0]:
                resolved_project_path = row[0]
            cursor.close()
        except Exception:
            pass

    if resolved_project_path:
        if os.path.isdir(resolved_project_path):
            db_path = os.path.join(resolved_project_path, ".codegraph", "codegraph.db")
        else:
            db_path = resolved_project_path
    else:
        db_path = os.path.join(os.getcwd(), ".codegraph", "codegraph.db")
        
    if not os.path.exists(db_path):
        # Check if codegraph command is available
        codegraph_bin = shutil.which("codegraph")
        if not codegraph_bin:
            # Force install it via npm globally
            try:
                res = subprocess.run(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True)
                if res.returncode != 0:
                    return (
                        f"Error: Codegraph database not found at {db_path}.\n"
                        f"Automatic installation of 'codegraph' CLI failed with exit code {res.returncode}.\n"
                        f"Command: npm install -g @colbymchenry/codegraph\n"
                        f"Stdout: {res.stdout}\n"
                        f"Stderr: {res.stderr}\n"
                        f"Please install it manually using:\n"
                        f"  npm install -g @colbymchenry/codegraph\n"
                        f"or, if permissions are required:\n"
                        f"  sudo npm install -g @colbymchenry/codegraph"
                    )
                # Re-check PATH
                codegraph_bin = shutil.which("codegraph")
                if not codegraph_bin:
                    return (
                        f"Error: Codegraph database not found at {db_path}.\n"
                        f"Successfully ran 'npm install -g @colbymchenry/codegraph' but the 'codegraph' executable is still not found in your PATH.\n"
                        f"Please ensure your npm global bin directory is included in your system's PATH."
                    )
            except Exception as inst_err:
                return (
                    f"Error: Codegraph database not found at {db_path}.\n"
                    f"Attempted to install 'codegraph' CLI but encountered an error: {str(inst_err)}.\n"
                    f"Please manually install it: npm install -g @colbymchenry/codegraph"
                )
        
        # Now we have codegraph_bin, let's run 'codegraph init' in the project directory
        target_dir = resolved_project_path if (resolved_project_path and os.path.isdir(resolved_project_path)) else os.getcwd()
        try:
            res_init = subprocess.run([codegraph_bin, "init"], cwd=target_dir, capture_output=True, text=True)
            if res_init.returncode != 0:
                return (
                    f"Error: Failed to initialize codegraph database via '{codegraph_bin} init' (exit code {res_init.returncode}) in directory {target_dir}.\n"
                    f"Stdout: {res_init.stdout}\n"
                    f"Stderr: {res_init.stderr}"
                )
        except Exception as init_err:
            return f"Error executing 'codegraph init' in directory {target_dir}: {str(init_err)}"
            
        # Re-check db path existence
        if not os.path.exists(db_path):
            return f"Error: Finished running 'codegraph init' but codegraph database file was not created at {db_path}."
        
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at FROM nodes;")
        nodes = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute("SELECT id, source, target, kind, metadata, line, col FROM edges;")
        edges = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        # Auto-detect git branch if branch is not explicitly provided
        resolved_branch = branch
        if not resolved_branch:
            try:
                git_bin = shutil.which("git")
                if git_bin and os.path.exists(os.path.join(target_dir, ".git")):
                    res_git = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=target_dir, capture_output=True, text=True)
                    if res_git.returncode == 0:
                        resolved_branch = res_git.stdout.strip()
            except Exception:
                pass
                
        if resolved_branch:
            if ":" in project_id:
                base_id, _ = project_id.split(":", 1)
                sync_project_id = f"{base_id}:{resolved_branch}"
            else:
                sync_project_id = f"{project_id}:{resolved_branch}"
        else:
            sync_project_id = project_id

        from ..server.routes.codegraph import sync_codegraph_data, SyncRequest
        
        resolved_author = author or os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous-mcp"
        
        req = SyncRequest(
            project_id=sync_project_id,
            project_name=sync_project_id,
            nodes=nodes,
            edges=edges,
            author=resolved_author,
            project_path=resolved_project_path
        )
        
        res = await sync_codegraph_data(req)
        return json.dumps(res, indent=2)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error synchronizing codegraph: {str(e)}"

@mcp_server.tool(name="projectbrain_diff_project_versions", description="Compare codebase structural symbols (codegraph) and context memories between two versions/branches of a project.")
async def projectbrain_diff_project_versions(base_project_id: str, target_project_id: str) -> str:
    """
    Compare codebase structural symbols (codegraph) and context memories between two versions/branches of a project.
    
    Args:
        base_project_id: The base project ID (e.g. 'E-commerce-jp-vn:main')
        target_project_id: The target project ID to compare against (e.g. 'E-commerce-jp-vn:feature-x')
    """
    try:
        from ..server.routes.codegraph import diff_projects
        
        # 1. Diff codegraph
        cg_diff = await diff_projects(base_project_id, target_project_id)
        added_nodes = cg_diff.get("added", [])
        deleted_nodes = cg_diff.get("deleted", [])
        modified_nodes = cg_diff.get("modified", [])
        
        # 2. Diff memories
        base_mems = db.fetchall("SELECT id, content, tags, primary_sector FROM memories WHERE user_id = ?", (base_project_id,))
        target_mems = db.fetchall("SELECT id, content, tags, primary_sector FROM memories WHERE user_id = ?", (target_project_id,))
        
        base_mem_map = {m["content"].strip(): m for m in base_mems}
        target_mem_map = {m["content"].strip(): m for m in target_mems}
        
        mems_added = [m for content, m in target_mem_map.items() if content not in base_mem_map]
        mems_deleted = [m for content, m in base_mem_map.items() if content not in target_mem_map]
        
        # 3. Format markdown report
        lines = []
        lines.append(f"# Project Version Comparison: `{base_project_id}` vs `{target_project_id}`")
        lines.append(f"Comparison generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # 3.1 Code Graph Section
        lines.append("## 📊 Codebase Structure Changes (Codegraph)")
        
        # Added Nodes
        if added_nodes:
            lines.append(f"### 🟢 Added Symbols ({len(added_nodes)})")
            # Group by kind
            by_kind = {}
            for n in added_nodes:
                by_kind.setdefault(n["kind"], []).append(n)
            for kind, items in by_kind.items():
                lines.append(f"#### {kind.capitalize()}")
                for item in items:
                    sig = f" `{item['signature']}`" if item.get('signature') else ""
                    lines.append(f"- **{item['name']}** in `{item['file_path']}`{sig}")
        else:
            lines.append("### 🟢 Added Symbols: None")
            
        # Deleted Nodes
        if deleted_nodes:
            lines.append(f"\n### 🔴 Deleted Symbols ({len(deleted_nodes)})")
            by_kind = {}
            for n in deleted_nodes:
                by_kind.setdefault(n["kind"], []).append(n)
            for kind, items in by_kind.items():
                lines.append(f"#### {kind.capitalize()}")
                for item in items:
                    lines.append(f"- **{item['name']}** (previously in `{item['file_path']}`)")
        else:
            lines.append("\n### 🔴 Deleted Symbols: None")
            
        # Modified Nodes
        if modified_nodes:
            lines.append(f"\n### 🟡 Modified Symbols ({len(modified_nodes)})")
            for m in modified_nodes:
                node = m["node"]
                changes = ", ".join(m["changes"])
                lines.append(f"- **{node['name']}** (`{node['kind']}`) in `{node['file_path']}`")
                lines.append(f"  - *Changes*: {changes}")
                if "signature" in m["changes"] and node.get("signature"):
                    lines.append(f"  - *New signature*: `{node['signature']}`")
        else:
            lines.append("\n### 🟡 Modified Symbols: None")
            
        # 3.2 Memories Section
        lines.append("\n## 🧠 Cognitive Memory Changes (ProjectBrain)")
        
        if mems_added:
            lines.append(f"### 🟢 Added Memories ({len(mems_added)})")
            for m in mems_added:
                tags_str = f" [Tags: {m['tags']}]" if m.get('tags') else ""
                lines.append(f"- [{m['primary_sector']}] \"{m['content']}\"{tags_str}")
        else:
            lines.append("### 🟢 Added Memories: None")
            
        if mems_deleted:
            lines.append(f"\n### 🔴 Deleted Memories ({len(mems_deleted)})")
            for m in mems_deleted:
                tags_str = f" [Tags: {m['tags']}]" if m.get('tags') else ""
                lines.append(f"- [{m['primary_sector']}] \"{m['content']}\"{tags_str}")
        else:
            lines.append("\n### 🔴 Deleted Memories: None")
            
        return "\n".join(lines)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return f"Error performing project comparison: {str(e)}"

async def run_mcp_server():
    await mcp_server.run_stdio_async()
