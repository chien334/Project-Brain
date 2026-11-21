# Security & Quality Audit Report: Project-Brain v3.0 Plugin Microkernel & Layer 0–2 Pipeline

**Document Version**: 1.1.0  
**Audit Date**: November 21, 2026  
**Auditor**: Quality & Security Auditor (`gemini-4-carbon`)  
**Task Reference**: `TASK-17B` (`PLUGIN-AUDIT`) -> Remediated in `TASK-19A` & `TASK-19B` (`PLUGIN-REMEDIATE`)  
**Scope Target**: Project-Brain v3.0 Plugin Microkernel (`src/projectbrain/plugins/`), Cross-Dialect Migration (`010_v3_plugin_microkernel.sql`), v3 REST Endpoints (`routes/plugins_v3.py`), REST/FastMCP Auth & RBAC (`server/api.py`, `ai/mcp.py`), Graph/Vector/Cache/Artifact Backends, Brain Consumers (`Planner`, `Oracle`, `RCA`, `Webhook`), and Secret Hygiene across `src/` & `tests/`.  
**Repository Working Directory**: `/Users/macbbook/SourceCodes/Project-Brain`  
**Audit Artifact**: [`docs/security/v3_plugin_security_audit.md`](file:///Users/macbbook/SourceCodes/Project-Brain/docs/security/v3_plugin_security_audit.md)

---

## 1. Executive Summary & Overall Verdict

### Overall Verdict: `APPROVED` *(Post-Remediation Verification Complete)*

A multi-axis security audit of the Project-Brain v3.0 Plugin Microkernel, Layer 0 (Webhook / ETL), Layer 1 (Graph, Vector, Script Cache, Telemetry Artifacts), and Layer 2 (`BrainConsumer` Planner / Oracle / RCA) architecture originally identified **1 `CRITICAL`**, **4 `HIGH`**, **3 `MEDIUM`**, and **1 `LOW`** findings (`ACTION_REQUIRED`).

All v3.0 `CRITICAL` (`V3-SEC-01`), `HIGH` (`V3-SEC-02`–`V3-SEC-05`), and v3 `MEDIUM`/`LOW` (`V3-SEC-06`, `V3-SEC-07`, and v3 `V3-SEC-09`) findings have now been **REMEDIATED** across `TASK-19A` and `TASK-19B` and verified by the new regression suite [`tests/test_v3_security_remediation.py`](file:///Users/macbbook/SourceCodes/Project-Brain/tests/test_v3_security_remediation.py) (`7/7` passed) alongside the full repository suite (`275 passed, 5 skipped, 0 failed` across 28 suites).

### Finding Summary Matrix

| ID | Severity | OWASP Top 10 (2021) | CWE | Component / Primary Target | Status |
|---|---|---|---|---|---|
| **V3-SEC-01** | `CRITICAL` | A01 Broken Access Control / A03 Injection | CWE-94, CWE-22, CWE-269 | [`registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L510-L570), [`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L186-L256), [`api.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L258-L280) | **REMEDIATED (`TASK-19A` + `TASK-19B`)** |
| **V3-SEC-02** | `HIGH` | A01 Broken Access Control / A02 Crypto Failures | CWE-200, CWE-522 | [`registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L60-L115), [`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L130-L176), [`mcp.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2403-L2515) | **REMEDIATED (`TASK-19A` + `TASK-19B`)** |
| **V3-SEC-03** | `HIGH` | A01 Broken Access Control (Path Traversal) | CWE-22 | [`mcp.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2362-L2545) (`projectbrain_v3_ingest_etl`) | **REMEDIATED (`TASK-19B`)** |
| **V3-SEC-04** | `HIGH` | A07 Auth Failures / A08 Integrity Failures | CWE-306, CWE-345, CWE-639 | [`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L1196-L1300), [`brain_consumer_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L2361-L2410) | **REMEDIATED (`TASK-19A` + `TASK-19B`)** |
| **V3-SEC-05** | `HIGH` | A01 Broken Access Control (IDOR / Cross-Tenant) | CWE-639, CWE-284 | [`cache_and_artifact_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L648-L833), [`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L987-L1095) | **REMEDIATED (`TASK-19A` + `TASK-19B`)** |
| **V3-SEC-06** | `MEDIUM` | A04 Insecure Design / Denial of Service | CWE-1333, CWE-400 | [`brain_consumer_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L412-L445,L1764-L1796) | **REMEDIATED (`TASK-19A`)** |
| **V3-SEC-07** | `MEDIUM` | A03 Injection / A10 SSRF | CWE-20, CWE-22, CWE-89, CWE-918 | [`cache_and_artifact_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1783-L1828) | **REMEDIATED (`TASK-19A`)** |
| **V3-SEC-08** | `MEDIUM` | A05 Security Misconfiguration | CWE-942, CWE-306 | [`api.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L220-L240) | **BASELINE CONFIG (Opt-in Auth/CORS)** |
| **V3-SEC-09** | `LOW` | A04 Insecure Design / A09 Logging Failures | CWE-400, CWE-532 | [`cache_and_artifact_plugins.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L339,L1103), [`registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L558,L592) | **REMEDIATED (v3 Scope, `TASK-19A`)** |

---

## 2. Threat Model & STRIDE Analysis

```
+-----------------------------------------------------------------------------+
| Untrusted / Semi-Trusted Network & MCP Surfaces                             |
| - External Webhook Senders -> POST /v3/webhooks/{source}, /sources/webhook/*|
| - REST Clients (anon / reader / collaborator / admin) -> /plugins/*, /v3/*  |
| - FastMCP SSE / Streamable HTTP / stdio Clients -> projectbrain_v3_* tools  |
+------------------------------+----------------------------------------------+
                               |
                               v
+-----------------------------------------------------------------------------+
| Enforcement & Control Boundary                                              |
| - rest_auth_middleware & mcp_auth_middleware (PB_REQUIRE_REST_AUTH="false"  |
|   by default; admin check omits /plugins/*; SSEFilter & _is_write_tool)     |
| - _validate_safe_path (enforced on REST /plugins/reload & /v3/etl/ingest;   |
|   scoped to CWD, absent on local FastMCP file/workspace tools)              |
+------------------------------+----------------------------------------------+
                               |
                               v
+-----------------------------------------------------------------------------+
| Plugin Microkernel & Execution Runtime                                      |
| - PluginRegistry: manifest loading, Python exec() hot-reload, project cfgs  |
| - Layer 0..2 Builtins: SQL/PG/Neo4j/Qdrant/HSG/Redis/MinIO/ClickHouse/Regex |
+------------------------------+----------------------------------------------+
                               |
                               v
+-----------------------------------------------------------------------------+
| High-Value Data & Compute Assets                                            |
| - Host Python Interpreter & Filesystem                                      |
| - Plugin Config Secrets (webhook_secret, API keys, DB/ClickHouse/Neo4j creds)|
| - Multi-Project UDCP Script Cache, Telemetry Artifacts, Graph & RCA State   |
+-----------------------------------------------------------------------------+
```

### STRIDE Assessment

| Threat | Boundary | Vulnerability / Vector | Status |
|---|---|---|---|
| **S**poofing | Layer 0 Webhook Ingest | Unverified `/v3/webhooks/{source}` by default; pre-auth `project_id` bypasses per-project HMAC secret; unsigned `/sources/webhook/*` | **EXPOSED (`V3-SEC-04`)** |
| **T**ampering | Script Cache & Telemetry Artifacts | Cross-project `cache_id`/`artifact_id` overwrites via `ON CONFLICT DO UPDATE SET project_id=...`; un-scoped `record_execution`, `invalidate_script`, `purge_expired` | **EXPOSED (`V3-SEC-05`)** |
| **T**ampering | Core SQLite / PostgreSQL Graph & Microkernel Tables | Parameterized queries (`?`/`%s`), ANSI `ON CONFLICT`, `sanitize_branch_schema_name`, `psql.Identifier`/`Literal` | **MITIGATED (PASS)** |
| **R**epudiation | Webhook & Plugin Config Mutations | Unauthenticated webhook acceptance when unconfigured; plugin hot-reload/toggle allowed to non-admin `collaborator` without role separation | **PARTIAL (`V3-SEC-01`, `V3-SEC-04`)** |
| **I**nformation Disclosure | Plugin Listing & Local MCP ETL | Plaintext `config` secrets leaked by `GET /plugins` & `projectbrain_list_plugins`; arbitrary local file read in FastMCP `projectbrain_v3_ingest_etl`; cross-project reads on `/v3/artifacts/window` | **EXPOSED (`V3-SEC-02`, `V3-SEC-03`, `V3-SEC-05`)** |
| **D**enial of Service | Oracle Regex & Artifact Purge | Caller-controlled `re.search` in `POST /v3/oracle/context` (`reader`-accessible); global TTL purge via un-scoped `now_ms`; blocking Redis `KEYS` | **EXPOSED (`V3-SEC-05`, `V3-SEC-06`, `V3-SEC-09`)** |
| **E**levation of Privilege | Plugin Control Plane | `collaborator` can call `POST /plugins/reload` (CWD-wide `.py` `exec()`), `PUT /plugins/{id}/config` (SSRF/disable sigs), and `projectbrain_toggle_plugin`; manifest `entrypoint` escapes plugin dir | **EXPOSED (`V3-SEC-01`)** |

---

## 3. Multi-Axis Audit Verification Matrix

### Axis 1: Plugin Discovery & Hot-Reload Safety
- `yaml.safe_load` used for YAML manifests ([`registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L462)): **PASS** (prevents YAML tag deserialization RCE).
- `POST /plugins/reload` enforces `_check_read_only()` against `PB_READ_ONLY` / `OM_READ_ONLY` ([`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L57-L66), [`L453`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L453)): **PASS**.
- `POST /plugins/reload` blocked for `reader` when `PB_REQUIRE_REST_AUTH=true` ([`api.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L64-L85), [`L265-L270`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L265-L270)): **PASS**.
- Manifest `entrypoint` confinement inside plugin subdirectory (`py_path = entry / manifest.entrypoint`, [`registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L464)): **FAIL (`V3-SEC-01`)** — allows `../` and absolute paths.
- `POST /plugins/reload` directory confinement strictly inside configured plugins directory ([`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L458-L461)): **FAIL (`V3-SEC-01`)** — validates against `Path.cwd()` instead of `PB_PLUGINS_DIR` / registered `_plugin_dirs`.
- `admin`-only privilege enforcement on `/plugins/*` ([`api.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L237-L263)): **FAIL (`V3-SEC-01`)** — `collaborator` is permitted to reload code and mutate plugin config.
- `sys.modules` cleanup during `reload_directory` ([`registry.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L418-L423), [`L499-L500`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L499-L500)): **FAIL (`V3-SEC-09`)** — pops `mod_name` rather than timestamped `unique_name`.

### Axis 2: SQL Injection & Cross-Dialect Safety
- [`010_v3_plugin_microkernel.sql`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/migrations/010_v3_plugin_microkernel.sql#L1-L88): **PASS** — 100% ANSI SQLite + PostgreSQL DDL (`TEXT`, `INTEGER`, `BIGINT`, `DOUBLE PRECISION`, `PRIMARY KEY`, `UNIQUE(project_id, signature_hash)`), zero `AUTOINCREMENT`/`$$` blocks.
- [`PluginRegistry`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L98-L186): **PASS** — Parameterized `?` placeholders and ANSI `INSERT ... ON CONFLICT (project_id, plugin_id) DO UPDATE`.
- [`SqlitePgScriptCachePlugin` & `LocalSqliteArtifactStorePlugin`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L664-L845): **PASS (SQLi)** — Parameterized `?` queries translated via [`DB.translate_query`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/core/db.py#L113-L119), ANSI `ON CONFLICT(...) DO UPDATE` (zero legacy `INSERT OR REPLACE`).
- [`RCATriageMemoryPlugin`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L1940-L2010): **PASS** — Parameterized `?` queries and ANSI `ON CONFLICT (project_id, signature_hash) DO UPDATE`.
- [`SqliteOverlayGraphStore` & `PostgresSchemaGraphStore`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/graphify_engine/store.py#L50-L80): **PASS** — Regex-validated schema identifier (`^[a-z_][a-z0-9_]{0,62}$`), `psycopg2.sql.Identifier`/`Literal` for schema DDL/views (`store.py:2401-2462`), parameterized `?`/`%s` queries.
- [`Neo4jGraphStore`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/storage_plugins.py#L249-L286): **PASS** — HTTP Transaction API uses parameterized Cypher `$project_id`, `$branch`, `$version_tag`; `max_hops` cast via `int()` ([`L665-L680`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/storage_plugins.py#L665-L680)) (recommend clamping bounds `1..16`).
- [`MinioClickHouseArtifactStorePlugin`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1734-L1903): **PARTIAL (`V3-SEC-07`)** — Query filter values properly use ClickHouse HTTP bound params (`{param:Type}`), but `clickhouse_database` and `clickhouse_table` from config are f-string interpolated without identifier allowlist validation, and `_build_s3_uri` fails to sanitize `s3://` prefixes or `run_id`/`step_id` path segments.

### Axis 3: Webhook HMAC & Token Verification
- [`WebhookEventIngestPlugin.verify_signature`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L2094-L2135): **PASS (Primitive)** — Constant-time `hmac.compare_digest` for `X-Gitlab-Token`, `X-Webhook-Token`, `X-Hub-Signature-256`, `X-PB-Signature`, and `X-Hub-Signature`. Rejects invalid/missing signatures when `active_secret` is supplied or `allow_unsigned=False`.
- [`POST /v3/webhooks/{source}`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L1013-L1063): **FAIL (`V3-SEC-04`)** — Defaults to fail-open (`require_sig=False`) when no secret env/config is configured; extracts attacker-supplied `project_id` from unverified JSON body before verifying signature; per-project `allow_unsigned` flag mismatches `WebhookEventIngestPlugin.verify_signature`.
- Legacy [`/sources/webhook/github` & `/sources/webhook/notion`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/sources.py#L423-L482): **FAIL (`V3-SEC-04`)** — Completely unsigned endpoints remain mounted.

### Axis 4: REST & FastMCP RBAC Enforcement
- [`_PROTECTED_REST_PREFIXES`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L76-L85): **PASS** — Includes `"/plugins"` and `"/v3"`.
- [`_READ_ONLY_POST_ROUTES`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L64-L74): **PASS (RBAC Scope)** — Permits `reader` only on `/v3/script-cache/lookup`, `/v3/planner/test-dag`, and `/v3/oracle/context` (note DoS risk on `/v3/oracle/context` in `V3-SEC-06`).
- [`_WRITE_TOOL_KEYWORDS`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L19-L48): **PASS** — Verified against all 10 v3 FastMCP tools in [`ai/mcp.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2345-L2879); all 5 mutating v3 tools (`toggle`, `ingest`, `store`, `index`, `triage`) match keywords and block `reader` in `mcp_auth_middleware` / `SSEFilter` and respect `check_read_only()`.
- REST `_validate_safe_path` on `/v3/etl/ingest` ([`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L565)): **PASS**.
- FastMCP `projectbrain_v3_ingest_etl` file path validation ([`mcp.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2485-L2516)): **FAIL (`V3-SEC-03`)** — Reads arbitrary host `file_path` in local mode without workspace validation.
- Multi-tenant / cross-project isolation across `/v3/script-cache/*` & `/v3/artifacts/*` ([`plugins_v3.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L816-L936)): **FAIL (`V3-SEC-05`)**.

### Axis 5: Secret Hygiene
- Static scan (`rg` across `src/projectbrain/plugins/`, `010_v3_plugin_microkernel.sql`, `plugins_v3.py`, and `tests/test_*plugin*.py`):
  - **PASS (Static Code)**: Zero hardcoded API keys, private keys, production webhook secrets, or real DB credentials exist in any v3 source file. All runtime credentials in v3 plugins read from environment variables (`PB_WEBHOOK_SECRET`, `PB_<SOURCE>_WEBHOOK_SECRET`) or dynamic runtime config.
  - **Context Notes (Non-v3 / Test Fixtures)**: Pre-existing legacy seed `"pb_tok_admin"` remains tracked in [`007_admin_seed.sql`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/migrations/007_admin_seed.sql#L2) and [`auth.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/auth.py#L109-L114), a placeholder default DSN appears in [`vector_store.py`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/core/vector_store.py#L114), and synthetic test tokens (`pb_tok_v3_reader`, `gitlab-e2e-secret`, `qdrant-key-123`) appear solely inside `tests/`.
  - **FAIL (Runtime Exposure — `V3-SEC-02`)**: Secrets injected into plugin configuration at runtime are exposed unredacted via `GET /plugins` and `projectbrain_list_plugins`.

---

## 4. Detailed Findings & Remediation Specifications

### [V3-SEC-01] `CRITICAL`: Arbitrary Code Execution via Manifest Entrypoint Traversal, Workspace-Wide Hot-Reload, & Non-Admin Access on `/plugins/*`
- **CWE**: CWE-94 (Improper Control of Generation of Code), CWE-22 (Path Traversal), CWE-269 (Improper Privilege Management)
- **Locations**:
  - [`src/projectbrain/plugins/registry.py:416-426`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L416-L426) (`_load_module_from_file`)
  - [`src/projectbrain/plugins/registry.py:456-483`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L456-L483) (`discover_directory`)
  - [`src/projectbrain/server/routes/plugins_v3.py:450-541`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L450-L541) (`/plugins/reload`, `/plugins/{id}/enable|disable|config`)
  - [`src/projectbrain/server/api.py:237-263`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L237-L263) (`rest_auth_middleware`) and [`L372-L399`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L372-L399) (`mcp_auth_middleware`)
- **Mechanics**:
  1. **Manifest `entrypoint` escape (`registry.py:463-464`)**: `py_path = entry / manifest.entrypoint` performs unvalidated `Path` joining. In Python `pathlib`, an absolute `entrypoint` ignores `entry` entirely, while `../` segments traverse upward outside the plugin bundle, causing `_load_module_from_file` (`registry.py:425`) to execute arbitrary `.py` files via `exec(compile(...))`.
  2. **Overly broad hot-reload scope (`plugins_v3.py:458-461`)**: `POST /plugins/reload` calls `_validate_safe_path(req.directory, "directory")`, which permits **any** directory inside `Path.cwd()` — not strictly the designated plugins directory (`env.plugins_dir` / `PB_PLUGINS_DIR`). Combined with `discover_directory` (`registry.py:477-482`) executing all non-`_` `.py` files in the target directory, pointing `/plugins/reload` at uploaded files, scratch folders, or workspace subdirectories triggers arbitrary Python execution inside the server process.
  3. **Missing `admin` gate (`api.py:237-263`)**: `is_admin_route` only protects `/auth` and `/memory/mcp`, omitting `/plugins/*`. Any `collaborator` (or any unauthenticated caller when `PB_REQUIRE_REST_AUTH` is `"false"`) can call `POST /plugins/reload`, `POST /plugins/{id}/enable|disable`, and `PUT /plugins/{id}/config`, as well as FastMCP `projectbrain_toggle_plugin` (no role check beyond `reader` write-keyword exclusion).
- **Impact**: A `collaborator` (or anonymous client in default auth-off mode) can execute arbitrary Python code in the API server process, hijack backend connections, bypass webhook signature enforcement (`allow_unsigned: true`), or tamper with artifact deletion paths (`artifact_root`).
- **Remediation**:
  1. Enforce `role == "admin"` on `/plugins/reload`, `/plugins/{id}/enable`, `/plugins/{id}/disable`, `/plugins/{id}/config`, and inside `projectbrain_toggle_plugin` / MCP auth middleware for privileged plugin-control tools (`api.py:237-242`).
  2. Confine `manifest.entrypoint` strictly within `entry` inside `registry.py`:
     ```python
     resolved_entry = entry.resolve()
     py_path = (resolved_entry / manifest.entrypoint).resolve()
     try:
         py_path.relative_to(resolved_entry)
     except ValueError as exc:
         raise ValueError(f"Unsafe plugin manifest entrypoint outside plugin directory: {manifest.entrypoint}") from exc
     ```
  3. Restrict `POST /plugins/reload` (`plugins_v3.py:458-461`) so caller-supplied `req.directory` MUST resolve inside `Path(env.plugins_dir).expanduser().resolve()` or an already-registered directory in `reg._plugin_dirs`, rather than arbitrary subdirectories under `cwd` (and optionally require `PB_ALLOW_DYNAMIC_PLUGIN_RELOAD=true` in production). Note: Update `tests/test_v3_plugin_integration.py:266` (`test_plugins_rest_list_toggle_config_and_hot_reload`) so the custom plugin fixture sits within the configured `PB_PLUGINS_DIR`.

---

### [V3-SEC-02] `HIGH`: Unredacted Sensitive Secrets Exposed via `GET /plugins` & `projectbrain_list_plugins`
- **CWE**: CWE-200 (Exposure of Sensitive Information to an Unauthorized Actor), CWE-522 (Insufficiently Protected Credentials)
- **Locations**:
  - [`src/projectbrain/plugins/registry.py:287-301`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L287-L301) (`PluginRegistry.list_plugins`)
  - [`src/projectbrain/server/routes/plugins_v3.py:432-447`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L432-L447) (`GET /plugins`) and [`L535-L541`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L535-L541) (`PUT /plugins/{id}/config`)
  - [`src/projectbrain/ai/mcp.py:2365-2378`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2365-L2378) (`projectbrain_list_plugins`) and [`L2428-L2437`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2428-L2437) (`projectbrain_toggle_plugin`)
- **Mechanics**: `PluginRegistry.list_plugins()` embeds raw `self.get_effective_config(pid, project_id=project_id)` under `"config"`, and both `GET /plugins` (open to `reader` accounts and unauthenticated in default config) and `projectbrain_list_plugins` (allowed for `reader` in MCP) serialize that object without masking sensitive keys (`webhook_secret`, `secret`, `api_key`, `password`, `clickhouse_password`, `redis_url`, `connection_url`, `token`, `auth`).
- **Impact**: Any read-only user (`reader`) querying `/plugins` or calling `projectbrain_list_plugins` immediately extracts plaintext webhook signing secrets, Qdrant/Neo4j/ClickHouse/Redis credentials, and connection strings stored per-project or globally.
- **Remediation**:
  Add a recursive `_redact_plugin_config(cfg: Dict[str, Any]) -> Dict[str, Any]` helper in `registry.py` (or REST/MCP serializers) that replaces values whose lowercase keys match `("secret", "password", "passwd", "token", "api_key", "apikey", "key", "auth")` and redacts credentials in URLs (`_redact_database_url` style for `redis_url`, `connection_url`, `db_url`) before returning responses from `list_plugins`, `GET /plugins`, `PUT /plugins/{id}/config`, `projectbrain_list_plugins`, and `projectbrain_toggle_plugin`.

---

### [V3-SEC-03] `HIGH`: Arbitrary Local File Read / Path Traversal in FastMCP `projectbrain_v3_ingest_etl`
- **CWE**: CWE-22 (Improper Limitation of a Pathname to a Restricted Directory)
- **Locations**:
  - [`src/projectbrain/ai/mcp.py:2485-2516`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2485-L2516) and [`L2568`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2568) (`projectbrain_v3_ingest_etl`)
  - *(Baseline pre-existing local MCP parity gap)*: [`ai/mcp.py:1444-1456`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L1444-L1456) (`projectbrain_local_graphify_diff`), [`L1578-L1580`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L1578-L1580) (`projectbrain_upload_document`), [`L2036`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2036) (`projectbrain_build_complete_kb`), [`L2214-L2254`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/ai/mcp.py#L2214-L2254) (`projectbrain_generate_project_docs` arbitrary `output_dir` mkdir/write).
- **Mechanics**: While REST `POST /v3/etl/ingest` enforces `_validate_safe_path(req.file_path, "file_path")` ([`plugins_v3.py:565`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L565)), FastMCP `projectbrain_v3_ingest_etl` in local/server-embedded mode takes `file_path` directly into `Path(file_path)` (`mcp.py:2486`), reads preview bytes (`L2492`), invokes `parser.parse(source=str(src_path), ...)` (`L2510`), and returns `"result": parsed` directly to the caller (`L2568`). Similarly, `projectbrain_upload_document` and `projectbrain_generate_project_docs` lack workspace path containment when executed via FastMCP over SSE/HTTP.
- **Impact**: Any `collaborator`/`admin` MCP caller on an SSE/HTTP-hosted FastMCP server running in local mode can read and exfiltrate sensitive host files outside the repository workspace (e.g., `/etc/passwd`, environment files, SSH keys, or server config) and write generated documents (`projectbrain_generate_project_docs`) to arbitrary host directories.
- **Remediation**:
  Extract a shared `_validate_mcp_safe_path` helper (enforcing `resolved.relative_to(Path(os.getenv("PB_WORKSPACE_ROOT") or Path.cwd()).resolve())` and rejecting `..`) and apply it in `projectbrain_v3_ingest_etl`, `projectbrain_upload_document`, `projectbrain_local_graphify_diff`, `projectbrain_build_complete_kb`, and `projectbrain_generate_project_docs` (`workspace_path` and `output_dir`) whenever executed on a networked FastMCP transport.

---

### [V3-SEC-04] `HIGH`: Fail-Open Webhook Verification Default, Attacker-Controlled `project_id` Secret Downgrade, & Legacy Unsigned Webhooks
- **CWE**: CWE-306 (Missing Authentication for Critical Function), CWE-345 (Insufficient Verification of Data Authenticity), CWE-639 (Authorization Bypass Through User-Controlled Key)
- **Locations**:
  - [`src/projectbrain/server/routes/plugins_v3.py:1013-1063`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L1013-L1063) (`POST /v3/webhooks/{source}`)
  - [`src/projectbrain/plugins/builtins/brain_consumer_plugins.py:2094-2135`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L2094-L2135) (`WebhookEventIngestPlugin.verify_signature`)
  - [`src/projectbrain/server/routes/sources.py:423-482`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/sources.py#L423-L482) (`/sources/webhook/github`, `/sources/webhook/notion`)
- **Mechanics**:
  1. **Fail-open default (`plugins_v3.py:1045-1049`)**: When no env secret (`PB_<SOURCE>_WEBHOOK_SECRET`, `PB_WEBHOOK_SECRET`), plugin `webhook_secret`/`secret`, or `PB_REQUIRE_WEBHOOK_SIGNATURE=true` is configured, `require_sig` is `False` and `POST /v3/webhooks/{source}` accepts unsigned JSON payloads.
  2. **Pre-authentication `project_id` downgrade (`plugins_v3.py:1025-1043`)**: `project_id` is parsed from the **unverified** JSON body (`payload.get("project_id")`) or query string *before* checking signatures, and then passed to `reg.get_effective_config(..., project_id=project_id)`. If no global env secret is configured and project `A` sets a per-project `webhook_secret`, an attacker sending `{"project_id": "unconfigured_or_other"}` triggers `active_secret = None` and `require_sig = False`, bypassing verification entirely (assuming REST auth is off, which is the default `PB_REQUIRE_REST_AUTH="false"`, OR if a low-privileged token posts webhooks).
  3. **Per-project `allow_unsigned` mismatch (`plugins_v3.py:1047` & `brain_consumer_plugins.py:2103-2104`)**: `plugins_v3.py:1047` checks `"allow_unsigned" in eff_cfg` (key presence, NOT truthiness), and `verify_signature` only checks global `self.config.get("allow_unsigned", False)` rather than receiving `eff_cfg`. Thus if global plugin config enables `allow_unsigned: True`, a project setting `{"allow_unsigned": False}` still has unsigned requests accepted by `verify_signature`.
  4. **Legacy unsigned endpoints (`sources.py:423-482`)**: `/sources/webhook/github` and `/sources/webhook/notion` perform zero signature verification and directly ingest payloads into memory via `ingest_document`.
- **Impact**: Unauthenticated or spoofed callers can forge GitLab/GitHub/Jira/TestRail/Notion webhook events, inject poisoned documents/memories, or trigger bogus build/PR event pipelines.
- **Remediation**:
  1. **Fail closed by default**: Require a configured secret (`PB_WEBHOOK_SECRET` / `PB_<SOURCE>_WEBHOOK_SECRET` / plugin `webhook_secret`) OR explicit `allow_unsigned: True` (boolean `True`, checked from `eff_cfg` with env override). Reject requests with `401` or `503` when neither secret nor explicit `allow_unsigned=True` exists.
  2. Bind `project_id` from a dedicated URL/config secret context or verify against a global/per-source HMAC secret before trusting JSON body fields (`payload["project_id"]`). Pass `allow_unsigned=bool(eff_cfg.get("allow_unsigned", False))` explicitly into `verify_signature(...)` and do not use `"allow_unsigned" in eff_cfg` key-presence checks.
  3. Deprecate or retrofit `sources.py` `/sources/webhook/github` (`X-Hub-Signature-256`) and `/sources/webhook/notion` (`X-Notion-Signature`) with `WebhookEventIngestPlugin.verify_signature`. (If `/v3/webhooks/*` receives external SaaS webhooks while `PB_REQUIRE_REST_AUTH=true`, move HMAC verification cleanly without trusting unauthenticated bodies.)

---

### [V3-SEC-05] `HIGH`: Cross-Project IDOR, Unauthorized Data Read, & Cross-Tenant Overwrite/Deletion in Script Cache & Artifact Store
- **CWE**: CWE-639 (Authorization Bypass Through User-Controlled Key), CWE-284 (Improper Access Control)
- **Locations**:
  - [`src/projectbrain/server/routes/plugins_v3.py:816-936`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/routes/plugins_v3.py#L816-L936)
  - [`src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py:648-690`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L648-L690) (`store_script`), [`L756-L833`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L756-L833) (`record_execution`, `invalidate_script`)
  - [`src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py:1381-1407`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1381-L1407) (`index_artifact`), [`L1469-L1543`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1469-L1543) (`query_evidence_window`, `purge_expired`), [`L1829-L1915`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1829-L1915) (`MinioClickHouseArtifactStorePlugin`)
- **Mechanics**:
  1. **Cross-project script cache mutation & theft (`cache_and_artifact_plugins.py:648, 673-674, 764, 826-830`, `plugins_v3.py:826, 853`)**:
     - `POST /v3/script-cache/feedback` and `POST /v3/script-cache/invalidate` accept `req.project_id` to resolve the plugin, then discard `project_id` when calling `record_execution(cache_id=...)` and `invalidate_script(cache_id=...)`. Both SQL methods filter purely by `WHERE cache_id = ?`.
     - `store_script` allows a caller-supplied `cache_id` (`line 648`) and executes `ON CONFLICT(cache_id) DO UPDATE SET project_id=EXCLUDED.project_id`, letting a caller overwrite or reassign ownership of another project's cached UDCP script.
  2. **Cross-project artifact read, hijack, and global purge (`cache_and_artifact_plugins.py:1381-1395, 1479-1491, 1524-1536, 1838-1903`, `plugins_v3.py:889-936`)**:
     - `index_artifact` computes `_compute_artifact_id(run_id, step_id, artifact_type, ntp_timestamp_ms, uri)` **without `project_id`** (and accepts caller-supplied `artifact_id`), then executes `ON CONFLICT(artifact_id) DO UPDATE SET ... project_id=EXCLUDED.project_id`, overwriting/re-attributing another tenant's artifact record.
     - `GET /v3/artifacts/window` (accessible to `reader` accounts!) passes only `(run_id, center_ts_ms, window_ms)` to `query_evidence_window`, exposing any tenant's telemetry artifacts matching `run_id`.
     - `POST /v3/artifacts/purge` (`plugins_v3.py:921-930`) ignores `req.project_id` inside `purge_expired(now_ms=now_ms)`, executing `DELETE FROM telemetry_artifacts WHERE expires_at <= ?` (and ClickHouse `ALTER TABLE ... DELETE WHERE expires_at <= {cutoff:Int64}`) globally across **all projects**. Supplying `now_ms=9999999999999` wipes all projects' artifact metadata and unlinks managed files under `artifact_root`.
- **Impact**: Multi-tenant data leakage (`GET /v3/artifacts/window`), cross-project UDCP script tampering/invalidation, and complete cross-tenant telemetry wipe via `/v3/artifacts/purge`.
- **Remediation**:
  1. Include `project_id` inside `_compute_artifact_id(...)`, and scope `ON CONFLICT(...) DO UPDATE` or pre-upsert checks so callers cannot hijack an existing `cache_id` / `artifact_id` belonging to a different `project_id`.
  2. Add optional (required on REST/MCP) `project_id` filtering (`AND project_id = ?`) to `record_execution`, `invalidate_script`, `query_evidence_window`, and `purge_expired` across SQLite/PG, Redis, and ClickHouse implementations, and enforce `role == "admin"` (or explicit project-scoped guard) on `POST /v3/artifacts/purge`.

---

### [V3-SEC-06] `MEDIUM`: Regular Expression Denial of Service (ReDoS) on `reader`-Accessible `POST /v3/oracle/context`
- **CWE**: CWE-1333 (Inefficient Regular Expression Complexity), CWE-400 (Uncontrolled Resource Consumption)
- **Locations**:
  - [`src/projectbrain/plugins/builtins/brain_consumer_plugins.py:1136, 1336, 1519-1526`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/brain_consumer_plugins.py#L1519-L1526) (`OracleKnowledgeJudgePlugin._match_pattern`)
  - [`src/projectbrain/server/api.py:73`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L73) (`_READ_ONLY_POST_ROUTES`)
- **Mechanics**: `POST /v3/oracle/context` is explicitly exempted in `_READ_ONLY_POST_ROUTES` (`reader`-accessible) and passes caller-supplied `telemetry_summary.error_rules[].pattern` and `telemetry_summary.srs_criteria[].pattern` straight into Python's backtracking `re.search(pattern, text, re.IGNORECASE | re.MULTILINE)` inside `async def execute`. No regex length limit, AST complexity validation, or subprocess/watchdog timeout is enforced.
- **Impact**: A low-privilege `reader` (or unauthenticated user in default auth-off mode) can submit catastrophic backtracking regex patterns paired with moderate log input, pinning the Python asyncio event-loop worker thread at 100% CPU and hanging all concurrent API requests.
- **Remediation**:
  Cap pattern length (e.g., `<= 256` chars), disallow nested quantifiers or treat unverified caller patterns as literal substring / `re.escape()` matches unless flagged safe, or validate regexes through `re2`/bounded worker execution with a strict timeout.

---

### [V3-SEC-07] `MEDIUM`: Unvalidated ClickHouse Identifiers, S3 Object Key Traversal, & SSRF via Dynamic Plugin Configurations
- **CWE**: CWE-20 (Improper Input Validation), CWE-22 (Path Traversal), CWE-89 (SQL Injection), CWE-918 (Server-Side Request Forgery)
- **Locations**:
  - [`src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py:1591-1597, 1633-1636, 1727-1734, 1813, 1839, 1845, 1901`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L1625-L1740) (`MinioClickHouseArtifactStorePlugin`)
  - [`src/projectbrain/plugins/builtins/storage_plugins.py:214, 267, 1273, 1327`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/storage_plugins.py#L267-L273) (`Neo4jGraphStore`, `QdrantVectorPlugin`)
- **Mechanics**:
  1. `MinioClickHouseArtifactStorePlugin` interpolates `self.clickhouse_database` and `self.clickhouse_table` directly via f-strings into ClickHouse SQL queries without verifying identifier syntax (`^[A-Za-z_][A-Za-z0-9_]{0,63}$`).
  2. `_build_s3_uri` (`1633-1636`) returns caller-supplied `s3://...` URIs verbatim and embeds un-sanitized `run_id` / `step_id` directly in `f"s3://{self.bucket}/{run_id}/{step_id}/{artifact_id}_{suffix}"`. When uploading `payload_bytes` (`1727-1729`), `s3_uri.replace(f"s3://{self.bucket}/", "", 1)` permits `..` segments or unexpected `s3://` prefixes inside the MinIO HTTP `PUT` URL.
  3. Configurable endpoints (`neo4j_uri`, `qdrant_url`, `clickhouse_url`, `minio_endpoint`, `redis_url`) lack `http`/`https` scheme and private/link-local IP blocklist validation, allowing SSRF if `PUT /plugins/{id}/config` or `projectbrain_toggle_plugin` is callable by non-admins (`V3-SEC-01`).
- **Impact**: Malformed/malicious `clickhouse_database`/`clickhouse_table` config values inject arbitrary ClickHouse SQL statements; unvalidated `run_id`/`step_id`/`uri` enables MinIO bucket path manipulation; arbitrary service URLs enable SSRF to cloud metadata (`169.254.169.254`) or internal services.
- **Remediation**:
  Validate `clickhouse_database` and `clickhouse_table` against `^[A-Za-z_][A-Za-z0-9_]{0,63}$` in `validate_config` / `__init__`; sanitize `run_id`/`step_id` path segments (`re.sub(r"[^A-Za-z0-9_.-]", "_", ...)`), normalize/reject `..` and mismatched `s3://` buckets in `_build_s3_uri` / `index_artifact`; restrict configurable external URLs in `validate_config` (plus enforce `admin`-only config edits per `V3-SEC-01`).

---

### [V3-SEC-08] `MEDIUM`: Permissive CORS Wildcard & Opt-In REST Authentication Default
- **CWE**: CWE-942 (Permissive Cross-domain Policy with Untrusted Domains), CWE-306 (Missing Authentication for Critical Function)
- **Locations**:
  - [`src/projectbrain/server/api.py:199-205, 216-218`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/server/api.py#L199-L218)
- **Mechanics**: FastAPI mounts `CORSMiddleware` with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`, and `rest_auth_middleware` bypasses all token/RBAC checks unless `PB_REQUIRE_REST_AUTH` is explicitly set to `"true"`. In out-of-the-box deployments, all `/plugins/*` and `/v3/*` endpoints (including `V3-SEC-01` hot-reload and `V3-SEC-02` secret listing) are completely unauthenticated and cross-origin reachable from any browser origin.
- **Impact**: Cross-origin web pages or unauthenticated LAN attackers can exploit `/plugins/*` and `/v3/*` endpoints when deployed without manual environment hardening.
- **Remediation**:
  Constrain CORS origins via `PB_CORS_ORIGINS` (defaulting away from wildcard `"*"` when authentication headers are used), and enforce fail-secure defaults (`PB_REQUIRE_REST_AUTH="true"` or explicit localhost-only dev binding warning on startup if disabled).

---

### [V3-SEC-09] `LOW`: Unescaped Redis Glob Patterns, `sys.modules` Leak on Hot-Reload, & DSN Credential Logging
- **CWE**: CWE-400 (Uncontrolled Resource Consumption), CWE-532 (Insertion of Sensitive Information into Log File)
- **Locations**:
  - [`src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py:977-987, 1008-1015`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/builtins/cache_and_artifact_plugins.py#L977-L1015) (`RedisScriptCachePlugin`)
  - [`src/projectbrain/plugins/registry.py:418-423, 471-480, 499-500`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/plugins/registry.py#L499-L500) (`PluginRegistry.reload_directory`)
  - [`src/projectbrain/core/vector_store.py:116`](file:///Users/macbbook/SourceCodes/Project-Brain/src/projectbrain/core/vector_store.py#L116)
- **Mechanics & Remediation**:
  1. `RedisScriptCachePlugin` concatenates unescaped `project_id`/`intent_id`/`platform`/`build_version` into Redis key patterns and calls `backend.keys(pattern)` ahead of `scan_iter`. Escape glob metacharacters (`*`, `?`, `[`, `]`, `\`) before key lookup and prefer `scan_iter` over blocking `KEYS`.
  2. `reload_directory` pops un-timestamped `mod_name` from `sys.modules`, whereas `_load_module_from_file` inserts `unique_name = f"{module_name}_{time.time_ns()}"`, leaking stale module entries across reloads. Return and track `module.__name__` in `_dir_modules`.
  3. `core/vector_store.py:116` logs raw `dsn` (`f"Using PostgresVectorStore at {dsn}"`); wrap with `_redact_database_url(dsn)` so embedded passwords never hit logs.

---

## 5. Verification Evidence Observed (Post-Remediation)

Full repository automated verification was executed after `TASK-19A` and `TASK-19B` remediations:

```bash
pytest -v
```

- **Observed Full-Suite Result**: `275 passed, 5 skipped, 79 warnings in 27.64s` (exit code `0`, `280` collected across `28` test suites; the 5 skips are pre-existing live PostgreSQL and MiniMax external API tests).
- **Verified Remediation Coverage (`tests/test_v3_security_remediation.py` — 7/7 passed)**:
  - `V3-SEC-01`: `collaborator` receives `403` on mutating `/plugins/*` REST routes and FastMCP `projectbrain_toggle_plugin` (and `SSEFilter` omits it) while `admin` receives `200` and `GET /plugins` remains open to `reader`/`collaborator`/`admin`; `POST /plugins/reload` rejects `"src"`, `"../"`, `"../outside"`, and `"/etc"` with `400`; `manifest.entrypoint="../evil.py"` is rejected with `ValueError`.
  - `V3-SEC-02`: `webhook_secret`, `api_key`, `password`, `access_token`, and DSN credentials in `redis_url` are masked (`"***"`) in `GET /plugins`, `PUT /plugins/{id}/config`, `projectbrain_list_plugins`, and `projectbrain_toggle_plugin` while internal effective config retains plaintext values.
  - `V3-SEC-03`: `projectbrain_v3_ingest_etl` rejects `../../etc/passwd`, `/etc/passwd`, `/proc/self/environ`, `/sys/kernel/notes`, `/var/run/docker.sock`, `~/.ssh/id_rsa`, `~/.aws/credentials`, and `~/.gnupg/secring.gpg` while allowing legitimate workspace and pytest `tmp_path` files.
  - `V3-SEC-04` / `HIGH-03`: `POST /v3/webhooks/gitlab` fails closed (`401`) when unconfigured or `allow_unsigned=False`, blocks unverified payload `project_id="other"` from downgrading/clearing active global secrets (`401`), and enforces `PB_REQUIRE_WEBHOOK_SIGNATURE=true`.
  - `V3-SEC-05`: Cross-project `purge_expired(project_id="proj_a")`, `GET /v3/artifacts/window`, `POST /v3/script-cache/{feedback,invalidate}`, `store_script`, and `index_artifact` strictly preserve tenant ownership.
  - `V3-SEC-06`, `V3-SEC-07`, `HIGH-01`: `OracleKnowledgeJudgePlugin` handles pathological `(a+)+$` ReDoS patterns without hanging, `MinioClickHouseArtifactStorePlugin` rejects SQLi ClickHouse database identifiers, and `GraphEdge.metadata` `expected_signals` round-trip cleanly into `OracleKnowledgeJudgePlugin`.

---

## 6. Prioritized Remediation Roadmap

1. **Immediate (Blocking Release — `CRITICAL` / `HIGH`)**:
   - **R1 (`V3-SEC-01`)**: Add `/plugins` to `is_admin_route` in `api.py`; restrict `projectbrain_toggle_plugin` to `admin`; enforce `relative_to(entry.resolve())` on `manifest.entrypoint` in `registry.py`; lock `POST /plugins/reload` directories strictly to `env.plugins_dir` / registered `_plugin_dirs`.
   - **R2 (`V3-SEC-02`)**: Redact secret/credential fields and URLs before returning `config` from `PluginRegistry.list_plugins`, `GET /plugins`, `PUT /plugins/{id}/config`, `projectbrain_list_plugins`, and `projectbrain_toggle_plugin`.
   - **R3 (`V3-SEC-03`)**: Apply shared workspace path validation (`_validate_safe_path` equivalent) inside `projectbrain_v3_ingest_etl` and sibling local-mode FastMCP file/workspace tools.
   - **R4 (`V3-SEC-04`)**: Make `/v3/webhooks/{source}` fail closed unless a secret is configured or per-project/global `allow_unsigned is True`; resolve secrets without trusting unauthenticated `payload["project_id"]`; deprecate/sign `/sources/webhook/*`.
   - **R5 (`V3-SEC-05`)**: Enforce `project_id` ownership checks on `store_script`, `record_execution`, `invalidate_script`, `index_artifact`, `query_evidence_window`, and `purge_expired` across all cache/artifact backends; require `admin` for global purge.
2. **Short-Term Hardening (`MEDIUM` / `LOW`)**:
   - **R6 (`V3-SEC-06`)**: Add regex complexity/length guards or literal fallback in `OracleKnowledgeJudgePlugin._match_pattern`.
   - **R7 (`V3-SEC-07`)**: Validate ClickHouse database/table identifiers (`^[A-Za-z_][A-Za-z0-9_]{0,63}$`), sanitize S3 object keys/URIs, and validate external service URL schemes/hosts in plugin `validate_config`.
   - **R8 (`V3-SEC-08`, `V3-SEC-09`)**: Restrict default CORS origins & REST auth fail-secure defaults; escape Redis glob keys & use `scan_iter`; track `unique_name` in `_dir_modules`; redact DSN in `core/vector_store.py`.
3. **Regression Testing**: Add targeted security tests covering R1–R7 inside `tests/test_security_hardening.py` and `tests/test_v3_plugin_integration.py`.
