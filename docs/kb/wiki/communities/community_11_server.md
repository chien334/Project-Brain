# Community 11: Server

> **Leiden Community Cluster 11**  
> Contains 55 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `server.py` | code | `tools/orchestrator/server.py` | `L1` | Community 11 |
| `compute_cost()` | code | `tools/orchestrator/server.py` | `L48` | Community 11 |
| `StateStore` | code | `tools/orchestrator/server.py` | `L82` | Community 11 |
| `.__init__()` | code | `tools/orchestrator/server.py` | `L83` | Community 11 |
| `._start_token_auditor()` | code | `tools/orchestrator/server.py` | `L91` | Community 11 |
| `._get_connection()` | code | `tools/orchestrator/server.py` | `L236` | Community 11 |
| `._init_db()` | code | `tools/orchestrator/server.py` | `L243` | Community 11 |
| `._row_to_task()` | code | `tools/orchestrator/server.py` | `L285` | Community 11 |
| `._calculate_metrics_conn()` | code | `tools/orchestrator/server.py` | `L323` | Community 11 |
| `.upsert_task()` | code | `tools/orchestrator/server.py` | `L376` | Community 11 |
| `.kill_task()` | code | `tools/orchestrator/server.py` | `L453` | Community 11 |
| `.delete_task()` | code | `tools/orchestrator/server.py` | `L499` | Community 11 |
| `.append_log()` | code | `tools/orchestrator/server.py` | `L523` | Community 11 |
| `.append_logs_batch()` | code | `tools/orchestrator/server.py` | `L556` | Community 11 |
| `.get_task_transcript()` | code | `tools/orchestrator/server.py` | `L595` | Community 11 |
| `.get_state()` | code | `tools/orchestrator/server.py` | `L727` | Community 11 |
| `.reset()` | code | `tools/orchestrator/server.py` | `L749` | Community 11 |
| `.subscribe()` | code | `tools/orchestrator/server.py` | `L762` | Community 11 |
| `.unsubscribe()` | code | `tools/orchestrator/server.py` | `L768` | Community 11 |
| `.broadcast()` | code | `tools/orchestrator/server.py` | `L772` | Community 11 |
| `OrchestratorHandler` | code | `tools/orchestrator/server.py` | `L2414` | Community 11 |
| `BaseHTTPRequestHandler` | code | `tools/orchestrator/server.py` | `tools/orchestrator/server.py:1` | Community 11 |
| `.log_message()` | code | `tools/orchestrator/server.py` | `L2415` | Community 11 |
| `._set_cors_headers()` | code | `tools/orchestrator/server.py` | `L2418` | Community 11 |
| `.do_OPTIONS()` | code | `tools/orchestrator/server.py` | `L2423` | Community 11 |
| `.do_GET()` | code | `tools/orchestrator/server.py` | `L2428` | Community 11 |
| `.do_POST()` | code | `tools/orchestrator/server.py` | `L2510` | Community 11 |
| `run_server()` | code | `tools/orchestrator/server.py` | `L2702` | Community 11 |
| `Calculate actual cost and cache savings in USD and VND.` | code | `tools/orchestrator/server.py` | `L49` | Community 11 |
| `Background daemon: automatically audits tokens and streams live agent activities` | code | `tools/orchestrator/server.py` | `L92` | Community 11 |
| `Cancel/kill a running task and record an audit log.` | code | `tools/orchestrator/server.py` | `L454` | Community 11 |
| `Permanently delete a task and its associated logs from the database.` | code | `tools/orchestrator/server.py` | `L500` | Community 11 |
| `Append multiple log entries in a single transaction with ring-buffer broadcastin` | code | `tools/orchestrator/server.py` | `L557` | Community 11 |
| `Retrieve and parse the live activity stream from a subagent transcript.` | code | `tools/orchestrator/server.py` | `L596` | Community 11 |
| `client.py` | code | `tools/orchestrator/client.py` | `L1` | Community 11 |
| `OrchestratorClient` | code | `tools/orchestrator/client.py` | `L17` | Community 11 |
| `.__init__()` | code | `tools/orchestrator/client.py` | `L20` | Community 11 |
| `._post()` | code | `tools/orchestrator/client.py` | `L23` | Community 11 |
| `._get()` | code | `tools/orchestrator/client.py` | `L38` | Community 11 |
| `.update_task()` | code | `tools/orchestrator/client.py` | `L47` | Community 11 |
| `.kill_task()` | code | `tools/orchestrator/client.py` | `L95` | Community 11 |
| `.log()` | code | `tools/orchestrator/client.py` | `L102` | Community 11 |
| `.get_state()` | code | `tools/orchestrator/client.py` | `L118` | Community 11 |
| `.reset()` | code | `tools/orchestrator/client.py` | `L122` | Community 11 |
| `.delete_task()` | code | `tools/orchestrator/client.py` | `L126` | Community 11 |
| `auto_detect_tokens_and_conv()` | code | `tools/orchestrator/client.py` | `L132` | Community 11 |
| `main()` | code | `tools/orchestrator/client.py` | `L177` | Community 11 |
| `Client for interacting with the Multi-Agent Orchestrator Server.` | code | `tools/orchestrator/client.py` | `L18` | Community 11 |
| `Update or create a task status in the Orchestrator dashboard.` | code | `tools/orchestrator/client.py` | `L64` | Community 11 |
| `Kill or cancel a running task via the Orchestrator.` | code | `tools/orchestrator/client.py` | `L96` | Community 11 |
| `Append a log event to the Orchestrator dashboard.` | code | `tools/orchestrator/client.py` | `L109` | Community 11 |
| `Retrieve full state snapshot (tasks, logs, metrics).` | code | `tools/orchestrator/client.py` | `L119` | Community 11 |
| `Reset state in the Orchestrator dashboard.` | code | `tools/orchestrator/client.py` | `L123` | Community 11 |
| `Permanently delete a task from the Orchestrator dashboard.` | code | `tools/orchestrator/client.py` | `L127` | Community 11 |
| `Auto-detect current Antigravity conversation ID and token metrics if not explici` | code | `tools/orchestrator/client.py` | `L133` | Community 11 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `server` | `contains` | `server_compute_cost` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_statestore` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_orchestratorhandler` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_run_server` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_init` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_start_token_auditor` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_get_connection` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_init_db` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_row_to_task` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_calculate_metrics_conn` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_upsert_task` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_kill_task` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_delete_task` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_append_log` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_append_logs_batch` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_get_task_transcript` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_get_state` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_reset` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_subscribe` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_unsubscribe` | EXTRACTED | 1.0 |
| `server_statestore` | `method` | `server_statestore_broadcast` | EXTRACTED | 1.0 |
| `server_statestore_init` | `calls` | `server_statestore_init_db` | EXTRACTED | 1.0 |
| `server_statestore_init` | `calls` | `server_statestore_start_token_auditor` | EXTRACTED | 1.0 |
| `server_statestore_init_db` | `calls` | `server_statestore_get_connection` | EXTRACTED | 1.0 |
| `server_statestore_row_to_task` | `calls` | `server_compute_cost` | EXTRACTED | 1.0 |

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
