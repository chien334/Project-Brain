# Community 2: Server

> **Leiden Community Cluster 2**  
> Contains 29 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `server.py` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L1` | Community 2 |
| `call_gemini()` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L10` | Community 2 |
| `translate_code_comments()` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L54` | Community 2 |
| `recommend_refactor()` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L113` | Community 2 |
| `batch_scan_logic()` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L166` | Community 2 |
| `plan_execution_phases()` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L182` | Community 2 |
| `Scans a code file, extracts and translates comments and docstrings, and returns` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L58` | Community 2 |
| `Analyzes a legacy source file and recommends modern tech-stack refactoring based` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L117` | Community 2 |
| `Recursively batch-scans a codebase, analyzes each function's business logic usin` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L170` | Community 2 |
| `Analyzes drafts and plans sequential refactoring/migration phases.          Perf` | code | `extensions_mcp/codebase_migration_helper/server.py` | `L186` | Community 2 |
| `client.py` | code | `extensions_mcp/codebase_migration_helper/client.py` | `L1` | Community 2 |
| `call_remote_tool()` | code | `extensions_mcp/codebase_migration_helper/client.py` | `L14` | Community 2 |
| `main()` | code | `extensions_mcp/codebase_migration_helper/client.py` | `L48` | Community 2 |
| `Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.` | code | `extensions_mcp/codebase_migration_helper/client.py` | `L15` | Community 2 |
| `python_codegraph.py` | code | `extensions_mcp/codebase_migration_helper/python_codegraph.py` | `L1` | Community 2 |
| `setup_db()` | code | `extensions_mcp/codebase_migration_helper/python_codegraph.py` | `L11` | Community 2 |
| `parse_project()` | code | `extensions_mcp/codebase_migration_helper/python_codegraph.py` | `L99` | Community 2 |
| `main()` | code | `extensions_mcp/codebase_migration_helper/python_codegraph.py` | `L233` | Community 2 |
| `__init__.py` | code | `extensions_mcp/codebase_migration_helper/__init__.py` | `L1` | Community 2 |
| `batch_scan_helper.py` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L1` | Community 2 |
| `get_project_functions()` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L11` | Community 2 |
| `extract_function_code()` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L42` | Community 2 |
| `analyze_business_logic()` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L57` | Community 2 |
| `run_batch_scan_logic()` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L119` | Community 2 |
| `run_phase_planning()` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L193` | Community 2 |
| `main()` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L344` | Community 2 |
| `Retrieves all function and method nodes from the codegraph database for a projec` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L12` | Community 2 |
| `Reads the source code lines corresponding to a specific function node.` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L43` | Community 2 |
| `Calls Gemini API to analyze the business logic of a single function snippet.` | code | `extensions_mcp/codebase_migration_helper/batch_scan_helper.py` | `L58` | Community 2 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `server` | `contains` | `server_call_gemini` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_translate_code_comments` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_recommend_refactor` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_batch_scan_logic` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_plan_execution_phases` | EXTRACTED | 1.0 |
| `server_translate_code_comments` | `calls` | `server_call_gemini` | EXTRACTED | 1.0 |
| `server_recommend_refactor` | `calls` | `server_call_gemini` | EXTRACTED | 1.0 |
| `server_rationale_58` | `rationale_for` | `server_translate_code_comments` | EXTRACTED | 1.0 |
| `server_rationale_117` | `rationale_for` | `server_recommend_refactor` | EXTRACTED | 1.0 |
| `server_rationale_170` | `rationale_for` | `server_batch_scan_logic` | EXTRACTED | 1.0 |
| `server_rationale_186` | `rationale_for` | `server_plan_execution_phases` | EXTRACTED | 1.0 |
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

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `server` | `os` | `imports` | EXTRACTED |
| `server` | `httpx` | `imports` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
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
| `server` | `openpyxl` | `imports` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `server_read_xlsx` | `contains` | EXTRACTED |
| `server` | `server_read_csv` | `contains` | EXTRACTED |
