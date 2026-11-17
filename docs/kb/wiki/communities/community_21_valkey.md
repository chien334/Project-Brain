# Community 21: Valkey

> **Leiden Community Cluster 21**  
> Contains 22 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `valkey.py` | code | `src/projectbrain/core/vector/valkey.py` | `L1` | Community 21 |
| `ValkeyVectorStore` | code | `src/projectbrain/core/vector/valkey.py` | `L11` | Community 21 |
| `VectorStore` | code | `src/projectbrain/core/vector/valkey.py` | `src/projectbrain/core/vector/valkey.py:1` | Community 21 |
| `.__init__()` | code | `src/projectbrain/core/vector/valkey.py` | `L12` | Community 21 |
| `._get_client()` | code | `src/projectbrain/core/vector/valkey.py` | `L17` | Community 21 |
| `._key()` | code | `src/projectbrain/core/vector/valkey.py` | `L23` | Community 21 |
| `.storeVector()` | code | `src/projectbrain/core/vector/valkey.py` | `L26` | Community 21 |
| `.getVectorsById()` | code | `src/projectbrain/core/vector/valkey.py` | `L40` | Community 21 |
| `.getVector()` | code | `src/projectbrain/core/vector/valkey.py` | `L57` | Community 21 |
| `.deleteVectors()` | code | `src/projectbrain/core/vector/valkey.py` | `L64` | Community 21 |
| `.search()` | code | `src/projectbrain/core/vector/valkey.py` | `L68` | Community 21 |
| `__init__.py` | code | `src/projectbrain/core/vector/__init__.py` | `L1` | Community 21 |
| `postgres.py` | code | `src/projectbrain/core/vector/postgres.py` | `L1` | Community 21 |
| `PostgresVectorStore` | code | `src/projectbrain/core/vector/postgres.py` | `L10` | Community 21 |
| `VectorStore` | code | `src/projectbrain/core/vector/postgres.py` | `src/projectbrain/core/vector/postgres.py:1` | Community 21 |
| `.__init__()` | code | `src/projectbrain/core/vector/postgres.py` | `L11` | Community 21 |
| `._get_pool()` | code | `src/projectbrain/core/vector/postgres.py` | `L16` | Community 21 |
| `.storeVector()` | code | `src/projectbrain/core/vector/postgres.py` | `L42` | Community 21 |
| `.getVectorsById()` | code | `src/projectbrain/core/vector/postgres.py` | `L57` | Community 21 |
| `.getVector()` | code | `src/projectbrain/core/vector/postgres.py` | `L69` | Community 21 |
| `.deleteVectors()` | code | `src/projectbrain/core/vector/postgres.py` | `L79` | Community 21 |
| `.search()` | code | `src/projectbrain/core/vector/postgres.py` | `L84` | Community 21 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `valkey` | `contains` | `valkey_valkeyvectorstore` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `inherits` | `vectorstore` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_init` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_get_client` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_key` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_storevector` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_getvectorsbyid` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_getvector` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_deletevectors` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore` | `method` | `valkey_valkeyvectorstore_search` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_storevector` | `calls` | `valkey_valkeyvectorstore_get_client` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_storevector` | `calls` | `valkey_valkeyvectorstore_key` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_getvectorsbyid` | `calls` | `valkey_valkeyvectorstore_get_client` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_getvectorsbyid` | `calls` | `valkey_valkeyvectorstore_key` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_getvector` | `calls` | `valkey_valkeyvectorstore_getvectorsbyid` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_deletevectors` | `calls` | `valkey_valkeyvectorstore_get_client` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_deletevectors` | `calls` | `valkey_valkeyvectorstore_key` | EXTRACTED | 1.0 |
| `valkey_valkeyvectorstore_search` | `calls` | `valkey_valkeyvectorstore_get_client` | EXTRACTED | 1.0 |
| `init` | `imports_from` | `postgres` | EXTRACTED | 1.0 |
| `init` | `imports_from` | `valkey` | EXTRACTED | 1.0 |
| `postgres` | `contains` | `postgres_postgresvectorstore` | EXTRACTED | 1.0 |
| `postgres_postgresvectorstore` | `inherits` | `vectorstore` | EXTRACTED | 1.0 |
| `postgres_postgresvectorstore` | `method` | `postgres_postgresvectorstore_init` | EXTRACTED | 1.0 |
| `postgres_postgresvectorstore` | `method` | `postgres_postgresvectorstore_get_pool` | EXTRACTED | 1.0 |
| `postgres_postgresvectorstore` | `method` | `postgres_postgresvectorstore_storevector` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `valkey` | `typing` | `imports_from` | EXTRACTED |
| `valkey` | `json` | `imports` | EXTRACTED |
| `valkey` | `logging` | `imports` | EXTRACTED |
| `valkey` | `asyncio` | `imports` | EXTRACTED |
| `valkey` | `numpy` | `imports` | EXTRACTED |
| `valkey` | `vector_store` | `imports_from` | EXTRACTED |
| `init` | `base` | `imports_from` | EXTRACTED |
| `init` | `python` | `imports_from` | EXTRACTED |
| `init` | `cobol` | `imports_from` | EXTRACTED |
| `init` | `vb6` | `imports_from` | EXTRACTED |
| `init` | `pascal` | `imports_from` | EXTRACTED |
| `init` | `fortran` | `imports_from` | EXTRACTED |
| `init` | `st_xml` | `imports_from` | EXTRACTED |
| `init` | `generic` | `imports_from` | EXTRACTED |
| `init` | `main` | `imports_from` | EXTRACTED |
| `init` | `trace` | `imports_from` | EXTRACTED |
| `init` | `base` | `imports_from` | EXTRACTED |
| `init` | `langchain` | `imports_from` | EXTRACTED |
| `init` | `agents` | `imports_from` | EXTRACTED |
| `init` | `google_drive` | `imports_from` | EXTRACTED |
| `init` | `google_sheets` | `imports_from` | EXTRACTED |
| `init` | `google_slides` | `imports_from` | EXTRACTED |
| `init` | `notion` | `imports_from` | EXTRACTED |
| `init` | `onedrive` | `imports_from` | EXTRACTED |
| `init` | `github` | `imports_from` | EXTRACTED |
