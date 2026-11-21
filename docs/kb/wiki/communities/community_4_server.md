# Community 4: Server

> **Leiden Community Cluster 4**  
> Contains 10 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `server.py` | code | `extensions_mcp/ocr_local/server.py` | `L1` | Community 4 |
| `ocr_engine_status()` | code | `extensions_mcp/ocr_local/server.py` | `L29` | Community 4 |
| `ocr_image_to_markdown()` | code | `extensions_mcp/ocr_local/server.py` | `L81` | Community 4 |
| `ocr_pdf_to_markdown()` | code | `extensions_mcp/ocr_local/server.py` | `L129` | Community 4 |
| `main()` | code | `extensions_mcp/ocr_local/server.py` | `L226` | Community 4 |
| `Report local OCR engine status: PaddleOCR/PyMuPDF installation, language, and en` | code | `extensions_mcp/ocr_local/server.py` | `L30` | Community 4 |
| `OCR a single image file using local PaddleOCR (no API key required).      Args:` | code | `extensions_mcp/ocr_local/server.py` | `L87` | Community 4 |
| `Convert PDF to Markdown by rendering each page and running local PaddleOCR on th` | code | `extensions_mcp/ocr_local/server.py` | `L141` | Community 4 |
| `__init__.py` | code | `extensions_mcp/ocr_local/__init__.py` | `L1` | Community 4 |
| `ocr-local: OCR offline (CPU) bang PaddleOCR cho anh va PDF (render -> OCR).` | code | `extensions_mcp/ocr_local/__init__.py` | `L1` | Community 4 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `server` | `contains` | `server_ocr_engine_status` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_ocr_image_to_markdown` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_ocr_pdf_to_markdown` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_main` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_main` | EXTRACTED | 1.0 |
| `server_rationale_30` | `rationale_for` | `server_ocr_engine_status` | EXTRACTED | 1.0 |
| `server_rationale_87` | `rationale_for` | `server_ocr_image_to_markdown` | EXTRACTED | 1.0 |
| `server_rationale_141` | `rationale_for` | `server_ocr_pdf_to_markdown` | EXTRACTED | 1.0 |
| `init_rationale_1` | `rationale_for` | `init` | EXTRACTED | 1.0 |
| `init_rationale_1` | `rationale_for` | `init` | EXTRACTED | 1.0 |
| `init_rationale_1` | `rationale_for` | `init` | EXTRACTED | 1.0 |
| `init_rationale_1` | `rationale_for` | `init` | EXTRACTED | 1.0 |

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
| `server` | `csv` | `imports` | EXTRACTED |
| `server` | `json` | `imports` | EXTRACTED |
| `server` | `os` | `imports` | EXTRACTED |
| `server` | `re` | `imports` | EXTRACTED |
| `server` | `pathlib` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
| `server` | `openpyxl` | `imports` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `server_read_xlsx` | `contains` | EXTRACTED |
