# API & FastMCP Tools Reference

> **Complete Catalog of Verified REST Endpoints, FastMCP Tools, and CLI Commands**  
> Deterministically extracted via Python AST static analysis from source code.

## 1. FastAPI REST Endpoints

| HTTP Method | Route Path | Handler Function | Source Location | Parameters | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/auth/accounts` | `list_accounts` | `src/projectbrain/server/routes/auth.py:88` | `request` | API endpoint |
| `POST` | `/auth/accounts` | `create_or_update_account` | `src/projectbrain/server/routes/auth.py:120` | `req, request` | API endpoint |
| `DELETE` | `/auth/accounts/{username}` | `delete_account` | `src/projectbrain/server/routes/auth.py:172` | `username, request` | API endpoint |
| `GET` | `/auth/tools` | `list_available_tools` | `src/projectbrain/server/routes/auth.py:193` | `None` | API endpoint |
| `GET` | `/auth/verify` | `verify_token` | `src/projectbrain/server/routes/auth.py:202` | `request` | API endpoint |
| `GET` | `/codegraph/data` | `get_codegraph_data` | `src/projectbrain/server/routes/codegraph.py:391` | `project_id, query, kinds, limit` | API endpoint |
| `GET` | `/codegraph/diff` | `diff_projects` | `src/projectbrain/server/routes/codegraph.py:182` | `base_project_id, target_project_id` | API endpoint |
| `GET` | `/codegraph/projects` | `get_projects` | `src/projectbrain/server/routes/codegraph.py:62` | `None` | API endpoint |
| `POST` | `/codegraph/projects` | `create_project` | `src/projectbrain/server/routes/codegraph.py:117` | `req` | API endpoint |
| `DELETE` | `/codegraph/projects/{project_id:path}` | `delete_project` | `src/projectbrain/server/routes/codegraph.py:145` | `project_id` | API endpoint |
| `GET` | `/codegraph/status` | `get_codegraph_status` | `src/projectbrain/server/routes/codegraph.py:305` | `project_id` | API endpoint |
| `POST` | `/codegraph/sync` | `sync_codegraph_data` | `src/projectbrain/server/routes/codegraph.py:639` | `req, request` | API endpoint |
| `POST` | `/codegraph/upload-db` | `upload_codegraph_db` | `src/projectbrain/server/routes/codegraph.py:829` | `file, project_id, project_name, author, project_path, request` | API endpoint |
| `DELETE` | `/graphify/branches` | `delete_branch` | `src/projectbrain/server/routes/graphify.py:392` | `project_id, branch` | API endpoint |
| `GET` | `/graphify/branches` | `list_branches` | `src/projectbrain/server/routes/graphify.py:381` | `project_id` | API endpoint |
| `GET` | `/graphify/diff` | `diff_graph` | `src/projectbrain/server/routes/graphify.py:253` | `project_id, branch, base_branch, target_branch, file_filter, community` | API endpoint |
| `POST` | `/graphify/local-diff` | `local_diff` | `src/projectbrain/server/routes/graphify.py:344` | `req` | API endpoint |
| `POST` | `/graphify/promote` | `promote_branch` | `src/projectbrain/server/routes/graphify.py:291` | `req` | API endpoint |
| `POST` | `/graphify/query` | `query_graph` | `src/projectbrain/server/routes/graphify.py:306` | `req` | API endpoint |
| `GET` | `/graphify/stats` | `get_stats` | `src/projectbrain/server/routes/graphify.py:404` | `project_id, branch` | API endpoint |
| `GET` | `/graphify/subgraph` | `get_subgraph` | `src/projectbrain/server/routes/graphify.py:231` | `project_id, branch, files, limit` | API endpoint |
| `POST` | `/graphify/sync` | `sync_graph` | `src/projectbrain/server/routes/graphify.py:172` | `req, request` | API endpoint |
| `GET` | `/health` | `health_check` | `src/projectbrain/server/routes/health.py:7` | `None` | API endpoint |
| `POST` | `/kb/build` | `build_kb` | `src/projectbrain/server/routes/kb.py:121` | `req` | Trigger full end-to-end Knowledge Base synthesis, chunking, linking, and HSG memory ingestion. |
| `GET` | `/kb/coverage` | `get_kb_coverage` | `src/projectbrain/server/routes/kb.py:163` | `project_id, branch, workspace_path` | Fetch repository-wide documentation and test coverage metrics, gap statistics, and drift alerts. |
| `POST` | `/kb/export` | `export_kb_artifacts` | `src/projectbrain/server/routes/kb.py:180` | `req` | Generate on-demand Markdown Wiki and multimodal Office suite artifacts from current GraphStore. |
| `GET` | `/kb/trace` | `trace_code_and_docs` | `src/projectbrain/server/routes/kb.py:144` | `project_id, target, branch, workspace_path` | Inspect cross-layer traceability connecting AST code nodes, documentation chunks, and verified tests. |
| `GET` | `/memory/active-project` | `get_active_project` | `src/projectbrain/server/routes/memory.py:254` | `None` | API endpoint |
| `POST` | `/memory/active-project` | `set_active_project` | `src/projectbrain/server/routes/memory.py:245` | `req` | API endpoint |
| `POST` | `/memory/add` | `add_memory` | `src/projectbrain/server/routes/memory.py:28` | `req` | API endpoint |
| `POST` | `/memory/delete_all` | `delete_all_memories` | `src/projectbrain/server/routes/memory.py:117` | `req` | API endpoint |
| `GET` | `/memory/diff` | `diff_memories` | `src/projectbrain/server/routes/memory.py:126` | `base_project_id, target_project_id` | API endpoint |
| `GET` | `/memory/history` | `get_history` | `src/projectbrain/server/routes/memory.py:53` | `user_id, limit, offset` | API endpoint |
| `GET` | `/memory/mcp/external` | `get_external_mcp_servers` | `src/projectbrain/server/routes/mcp_registry.py:157` | `None` | API endpoint |
| `POST` | `/memory/mcp/external` | `save_external_mcp_server` | `src/projectbrain/server/routes/mcp_registry.py:175` | `req` | API endpoint |
| `DELETE` | `/memory/mcp/external/{name}` | `delete_external_mcp_server` | `src/projectbrain/server/routes/mcp_registry.py:211` | `name` | API endpoint |
| `POST` | `/memory/mcp/register` | `register_mcp` | `src/projectbrain/server/routes/mcp_registry.py:58` | `req` | API endpoint |
| `GET` | `/memory/mcp/status` | `get_mcp_status` | `src/projectbrain/server/routes/mcp_registry.py:29` | `None` | API endpoint |
| `POST` | `/memory/mcp/unregister` | `unregister_mcp` | `src/projectbrain/server/routes/mcp_registry.py:118` | `None` | API endpoint |
| `POST` | `/memory/search` | `search_memory` | `src/projectbrain/server/routes/memory.py:40` | `req` | API endpoint |
| `GET` | `/memory/stats` | `get_stats` | `src/projectbrain/server/routes/memory.py:67` | `user_id` | API endpoint |
| `DELETE` | `/memory/{memory_id}` | `delete_single_memory` | `src/projectbrain/server/routes/memory.py:218` | `memory_id` | API endpoint |
| `GET` | `/memory/{memory_id}` | `get_single_memory` | `src/projectbrain/server/routes/memory.py:208` | `memory_id` | API endpoint |
| `POST` | `/memory/{memory_id}/reinforce` | `reinforce_memory` | `src/projectbrain/server/routes/memory.py:226` | `memory_id` | API endpoint |
| `POST` | `/pm/generate-doc` | `pm_generate_doc` | `src/projectbrain/server/routes/pm.py:64` | `req` | API endpoint |
| `POST` | `/pm/search` | `pm_search` | `src/projectbrain/server/routes/pm.py:23` | `req` | API endpoint |
| `GET` | `/sources` | `list_sources` | `src/projectbrain/server/routes/sources.py:276` | `None` | API endpoint |
| `POST` | `/sources/push-processed` | `push_processed` | `src/projectbrain/server/routes/sources.py:49` | `req` | API endpoint |
| `POST` | `/sources/upload` | `upload_document` | `src/projectbrain/server/routes/sources.py:162` | `file, project_id, branch, tags, author, request` | API endpoint |
| `POST` | `/sources/webhook/github` | `github_webhook` | `src/projectbrain/server/routes/sources.py:315` | `request` | API endpoint |
| `POST` | `/sources/webhook/notion` | `notion_webhook` | `src/projectbrain/server/routes/sources.py:355` | `request` | API endpoint |
| `POST` | `/sources/{source}/ingest` | `ingest_source` | `src/projectbrain/server/routes/sources.py:286` | `source, req` | API endpoint |

## 2. FastMCP Server Tools Catalog

| Tool Name | Handler Function | Source Location | Parameters | Description |
| :--- | :--- | :--- | :--- | :--- |
| `docx_create_document` | `create_document` | `extensions_mcp/convert_docx/server_stdio.py:63` | `params` | Creates a new DOCX file with the given content. |
| `docx_extract_tables` | `extract_tables` | `extensions_mcp/convert_docx/server_stdio.py:39` | `params` | Extracts all tables from a DOCX file. |
| `docx_extract_text` | `extract_text` | `extensions_mcp/convert_docx/server_stdio.py:22` | `params` | Extracts all text from a DOCX file. |
| `excel_convert_sheet_to_markdown` | `excel_convert_sheet_to_markdown` | `extensions_mcp/convert_excel/server_stdio.py:243` | `file_path, sheet_name, row_offset, row_limit` | Convert a single sheet from an Excel or CSV file to a Markdown table. |
| `excel_convert_to_markdown` | `excel_convert_to_markdown` | `extensions_mcp/convert_excel/server.py:202` | `file_path, sheets` | Convert an Excel or CSV file to Markdown tables. |
| `excel_convert_to_markdown` | `excel_convert_to_markdown` | `extensions_mcp/convert_excel/server_stdio.py:203` | `file_path, sheets` | Convert an Excel or CSV file to Markdown tables. |
| `excel_list_sheets` | `excel_list_sheets` | `extensions_mcp/convert_excel/server.py:172` | `file_path` | List all sheet names in an Excel or CSV file (.xlsx, .xlsm, .csv). |
| `excel_list_sheets` | `excel_list_sheets` | `extensions_mcp/convert_excel/server_stdio.py:173` | `file_path` | List all sheet names in an Excel or CSV file (.xlsx, .xlsm, .csv). |
| `img2md_extract_folder` | `img2md_extract_folder` | `extensions_mcp/image_to_markdown/server.py:25` | `folder_path, output_path, max_concurrency, output_mode, model, prompt_override, recursive` | Extract text from all images in a folder and format as Markdown, sorted naturally by filename. |
| `img2md_extract_image` | `img2md_extract_image` | `extensions_mcp/image_to_markdown/server.py:132` | `image_path, model, prompt_override, output_path` | Extract text from a single image and return it as Markdown (optionally saves to a file). |
| `img2md_list_images` | `img2md_list_images` | `extensions_mcp/image_to_markdown/server.py:98` | `folder_path, recursive` | List images in natural sorted order within a directory (does not make LLM calls). |
| `markdown_convert_to_excel` | `markdown_convert_to_excel` | `extensions_mcp/convert_excel/server.py:246` | `markdown_content, output_file_path` | Convert Markdown tables to an Excel file. |
| `markdown_convert_to_excel` | `markdown_convert_to_excel` | `extensions_mcp/convert_excel/server_stdio.py:329` | `markdown_content, output_file_path` | Convert Markdown tables to an Excel file. |
| `migration_batch_scan_logic` | `batch_scan_logic` | `extensions_mcp/codebase_migration_helper/server.py:166` | `project_path, project_id` | Recursively batch-scans a codebase, analyzes each function's business logic using Gemini, and saves a JSON draft. |
| `migration_plan_execution_phases` | `plan_execution_phases` | `extensions_mcp/codebase_migration_helper/server.py:182` | `project_path, max_phases` | Analyzes drafts and plans sequential refactoring/migration phases. |
| `migration_recommend_refactor` | `recommend_refactor` | `extensions_mcp/codebase_migration_helper/server.py:113` | `file_path, tech_stack` | Analyzes a legacy source file and recommends modern tech-stack refactoring based on industry migration standards. |
| `migration_translate_code_comments` | `translate_code_comments` | `extensions_mcp/codebase_migration_helper/server.py:54` | `file_path, direction` | Scans a code file, extracts and translates comments and docstrings, and returns the translated code. |
| `ocr_engine_status` | `ocr_engine_status` | `extensions_mcp/ocr_local/server.py:29` | `None` | Report local OCR engine status: PaddleOCR/PyMuPDF installation, language, and engine settings. |
| `ocr_image_to_markdown` | `ocr_image_to_markdown` | `extensions_mcp/ocr_local/server.py:81` | `image_path, output_path` | OCR a single image file using local PaddleOCR (no API key required). |
| `ocr_pdf_to_markdown` | `ocr_pdf_to_markdown` | `extensions_mcp/ocr_local/server.py:129` | `pdf_path, dpi, force_ocr, output_path` | Convert PDF to Markdown by rendering each page and running local PaddleOCR on the images. |
| `pdf_convert_to_markdown` | `pdf_convert_to_markdown` | `extensions_mcp/convert_pdf/server_stdio.py:322` | `file_path, include_tables, include_images, images_dir` | Convert a PDF file to Markdown format. |
| `pdf_extract_images` | `pdf_extract_images` | `extensions_mcp/convert_pdf/server_stdio.py:281` | `file_path, output_dir` | Extract images from all pages of a PDF file. |
| `pdf_extract_page_text` | `pdf_extract_page_text` | `extensions_mcp/convert_pdf/server_stdio.py:421` | `file_path, page_number` | Extract text from a specific page of a PDF file. |
| `pdf_extract_tables` | `pdf_extract_tables` | `extensions_mcp/convert_pdf/server_stdio.py:244` | `file_path` | Extract tables from all pages of a PDF file. |
| `pdf_extract_text` | `pdf_extract_text` | `extensions_mcp/convert_pdf/server_stdio.py:212` | `file_path` | Extract text from all pages of a PDF file. |
| `pdf_get_page_count` | `pdf_get_page_count` | `extensions_mcp/convert_pdf/server_stdio.py:382` | `file_path` | Get the total number of pages in a PDF file. |
| `pptx_create_presentation` | `create_presentation` | `extensions_mcp/convert_pptx/server_stdio.py:51` | `params` | Creates a new PPTX file with a title and content. |
| `pptx_extract_images` | `extract_images` | `extensions_mcp/convert_pptx/server_stdio.py:74` | `params` | Extracts all images from a PPTX file and saves them to a directory. |
| `pptx_extract_text` | `extract_text` | `extensions_mcp/convert_pptx/server_stdio.py:30` | `params` | Extracts all text from a PPTX file. |
| `pptx_to_markdown` | `to_markdown` | `extensions_mcp/convert_pptx/server_stdio.py:104` | `params` | Converts a PPTX file to a Markdown document. |
| `projectbrain_build_complete_kb` | `projectbrain_build_complete_kb` | `src/projectbrain/ai/mcp.py:1689` | `workspace_path, project_id, branch, persist_memory, output_dir, generate_office_exports` | Synthesize zero-hallucination Markdown wiki, multimodal Office documents (.docx, .xlsx, .pptx), chunk documents into HSG cognitive memory, and build multi-signal code-doc-test cross-links. |
| `projectbrain_delete` | `projectbrain_delete` | `src/projectbrain/ai/mcp.py:323` | `id, user_id` | Delete a memory by ID |
| `projectbrain_delete_all` | `projectbrain_delete_all` | `src/projectbrain/ai/mcp.py:369` | `user_id` | Delete all memories for a user |
| `projectbrain_diff_project_versions` | `projectbrain_diff_project_versions` | `src/projectbrain/ai/mcp.py:753` | `base_project_id, target_project_id` | Compare codebase structural symbols (codegraph) and context memories between two versions/branches of a project. |
| `projectbrain_generate_project_docs` | `projectbrain_generate_project_docs` | `src/projectbrain/ai/mcp.py:1858` | `workspace_path, project_id, branch, output_dir, formats` | Generate on-demand Markdown Knowledge Wiki and Multimodal Office exports (.docx, .xlsx, .pptx) from current GraphStore. |
| `projectbrain_get` | `projectbrain_get` | `src/projectbrain/ai/mcp.py:311` | `id` | Fetch a single memory by ID |
| `projectbrain_hybrid_query` | `projectbrain_hybrid_query` | `src/projectbrain/ai/mcp.py:1371` | `query, project_id, branch, local_path, include_memories, mode, budget` | Execute 3-tier Hybrid Knowledge Base query combining Graphify structural graph traversal and HSG cognitive memories. |
| `projectbrain_ingest` | `projectbrain_ingest` | `src/projectbrain/ai/mcp.py:411` | `source, creds, filters, user_id` | Ingest data from an external connector (GitHub, Notion, Web Crawler, OneDrive, Google Drive/Sheets/Slides) |
| `projectbrain_kb_coverage_report` | `projectbrain_kb_coverage_report` | `src/projectbrain/ai/mcp.py:1809` | `project_id, branch, workspace_path` | Get repository-wide knowledge base coverage analytics, documented/inferred/undocumented ratios, API & MCP coverage %, and drift alerts. |
| `projectbrain_list` | `projectbrain_list` | `src/projectbrain/ai/mcp.py:341` | `limit, user_id` | List recent memories |
| `projectbrain_local_graphify_diff` | `projectbrain_local_graphify_diff` | `src/projectbrain/ai/mcp.py:1319` | `workspace_path, project_id, branch, scoped_paths, git_dirty_only, refresh_local` | Compute structural difference between local uncommitted graph and remote server branch/root overlay. |
| `projectbrain_promote_branch_graph` | `projectbrain_promote_branch_graph` | `src/projectbrain/ai/mcp.py:1644` | `project_id, branch` | Atomically promote branch overlay schema mutations into canonical root schema. |
| `projectbrain_push_processed_document` | `projectbrain_push_processed_document` | `src/projectbrain/ai/mcp.py:1538` | `filename, markdown_content, project_id, branch, author, tags, server_url` | Directly push pre-extracted document Markdown and Graphify entities to ProjectBrain server or local memory. |
| `projectbrain_query` | `projectbrain_query` | `src/projectbrain/ai/mcp.py:85` | `query, type, fact_pattern, at, k, user_id, sector, author` | Query ProjectBrain for contextual memories (HSG) and/or temporal facts |
| `projectbrain_query_codegraph` | `projectbrain_query_codegraph` | `src/projectbrain/ai/mcp.py:916` | `project_id, query, kinds, node_id, follow_connections` | Query ProjectBrain's codebase structure graph (codegraph) to find classes, functions, files, variables, clients, servers, and trace their connections (edges) in the project. |
| `projectbrain_query_graphify` | `projectbrain_query_graphify` | `src/projectbrain/ai/mcp.py:1227` | `query, project_id, branch, action, target_symbol, mode, max_hops, budget` | Query ProjectBrain Graphify hybrid graph with actions: query, trace, impact, callers, callees, node, god_nodes, community. |
| `projectbrain_register_external_mcp` | `projectbrain_register_external_mcp_tool` | `src/projectbrain/ai/mcp.py:885` | `name, command, args, env` | Register or update an external/backup MCP server in Claude Desktop config. |
| `projectbrain_register_mcp` | `projectbrain_register_mcp_tool` | `src/projectbrain/ai/mcp.py:854` | `transport_type, user_id, tags, sse_url` | Register or update the projectbrain MCP server in Claude Desktop config. |
| `projectbrain_reinforce` | `projectbrain_reinforce` | `src/projectbrain/ai/mcp.py:352` | `id` | Reinforce a memory's salience/importance by its ID |
| `projectbrain_stats` | `projectbrain_stats` | `src/projectbrain/ai/mcp.py:382` | `user_id` | Retrieve cognitive memory engine statistics (total count, sectors, tags) |
| `projectbrain_store` | `projectbrain_store` | `src/projectbrain/ai/mcp.py:190` | `content, type, facts, user_id, tags, metadata, author` | Persist new content into ProjectBrain (HSG contextual memory and/or temporal facts) |
| `projectbrain_sync_codegraph` | `projectbrain_sync_codegraph` | `src/projectbrain/ai/mcp.py:491` | `project_id, project_path, branch, sync_memories, author` | Synchronize local codegraph database structure (nodes/edges) and codebase files (as memories) to ProjectBrain. |
| `projectbrain_sync_graphify` | `projectbrain_sync_graphify` | `src/projectbrain/ai/mcp.py:1118` | `workspace_path, project_id, branch, scoped_paths, git_dirty_only, local_only, cluster, include_docs` | Extract AST code graph and document entities via Graphify and synchronize to ProjectBrain local or remote graph store. |
| `projectbrain_trace_code_and_docs` | `projectbrain_trace_code_and_docs` | `src/projectbrain/ai/mcp.py:1757` | `target, project_id, branch, workspace_path` | Trace unified code AST symbols, linked documentation chunks, tests, callers/callees, and drift detection status for any symbol, route, or file. |
| `projectbrain_upload_document` | `projectbrain_upload_document` | `src/projectbrain/ai/mcp.py:1436` | `file_path, project_id, branch, author, tags, push_to_server` | Process and ingest multimodal document (.pdf, .docx, .xlsx, .pptx, .csv, .md, images) into Graphify and ProjectBrain memory. |
| `template_hello_world` | `hello_world` | `extensions_mcp/template/server.py:9` | `name` | A template greeting tool. |

## 3. CLI Subcommands

| Subcommand | Handler / Source Location | Usage | Purpose |
| :--- | :--- | :--- | :--- |
| `build-kb` | `src/projectbrain/main.py:860` | `python -m projectbrain.main build-kb` | CLI command for build-kb |
| `codegraph-sync` | `src/projectbrain/main.py:824` | `python -m projectbrain.main codegraph-sync` | CLI command for codegraph-sync |
| `graphify-diff` | `src/projectbrain/main.py:803` | `python -m projectbrain.main graphify-diff` | CLI command for graphify-diff |
| `graphify-sync` | `src/projectbrain/main.py:789` | `python -m projectbrain.main graphify-sync` | CLI command for graphify-sync |
| `ingest-files` | `src/projectbrain/main.py:843` | `python -m projectbrain.main ingest-files` | CLI command for ingest-files |
| `kb-coverage` | `src/projectbrain/main.py:881` | `python -m projectbrain.main kb-coverage` | CLI command for kb-coverage |
| `kb-trace` | `src/projectbrain/main.py:870` | `python -m projectbrain.main kb-trace` | CLI command for kb-trace |
| `mcp` | `src/projectbrain/main.py:787` | `python -m projectbrain.main mcp` | CLI command for mcp |
| `serve` | `src/projectbrain/main.py:785` | `python -m projectbrain.main serve` | CLI command for serve |
| `upload-doc` | `src/projectbrain/main.py:814` | `python -m projectbrain.main upload-doc` | CLI command for upload-doc |
| `upload-file` | `src/projectbrain/main.py:850` | `python -m projectbrain.main upload-file` | CLI command for upload-file |
