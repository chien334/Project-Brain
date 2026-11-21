# Project-Brain v3.0 Plugin Microkernel Performance & SLA Benchmark Report

**Task:** `TASK-18` (`PLUGIN-PERF-SIGNOFF`)  
**Date:** 2026-11-21  
**Auditor Role:** `performance-auditor` (`gemini-4-hydrogen`, effort: `high`)  
**Workspace:** `/Users/macbbook/SourceCodes/Project-Brain`  
**Verdict:** **PASS (ALL v3.0 BLUEPRINT SLAs SATISFIED)**

---

## 1. Executive Summary

A deterministic, read-only micro-benchmark audit was executed against the Project-Brain v3.0 Plugin Microkernel (`25` built-in plugins across `9` categories), the Verified UDCP Script Cache (`SqlitePgScriptCachePlugin`), the NTP-Aligned Multimodal Artifact Store (`LocalSqliteArtifactStorePlugin`), and all three Layer 2 Brain Consumer plugins (`ImpactTestDAGPlannerPlugin`, `OracleKnowledgeJudgePlugin`, `RCATriageMemoryPlugin`). All disk-backed SQLite workloads ran inside ephemeral `tempfile.TemporaryDirectory()` directories, leaving zero temporary artifacts in the repository.

Every runtime hot path comfortably satisfies the v3.0 blueprint latency SLAs with substantial headroom:

| Subsystem / Operation | Target SLA | Measured p50 | Measured p95 | Mean | Throughput | Status |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `PluginRegistry.discover_builtins()` (in-process, 25 plugins) | Warm registration | 0.0492 ms | 0.0569 ms | 0.0627 ms | 15,940.7 ops/s | **PASS** |
| `PluginRegistry.discover_builtins()` (cold subprocess import + load) | Process bootstrap | 722.8237 ms | 764.9365 ms | 731.4230 ms | 1.4 ops/s | **OBSERVED** |
| `PluginRegistry.resolve(category)` (warm, 9 categories) | $< 1\text{ ms}$ | 0.0072 ms | 0.0128 ms | 0.0082 ms | 122,016.5 ops/s | **PASS** |
| `PluginRegistry.resolve(category, preferred_id)` (warm direct) | $< 1\text{ ms}$ | 0.0016 ms | 0.0037 ms | 0.0020 ms | 501,393.6 ops/s | **PASS** |
| `SqlitePgScriptCachePlugin.lookup_script()` (exact `screen_hash`, 500 rows) | $< 15\text{ ms}$ | 0.0185 ms | 0.0240 ms | 0.0210 ms | 47,660.2 ops/s | **PASS** |
| `SqlitePgScriptCachePlugin.lookup_script()` (`ui_fingerprint`, 500 rows) | $< 15\text{ ms}$ | 0.0193 ms | 0.0202 ms | 0.0196 ms | 50,977.1 ops/s | **PASS** |
| `LocalSqliteArtifactStorePlugin.query_evidence_window()` ($\pm 5\text{ s}$, 101/200 matched) | Sub-15 ms query | 0.5902 ms | 1.0468 ms | 0.6367 ms | 1,570.5 ops/s | **PASS** |
| `LocalSqliteArtifactStorePlugin.query_evidence_window()` (full 200 artifacts, 8 streams) | Correlated window | 1.1575 ms | 2.0752 ms | 1.2521 ms | 798.6 ops/s | **PASS** |
| `ImpactTestDAGPlannerPlugin.execute()` (100 nodes, 150 edges, 30 intents, real cache) | Interactive planning | 2.1378 ms | 3.3493 ms | 2.3262 ms | 429.9 ops/s | **PASS** |
| `OracleKnowledgeJudgePlugin.execute()` (50 UART lines, 10 rules, 100 power samples) | Real-time verdict | 0.1362 ms | 0.1851 ms | 0.1481 ms | 6,753.1 ops/s | **PASS** |
| `RCATriageMemoryPlugin.execute()` (SQLite dedup + SHA256 + classification) | Sub-15 ms triage | 0.3571 ms | 0.8197 ms | 0.4364 ms | 2,291.5 ops/s | **PASS** |

---

## 2. Benchmark Environment & Methodology

- **Runtime:** Python `3.14.4`
- **Platform:** `macOS-26.6-arm64-arm-64bit-Mach-O` (`arm64`, `8` CPU cores)
- **Persistence:** SQLite backed by ephemeral `tempfile.TemporaryDirectory()` files on local disk (no `:memory:` shortcuts for `SqlitePgScriptCachePlugin`, `LocalSqliteArtifactStorePlugin`, or `RCATriageMemoryPlugin`).
- **Statistical Aggregation:** Measured with `time.perf_counter()` after warm-up calls (except cold subprocess import benchmarks). Percentiles (`p50`, `p95`, `p99`) are order statistics across `N` deterministic iterations.

---

## 3. Detailed Subsystem Benchmarks

### 3.1 Plugin Microkernel Registry (`PluginRegistry`)

Source: [`src/projectbrain/plugins/registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L59-L698)

- **`discover_builtins()`** ([L400-L414](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L400-L414)): Discovers and registers all `25` built-in plugins across `4` submodules (`storage_plugins`, `etl_and_flow_plugins`, `cache_and_artifact_plugins`, `brain_consumer_plugins`).
  - **Cold Subprocess (`N=10`):** Spawns a fresh Python interpreter, imports `PluginRegistry` + all 4 built-in plugin modules + transitive dependencies (`yaml`, `pydantic`, `sqlite3`, etc.), and registers 25 plugins.
    - `min`: `701.6140 ms` | `p50`: `722.8237 ms` | `mean`: `731.4230 ms` | `p95`: `764.9365 ms` | `max`: `774.0001 ms` | `stdev`: `23.7512 ms`
  - **In-Process Registration (`N=50`):** Instantiates a fresh `PluginRegistry()` and invokes `discover_builtins()` with Python modules already cached in `sys.modules`.
    - `min`: `0.0483 ms` | `p50`: `0.0492 ms` | `mean`: `0.0627 ms` | `p95`: `0.0569 ms` | `max`: `0.6572 ms` | `ops/s`: `15,940.7`
- **`resolve(category)`** ([L675-L697](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L675-L697)): Warm priority-ordered resolution cycled across all `9` plugin categories (`artifact_store`, `brain_consumer`, `doc_exporter`, `etl_parser`, `graph_backend`, `knowledge_linker`, `script_cache`, `vector_backend`, `webhook`).
  - **Category Priority Resolution (`N=1000`):** `min`: `0.0055 ms` | `p50`: `0.0072 ms` | `mean`: `0.0082 ms` | `p95`: `0.0128 ms` | `p99`: `0.0129 ms` | `max`: `0.0186 ms` | `ops/s`: `122,016.5`
  - **Direct `preferred_id` Resolution (`N=1000`):** `min`: `0.0015 ms` | `p50`: `0.0016 ms` | `mean`: `0.0020 ms` | `p95`: `0.0037 ms` | `p99`: `0.0135 ms` | `max`: `0.0597 ms` | `ops/s`: `501,393.6`
  - **SLA Assessment:** Target `< 1.0 ms` is met with a **78x margin** (`0.0128 ms` p95 vs `1.0000 ms` SLA).

### 3.2 Verified UDCP Script Cache (`SqlitePgScriptCachePlugin`)

Source: [`src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L515-L755)

Database seeded with `500` `UDCPScriptCacheEntry` rows across `5` projects, `4` platforms (`android`, `ios`, `qt`, `web`), and `3` build versions, exercising composite B-tree indices `idx_udcp_cache_lookup` and `idx_udcp_cache_screen` ([L600-L607](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L600-L607)).

| Operation | N | min (ms) | p50 (ms) | mean (ms) | p95 (ms) | p99 (ms) | max (ms) | ops/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `store_script(...)` (ANSI upsert) | 500 | 0.2865 | 0.3550 | 0.4031 | 0.6265 | 1.2194 | 1.5854 | 2,480.5 |
| `lookup_script(...)` exact `screen_hash` hit | 500 | 0.0177 | 0.0185 | 0.0210 | 0.0240 | 0.0952 | 0.1414 | 47,660.2 |
| `lookup_script(...)` `ui_fingerprint` similarity hit | 500 | 0.0190 | 0.0193 | 0.0196 | 0.0202 | 0.0223 | 0.0873 | 50,977.1 |
| `lookup_script(...)` miss | 200 | 0.0074 | 0.0075 | 0.0107 | 0.0359 | 0.0559 | 0.0854 | 93,127.9 |

- **SLA Assessment:** The v3.0 Fast-Path UDCP cache lookup SLA (`< 15.0 ms`) is satisfied with a **625x margin** at `p95` (`0.0240 ms` exact / `0.0202 ms` fingerprint).

### 3.3 Multimodal Telemetry & Artifact Store (`LocalSqliteArtifactStorePlugin`)

Source: [`src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1234-L1503)

Indexed `200` artifacts for `run-perf-001` evenly distributed across all `8` v3.0 evidence stream types (`video_h264`, `har`, `pcap`, `crashpad_dmp`, `gdb_hardfault`, `uart_log`, `power_trace_ma`, `screenshot_png`) at `100 ms` intervals over a `20 s` timeline, exercising `idx_telemetry_run_ts` and `video_offset_ms` alignment ([L1469-L1502](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1469-L1502)).

| Operation | N | Matched Rows | min (ms) | p50 (ms) | mean (ms) | p95 (ms) | p99 (ms) | max (ms) | ops/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `index_artifact(...)` | 200 | 1/call | 0.2817 | 0.3273 | 0.3845 | 0.6374 | 1.2488 | 1.4188 | 2,600.7 |
| `query_evidence_window(..., window_ms=5000)` | 200 | 101 | 0.5861 | 0.5902 | 0.6367 | 1.0468 | 1.4779 | 1.6181 | 1,570.5 |
| `query_evidence_window(..., window_ms=25000)` | 200 | 200 (8 streams) | 1.1490 | 1.1575 | 1.2521 | 2.0752 | 2.2296 | 2.5203 | 798.6 |

- **Observation:** Even when correlating and grouping all `200` multi-stream artifacts with `video_offset_ms` calculation in a single window query, `p95` stays at `2.0752 ms` (`~800 queries/sec`).

### 3.4 Layer 2 Brain Consumer Plugins (`Planner`, `Oracle`, `RCA`)

Source: [`src/projectbrain/plugins/builtins/brain_consumer_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L480-L1925)

1. **`ImpactTestDAGPlannerPlugin.execute()`** ([L503-L760](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L503-L760), `N=100`):
   - **Workload:** `100` graph nodes, `150` edges (`implements`, `transitions_to`), `30` candidate `CanonicalTestIntents`, firmware + APK artifact precondition injection, real SQLite `SqlitePgScriptCachePlugin` lookup binding (`500`-row DB), generating a `22`-node, `42`-edge acyclic DAG across `4` topological execution waves.
   - **Latency:** `min`: `2.1073 ms` | `p50`: `2.1378 ms` | `mean`: `2.3262 ms` | `p95`: `3.3493 ms` | `p99`: `3.5366 ms` | `max`: `3.6547 ms` | `ops/s`: `429.9`
2. **`OracleKnowledgeJudgePlugin.execute()`** ([L1028-L1200](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L1028-L1200), `N=200`):
   - **Workload:** `50` UART log lines, `10` regex `ErrorCodeRules` + built-in HardFault/CFSR checks, `5` SRS acceptance criteria, `3` expected state transitions, `100` `power_trace_ma` samples, and `5` HTTP responses (`PASS` verdict with `9` matched criteria).
   - **Latency:** `min`: `0.1349 ms` | `p50`: `0.1362 ms` | `mean`: `0.1481 ms` | `p95`: `0.1851 ms` | `p99`: `0.4210 ms` | `max`: `0.4979 ms` | `ops/s`: `6,753.1`
3. **`RCATriageMemoryPlugin.execute()`** ([L1855-L1925](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L1855-L1925), `N=200`):
   - **Workload:** Signature normalization (timestamps, PIDs, CPU registers, hex address stripping while preserving `CFSR=0x00008200`), SHA256 hashing, fault-domain classification, ticket linking, and ANSI `ON CONFLICT` SQLite upsert into `v3_rca_incidents`.
   - **Unique Incident Insert (`N=100`):** `p50`: `0.3776 ms` | `mean`: `0.4680 ms` | `p95`: `0.8762 ms` | `ops/s`: `2,136.5`
   - **Duplicate Incident Upsert (`N=100`):** `p50`: `0.3401 ms` | `mean`: `0.4048 ms` | `p95`: `0.7226 ms` | `ops/s`: `2,470.6`
   - **Combined (`N=200`):** `min`: `0.3135 ms` | `p50`: `0.3571 ms` | `mean`: `0.4364 ms` | `p95`: `0.8197 ms` | `p99`: `1.2706 ms` | `max`: `1.4426 ms` | `ops/s`: `2,291.5`

---

## 4. Observations & Optimization Notes

1. **Cold Subprocess Startup (`~723 ms` p50):** Cold startup time is dominated by Python importing transitive dependencies across all 4 built-in plugin submodules (`yaml`, `pydantic`, etc.), whereas in-process `PluginRegistry.discover_builtins()` registration takes only `0.049 ms` p50. For short-lived CLI invocations (`projectbrain ...`), lazy-loading only the needed plugin submodule or keeping the FastAPI/FastMCP daemon warm avoids paying the `~0.7 s` import cost per call.
2. **Environment-Variable Parsing in `PluginRegistry.is_enabled()`** ([L639-L644](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L639-L644)): `resolve()` already achieves `7.2 µs` p50 (`122k+ ops/s`), well below the `< 1 ms` target. Should plugin counts grow significantly (>200 plugins), caching parsed `PB_DISABLED_PLUGINS` / `PB_ENABLED_PLUGINS` CSV sets and indexing `_plugins` by category can keep `resolve()` $O(1)$ with plugin count.
3. **Zero Regressions / Clean Workspace:** All benchmarks operated read-only against `src/` with ephemeral `tempfile.TemporaryDirectory()` storage.

---

## 5. Sign-Off Decision

- **Warm `PluginRegistry.resolve(category)` `< 1 ms` SLA:** **PASSED** (`0.0072 ms` p50, `0.0128 ms` p95)
- **UDCP Fast-Path `SqlitePgScriptCachePlugin.lookup_script()` `< 15 ms` SLA:** **PASSED** (`0.0185 ms` p50, `0.0240 ms` p95)
- **200-Artifact Multi-Stream `query_evidence_window()` (`video_offset_ms` correlation):** **PASSED** (`0.5902 ms` p50 / `1.0468 ms` p95 for 101-row window; `1.1575 ms` p50 / `2.0752 ms` p95 for full 200-row window)
- **Layer 2 Brain Consumers (`Planner` / `Oracle` / `RCA`):** **PASSED** (`2.14 ms` / `0.14 ms` / `0.36 ms` p50)

**Sign-Off Status:** **APPROVED FOR RELEASE (v3.0 Plugin Microkernel Performance Audit)**
