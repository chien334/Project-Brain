# ProjectBrain MCP Extensions Specification & Standards

This directory serves as the unified folder for custom Model Context Protocol (MCP) extensions. By placing an extension directory here, it is dynamically detected, loaded, and registered to the main ProjectBrain MCP server.

---

## 📁 Directory Structure Standard

Every extension folder under `extensions_mcp/` must follow this structure:

```
extensions_mcp/
└── <your_extension_name>/
    ├── extension.json        # Extension metadata and dependency definitions (Required)
    ├── server.py             # Entrypoint file exposing FastMCP instance (Required)
    ├── client.py             # HTTP client helper for remote execution (Optional)
    └── ...                   # Custom utility files and modules
```

---

## 📄 Extension Metadata (`extension.json`)

The `extension.json` file describes the extension, its entrypoint, environment variables, and dependencies.

```json
{
  "name": "my-extension",
  "version": "1.0.0",
  "description": "Exposes custom tools for my tasks.",
  "entrypoint": "server.py",
  "env_vars": ["MY_ENV_VAR"],
  "dependencies": ["requests>=2.0.0"]
}
```

- `name`: Unique lowercase name.
- `entrypoint`: The python file where the `FastMCP` instance `mcp` is defined.
- `env_vars`: List of environment variables that the extension requires.
- `dependencies`: List of pip packages that must be installed.

---

## 🐍 Entrypoint Code (`server.py`)

The entrypoint file (typically `server.py`) must instantiate a `FastMCP` server named `mcp` and decorate its tools.

```python
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# The instance name MUST be 'mcp'
mcp = FastMCP("my_extension")

@mcp.tool(name="my_custom_tool")
async def my_custom_tool(x: int) -> str:
    """A custom tool description."""
    return f"Result: {x}"
```

---

## 🌐 Running Locally vs Remotely

Extensions can be executed in two modes:

### 1. Local Stdio Mode
When running the ProjectBrain MCP server locally via `python3 -m projectbrain.main mcp`, the loader dynamically registers all tools. AI clients like Cursor or Claude Desktop interact with them directly via Stdio.

### 2. Remote HTTP Client Mode
When ProjectBrain is hosted on a remote server, all extensions are mounted to the server's HTTP endpoints (SSE at `/mcp` and JSON-RPC at `/mcp-http`).
You can use the built-in HTTP client wrapper (`client.py`) to execute these tools remotely from any environment.

Example HTTP call pattern for tool execution:
```python
import httpx

def invoke_remote_tool(server_url: str, tool_name: str, arguments: dict):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    response = httpx.post(f"{server_url}/mcp-http/mcp", json=payload)
    return response.json()
```
