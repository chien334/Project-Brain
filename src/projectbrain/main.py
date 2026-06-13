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
            async with httpx.AsyncClient() as client:
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

def run_codegraph_sync(project_id: str, server_url: str = None, project_path: str = None):
    import sqlite3
    import httpx
    import os
    
    if project_path:
        if os.path.isdir(project_path):
            db_path = os.path.join(project_path, ".codegraph", "codegraph.db")
        else:
            db_path = project_path
    else:
        db_path = os.path.join(os.getcwd(), ".codegraph", "codegraph.db")
        
    if not os.path.exists(db_path):
        import shutil
        import subprocess
        print(f"Codegraph database not found at {db_path}. Checking for 'codegraph' CLI...")
        codegraph_bin = shutil.which("codegraph")
        if not codegraph_bin:
            print("'codegraph' CLI not found. Attempting to install it globally via npm...")
            try:
                res = subprocess.run(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True)
                if res.returncode != 0:
                    print(f"Error: Automatic global installation of 'codegraph' CLI failed with exit code {res.returncode}.")
                    print(f"Stdout: {res.stdout}")
                    print(f"Stderr: {res.stderr}")
                    print("Please install it manually: npm install -g @colbymchenry/codegraph (or with sudo)")
                    sys.exit(1)
                codegraph_bin = shutil.which("codegraph")
                if not codegraph_bin:
                    print("Error: Successfully installed 'codegraph' via npm, but 'codegraph' command is still not found in PATH.")
                    print("Please verify your npm global bin folder is in your PATH.")
                    sys.exit(1)
            except Exception as inst_err:
                print(f"Error attempting to install 'codegraph' CLI: {str(inst_err)}")
                sys.exit(1)
        
        target_dir = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
        print(f"Initializing codegraph database in directory '{target_dir}' using '{codegraph_bin} init'...")
        try:
            res_init = subprocess.run([codegraph_bin, "init"], cwd=target_dir, capture_output=True, text=True)
            if res_init.returncode != 0:
                print(f"Error running 'codegraph init' (exit code {res_init.returncode}):")
                print(f"Stdout: {res_init.stdout}")
                print(f"Stderr: {res_init.stderr}")
                sys.exit(1)
        except Exception as init_err:
            print(f"Error running 'codegraph init': {str(init_err)}")
            sys.exit(1)
            
        if not os.path.exists(db_path):
            print(f"Error: 'codegraph init' completed but database file was still not found at {db_path}.")
            sys.exit(1)
        
    server_url = server_url or os.getenv("PB_URL") or os.getenv("PB_API_URL") or os.getenv("OM_URL") or os.getenv("OM_API_URL") or "http://localhost:8080"
    
    print(f"Reading local codegraph database at {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at FROM nodes;")
        nodes = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute("SELECT id, source, target, kind, metadata, line, col FROM edges;")
        edges = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        print(f"Found {len(nodes)} nodes and {len(edges)} edges. Synchronizing to {server_url}/codegraph/sync...")
        
        import getpass
        author = os.getenv("PB_USER_NAME") or os.getenv("OM_USER_NAME") or os.getenv("USER") or getpass.getuser() or "anonymous"
        
        resp = httpx.post(
            f"{server_url}/codegraph/sync",
            json={
                "project_id": project_id,
                "project_name": project_id,
                "nodes": nodes,
                "edges": edges,
                "author": author
            },
            timeout=120.0
        )
        
        if resp.status_code == 200:
            print("Successfully synchronized codegraph data!")
            print(resp.json().get("message", ""))
        else:
            print(f"Sync failed (Status {resp.status_code}): {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"Error during sync: {e}")
        sys.exit(1)

async def async_ingest_files(project_id: str, dir_path: str):
    import os
    from .main import Memory
    
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
            # Extra safeguard: check if path contains any excluded directory names
            parts = file_path.split(os.sep)
            if any(p in exclude_dirs for p in parts):
                continue
            
            rel_path = os.path.relpath(file_path, dir_path)
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

def run_ingest_files(project_id: str, dir_path: str):
    import os
    import asyncio
    if not os.path.exists(dir_path):
        print(f"Error: Directory '{dir_path}' does not exist.")
        sys.exit(1)
    asyncio.run(async_ingest_files(project_id, dir_path))

if __name__ == "__main__":
    import sys
    import os
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        run_server()
    elif len(sys.argv) > 1 and sys.argv[1] == "mcp":
        run_mcp()
    elif len(sys.argv) > 1 and sys.argv[1] == "codegraph-sync":
        if len(sys.argv) < 3:
            print("Usage: python -m projectbrain.main codegraph-sync <project_id> [server_url] [project_path]")
            sys.exit(1)
        project_id = sys.argv[2]
        server_url = sys.argv[3] if len(sys.argv) > 3 else None
        project_path = sys.argv[4] if len(sys.argv) > 4 else None
        run_codegraph_sync(project_id, server_url, project_path)
    elif len(sys.argv) > 1 and sys.argv[1] == "ingest-files":
        if len(sys.argv) < 4:
            print("Usage: python -m projectbrain.main ingest-files <project_id> <dir_path>")
            sys.exit(1)
        project_id = sys.argv[2]
        dir_path = sys.argv[3]
        run_ingest_files(project_id, dir_path)
    else:
        print("ProjectBrain Python SDK / Server")
        print("Usage:")
        print("  python -m projectbrain.main serve                                 # Start REST API & Dashboard")
        print("  python -m projectbrain.main mcp                                   # Start stdio MCP server")
        print("  python -m projectbrain.main codegraph-sync <project_id> [url] [project_path] # Sync local codegraph to server")
        print("  python -m projectbrain.main ingest-files <project_id> <dir_path>   # Ingest codebase files into memories")
