# Graph Structural Analysis & Topological Report

> **Deterministic Architectural Health & Topological Overview**  
> Generated automatically from 1806 graph nodes and 3285 relations.

## 1. Executive Structural Metrics

| Metric | Value | Description |
| :--- | :--- | :--- |
| Total AST Nodes | **1806** | Entities discovered across codebase |
| Total Graph Edges | **3285** | Relations connecting nodes |
| God Nodes Isolated | **15** | Core high-degree components |
| Cross-Community Bridges | **15** | Connective links between distinct clusters |

## 2. Epistemological Confidence Breakdown

| Confidence Tier | Edge Count | Percentage | Epistemological Basis |
| :--- | :--- | :--- | :--- |
| `EXTRACTED` | **3283** | 99.9% | Direct AST function calls, class inheritance, file imports |
| `INFERRED` | **2** | 0.1% | Semantic docstring mentions, shared community co-occurrence |
| `AMBIGUOUS` | **0** | 0.0% | Unresolved dynamic imports or fuzzy matches |

## 3. Centrality & God Nodes Ranking

| Rank | Symbol Identifier | Label | Source Location | Degree Centrality | Community |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `typing` | `typing` | `:1` | 72 | Community None |
| 2 | `mcp` | `mcp.py` | `L1` | 54 | Community None |
| 3 | `os` | `os` | `:1` | 51 | Community None |
| 4 | `server_stdio` | `server_stdio.py` | `L1` | 50 | Community None |
| 5 | `basemodel` | `BaseModel` | `src/projectbrain/graphify_engine/hybrid_resolver.py:1` | 49 | Community None |
| 6 | `json` | `json` | `:1` | 49 | Community None |
| 7 | `hsg` | `hsg.py` | `L1` | 46 | Community None |
| 8 | `store` | `store.py` | `L1` | 46 | Community None |
| 9 | `app` | `app.js` | `L1` | 44 | Community None |
| 10 | `init` | `__init__.py` | `L1` | 39 | Community None |
| 11 | `server` | `server.py` | `L1` | 39 | Community None |
| 12 | `extractor` | `extractor.py` | `L1` | 37 | Community None |
| 13 | `kb_linker` | `kb_linker.py` | `L1` | 36 | Community None |
| 14 | `pathlib` | `pathlib` | `:1` | 35 | Community None |
| 15 | `test_minimax` | `test_minimax.py` | `L1` | 32 | Community None |

## 4. Surprising Cross-Community Bridges

| Source Node | Target Node | Relation | Confidence | Cluster Transition | Architectural Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `client` | `client_call_remote_tool` | `contains` | EXTRACTED | 15 -> 10 | Bridge from cluster 15 to 10 |
| `client` | `client_main` | `contains` | EXTRACTED | 15 -> 10 | Bridge from cluster 15 to 10 |
| `server_stdio` | `server_stdio_extract_text_from_pdf` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_extract_tables_from_pdf` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_table_to_markdown` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_extract_images_from_pdf` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_to_markdown` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_extract_text` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_extract_tables` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_extract_images` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_convert_to_markdown` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_get_page_count` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio` | `server_stdio_pdf_extract_page_text` | `contains` | EXTRACTED | 8 -> 1 | Bridge from cluster 8 to 1 |
| `server_stdio_rationale_28` | `server_stdio_extract_text_from_pdf` | `rationale_for` | EXTRACTED | 6 -> 1 | Bridge from cluster 6 to 1 |
| `server_stdio_rationale_49` | `server_stdio_extract_tables_from_pdf` | `rationale_for` | EXTRACTED | 6 -> 1 | Bridge from cluster 6 to 1 |
