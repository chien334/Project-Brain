# ProjectBrain Developer Runbook & Workflows

This document outlines standard developer workflows, setup scripts, testing procedures, and extension integration guides for ProjectBrain.

---

## 🏃 1. Running the Project

### Start the REST API & Web Dashboard
The main FastAPI server serves the REST API, the SSE MCP mount, and the web dashboard:
```bash
python3 -m projectbrain.main serve
```
- **Web Dashboard**: Open [http://localhost:8080/dashboard/](http://localhost:8080/dashboard/)
- **REST Endpoints**: `/health`, `/memory/stats`, `/memory/history`, `/memory/diff`, `/codegraph/projects`, `/codegraph/diff`, `/sources/upload`.
- **MCP SSE Connection**: `http://localhost:8080/mcp/sse`
- **MCP Streamable HTTP JSON-RPC**: `http://localhost:8080/mcp-http/mcp`

### Start the Local Stdio MCP Server
AI clients (such as Claude Desktop or Cursor) connect to the local Stdio server:
```bash
python3 -m projectbrain.main mcp
```

---

## 🧪 2. Running Automated Tests

ProjectBrain uses `pytest` for unit and integration testing.

```bash
# Run all tests
pytest

# Run a specific test suite
pytest tests/test_mcp_integration.py
pytest tests/test_memory_diff.py
pytest tests/test_compat.py
pytest tests/test_sync_autoinstall.py
```

---

## ➕ 3. Creating a New MCP Extension

Follow these steps to add a new custom MCP extension:

### Step 3.1: Copy the Template
Copy the boilerplate template directory to create your extension:
```bash
cp -r extensions_mcp/template extensions_mcp/my_custom_extension
```

### Step 3.2: Configure Metadata (`extension.json`)
Edit `extensions_mcp/my_custom_extension/extension.json` to specify details:
```json
{
  "name": "my-custom-extension",
  "version": "1.0.0",
  "description": "Custom tools for processing my tasks.",
  "entrypoint": "server.py",
  "env_vars": ["MY_REQUIRED_SECRET"],
  "dependencies": ["requests>=2.0.0"]
}
```

### Step 3.3: Implement the Tools (`server.py`)
Add your tools using the `@mcp.tool()` decorator in `server.py`:
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-custom-extension")

@mcp.tool(name="greet_developer")
async def greet_developer(name: str) -> str:
    """Greets the developer."""
    return f"Hello, {name}!"
```

### Step 3.4: Verify Registration
Run the integration test or startup script to verify that your new tools load successfully:
```bash
python3 -c "from projectbrain.ai.mcp import mcp_server; print([t.name for t in mcp_server._tool_manager.list_tools()])"
```

---

## 🌐 4. Running Tools Remotely (HTTP Client)

Each extension includes a `client.py` wrapper that allows invoking tools remotely via JSON-RPC POST requests to the Streamable HTTP endpoint:

```bash
# Call a tool remotely on localhost (default)
python3 extensions_mcp/convert_excel/client.py excel_list_sheets '{"file_path": "data.xlsx"}'

# Call a tool remotely on a hosted server
python3 extensions_mcp/convert_pdf/client.py pdf_get_page_count '{"file_path": "manual.pdf"}' --url http://5.104.85.38:8080
```
