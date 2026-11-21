# Community 1: Server Stdio

> **Leiden Community Cluster 1**  
> Contains 28 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `client.py` | code | `extensions_mcp/convert_pdf/client.py` | `L1` | Community 1 |
| `call_remote_tool()` | code | `extensions_mcp/convert_pdf/client.py` | `L14` | Community 1 |
| `main()` | code | `extensions_mcp/convert_pdf/client.py` | `L48` | Community 1 |
| `Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.` | code | `extensions_mcp/convert_pdf/client.py` | `L15` | Community 1 |
| `__init__.py` | code | `extensions_mcp/convert_pdf/__init__.py` | `L1` | Community 1 |
| `server_stdio.py` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L1` | Community 1 |
| `_extract_text_from_pdf()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L27` | Community 1 |
| `_extract_tables_from_pdf()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L48` | Community 1 |
| `_table_to_markdown()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L69` | Community 1 |
| `_extract_images_from_pdf()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L97` | Community 1 |
| `_pdf_to_markdown()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L143` | Community 1 |
| `pdf_extract_text()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L212` | Community 1 |
| `pdf_extract_tables()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L244` | Community 1 |
| `pdf_extract_images()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L281` | Community 1 |
| `pdf_convert_to_markdown()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L322` | Community 1 |
| `pdf_get_page_count()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L382` | Community 1 |
| `pdf_extract_page_text()` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L421` | Community 1 |
| `Extract text from each page of a PDF file.          Returns: {page_number: text_` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L28` | Community 1 |
| `Extract tables from each page of a PDF file.          Returns: {page_number: [ta` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L49` | Community 1 |
| `Convert a table (list of lists) to Markdown format.` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L70` | Community 1 |
| `Extract images from each page of a PDF file.          Returns: {page_number: [im` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L98` | Community 1 |
| `Convert PDF to Markdown format with text, optional tables, and optional images.` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L144` | Community 1 |
| `Extract text from all pages of a PDF file.      Returns JSON with page-by-page t` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L213` | Community 1 |
| `Extract tables from all pages of a PDF file.      Returns JSON with page-by-page` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L245` | Community 1 |
| `Extract images from all pages of a PDF file.      Saves images to the specified` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L288` | Community 1 |
| `Convert a PDF file to Markdown format.      Extracts text from all pages and opt` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L337` | Community 1 |
| `Get the total number of pages in a PDF file.      Returns JSON: {"file": str, "p` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L383` | Community 1 |
| `Extract text from a specific page of a PDF file.      Returns JSON: {"file": str` | code | `extensions_mcp/convert_pdf/server_stdio.py` | `L428` | Community 1 |

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
