# Community 16: Main

> **Leiden Community Cluster 16**  
> Contains 36 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `trace.py` | code | `src/projectbrain/trace.py` | `L1` | Community 16 |
| `Tracer` | code | `src/projectbrain/trace.py` | `L4` | Community 16 |
| `.__init__()` | code | `src/projectbrain/trace.py` | `L5` | Community 16 |
| `.trace()` | code | `src/projectbrain/trace.py` | `L8` | Community 16 |
| `Explainable retrieval.` | code | `src/projectbrain/trace.py` | `L9` | Community 16 |
| `client.py` | code | `src/projectbrain/client.py` | `L1` | Community 16 |
| `__init__.py` | code | `src/projectbrain/__init__.py` | `L1` | Community 16 |
| `openai_handler.py` | code | `src/projectbrain/openai_handler.py` | `L1` | Community 16 |
| `OpenAIRegistrar` | code | `src/projectbrain/openai_handler.py` | `L8` | Community 16 |
| `.__init__()` | code | `src/projectbrain/openai_handler.py` | `L9` | Community 16 |
| `.register()` | code | `src/projectbrain/openai_handler.py` | `L12` | Community 16 |
| `main.py` | code | `src/projectbrain/main.py` | `L1` | Community 16 |
| `Memory` | code | `src/projectbrain/main.py` | `L12` | Community 16 |
| `.__init__()` | code | `src/projectbrain/main.py` | `L13` | Community 16 |
| `openai()` | code | `src/projectbrain/main.py` | `L24` | Community 16 |
| `.add()` | code | `src/projectbrain/main.py` | `L29` | Community 16 |
| `.search()` | code | `src/projectbrain/main.py` | `L54` | Community 16 |
| `.get()` | code | `src/projectbrain/main.py` | `L77` | Community 16 |
| `.delete()` | code | `src/projectbrain/main.py` | `L95` | Community 16 |
| `.delete_all()` | code | `src/projectbrain/main.py` | `L105` | Community 16 |
| `.history()` | code | `src/projectbrain/main.py` | `L121` | Community 16 |
| `.source()` | code | `src/projectbrain/main.py` | `L144` | Community 16 |
| `run_mcp()` | code | `src/projectbrain/main.py` | `L173` | Community 16 |
| `run_server()` | code | `src/projectbrain/main.py` | `L181` | Community 16 |
| `run_codegraph_sync()` | code | `src/projectbrain/main.py` | `L190` | Community 16 |
| `async_ingest_files()` | code | `src/projectbrain/main.py` | `L372` | Community 16 |
| `run_ingest_files()` | code | `src/projectbrain/main.py` | `L437` | Community 16 |
| `run_upload_file()` | code | `src/projectbrain/main.py` | `L442` | Community 16 |
| `run_graphify_sync()` | code | `src/projectbrain/main.py` | `L497` | Community 16 |
| `run_graphify_diff()` | code | `src/projectbrain/main.py` | `L579` | Community 16 |
| `run_upload_doc()` | code | `src/projectbrain/main.py` | `L635` | Community 16 |
| `run_build_kb()` | code | `src/projectbrain/main.py` | `L675` | Community 16 |
| `run_kb_trace()` | code | `src/projectbrain/main.py` | `L709` | Community 16 |
| `run_kb_coverage()` | code | `src/projectbrain/main.py` | `L741` | Community 16 |
| `_get_flag()` | code | `src/projectbrain/main.py` | `L775` | Community 16 |
| `get a pre-configured source connector.          usage:             github = mem.` | code | `src/projectbrain/main.py` | `L145` | Community 16 |

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
| `main` | `contains` | `main_run_build_kb` | EXTRACTED | 1.0 |
| `main` | `contains` | `main_run_kb_trace` | EXTRACTED | 1.0 |

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
