# Specification: Dynamic Base Branch (`develop` -> `feat/*`) Delta, HSG Vector Inheritance & Document Versioning

> **Specification ID**: `SPEC-BRANCH-DELTA-DOC-VER-01`  
> **Task**: `TASK-49` (Checkpoint 1 Spec for `TASK-50`, `TASK-51`, `TASK-52`)  
> **Target Workspace**: `/Users/macbbook/SourceCodes/Project-Brain`  
> **Branch**: `feat/branch-delta-and-doc-versioning`

---

## 1. Goals & Scope

Eliminate full repository re-parsing, full network payload uploads, duplicate vector embeddings across child branches, and stale RAG pollution across document revisions (`v1.0 -> v1.1`):

1. **Multi-Level Base Branch Code Delta (`TASK-50`)**:
   - Allow configuring canonical `root_branch` via `PB_ROOT_BRANCH` (`OM_ROOT_BRANCH`, default `"main"`), e.g. `"develop"`.
   - Support arbitrary parent branch chains (`main -> develop -> feat/*`, cycle-checked up to depth 8) in `SqliteOverlayGraphStore`, `PostgresSchemaGraphStore`, and `HybridResolver`.
   - When syncing child branch `feat/x` with `base_branch="develop"`, compute `*_upserts` and `*_tombstones` relative to the **resolved state of `develop`**, not stale `"main"`.
   - Enable client auto-delta extraction via `git diff --name-status <base_branch>...HEAD` + uncommitted `git status --porcelain` in `GraphifyExtractor.extract_workspace(..., base_branch=...)` and transmit `deleted_files` cleanly in CLI (`main.py`) & MCP (`ai/mcp.py`).
   - Support promoting a child branch (`feat/x`) atomically into its parent branch (`develop`) or canonical root.

2. **HSG Vector Memory Ancestor Overlay Inheritance & Tombstones (`TASK-51`)**:
   - Add `memory_branch_links(project_id, branch, base_branch, updated_at)` and `memory_overlay_tombstones(project_id, branch, target_type, target_key, created_at)` (`target_type` in `{'file', 'doc'}`) via SQLite/Postgres compatible migration `008_memory_branch_overlay.sql`.
   - Provide `register_memory_branch`, `record_memory_tombstone`, `get_memory_branch_lineage`, and file/doc chunk replacement helpers that clean up old vectors on the child branch before inserting updated chunks.
   - Update `hsg_query` (`inherit_base: bool = True`) to resolve registered ancestor `user_id` chains, search across lineage buckets, shadow ancestor chunks overridden or tombstoned on closer descendant branches, and preserve strict isolation when branches are unlinked (`tests/test_branch_search.py`).
   - Update `sync_codebase_memories` (`ai/mcp.py`) to accept `base_branch` so only git-changed files are embedded into `proj:feat/x` and deleted/modified files record tombstones against `proj:develop`.

3. **Document Versioning & Page/Section Delta Engine (`TASK-52` — Checkpoint 2)**:
   - Track `(project_id, branch, doc_key)` document revisions (`v1.0`, `v1.1`, ...) and per-page / per-section SHA256 fingerprints (`documents`, `doc_versions`, `doc_page_cache`).
   - Skip re-OCR and re-embedding on unchanged pages/chunks when ingesting `Spec_v2.pdf`, replace only modified/removed sections, and link version-scoped tombstones cleanly.

---

## 2. Backward Compatibility Contracts

- Unconfigured environments default `PB_ROOT_BRANCH="main"`, keeping all existing root-branch behavior unchanged.
- `GraphStore` methods keep default `base_branch=None` / `target_branch=None` signatures.
- Unregistered `user_id` values (`proj:feature-x` without a `memory_branch_links` or `graph_branches` parent entry) remain strictly isolated so `tests/test_branch_search.py` and existing multi-tenant tests stay green.
