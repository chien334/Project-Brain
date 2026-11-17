# Community 15: Verify Diff

> **Leiden Community Cluster 15**  
> Contains 16 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `verify_diff.py` | code | `scratch/verify_diff.py` | `L1` | Community 15 |
| `setup_test_data()` | code | `scratch/verify_diff.py` | `L8` | Community 15 |
| `test_diff_api()` | code | `scratch/verify_diff.py` | `L77` | Community 15 |
| `test_mcp_tool()` | code | `scratch/verify_diff.py` | `L88` | Community 15 |
| `check_nodes_diff.py` | code | `scratch/check_nodes_diff.py` | `L1` | Community 15 |
| `main()` | code | `scratch/check_nodes_diff.py` | `L3` | Community 15 |
| `test_openrouter.py` | code | `scratch/test_openrouter.py` | `L1` | Community 15 |
| `main()` | code | `scratch/test_openrouter.py` | `L5` | Community 15 |
| `test_query.py` | code | `scratch/test_query.py` | `L1` | Community 15 |
| `main()` | code | `scratch/test_query.py` | `L9` | Community 15 |
| `inspect_db.py` | code | `scratch/inspect_db.py` | `L1` | Community 15 |
| `main()` | code | `scratch/inspect_db.py` | `L3` | Community 15 |
| `verify_upload.py` | code | `scratch/verify_upload.py` | `L1` | Community 15 |
| `test_upload()` | code | `scratch/verify_upload.py` | `L4` | Community 15 |
| `test_similarity.py` | code | `scratch/test_similarity.py` | `L1` | Community 15 |
| `main()` | code | `scratch/test_similarity.py` | `L9` | Community 15 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `verify_diff` | `contains` | `verify_diff_setup_test_data` | EXTRACTED | 1.0 |
| `verify_diff` | `contains` | `verify_diff_test_diff_api` | EXTRACTED | 1.0 |
| `verify_diff` | `contains` | `verify_diff_test_mcp_tool` | EXTRACTED | 1.0 |
| `check_nodes_diff` | `contains` | `check_nodes_diff_main` | EXTRACTED | 1.0 |
| `test_openrouter` | `contains` | `test_openrouter_main` | EXTRACTED | 1.0 |
| `test_query` | `contains` | `test_query_main` | EXTRACTED | 1.0 |
| `inspect_db` | `contains` | `inspect_db_main` | EXTRACTED | 1.0 |
| `verify_upload` | `contains` | `verify_upload_test_upload` | EXTRACTED | 1.0 |
| `test_similarity` | `contains` | `test_similarity_main` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `verify_diff` | `asyncio` | `imports` | EXTRACTED |
| `verify_diff` | `sqlite3` | `imports` | EXTRACTED |
| `verify_diff` | `time` | `imports` | EXTRACTED |
| `verify_diff` | `httpx` | `imports` | EXTRACTED |
| `verify_diff` | `openmemory_ai_mcp` | `imports_from` | EXTRACTED |
| `verify_diff` | `openmemory_core_db` | `imports_from` | EXTRACTED |
| `check_nodes_diff` | `sqlite3` | `imports` | EXTRACTED |
| `test_openrouter` | `os` | `imports` | EXTRACTED |
| `test_openrouter` | `asyncio` | `imports` | EXTRACTED |
| `test_openrouter` | `httpx` | `imports` | EXTRACTED |
| `test_query` | `sys` | `imports` | EXTRACTED |
| `test_query` | `pathlib` | `imports_from` | EXTRACTED |
| `test_query` | `projectbrain_core_db` | `imports_from` | EXTRACTED |
| `inspect_db` | `sqlite3` | `imports` | EXTRACTED |
| `verify_upload` | `httpx` | `imports` | EXTRACTED |
| `verify_upload` | `asyncio` | `imports` | EXTRACTED |
| `test_similarity` | `sys` | `imports` | EXTRACTED |
| `test_similarity` | `asyncio` | `imports` | EXTRACTED |
| `test_similarity` | `numpy` | `imports` | EXTRACTED |
| `test_similarity` | `pathlib` | `imports_from` | EXTRACTED |
| `test_similarity` | `projectbrain_ai_synthetic` | `imports_from` | EXTRACTED |
