from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import time
import logging
from pathlib import Path
from ..core.config import env
from .routes import memory, health, sources, pm, mcp_registry, codegraph

logger = logging.getLogger("server")

def create_app() -> FastAPI:
    app = FastAPI(title="ProjectBrain API", version="1.2.2")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
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
