# Community 22: Api

> **Leiden Community Cluster 22**  
> Contains 5 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `api.py` | code | `src/projectbrain/server/api.py` | `L1` | Community 22 |
| `SSEFilter` | code | `src/projectbrain/server/api.py` | `L14` | Community 22 |
| `.__init__()` | code | `src/projectbrain/server/api.py` | `L15` | Community 22 |
| `.process_chunk()` | code | `src/projectbrain/server/api.py` | `L19` | Community 22 |
| `create_app()` | code | `src/projectbrain/server/api.py` | `L62` | Community 22 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `api` | `contains` | `api_ssefilter` | EXTRACTED | 1.0 |
| `api` | `contains` | `api_create_app` | EXTRACTED | 1.0 |
| `api_ssefilter` | `method` | `api_ssefilter_init` | EXTRACTED | 1.0 |
| `api_ssefilter` | `method` | `api_ssefilter_process_chunk` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `api` | `fastapi` | `imports_from` | EXTRACTED |
| `api` | `fastapi_middleware_cors` | `imports_from` | EXTRACTED |
| `api` | `fastapi_staticfiles` | `imports_from` | EXTRACTED |
| `api` | `time` | `imports` | EXTRACTED |
| `api` | `logging` | `imports` | EXTRACTED |
| `api` | `pathlib` | `imports_from` | EXTRACTED |
| `api` | `core_config` | `imports_from` | EXTRACTED |
| `api` | `routes` | `imports_from` | EXTRACTED |
| `api` | `json` | `imports` | EXTRACTED |
