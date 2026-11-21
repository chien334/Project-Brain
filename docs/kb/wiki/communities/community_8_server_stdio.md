# Community 8: Server Stdio

> **Leiden Community Cluster 8**  
> Contains 18 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `client.py` | code | `extensions_mcp/convert_pptx/client.py` | `L1` | Community 8 |
| `call_remote_tool()` | code | `extensions_mcp/convert_pptx/client.py` | `L14` | Community 8 |
| `main()` | code | `extensions_mcp/convert_pptx/client.py` | `L48` | Community 8 |
| `Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.` | code | `extensions_mcp/convert_pptx/client.py` | `L15` | Community 8 |
| `__init__.py` | code | `extensions_mcp/convert_pptx/__init__.py` | `L1` | Community 8 |
| `server_stdio.py` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L1` | Community 8 |
| `PptxFilePath` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L12` | Community 8 |
| `BaseModel` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `extensions_mcp/convert_pptx/server_stdio.py:1` | Community 8 |
| `ExtractImagesInput` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L15` | Community 8 |
| `CreatePptxInput` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L19` | Community 8 |
| `extract_text()` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L30` | Community 8 |
| `create_presentation()` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L51` | Community 8 |
| `extract_images()` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L74` | Community 8 |
| `to_markdown()` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L104` | Community 8 |
| `Extracts all text from a PPTX file.` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L31` | Community 8 |
| `Creates a new PPTX file with a title and content.` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L52` | Community 8 |
| `Extracts all images from a PPTX file and saves them to a directory.` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L75` | Community 8 |
| `Converts a PPTX file to a Markdown document.` | code | `extensions_mcp/convert_pptx/server_stdio.py` | `L105` | Community 8 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
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
| `client_rationale_15` | `rationale_for` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client_rationale_15` | `rationale_for` | `client_call_remote_tool` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
