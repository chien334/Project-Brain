# Community 17: Main

> **Leiden Community Cluster 17**  
> Contains 33 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `trace.py` | code | `src/projectbrain/trace.py` | `L1` | Community 17 |
| `Tracer` | code | `src/projectbrain/trace.py` | `L4` | Community 17 |
| `.__init__()` | code | `src/projectbrain/trace.py` | `L5` | Community 17 |
| `.trace()` | code | `src/projectbrain/trace.py` | `L8` | Community 17 |
| `Explainable retrieval.` | code | `src/projectbrain/trace.py` | `L9` | Community 17 |
| `client.py` | code | `src/projectbrain/client.py` | `L1` | Community 17 |
| `__init__.py` | code | `src/projectbrain/__init__.py` | `L1` | Community 17 |
| `openai_handler.py` | code | `src/projectbrain/openai_handler.py` | `L1` | Community 17 |
| `OpenAIRegistrar` | code | `src/projectbrain/openai_handler.py` | `L8` | Community 17 |
| `.__init__()` | code | `src/projectbrain/openai_handler.py` | `L9` | Community 17 |
| `.register()` | code | `src/projectbrain/openai_handler.py` | `L12` | Community 17 |
| `main.py` | code | `src/projectbrain/main.py` | `L1` | Community 17 |
| `Memory` | code | `src/projectbrain/main.py` | `L12` | Community 17 |
| `.__init__()` | code | `src/projectbrain/main.py` | `L13` | Community 17 |
| `openai()` | code | `src/projectbrain/main.py` | `L24` | Community 17 |
| `.add()` | code | `src/projectbrain/main.py` | `L29` | Community 17 |
| `.search()` | code | `src/projectbrain/main.py` | `L54` | Community 17 |
| `.get()` | code | `src/projectbrain/main.py` | `L77` | Community 17 |
| `.delete()` | code | `src/projectbrain/main.py` | `L95` | Community 17 |
| `.delete_all()` | code | `src/projectbrain/main.py` | `L105` | Community 17 |
| `.history()` | code | `src/projectbrain/main.py` | `L121` | Community 17 |
| `.source()` | code | `src/projectbrain/main.py` | `L144` | Community 17 |
| `run_mcp()` | code | `src/projectbrain/main.py` | `L173` | Community 17 |
| `run_server()` | code | `src/projectbrain/main.py` | `L181` | Community 17 |
| `run_codegraph_sync()` | code | `src/projectbrain/main.py` | `L190` | Community 17 |
| `async_ingest_files()` | code | `src/projectbrain/main.py` | `L372` | Community 17 |
| `run_ingest_files()` | code | `src/projectbrain/main.py` | `L437` | Community 17 |
| `run_upload_file()` | code | `src/projectbrain/main.py` | `L442` | Community 17 |
| `run_graphify_sync()` | code | `src/projectbrain/main.py` | `L497` | Community 17 |
| `run_graphify_diff()` | code | `src/projectbrain/main.py` | `L579` | Community 17 |
| `run_upload_doc()` | code | `src/projectbrain/main.py` | `L635` | Community 17 |
| `_get_flag()` | code | `src/projectbrain/main.py` | `L679` | Community 17 |
| `get a pre-configured source connector.          usage:             github = mem.` | code | `src/projectbrain/main.py` | `L145` | Community 17 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `trace` | `imports_from` | `main` | EXTRACTED | 1.0 |
| `trace` | `contains` | `trace_tracer` | EXTRACTED | 1.0 |
| `trace_tracer` | `method` | `trace_tracer_init` | EXTRACTED | 1.0 |
| `trace_tracer` | `method` | `trace_tracer_trace` | EXTRACTED | 1.0 |
| `trace_rationale_9` | `rationale_for` | `trace_tracer_trace` | EXTRACTED | 1.0 |
| `client` | `imports_from` | `main` | EXTRACTED | 1.0 |
| `init` | `imports_from` | `main` | EXTRACTED | 1.0 |
| `init` | `imports_from` | `trace` | EXTRACTED | 1.0 |
| `openai_handler` | `contains` | `openai_handler_openairegistrar` | EXTRACTED | 1.0 |
| `openai_handler_openairegistrar` | `method` | `openai_handler_openairegistrar_init` | EXTRACTED | 1.0 |
| `openai_handler_openairegistrar` | `method` | `openai_handler_openairegistrar_register` | EXTRACTED | 1.0 |
| `main` | `imports_from` | `openai_handler` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_memory` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_openai` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_mcp` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_server` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_codegraph_sync` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_async_ingest_files` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_ingest_files` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_upload_file` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_graphify_sync` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_graphify_diff` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_upload_doc` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_get_flag` | EXTRACTED | 1.0 |
| `main_memory` | `method` | `main_memory_init` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `trace` | `typing` | `imports_from` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `client_call_remote_tool` | `contains` | EXTRACTED |
| `client` | `client_main` | `contains` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `client_call_remote_tool` | `contains` | EXTRACTED |
| `client` | `client_main` | `contains` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
| `client` | `urllib_request` | `imports` | EXTRACTED |
| `client` | `urllib_error` | `imports` | EXTRACTED |
| `client` | `client_call_remote_tool` | `contains` | EXTRACTED |
| `client` | `client_main` | `contains` | EXTRACTED |
| `client` | `sys` | `imports` | EXTRACTED |
| `client` | `json` | `imports` | EXTRACTED |
| `client` | `argparse` | `imports` | EXTRACTED |
