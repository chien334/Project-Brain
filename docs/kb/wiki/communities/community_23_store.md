# Community 23: Store

> **Leiden Community Cluster 23**  
> Contains 33 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `store.py` | code | `src/projectbrain/temporal_graph/store.py` | `L1` | Community 23 |
| `insert_fact()` | code | `src/projectbrain/temporal_graph/store.py` | `L11` | Community 23 |
| `update_fact()` | code | `src/projectbrain/temporal_graph/store.py` | `L36` | Community 23 |
| `invalidate_fact()` | code | `src/projectbrain/temporal_graph/store.py` | `L58` | Community 23 |
| `delete_fact()` | code | `src/projectbrain/temporal_graph/store.py` | `L63` | Community 23 |
| `insert_edge()` | code | `src/projectbrain/temporal_graph/store.py` | `L67` | Community 23 |
| `invalidate_edge()` | code | `src/projectbrain/temporal_graph/store.py` | `L78` | Community 23 |
| `batch_insert_facts()` | code | `src/projectbrain/temporal_graph/store.py` | `L83` | Community 23 |
| `apply_confidence_decay()` | code | `src/projectbrain/temporal_graph/store.py` | `L111` | Community 23 |
| `timeline.py` | code | `src/projectbrain/temporal_graph/timeline.py` | `L1` | Community 23 |
| `get_subject_timeline()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L8` | Community 23 |
| `get_predicate_timeline()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L47` | Community 23 |
| `get_changes_in_window()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L88` | Community 23 |
| `_row_to_fact()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L131` | Community 23 |
| `compare_time_points()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L144` | Community 23 |
| `get_change_frequency()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L181` | Community 23 |
| `get_volatile_facts()` | code | `src/projectbrain/temporal_graph/timeline.py` | `L213` | Community 23 |
| `query.py` | code | `src/projectbrain/temporal_graph/query.py` | `L1` | Community 23 |
| `query_facts_at_time()` | code | `src/projectbrain/temporal_graph/query.py` | `L7` | Community 23 |
| `get_current_fact()` | code | `src/projectbrain/temporal_graph/query.py` | `L40` | Community 23 |
| `query_facts_in_range()` | code | `src/projectbrain/temporal_graph/query.py` | `L52` | Community 23 |
| `find_conflicting_facts()` | code | `src/projectbrain/temporal_graph/query.py` | `L86` | Community 23 |
| `get_facts_by_subject()` | code | `src/projectbrain/temporal_graph/query.py` | `L98` | Community 23 |
| `search_facts()` | code | `src/projectbrain/temporal_graph/query.py` | `L121` | Community 23 |
| `get_related_facts()` | code | `src/projectbrain/temporal_graph/query.py` | `L137` | Community 23 |
| `format_fact()` | code | `src/projectbrain/temporal_graph/query.py` | `L165` | Community 23 |
| `__init__.py` | code | `src/projectbrain/temporal_graph/__init__.py` | `L1` | Community 23 |
| `types.py` | code | `src/projectbrain/temporal_graph/types.py` | `L1` | Community 23 |
| `TemporalFact` | code | `src/projectbrain/temporal_graph/types.py` | `L3` | Community 23 |
| `TypedDict` | code | `src/projectbrain/temporal_graph/types.py` | `src/projectbrain/temporal_graph/types.py:1` | Community 23 |
| `TemporalEdge` | code | `src/projectbrain/temporal_graph/types.py` | `L14` | Community 23 |
| `TimelineEntry` | code | `src/projectbrain/temporal_graph/types.py` | `L24` | Community 23 |
| `TemporalQuery` | code | `src/projectbrain/temporal_graph/types.py` | `L32` | Community 23 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `store` | `contains` | `store_insert_fact` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_update_fact` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_invalidate_fact` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_delete_fact` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_insert_edge` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_invalidate_edge` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_batch_insert_facts` | EXTRACTED | 1.0 |
| `store` | `contains` | `store_apply_confidence_decay` | EXTRACTED | 1.0 |
| `timeline` | `imports_from` | `query` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_get_subject_timeline` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_get_predicate_timeline` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_get_changes_in_window` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_row_to_fact` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_compare_time_points` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_get_change_frequency` | EXTRACTED | 1.0 |
| `timeline` | `contains` | `timeline_get_volatile_facts` | EXTRACTED | 1.0 |
| `timeline_compare_time_points` | `calls` | `timeline_row_to_fact` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_query_facts_at_time` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_get_current_fact` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_query_facts_in_range` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_find_conflicting_facts` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_get_facts_by_subject` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_search_facts` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_get_related_facts` | EXTRACTED | 1.0 |
| `query` | `contains` | `query_format_fact` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `store` | `time` | `imports` | EXTRACTED |
| `store` | `uuid` | `imports` | EXTRACTED |
| `store` | `json` | `imports` | EXTRACTED |
| `store` | `logging` | `imports` | EXTRACTED |
| `store` | `typing` | `imports_from` | EXTRACTED |
| `store` | `core_db` | `imports_from` | EXTRACTED |
| `store` | `hashlib` | `imports` | EXTRACTED |
| `store` | `json` | `imports` | EXTRACTED |
| `store` | `logging` | `imports` | EXTRACTED |
| `store` | `os` | `imports` | EXTRACTED |
| `store` | `re` | `imports` | EXTRACTED |
| `store` | `sqlite3` | `imports` | EXTRACTED |
| `store` | `abc` | `imports_from` | EXTRACTED |
| `store` | `typing` | `imports_from` | EXTRACTED |
| `store` | `psycopg2` | `imports` | EXTRACTED |
| `store` | `psycopg2_extras` | `imports` | EXTRACTED |
| `store` | `psycopg2_sql` | `imports` | EXTRACTED |
| `store` | `models` | `imports_from` | EXTRACTED |
| `store` | `store_sanitize_branch_schema_name` | `contains` | EXTRACTED |
| `store` | `store_graphstore` | `contains` | EXTRACTED |
| `store` | `store_ensure_root_schema` | `contains` | EXTRACTED |
| `store` | `store_ensure_branch_schema` | `contains` | EXTRACTED |
| `store` | `store_sync_branch_delta` | `contains` | EXTRACTED |
| `store` | `store_diff_branch_vs_root` | `contains` | EXTRACTED |
| `store` | `store_diff_branches` | `contains` | EXTRACTED |
