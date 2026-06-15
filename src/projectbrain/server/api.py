from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import time
import logging
from pathlib import Path
from ..core.config import env
from .routes import memory, health, sources, pm, mcp_registry, codegraph, auth

logger = logging.getLogger("server")

import json

class SSEFilter:
    def __init__(self, role: str, allowed_tools_str: str):
        self.role = role
        self.allowed_tools_str = allowed_tools_str or "*"

    def process_chunk(self, chunk: bytes) -> bytes:
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            return chunk
        
        if "result" not in text or "tools" not in text:
            return chunk
            
        lines = text.split("\n")
        output_lines = []
        for line in lines:
            if line.startswith("data:"):
                data_content = line[5:].strip()
                if data_content.startswith("{") and data_content.endswith("}"):
                    try:
                        res_json = json.loads(data_content)
                        # SSE JSON-RPC message can have 'result' with 'tools'
                        if "result" in res_json and isinstance(res_json["result"], dict) and "tools" in res_json["result"]:
                            tools = res_json["result"]["tools"]
                            filtered_tools = []
                            allowed_list = []
                            if self.allowed_tools_str != "*":
                                allowed_list = [t.strip() for t in self.allowed_tools_str.split(",") if t.strip()]
                            write_keywords = ["store", "delete", "sync", "ingest", "register", "reinforce"]
                            
                            for tool in tools:
                                tool_name = tool.get("name", "")
                                short_tool_name = tool_name.split("-", 1)[1] if "-" in tool_name else tool_name
                                if self.allowed_tools_str != "*":
                                    if tool_name not in allowed_list and short_tool_name not in allowed_list:
                                        continue
                                if self.role == "reader":
                                    if any(kw in tool_name.lower() for kw in write_keywords):
                                        continue
                                filtered_tools.append(tool)
                            res_json["result"]["tools"] = filtered_tools
                            line = f"data: {json.dumps(res_json)}"
                    except Exception:
                        pass
            output_lines.append(line)
        return "\n".join(output_lines).encode("utf-8")

def create_app() -> FastAPI:
    app = FastAPI(title="ProjectBrain API", version="1.2.2")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    @app.middleware("http")
    async def add_sse_headers(request: Request, call_next):
        response = await call_next(request)
        if "/mcp" in request.url.path or response.headers.get("content-type") == "text/event-stream":
            response.headers["X-Accel-Buffering"] = "no"
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.middleware("http")
    async def mcp_auth_middleware(request: Request, call_next):
        import json
        path = request.url.path
        
        # If client POSTs to /mcp/sse or /mcp by mistake, rewrite internally to /mcp/messages/
        if request.method == "POST" and (path.endswith("/sse") or path == "/mcp"):
            new_path = path.replace("/sse", "").rstrip("/") + "/messages/"
            request.scope["path"] = new_path
            path = new_path

        if not (path.startswith("/mcp") or path.startswith("/mcp-http")):
            return await call_next(request)
            
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "").strip()
        if not token:
            token = request.query_params.get("token") or request.query_params.get("api_key")

        if request.method == "GET":
            # Authenticate SSE connection start
            if "/sse" in path or path == "/mcp":
                if not token:
                    from fastapi.responses import PlainTextResponse
                    return PlainTextResponse("Authentication required. Please provide a token parameter.", status_code=403)
                
                from ..core.db import db
                db.connect()
                cursor = db.conn.cursor()
                sql = db.translate_query("SELECT username, role, allowed_tools FROM mcp_accounts WHERE token = ?;")
                cursor.execute(sql, (token,))
                row = cursor.fetchone()
                cursor.close()
                
                if not row:
                    from fastapi.responses import PlainTextResponse
                    return PlainTextResponse("Permission denied: Invalid token.", status_code=403)
                
                if db.is_postgres:
                    account = {"username": row[0], "role": row[1], "allowed_tools": row[2]}
                else:
                    account = dict(row)
                
                role = account["role"]
                allowed_tools_str = account["allowed_tools"] or "*"
                
                # Intercept stream and inject token into message POST endpoints, plus filter tools in SSE events
                response = await call_next(request)
                if response.status_code == 200 and hasattr(response, "body_iterator") and "text/event-stream" in response.headers.get("content-type", ""):
                    original_iterator = response.body_iterator
                    
                    async def wrapped_iterator():
                        import re
                        sse_filter = SSEFilter(role, allowed_tools_str)
                        async for chunk in original_iterator:
                            if isinstance(chunk, bytes):
                                # First apply endpoint / session_id injection
                                try:
                                    text = chunk.decode("utf-8")
                                    if "session_id=" in text:
                                        text = re.sub(
                                            r'(session_id=[a-zA-Z0-9\-]+)',
                                            rf'\1&token={token}',
                                            text
                                        )
                                        chunk = text.encode("utf-8")
                                except Exception:
                                    pass
                                
                                # Then filter tools list
                                if role == "reader" or allowed_tools_str != "*":
                                    chunk = sse_filter.process_chunk(chunk)
                            yield chunk
                             
                    response.body_iterator = wrapped_iterator()
                return response
            return await call_next(request)
            
        body_bytes = await request.body()
        
        async def receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        request._receive = receive
        
        try:
            payload = json.loads(body_bytes) if body_bytes else {}
        except:
            payload = {}
            
        method = payload.get("method")
        
        if method in ["tools/call", "tools/list", "resources/list", "prompts/list"]:
            if not token:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32001,
                        "message": "Authentication required. Please provide a Bearer token in the Authorization header or query params."
                    },
                    "id": payload.get("id")
                })
                
            from ..core.db import db
            db.connect()
            cursor = db.conn.cursor()
            sql = db.translate_query("SELECT username, role, allowed_tools FROM mcp_accounts WHERE token = ?;")
            cursor.execute(sql, (token,))
            row = cursor.fetchone()
            cursor.close()
            
            if not row:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32003,
                        "message": "Permission denied: Invalid access token."
                    },
                    "id": payload.get("id")
                })
            
            if db.is_postgres:
                account = {"username": row[0], "role": row[1], "allowed_tools": row[2]}
            else:
                account = dict(row)
                
            role = account["role"]
            allowed_tools_str = account["allowed_tools"] or "*"
            
            if method == "tools/call":
                params = payload.get("params", {})
                tool_name = params.get("name", "")
                
                # Check allowed tools list
                if allowed_tools_str != "*":
                    allowed_list = [t.strip() for t in allowed_tools_str.split(",") if t.strip()]
                    # Also support extension prefix checking, e.g. "projectbrain-projectbrain_query" -> "projectbrain_query"
                    short_tool_name = tool_name.split("-", 1)[1] if "-" in tool_name else tool_name
                    if tool_name not in allowed_list and short_tool_name not in allowed_list:
                        from fastapi.responses import JSONResponse
                        return JSONResponse(status_code=403, content={
                            "jsonrpc": "2.0",
                            "error": {
                                "code": -32003,
                                "message": f"Permission denied: Your account is not authorized to call tool '{tool_name}'."
                            },
                            "id": payload.get("id")
                        })
                        
                # Check read-only operations for reader role
                if role == "reader":
                    write_keywords = ["store", "delete", "sync", "ingest", "register", "reinforce"]
                    is_write_tool = any(kw in tool_name.lower() for kw in write_keywords)
                    if is_write_tool:
                        from fastapi.responses import JSONResponse
                        return JSONResponse(status_code=403, content={
                            "jsonrpc": "2.0",
                            "error": {
                                "code": -32003,
                                "message": f"Permission denied: Read-only accounts cannot execute modifying tool '{tool_name}'."
                            },
                            "id": payload.get("id")
                        })
            if method == "tools/list" and (allowed_tools_str != "*" or role == "reader"):
                response = await call_next(request)
                if response.status_code == 200:
                    response_body = b""
                    async for chunk in response.body_iterator:
                        response_body += chunk
                    try:
                        res_json = json.loads(response_body)
                        if "result" in res_json and "tools" in res_json["result"]:
                            tools = res_json["result"]["tools"]
                            filtered_tools = []
                            allowed_list = []
                            if allowed_tools_str != "*":
                                allowed_list = [t.strip() for t in allowed_tools_str.split(",") if t.strip()]
                            write_keywords = ["store", "delete", "sync", "ingest", "register", "reinforce"]
                            for tool in tools:
                                tool_name = tool.get("name", "")
                                short_tool_name = tool_name.split("-", 1)[1] if "-" in tool_name else tool_name
                                if allowed_tools_str != "*":
                                    if tool_name not in allowed_list and short_tool_name not in allowed_list:
                                        continue
                                if role == "reader":
                                    if any(kw in tool_name.lower() for kw in write_keywords):
                                        continue
                                filtered_tools.append(tool)
                            res_json["result"]["tools"] = filtered_tools
                            new_body = json.dumps(res_json).encode("utf-8")
                            from fastapi.responses import Response
                            # Reconstruct response and update content-length header
                            headers = dict(response.headers)
                            headers["content-length"] = str(len(new_body))
                            return Response(
                                content=new_body,
                                status_code=response.status_code,
                                headers=headers,
                                media_type=response.media_type
                            )
                    except Exception:
                        from fastapi.responses import Response
                        return Response(
                            content=response_body,
                            status_code=response.status_code,
                            headers=dict(response.headers),
                            media_type=response.media_type
                        )
                return response
                        
        return await call_next(request)
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        process_time = (time.time() - start) * 1000
        logger.info(f"{request.method} {request.url.path} - {response.status_code} ({process_time:.2f}ms)")
        return response
        
    @app.middleware("http")
    async def capture_mcp_project_id(request: Request, call_next):
        project_id = request.query_params.get("project_id") or request.query_params.get("user_id")
        token = None
        if project_id:
            try:
                from ..ai.mcp import mcp_request_project_id
                token = mcp_request_project_id.set(project_id)
            except Exception:
                pass
        try:
            return await call_next(request)
        finally:
            if token is not None:
                try:
                    from ..ai.mcp import mcp_request_project_id
                    mcp_request_project_id.reset(token)
                except Exception:
                    pass
        
    app.include_router(health.router)
    app.include_router(memory.router, prefix="/memory", tags=["memory"])
    app.include_router(mcp_registry.router, prefix="/memory/mcp", tags=["mcp"])
    app.include_router(codegraph.router, prefix="/codegraph", tags=["codegraph"])
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(sources.router)
    app.include_router(pm.router, prefix="/pm", tags=["pm"])

    # Mount FastMCP SSE app and Streamable HTTP app
    from ..ai.mcp import mcp_server
    app.mount("/mcp", mcp_server.sse_app())
    app.mount("/mcp-http", mcp_server.streamable_http_app())
    logger.info("Mounted MCP SSE app at /mcp and Streamable HTTP app at /mcp-http")

    # Serve static dashboard
    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/dashboard", StaticFiles(directory=str(static_path), html=True), name="dashboard")
        logger.info("Mounted static dashboard at /dashboard")
    else:
        logger.warning(f"Static directory not found at {static_path}, dashboard mount skipped.")

    @app.on_event("startup")
    async def startup():
        logger.info(f"ProjectBrain Server running on port {env.database_url}")

    return app
