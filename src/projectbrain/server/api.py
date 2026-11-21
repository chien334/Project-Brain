from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..core.config import env
from .routes import memory, health, sources, pm, mcp_registry, codegraph, auth, graphify, kb

logger = logging.getLogger("server")

_WRITE_TOOL_KEYWORDS = (
    "store",
    "delete",
    "sync",
    "ingest",
    "register",
    "reinforce",
    "push",
    "promote",
    "upload",
    "build",
    "generate",
    "export",
    "create",
    "write",
    "extract_images",
    "batch_scan",
    "plan_execution",
    "ocr_image",
    "ocr_pdf",
    "img2md_extract",
)

_EXPLICIT_WRITE_TOOLS = {
    "docx_create_document",
    "markdown_convert_to_excel",
    "pptx_create_presentation",
    "pdf_extract_images",
    "pptx_extract_images",
    "ocr_image_to_markdown",
    "ocr_pdf_to_markdown",
    "img2md_extract_image",
    "img2md_extract_folder",
    "migration_batch_scan_logic",
    "migration_plan_execution_phases",
}

_READ_ONLY_POST_ROUTES = {
    "/memory/search",
    "/codegraph/query",
    "/graphify/query",
    "/graphify/hybrid-query",
    "/graphify/local-diff",
    "/pm/search",
}

_PROTECTED_REST_PREFIXES = (
    "/memory",
    "/codegraph",
    "/graphify",
    "/kb",
    "/sources",
    "/pm",
)


def _is_write_tool(tool_name: str, tool_obj: Optional[Dict[str, Any]] = None) -> bool:
    """Return True if a FastMCP tool performs mutations or disk writes."""
    if isinstance(tool_obj, dict):
        annotations = tool_obj.get("annotations")
        if isinstance(annotations, dict) and annotations.get("readOnlyHint") is False:
            return True
        if tool_obj.get("readOnlyHint") is False:
            return True
    elif tool_obj is not None:
        annotations = getattr(tool_obj, "annotations", None)
        if annotations is not None:
            if isinstance(annotations, dict) and annotations.get("readOnlyHint") is False:
                return True
            if getattr(annotations, "readOnlyHint", None) is False:
                return True
    elif tool_name:
        try:
            from ..ai.mcp import mcp_server
            tools_map = getattr(mcp_server._tool_manager, "_tools", None) or {}
            reg_tool = tools_map.get(tool_name)
            if reg_tool is not None:
                annotations = getattr(reg_tool, "annotations", None)
                if isinstance(annotations, dict) and annotations.get("readOnlyHint") is False:
                    return True
                if getattr(annotations, "readOnlyHint", None) is False:
                    return True
        except Exception:
            pass

    name_lower = (tool_name or "").lower().strip()
    if not name_lower:
        return False

    short_name = name_lower.split("-", 1)[1] if "-" in name_lower else name_lower
    tail_name = name_lower.rsplit("-", 1)[-1] if "-" in name_lower else name_lower

    if name_lower in _EXPLICIT_WRITE_TOOLS or short_name in _EXPLICIT_WRITE_TOOLS or tail_name in _EXPLICIT_WRITE_TOOLS:
        return True

    if any(kw in name_lower for kw in _WRITE_TOOL_KEYWORDS):
        return True

    return False


def _redact_database_url(db_url: Optional[str]) -> str:
    """Return a credential-free database descriptor safe for logs."""
    if not db_url:
        return "sqlite"
    if db_url.startswith("sqlite"):
        return "sqlite"
    try:
        parsed = urlparse(db_url)
        if parsed.scheme and parsed.hostname:
            port_part = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port_part}"
        if parsed.scheme:
            return f"{parsed.scheme}://[REDACTED]"
    except Exception:
        pass
    return re.sub(r"://[^/@]+@", "://[REDACTED]@", str(db_url))


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
                        if "result" in res_json and isinstance(res_json["result"], dict) and "tools" in res_json["result"]:
                            tools = res_json["result"]["tools"]
                            filtered_tools = []
                            allowed_list = []
                            if self.allowed_tools_str != "*":
                                allowed_list = [t.strip() for t in self.allowed_tools_str.split(",") if t.strip()]

                            for tool in tools:
                                tool_name = tool.get("name", "")
                                short_tool_name = tool_name.split("-", 1)[1] if "-" in tool_name else tool_name
                                if self.allowed_tools_str != "*":
                                    if tool_name not in allowed_list and short_tool_name not in allowed_list:
                                        continue
                                if self.role == "reader" and _is_write_tool(tool_name, tool):
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
    async def rest_auth_middleware(request: Request, call_next):
        if os.getenv("PB_REQUIRE_REST_AUTH", "false").lower() not in ("true", "1", "yes"):
            return await call_next(request)

        method = request.method.upper()
        if method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        normalized_path = path.rstrip("/") or "/"

        # Exempt public/docs/dashboard/MCP endpoints
        if (
            normalized_path in ("/health", "/auth/verify", "/docs", "/openapi.json", "/redoc")
            or path.startswith("/docs/")
            or path.startswith("/redoc/")
            or path.startswith("/dashboard")
            or path.startswith("/mcp")
        ):
            return await call_next(request)

        is_admin_route = (
            normalized_path == "/auth"
            or path.startswith("/auth/")
            or normalized_path == "/memory/mcp"
            or path.startswith("/memory/mcp/")
        )
        is_protected_route = is_admin_route or any(
            normalized_path == prefix or path.startswith(prefix + "/")
            for prefix in _PROTECTED_REST_PREFIXES
        )

        if not is_protected_route:
            return await call_next(request)

        account = auth.get_authenticated_account(request)
        if not account:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required: Valid Bearer token required."},
            )

        role = account.get("role", "")
        if is_admin_route and role != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "Permission denied: Admin role required."},
            )

        if role == "reader" and method in ("POST", "PUT", "PATCH", "DELETE"):
            if not (method == "POST" and normalized_path in _READ_ONLY_POST_ROUTES):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Permission denied: Read-only accounts cannot perform mutating operations."},
                )

        return await call_next(request)

    @app.middleware("http")
    async def mcp_auth_middleware(request: Request, call_next):
        path = request.url.path

        # If client POSTs to /mcp/sse or /mcp by mistake, rewrite internally to /mcp/messages/
        if request.method == "POST" and (path.endswith("/sse") or path == "/mcp"):
            new_path = path.replace("/sse", "").rstrip("/") + "/messages/"
            request.scope["path"] = new_path
            path = new_path

        if not (path.startswith("/mcp") or path.startswith("/mcp-http")):
            return await call_next(request)

        token = auth.extract_bearer_token(request)

        if request.method == "GET":
            # Authenticate SSE connection start
            if "/sse" in path or path == "/mcp":
                if not token:
                    return PlainTextResponse("Authentication required. Please provide a token parameter.", status_code=403)

                account = auth.get_authenticated_account(request)
                if not account:
                    return PlainTextResponse("Permission denied: Invalid token.", status_code=403)

                role = account["role"]
                allowed_tools_str = account["allowed_tools"] or "*"

                # Intercept stream and inject token into message POST endpoints, plus filter tools in SSE events
                response = await call_next(request)
                if response.status_code == 200 and hasattr(response, "body_iterator") and "text/event-stream" in response.headers.get("content-type", ""):
                    original_iterator = response.body_iterator

                    async def wrapped_iterator():
                        sse_filter = SSEFilter(role, allowed_tools_str)
                        async for chunk in original_iterator:
                            if isinstance(chunk, bytes):
                                # First apply endpoint / session_id injection
                                try:
                                    text = chunk.decode("utf-8")
                                    if "session_id=" in text:
                                        text = re.sub(
                                            r"(session_id=[a-zA-Z0-9\-]+)",
                                            rf"\1&token={token}",
                                            text,
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
        except Exception:
            payload = {}

        method = payload.get("method")

        if method in ["tools/call", "tools/list", "resources/list", "prompts/list"]:
            if not token:
                return JSONResponse(status_code=403, content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32001,
                        "message": "Authentication required. Please provide a Bearer token in the Authorization header or query params."
                    },
                    "id": payload.get("id")
                })

            account = auth.get_authenticated_account(request)
            if not account:
                return JSONResponse(status_code=403, content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32003,
                        "message": "Permission denied: Invalid access token."
                    },
                    "id": payload.get("id")
                })

            role = account["role"]
            allowed_tools_str = account["allowed_tools"] or "*"

            if method == "tools/call":
                params = payload.get("params", {})
                tool_name = params.get("name", "")

                # Check allowed tools list
                if allowed_tools_str != "*":
                    allowed_list = [t.strip() for t in allowed_tools_str.split(",") if t.strip()]
                    short_tool_name = tool_name.split("-", 1)[1] if "-" in tool_name else tool_name
                    if tool_name not in allowed_list and short_tool_name not in allowed_list:
                        return JSONResponse(status_code=403, content={
                            "jsonrpc": "2.0",
                            "error": {
                                "code": -32003,
                                "message": f"Permission denied: Your account is not authorized to call tool '{tool_name}'."
                            },
                            "id": payload.get("id")
                        })

                # Check read-only operations for reader role
                if role == "reader" and _is_write_tool(tool_name):
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
                            for tool in tools:
                                tool_name = tool.get("name", "")
                                short_tool_name = tool_name.split("-", 1)[1] if "-" in tool_name else tool_name
                                if allowed_tools_str != "*":
                                    if tool_name not in allowed_list and short_tool_name not in allowed_list:
                                        continue
                                if role == "reader" and _is_write_tool(tool_name, tool):
                                    continue
                                filtered_tools.append(tool)
                            res_json["result"]["tools"] = filtered_tools
                            new_body = json.dumps(res_json).encode("utf-8")
                            headers = dict(response.headers)
                            headers["content-length"] = str(len(new_body))
                            return Response(
                                content=new_body,
                                status_code=response.status_code,
                                headers=headers,
                                media_type=response.media_type,
                            )
                    except Exception:
                        return Response(
                            content=response_body,
                            status_code=response.status_code,
                            headers=dict(response.headers),
                            media_type=response.media_type,
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
    app.include_router(graphify.router)
    app.include_router(kb.router)
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
        logger.info(f"ProjectBrain Server running (database: {_redact_database_url(getattr(env, 'database_url', None))})")

    return app
