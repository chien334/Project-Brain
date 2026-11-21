# Community 9: Client

> **Leiden Community Cluster 9**  
> Contains 7 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `server.py` | code | `extensions_mcp/template/server.py` | `L1` | Community 9 |
| `hello_world()` | code | `extensions_mcp/template/server.py` | `L9` | Community 9 |
| `A template greeting tool.` | code | `extensions_mcp/template/server.py` | `L12` | Community 9 |
| `client.py` | code | `extensions_mcp/template/client.py` | `L1` | Community 9 |
| `call_remote_tool()` | code | `extensions_mcp/template/client.py` | `L14` | Community 9 |
| `main()` | code | `extensions_mcp/template/client.py` | `L48` | Community 9 |
| `Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.` | code | `extensions_mcp/template/client.py` | `L15` | Community 9 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `server` | `contains` | `server_hello_world` | EXTRACTED | 1.0 |
| `server_rationale_12` | `rationale_for` | `server_hello_world` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_main` | `calls` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_rationale_15` | `rationale_for` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_rationale_15` | `rationale_for` | `client_call_remote_tool` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `server` | `os` | `imports` | EXTRACTED |
| `server` | `httpx` | `imports` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
| `server` | `server_call_gemini` | `contains` | EXTRACTED |
| `server` | `server_translate_code_comments` | `contains` | EXTRACTED |
| `server` | `server_recommend_refactor` | `contains` | EXTRACTED |
| `server` | `server_batch_scan_logic` | `contains` | EXTRACTED |
| `server` | `server_plan_execution_phases` | `contains` | EXTRACTED |
| `server` | `pathlib` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `mcp_types` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `server_ocr_engine_status` | `contains` | EXTRACTED |
| `server` | `server_ocr_image_to_markdown` | `contains` | EXTRACTED |
| `server` | `server_ocr_pdf_to_markdown` | `contains` | EXTRACTED |
| `server` | `server_main` | `contains` | EXTRACTED |
| `server` | `csv` | `imports` | EXTRACTED |
| `server` | `json` | `imports` | EXTRACTED |
| `server` | `os` | `imports` | EXTRACTED |
| `server` | `re` | `imports` | EXTRACTED |
| `server` | `pathlib` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
