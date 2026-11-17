import logging
import os
import sys
from typing import List, Dict, Optional, Any
from .core.db import db, q
from .memory.hsg import hsg_query, add_hsg_memory
from .ops.ingest import ingest_document
from .openai_handler import OpenAIRegistrar

logger = logging.getLogger("projectbrain")

class Memory:
    def __init__(self, user: str = None, mode: str = None, url: str = None, api_key: str = None):
        self.default_user = user
        self.mode = mode or os.getenv("PB_MODE") or os.getenv("OM_MODE", "local")
        self.url = url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
        self.api_key = api_key or os.getenv("PB_API_KEY") or os.getenv("OM_API_KEY", "")
        
        if self.mode == "local":
            db.connect()
            self._openai = OpenAIRegistrar(self)

    @property
    def openai(self):
        if self.mode == "remote":
            raise NotImplementedError("OpenAI handler integration is not available in remote mode.")
        return self._openai

    async def add(self, content: str, user_id: str = None, **kwargs) -> Dict[str, Any]:
        uid = user_id or self.default_user
        if self.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.url}/memory/add",
                    json={
                        "content": content,
                        "user_id": uid,
                        "tags": kwargs.get("tags") or [],
                        "metadata": kwargs.get("meta") or {}
                    },
                    headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("data") or data
        else:
            res = await ingest_document("text", content, meta=kwargs.get("meta"), user_id=uid, tags=kwargs.get("tags"))
            if "root_memory_id" in res:
                res["id"] = res["root_memory_id"]
            return res

    async def search(self, query: str, user_id: str = None, limit: int = 10, **kwargs) -> List[Dict[str, Any]]:
        uid = user_id or self.default_user
        if self.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.url}/memory/search",
                    json={
                        "query": query,
                        "user_id": uid,
                        "limit": limit,
                        "filters": kwargs
                    },
                    headers=headers
                )
                resp.raise_for_status()
                return resp.json().get("results") or []
        else:
            filters = kwargs.copy()
            filters["user_id"] = uid
            return await hsg_query(query, limit, filters)

    async def get(self, memory_id: str):
        if self.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.url}/memory/{memory_id}", headers=headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json().get("data")
        else:
            row = q.get_mem(memory_id)
            if not row: return None
            d = dict(row)
            for k in ["vector", "norm_vector", "compressed_vec", "mean_vec"]:
                if k in d: d.pop(k)
            return d

    async def delete(self, memory_id: str):
        if self.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient() as client:
                resp = await client.delete(f"{self.url}/memory/{memory_id}", headers=headers)
                resp.raise_for_status()
        else:
            q.del_mem(memory_id)

    async def delete_all(self, user_id: str = None):
        uid = user_id or self.default_user
        if self.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.url}/memory/delete_all",
                    json={"user_id": uid},
                    headers=headers
                )
                resp.raise_for_status()
        else:
            if uid:
                q.del_mem_by_user(uid)

    def history(self, user_id: str = None, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        uid = user_id or self.default_user
        if self.mode == "remote":
            import httpx
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            with httpx.Client() as client:
                resp = client.get(
                    f"{self.url}/memory/history",
                    params={"user_id": uid, "limit": limit, "offset": offset},
                    headers=headers
                )
                resp.raise_for_status()
                return resp.json().get("history") or []
        else:
            rows = q.all_mem_by_user(uid, limit, offset)
            res = []
            for r in rows:
                d = dict(r)
                for k in ["vector", "norm_vector", "compressed_vec", "mean_vec"]:
                    if k in d: d.pop(k)
                res.append(d)
            return res

    def source(self, name: str):
        """
        get a pre-configured source connector.

        usage:
            github = mem.source("github")
            await github.connect(token="ghp_...")
            await github.ingest_all(repo="owner/repo")

        available sources: github, notion, google_drive, google_sheets,
                          google_slides, onedrive, web_crawler
        """
        from . import connectors

        sources = {
            "github": connectors.github_connector,
            "notion": connectors.notion_connector,
            "google_drive": connectors.google_drive_connector,
            "google_sheets": connectors.google_sheets_connector,
            "google_slides": connectors.google_slides_connector,
            "onedrive": connectors.onedrive_connector,
            "web_crawler": connectors.web_crawler_connector,
        }

        if name not in sources:
            raise ValueError(f"unknown source: {name}. available: {list(sources.keys())}")

        return sources[name](user_id=self.default_user)

def run_mcp():
    import asyncio
    from .ai.mcp import run_mcp_server
    try:
        asyncio.run(run_mcp_server())
    except KeyboardInterrupt:
        pass

def run_server():
    import uvicorn
    from .server.api import create_app
    app = create_app()
    # Read port from env or default to 8080
    port = int(os.getenv("PB_PORT") or os.getenv("OM_PORT", 8080))
    print(f"Starting ProjectBrain Server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)

def run_codegraph_sync(project_id: str, server_url: str = None, project_path: str = None, branch: str = None, sync_memories: bool = False, upload_db: bool = False):
    import sqlite3
    import httpx
    import os
    
    target_dir = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
    
    if project_path:
        if os.path.isdir(project_path):
            db_path = os.path.join(project_path, ".codegraph", "codegraph.db")
        else:
            db_path = project_path
    else:
        db_path = os.path.join(os.getcwd(), ".codegraph", "codegraph.db")
        
    force_python = os.getenv("PB_USE_PURE_PYTHON_PARSER", "false").lower() in ("true", "1", "yes")
    codegraph_bin = None
    
    if not os.path.exists(db_path) or force_python:
        if force_python:
            try:
                from extensions_mcp.codebase_migration_helper.python_codegraph import main as run_python_parser
                run_python_parser(target_dir)
            except Exception as py_err:
                print(f"Error: Forced to use pure-Python parser, but execution failed: {str(py_err)}")
                sys.exit(1)
        else:
            import shutil
            import subprocess
            codegraph_bin = shutil.which("codegraph")
            if not codegraph_bin:
                print(f"Notice: .codegraph/codegraph.db not found. Synchronizing seamlessly via GraphifyExtractor...")
                run_graphify_sync(
                    project_id=project_id,
                    workspace_path=target_dir,
                    branch=branch or "main",
                    server_url=server_url,
                )
                return
            else:
                print(f"Initializing codegraph database in directory '{target_dir}' using '{codegraph_bin} init'...")
                try:
                    res_init = subprocess.run([codegraph_bin, "init"], cwd=target_dir, capture_output=True, text=True)
                    if res_init.returncode != 0:
                        print(f"Warning: 'codegraph init' failed with exit code {res_init.returncode}. Falling back to GraphifyExtractor...")
                        run_graphify_sync(
                            project_id=project_id,
                            workspace_path=target_dir,
                            branch=branch or "main",
                            server_url=server_url,
                        )
                        return
                except Exception as init_err:
                    print(f"Warning: Executing 'codegraph init' failed: {str(init_err)}. Falling back to GraphifyExtractor...")
                    run_graphify_sync(
                        project_id=project_id,
                        workspace_path=target_dir,
                        branch=branch or "main",
                        server_url=server_url,
                    )
                    return
        
        if not os.path.exists(db_path):
            print(f"Notice: .codegraph/codegraph.db absent. Synchronizing seamlessly via GraphifyExtractor...")
            run_graphify_sync(
                project_id=project_id,
                workspace_path=target_dir,
                branch=branch or "main",
                server_url=server_url,
                scoped_paths=None,
                git_dirty=False,
                local_only=False,
            )
            return
        
    server_url = server_url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
    
    # Auto-detect git branch if branch is not explicitly provided
    resolved_branch = branch
    if not resolved_branch:
        try:
            import shutil
            import subprocess
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
        print(f"Syncing branch '{resolved_branch}' (Project ID: {sync_project_id})...")
    else:
        sync_project_id = project_id
        
    import getpass
    author = os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous"

    if upload_db:
        print(f"Synchronizing raw database file {db_path} to {server_url}/codegraph/upload-db...")
        try:
            with open(db_path, "rb") as f:
                files = {"file": (os.path.basename(db_path), f, "application/octet-stream")}
                data = {
                    "project_id": sync_project_id,
                    "project_name": sync_project_id,
                    "author": author,
                    "project_path": os.path.abspath(target_dir)
                }
                resp = httpx.post(
                    f"{server_url}/codegraph/upload-db",
                    files=files,
                    data=data,
                    timeout=120.0
                )
            
            if resp.status_code == 200:
                print("Successfully synchronized codegraph data via file upload backup mechanism!")
                print(resp.json().get("message", ""))
                
                if sync_memories:
                    print("Proceeding to ingest codebase files as memories...")
                    import asyncio
                    asyncio.run(async_ingest_files(sync_project_id, target_dir, server_url))
            else:
                print(f"Sync via upload failed (Status {resp.status_code}): {resp.text}")
                sys.exit(1)
        except Exception as e:
            print(f"Error during upload sync: {e}")
            sys.exit(1)
        return

    print(f"Reading local codegraph database at {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn_cursor = conn.cursor()
        
        conn_cursor.execute("SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at FROM nodes;")
        nodes = [dict(row) for row in conn_cursor.fetchall()]
        
        conn_cursor.execute("SELECT id, source, target, kind, metadata, line, col FROM edges;")
        edges = [dict(row) for row in conn_cursor.fetchall()]
        
        conn.close()
            
        print(f"Found {len(nodes)} nodes and {len(edges)} edges. Synchronizing to {server_url}/codegraph/sync...")
        
        resp = httpx.post(
            f"{server_url}/codegraph/sync",
            json={
                "project_id": sync_project_id,
                "project_name": sync_project_id,
                "nodes": nodes,
                "edges": edges,
                "author": author,
                "project_path": os.path.abspath(target_dir)
            },
            timeout=120.0
        )
        
        if resp.status_code == 200:
            print("Successfully synchronized codegraph data!")
            print(resp.json().get("message", ""))
            
            if sync_memories:
                print("Proceeding to ingest codebase files as memories...")
                import asyncio
                asyncio.run(async_ingest_files(sync_project_id, target_dir, server_url))
        else:
            print(f"Sync failed (Status {resp.status_code}): {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"Error during sync: {e}")
        sys.exit(1)

async def async_ingest_files(project_id: str, dir_path: str, server_url: Optional[str] = None):
    import os
    from .main import Memory
    
    if server_url:
        mem = Memory(url=server_url, mode="remote")
    else:
        mem = Memory()
    exclude_dirs = {
        ".git", "node_modules", "dist", "build", "__pycache__", 
        ".pytest_cache", ".codegraph", "cloned_repos", "bin", "obj", 
        ".vs", "uploads", "venv", ".venv", ".next", ".nuxt", ".out", 
        "target", "vendor", "staticwebassets"
    }
    exclude_exts = {
        ".db", ".sqlite", ".sqlite3",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
        ".exe", ".bin", ".dll", ".pdb", ".so", ".dylib",
        ".woff", ".woff2", ".ttf", ".eot",
        ".cache", ".up2date", ".log",
        ".suo", ".user", ".map", ".lock",
        ".mp3", ".mp4", ".wav", ".avi", ".mov", ".flac", ".ogg",
        ".dll.config", ".exe.config"
    }
    
    count = 0
    print(f"Ingesting text files from '{dir_path}' as memories for project '{project_id}'...", flush=True)
    
    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in exclude_exts:
                continue
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, dir_path)
            # Extra safeguard: check if relative path contains any excluded directory names
            parts = rel_path.split(os.sep)
            if any(p in exclude_dirs for p in parts):
                continue
            
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if not content.strip():
                    continue
                meta = {
                    "source": "cli_ingest",
                    "filename": file,
                    "file_path": rel_path,
                    "project_id": project_id
                }
                res = await mem.add(
                    content=f"File: {rel_path}\n\n{content}",
                    user_id=project_id,
                    meta=meta,
                    tags=["codebase", "file"]
                )
                count += 1
                print(f"Ingested {rel_path}", flush=True)
            except Exception as e:
                print(f"Failed to ingest {rel_path}: {e}", flush=True)
    print(f"Successfully ingested {count} codebase files as memories!", flush=True)

def run_ingest_files(project_id: str, dir_path: str, server_url: Optional[str] = None):
    import os
    import asyncio
    asyncio.run(async_ingest_files(project_id, dir_path, server_url))

def run_upload_file(project_id: str, file_path: str, server_url: str = None, tags: str = "", author: str = None):
    import os
    import sys
    import httpx
    import mimetypes

    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' does not exist.")
        sys.exit(1)

    if not os.path.isfile(file_path):
        print(f"Error: '{file_path}' is not a file.")
        sys.exit(1)

    server_url = server_url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
    
    filename = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or "application/octet-stream"

    print(f"Uploading file '{filename}' to {server_url}/sources/upload...")

    try:
        with open(file_path, "rb") as f:
            files = {"file": (filename, f, mime_type)}
            data = {
                "project_id": project_id,
                "tags": tags,
                "author": author or "cli"
            }
            
            headers = {}
            api_key = os.getenv("PB_API_KEY") or os.getenv("OM_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            resp = httpx.post(
                f"{server_url}/sources/upload",
                files=files,
                data=data,
                headers=headers,
                timeout=120.0
            )

        if resp.status_code == 200:
            print("Successfully uploaded and indexed document!")
            import json
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Upload failed (Status {resp.status_code}): {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"Error uploading file: {e}")
        sys.exit(1)

def run_graphify_sync(
    project_id: str,
    workspace_path: str = ".",
    branch: str = "main",
    server_url: Optional[str] = None,
    scoped_paths: Optional[List[str]] = None,
    git_dirty: bool = False,
    local_only: bool = False,
    cluster: bool = True
):
    import os
    import sys
    import json
    import httpx
    from .graphify_engine.extractor import GraphifyExtractor

    target_dir = os.path.abspath(workspace_path)
    if not os.path.isdir(target_dir):
        print(f"Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)

    print(f"Scanning codebase at '{target_dir}' via GraphifyExtractor (branch: {branch})...")
    extractor = GraphifyExtractor()
    delta = extractor.extract_workspace(
        workspace_path=target_dir,
        project_id=project_id,
        branch=branch,
        scoped_paths=scoped_paths,
        git_dirty_only=git_dirty,
        include_docs=True
    )
    if cluster and delta.nodes:
        try:
            delta = extractor.cluster_graph(delta)
        except Exception as ce:
            print(f"Clustering warning: {ce}")

    local_path = extractor.save_local_partial_graph(
        delta,
        output_dir=os.path.join(target_dir, ".projectbrain", "local_graph")
    )
    print(f"Cached partial graph at {local_path} ({len(delta.nodes)} nodes, {len(delta.edges)} edges).")

    if local_only:
        print("Completed local-only Graphify sync.")
        return

    server_url = server_url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
    server_url = server_url.rstrip("/")
    api_key = os.getenv("PB_API_KEY") or os.getenv("OM_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    payload = {
        "project_id": project_id,
        "branch": branch,
        "nodes": [n.to_dict() if hasattr(n, "to_dict") else n.model_dump() for n in delta.nodes],
        "edges": [e.to_dict() if hasattr(e, "to_dict") else e.model_dump() for e in delta.edges],
        "hyperedges": [h.to_dict() if hasattr(h, "to_dict") else h.model_dump() for h in delta.hyperedges],
        "node_tombstones": delta.node_tombstones,
        "edge_tombstones": delta.edge_tombstones,
        "hyperedge_tombstones": delta.hyperedge_tombstones,
        "synced_files": delta.synced_files,
        "partial": bool(scoped_paths or git_dirty),
        "author": os.getenv("USER", "cli"),
        "cluster": False,
        "project_path": target_dir
    }

    print(f"Synchronizing graph delta to {server_url}/graphify/sync...")
    try:
        resp = httpx.post(f"{server_url}/graphify/sync", json=payload, headers=headers, timeout=120.0)
        if resp.status_code == 200:
            print("Successfully synchronized Graphify graph to server!")
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Sync failed (Status {resp.status_code}): {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"Error during graphify-sync: {e}")
        sys.exit(1)


def run_graphify_diff(
    project_id: str,
    base_branch: str = "main",
    target_branch: Optional[str] = None,
    local: bool = False,
    workspace_path: str = ".",
    server_url: Optional[str] = None
):
    import os
    import sys
    import json
    import httpx

    server_url = server_url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
    server_url = server_url.rstrip("/")
    api_key = os.getenv("PB_API_KEY") or os.getenv("OM_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    if local:
        from .graphify_engine.hybrid_resolver import HybridResolver
        from .graphify_engine.store import get_graph_store
        target_dir = os.path.abspath(workspace_path)
        local_path = os.path.join(target_dir, ".projectbrain", "local_graph", "partial_graph.json")
        if server_url and (os.getenv("PB_MODE") == "remote" or server_url != "http://localhost:8080"):
            resolver = HybridResolver(remote_url=server_url, api_token=api_key)
        else:
            store = get_graph_store()
            resolver = HybridResolver(store=store)
        res = resolver.diff_local_vs_remote(
            project_id=project_id,
            branch=base_branch,
            local_path=local_path if os.path.exists(local_path) else None,
        )
        print("Local vs Remote Graph Diff:")
        print(json.dumps(res, indent=2))
        return

    params = {"project_id": project_id, "base_branch": base_branch}
    if target_branch:
        params["target_branch"] = target_branch
    else:
        params["branch"] = base_branch

    try:
        resp = httpx.get(f"{server_url}/graphify/diff", params=params, headers=headers, timeout=30.0)
        if resp.status_code == 200:
            print("Graph Diff Result:")
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Diff failed (Status {resp.status_code}): {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"Error during graphify-diff: {e}")
        sys.exit(1)


def run_upload_doc(
    project_id: str,
    file_path: str,
    branch: str = "main",
    server_url: Optional[str] = None,
    author: Optional[str] = None
):
    import os
    import sys
    import asyncio
    import json
    from .graphify_engine.doc_pipeline import DocumentPipeline

    if not os.path.isfile(file_path):
        print(f"Error: File '{file_path}' does not exist or is not a file.")
        sys.exit(1)

    server_url = server_url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
    server_url = server_url.rstrip("/")
    api_key = os.getenv("PB_API_KEY") or os.getenv("OM_API_KEY")

    print(f"Processing document '{file_path}' via DocumentPipeline (branch: {branch})...")
    pipeline = DocumentPipeline()
    payload = asyncio.run(pipeline.process_file(
        file_path=file_path,
        project_id=project_id,
        branch=branch,
        author=author or os.getenv("USER", "cli")
    ))

    print(f"Pushing processed document and graph delta to {server_url}/sources/push-processed...")
    res = asyncio.run(pipeline.push_processed_to_server(
        server_url=server_url,
        payload=payload,
        token=api_key
    ))
    print("Successfully ingested document and synchronized graph delta!")
    print(json.dumps(res, indent=2))


def run_build_kb(
    project_id: str = "project-brain",
    workspace_path: str = ".",
    branch: str = "main",
    persist_memory: bool = True,
    generate_office_exports: bool = True,
    output_dir: Optional[str] = None,
    db_path: Optional[str] = None,
):
    import os
    import sys
    import asyncio
    import json
    from .graphify_engine.kb_linker import UnifiedKBBuilder

    target_dir = os.path.abspath(workspace_path)
    if not os.path.isdir(target_dir):
        print(f"Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)

    print(f"Building unified knowledge base for '{project_id}' at '{target_dir}' (branch: {branch})...")
    builder = UnifiedKBBuilder(workspace_path=target_dir)
    res = asyncio.run(builder.build_unified_kb(
        project_id=project_id,
        branch=branch,
        persist_memory=persist_memory,
        output_dir=output_dir,
        generate_office_exports=generate_office_exports,
        db_path=db_path,
    ))
    print("Successfully built Unified Knowledge Base!")
    print(json.dumps(res, indent=2))


def run_kb_trace(
    project_id: str,
    target: str,
    branch: str = "main",
    workspace_path: str = ".",
    db_path: Optional[str] = None,
):
    import os
    import json
    from .graphify_engine.kb_linker import UnifiedKBBuilder

    target_dir = os.path.abspath(workspace_path)
    builder = UnifiedKBBuilder(workspace_path=target_dir)
    effective_db = (
        db_path
        or os.getenv("OM_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or (os.path.join(target_dir, ".projectbrain", "kb", "projectbrain_kb.db")
            if os.path.exists(os.path.join(target_dir, ".projectbrain", "kb", "projectbrain_kb.db"))
            else None)
    )
    if effective_db:
        from .graphify_engine.store import get_graph_store
        builder.store = get_graph_store(db_path=effective_db)
    res = builder.trace_code_and_docs(
        project_id=project_id,
        target=target,
        branch=branch,
    )
    print(json.dumps(res.model_dump(), indent=2))


def run_kb_coverage(
    project_id: str = "project-brain",
    branch: str = "main",
    workspace_path: str = ".",
    db_path: Optional[str] = None,
):
    import os
    import json
    from .graphify_engine.kb_linker import UnifiedKBBuilder

    target_dir = os.path.abspath(workspace_path)
    builder = UnifiedKBBuilder(workspace_path=target_dir)
    effective_db = (
        db_path
        or os.getenv("OM_DB_PATH")
        or os.getenv("PB_DB_PATH")
        or (os.path.join(target_dir, ".projectbrain", "kb", "projectbrain_kb.db")
            if os.path.exists(os.path.join(target_dir, ".projectbrain", "kb", "projectbrain_kb.db"))
            else None)
    )
    if effective_db:
        from .graphify_engine.store import get_graph_store
        builder.store = get_graph_store(db_path=effective_db)
    res = builder.get_kb_coverage_report(
        project_id=project_id,
        branch=branch,
    )
    print(json.dumps(res.model_dump(), indent=2))


if __name__ == "__main__":
    import sys
    import os

    def _get_flag(flag_name: str, default: Any = None) -> Any:
        for i, a in enumerate(sys.argv):
            if a == flag_name:
                if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
                    return sys.argv[i + 1]
                return True
            if a.startswith(f"{flag_name}="):
                return a.split("=", 1)[1]
        return default

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        run_server()
    elif len(sys.argv) > 1 and sys.argv[1] == "mcp":
        run_mcp()
    elif len(sys.argv) > 1 and sys.argv[1] == "graphify-sync":
        if len(sys.argv) < 3:
            print("Usage: python -m projectbrain.main graphify-sync <project_id> [path] [--branch B] [--url URL] [--scoped P1,P2] [--dirty] [--local-only]")
            sys.exit(1)
        project_id = sys.argv[2]
        pos_args = [a for a in sys.argv[3:] if not a.startswith("-")]
        workspace_path = pos_args[0] if pos_args else "."
        branch = _get_flag("--branch", "main")
        server_url = _get_flag("--url", None)
        scoped_raw = _get_flag("--scoped", None)
        scoped_paths = [p.strip() for p in scoped_raw.split(",") if p.strip()] if scoped_raw else None
        git_dirty = any(a in sys.argv for a in ["--dirty", "-d"])
        local_only = any(a in sys.argv for a in ["--local-only", "-l"])
        run_graphify_sync(project_id, workspace_path, branch, server_url, scoped_paths, git_dirty, local_only)
    elif len(sys.argv) > 1 and sys.argv[1] == "graphify-diff":
        if len(sys.argv) < 3:
            print("Usage: python -m projectbrain.main graphify-diff <project_id> [--base main] [--target branch] [--local] [--path .] [--url URL]")
            sys.exit(1)
        project_id = sys.argv[2]
        base_branch = _get_flag("--base", "main")
        target_branch = _get_flag("--target", None)
        local = any(a in sys.argv for a in ["--local", "-l"])
        workspace_path = _get_flag("--path", ".")
        server_url = _get_flag("--url", None)
        run_graphify_diff(project_id, base_branch, target_branch, local, workspace_path, server_url)
    elif len(sys.argv) > 1 and sys.argv[1] == "upload-doc":
        if len(sys.argv) < 4:
            print("Usage: python -m projectbrain.main upload-doc <project_id> <file_path> [--branch main] [--url URL] [--author A]")
            sys.exit(1)
        project_id = sys.argv[2]
        file_path = sys.argv[3]
        branch = _get_flag("--branch", "main")
        server_url = _get_flag("--url", None)
        author = _get_flag("--author", None)
        run_upload_doc(project_id, file_path, branch, server_url, author)
    elif len(sys.argv) > 1 and sys.argv[1] == "codegraph-sync":
        if len(sys.argv) < 3:
            print("Usage: python -m projectbrain.main codegraph-sync <project_id> [server_url] [project_path] [branch]")
            sys.exit(1)
        project_id = sys.argv[2]
        server_url = sys.argv[3] if len(sys.argv) > 3 else None
        project_path = sys.argv[4] if len(sys.argv) > 4 else None
        branch = sys.argv[5] if len(sys.argv) > 5 else None
        
        sync_memories = any(arg in sys.argv for arg in ["--sync-memories", "-m"])
        upload_db = any(arg in sys.argv for arg in ["--upload-db", "-u"])
        
        # Filter out flags from positional arguments
        flags = ["--sync-memories", "-m", "--upload-db", "-u"]
        if server_url in flags: server_url = None
        if project_path in flags: project_path = None
        if branch in flags: branch = None
        
        run_codegraph_sync(project_id, server_url, project_path, branch, sync_memories, upload_db)
    elif len(sys.argv) > 1 and sys.argv[1] == "ingest-files":
        if len(sys.argv) < 4:
            print("Usage: python -m projectbrain.main ingest-files <project_id> <dir_path>")
            sys.exit(1)
        project_id = sys.argv[2]
        dir_path = sys.argv[3]
        run_ingest_files(project_id, dir_path)
    elif len(sys.argv) > 1 and sys.argv[1] == "upload-file":
        if len(sys.argv) < 4:
            print("Usage: python -m projectbrain.main upload-file <project_id> <file_path> [url] [tags] [author]")
            sys.exit(1)
        project_id = sys.argv[2]
        file_path = sys.argv[3]
        server_url = sys.argv[4] if len(sys.argv) > 4 else None
        tags = sys.argv[5] if len(sys.argv) > 5 else ""
        author = sys.argv[6] if len(sys.argv) > 6 else None
        run_upload_file(project_id, file_path, server_url, tags, author)
    elif len(sys.argv) > 1 and sys.argv[1] == "build-kb":
        pos_args = [a for a in sys.argv[2:] if not a.startswith("-")]
        project_id = pos_args[0] if len(pos_args) > 0 else "project-brain"
        workspace_path = pos_args[1] if len(pos_args) > 1 else "."
        branch = _get_flag("--branch", "main")
        persist_memory = not any(a in sys.argv for a in ["--no-memory"])
        generate_office = not any(a in sys.argv for a in ["--no-office"])
        output_dir = _get_flag("--output", None)
        db_path = _get_flag("--db-path", None)
        run_build_kb(project_id, workspace_path, branch, persist_memory, generate_office, output_dir, db_path)
    elif len(sys.argv) > 1 and sys.argv[1] == "kb-trace":
        pos_args = [a for a in sys.argv[2:] if not a.startswith("-")]
        if len(pos_args) < 2:
            print("Usage: python -m projectbrain.main kb-trace <project_id> <target> [--branch B] [--path PATH] [--db-path DB]")
            sys.exit(1)
        project_id = pos_args[0]
        target = pos_args[1]
        branch = _get_flag("--branch", "main")
        workspace_path = _get_flag("--path", ".")
        db_path = _get_flag("--db-path", None)
        run_kb_trace(project_id, target, branch, workspace_path, db_path)
    elif len(sys.argv) > 1 and sys.argv[1] == "kb-coverage":
        pos_args = [a for a in sys.argv[2:] if not a.startswith("-")]
        if len(pos_args) < 1:
            print("Usage: python -m projectbrain.main kb-coverage <project_id> [--branch B] [--path PATH] [--db-path DB]")
            sys.exit(1)
        project_id = pos_args[0]
        branch = _get_flag("--branch", "main")
        workspace_path = _get_flag("--path", ".")
        db_path = _get_flag("--db-path", None)
        run_kb_coverage(project_id, branch, workspace_path, db_path)
    else:
        print("ProjectBrain Python SDK / Server")
        print("Usage:")
        print("  python -m projectbrain.main serve                                         # Start REST API & Dashboard")
        print("  python -m projectbrain.main mcp                                           # Start stdio MCP server")
        print("  python -m projectbrain.main graphify-sync <project_id> [path] [--branch B] [--url URL] [--scoped P1,P2] [--dirty] [--local-only]")
        print("  python -m projectbrain.main graphify-diff <project_id> [--base main] [--target branch] [--local] [--path .] [--url URL]")
        print("  python -m projectbrain.main upload-doc <project_id> <file_path> [--branch main] [--url URL] [--author A]")
        print("  python -m projectbrain.main codegraph-sync <project_id> [url] [path] [branch] [-m] [-u] # Sync local codegraph and memories to server (use -u to upload sqlite db file)")
        print("  python -m projectbrain.main ingest-files <project_id> <dir_path>           # Ingest codebase files into memories")
        print("  python -m projectbrain.main upload-file <project_id> <file_path> [url] [tags] [author] # Upload a document to RAG server")
        print("  python -m projectbrain.main build-kb [project_id] [path] [--branch B] [--no-memory] [--no-office] [--output DIR] [--db-path DB]")
        print("  python -m projectbrain.main kb-trace <project_id> <target> [--branch B] [--path PATH] [--db-path DB]")
        print("  python -m projectbrain.main kb-coverage <project_id> [--branch B] [--path PATH] [--db-path DB]")
