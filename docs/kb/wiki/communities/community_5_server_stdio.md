# Community 5: Server Stdio

> **Leiden Community Cluster 5**  
> Contains 18 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `extract_images_ocr.py` | code | `extensions_mcp/convert_docx/extract_images_ocr.py` | `L1` | Community 5 |
| `client.py` | code | `extensions_mcp/convert_docx/client.py` | `L1` | Community 5 |
| `call_remote_tool()` | code | `extensions_mcp/convert_docx/client.py` | `L14` | Community 5 |
| `main()` | code | `extensions_mcp/convert_docx/client.py` | `L48` | Community 5 |
| `Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.` | code | `extensions_mcp/convert_docx/client.py` | `L15` | Community 5 |
| `extract_images_from_docx.py` | code | `extensions_mcp/convert_docx/extract_images_from_docx.py` | `L1` | Community 5 |
| `__init__.py` | code | `extensions_mcp/convert_docx/__init__.py` | `L1` | Community 5 |
| `server_stdio.py` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L1` | Community 5 |
| `DocxFilePath` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L9` | Community 5 |
| `BaseModel` | code | `extensions_mcp/convert_docx/server_stdio.py` | `extensions_mcp/convert_docx/server_stdio.py:1` | Community 5 |
| `CreateDocxInput` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L12` | Community 5 |
| `extract_text()` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L22` | Community 5 |
| `extract_tables()` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L39` | Community 5 |
| `create_document()` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L63` | Community 5 |
| `Extracts all text from a DOCX file.` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L23` | Community 5 |
| `Extracts all tables from a DOCX file.` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L40` | Community 5 |
| `Creates a new DOCX file with the given content.` | code | `extensions_mcp/convert_docx/server_stdio.py` | `L64` | Community 5 |
| `extract_images_paddleocr.py` | code | `extensions_mcp/convert_docx/extract_images_paddleocr.py` | `L1` | Community 5 |

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
| `extract_images_ocr` | `docx` | `imports_from` | EXTRACTED |
| `extract_images_ocr` | `os` | `imports` | EXTRACTED |
| `extract_images_ocr` | `pil` | `imports_from` | EXTRACTED |
| `extract_images_ocr` | `io` | `imports` | EXTRACTED |
| `extract_images_ocr` | `easyocr` | `imports` | EXTRACTED |
| `extract_images_ocr` | `easyocr` | `imports` | EXTRACTED |
| `extract_images_ocr` | `numpy` | `imports` | EXTRACTED |
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
