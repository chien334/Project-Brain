# Project-Brain Knowledge Base Index

> **Deterministic Zero-Hallucination Technical Documentation**  
> Grounded strictly in AST code graphs, Leiden community structures, route tables, and database schemas.

## 1. Repository Knowledge Graph Statistics

| Metric | Count | Description |
| :--- | :--- | :--- |
| Total AST Nodes | **1806** | Classes, functions, methods, routes, documents |
| Total Graph Edges | **3285** | Direct calls, imports, inherits, citations |
| Leiden Communities | **30** | Modular functional clusters |
| Architectural God Nodes | **15** | High-centrality hub components |
| Discovered REST Routes | **52** | FastAPI HTTP endpoints |
| Discovered FastMCP Tools | **56** | Model Context Protocol agent tools |
| Pydantic Data Models | **67** | Typed domain transfer objects |

## 2. Master Navigation Directory

- [Architecture Overview](architecture_overview.md) — System topology, Mermaid call flowchart, and core subsystem breakdown.
- [API & FastMCP Reference](api_and_mcp_reference.md) — Complete catalog of REST endpoints and FastMCP tools with `file:line` locations.
- [Database & Schemas](database_and_schemas.md) — PostgreSQL `pb_root` / `pb_br_*` multi-schema, SQLite overlays, HSG Cognitive Memory, and Pydantic models.
- [Graph Structural Report](../GRAPH_REPORT.md) — God nodes centrality ranking, cross-community bridges, and confidence breakdown.

## 3. Key God Nodes (Architectural Hubs)

| Rank | Symbol | File Type | Source Location | Degree | Community |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `typing` | code | `:1` | 72 | Community None |
| 2 | `mcp.py` | code | `L1` | 54 | Community None |
| 3 | `os` | code | `:1` | 51 | Community None |
| 4 | `server_stdio.py` | code | `L1` | 50 | Community None |
| 5 | `BaseModel` | code | `src/projectbrain/graphify_engine/hybrid_resolver.py:1` | 49 | Community None |
| 6 | `json` | code | `:1` | 49 | Community None |
| 7 | `hsg.py` | code | `L1` | 46 | Community None |
| 8 | `store.py` | code | `L1` | 46 | Community None |
| 9 | `app.js` | code | `L1` | 44 | Community None |
| 10 | `__init__.py` | code | `L1` | 39 | Community None |

## 4. Leiden Communities Directory

| Community ID | Name / Slug | Member Symbols | Primary Modules / Focus | Deep Dive Link |
| :--- | :--- | :--- | :--- | :--- |
| Community 0 | `loader` | 4 symbols | `__init__.py`, `loader.py`, `load_extensions()` | [Community 0 Detail](communities/community_0_loader.md) |
| Community 1 | `server_stdio` | 28 symbols | `client.py`, `call_remote_tool()`, `main()` | [Community 1 Detail](communities/community_1_server_stdio.md) |
| Community 2 | `server` | 29 symbols | `server.py`, `call_gemini()`, `translate_code_comments()` | [Community 2 Detail](communities/community_2_server.md) |
| Community 3 | `python` | 44 symbols | `cobol.py`, `CobolParser`, `BaseParser` | [Community 3 Detail](communities/community_3_python.md) |
| Community 4 | `server` | 10 symbols | `server.py`, `ocr_engine_status()`, `ocr_image_to_markdown()` | [Community 4 Detail](communities/community_4_server.md) |
| Community 5 | `server_stdio` | 18 symbols | `extract_images_ocr.py`, `client.py`, `call_remote_tool()` | [Community 5 Detail](communities/community_5_server_stdio.md) |
| Community 6 | `server` | 49 symbols | `test_mcp.ps1`, `server.py`, `_read_xlsx()` | [Community 6 Detail](communities/community_6_server.md) |
| Community 7 | `connections` | 47 symbols | `evaluation.py`, `parse_evaluation_file()`, `extract_xml_content()` | [Community 7 Detail](communities/community_7_connections.md) |
| Community 8 | `server_stdio` | 18 symbols | `client.py`, `call_remote_tool()`, `main()` | [Community 8 Detail](communities/community_8_server_stdio.md) |
| Community 9 | `client` | 7 symbols | `server.py`, `hello_world()`, `A template greeting tool.` | [Community 9 Detail](communities/community_9_client.md) |
| Community 10 | `paddle_client` | 64 symbols | `server.py`, `img2md_extract_folder()`, `img2md_list_images()` | [Community 10 Detail](communities/community_10_paddle_client.md) |
| Community 11 | `test_minimax` | 380 symbols | `test_graphify_store.py`, `test_sanitize_branch_schema_name_basic()`, `test_sanitize_branch_schema_name_long_branch()` | [Community 11 Detail](communities/community_11_test_minimax.md) |
| Community 12 | `test_multilingual_dedup` | 9 symbols | `test_multilingual_dedup.py`, `_ensure_pkg()`, `_stub_module()` | [Community 12 Detail](communities/community_12_test_multilingual_dedup.md) |
| Community 13 | `verify_diff` | 16 symbols | `verify_diff.py`, `setup_test_data()`, `test_diff_api()` | [Community 13 Detail](communities/community_13_verify_diff.md) |
| Community 14 | `diag_ocr` | 8 symbols | `diag_ocr.py`, `run()`, `Diagnose why an image yields chars=0 — runs predict() WITHOUT swallowing errors.` | [Community 14 Detail](communities/community_14_diag_ocr.md) |
| Community 15 | `main` | 36 symbols | `trace.py`, `Tracer`, `.__init__()` | [Community 15 Detail](communities/community_15_main.md) |
| Community 16 | `base` | 122 symbols | `__init__.py`, `projectbrain connectors - data source integrations`, `google_slides.py` | [Community 16 Detail](communities/community_16_base.md) |
| Community 17 | `init` | 1 symbols | `__init__.py` | [Community 17 Detail](communities/community_17_init.md) |
| Community 18 | `db` | 64 symbols | `db.py`, `DB`, `.__init__()` | [Community 18 Detail](communities/community_18_db.md) |
| Community 19 | `valkey` | 22 symbols | `valkey.py`, `ValkeyVectorStore`, `VectorStore` | [Community 19 Detail](communities/community_19_valkey.md) |
| Community 20 | `hsg` | 67 symbols | `embed.py`, `emb_dispatch()`, `embed_for_sector()` | [Community 20 Detail](communities/community_20_hsg.md) |
| Community 21 | `store` | 33 symbols | `store.py`, `insert_fact()`, `update_fact()` | [Community 21 Detail](communities/community_21_store.md) |
| Community 22 | `api` | 5 symbols | `api.py`, `SSEFilter`, `.__init__()` | [Community 22 Detail](communities/community_22_api.md) |
| Community 23 | `app` | 45 symbols | `app.js`, `updateBranchDropdown()`, `recalculateActiveUser()` | [Community 23 Detail](communities/community_23_app.md) |
| Community 24 | `graphify` | 124 symbols | `mcp_registry.py`, `RegisterMCPRequest`, `BaseModel` | [Community 24 Detail](communities/community_24_graphify.md) |
| Community 25 | `doc_parser` | 55 symbols | `vectors.py`, `now()`, `rid()` | [Community 25 Detail](communities/community_25_doc_parser.md) |
| Community 26 | `store` | 317 symbols | `store.py`, `sanitize_branch_schema_name()`, `GraphStore` | [Community 26 Detail](communities/community_26_store.md) |
| Community 27 | `langchain` | 23 symbols | `agents.py`, `CrewAIMemory`, `.__init__()` | [Community 27 Detail](communities/community_27_langchain.md) |
| Community 28 | `mcp` | 127 symbols | `synthetic.py`, `SyntheticAdapter`, `AIAdapter` | [Community 28 Detail](communities/community_28_mcp.md) |
| Community 29 | `compress` | 34 symbols | `ingest.py`, `split_text()`, `mk_root()` | [Community 29 Detail](communities/community_29_compress.md) |
