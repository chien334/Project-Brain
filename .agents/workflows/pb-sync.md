---
name: pb-sync
description: ProjectBrain Sync Command - Incrementally sync modified sub-sources via Graphify delta, upload shared docs, and verify cross-source links
---

# `/pb-sync` — Incremental Multi-Source Hub Sync

Keep ProjectBrain's AST graphs, HSG memories, `__shared__` documents, and cross-source edges fresh after changes.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read [`.agents/skills/documentation-and-adrs/SKILL.md`](../skills/documentation-and-adrs/SKILL.md) **AND** all `.agents/skills/documentation-and-adrs/sub/*.md` (excluding `README.md`).
   - Read [`.agents/skills/git-workflow-and-versioning/SKILL.md`](../skills/git-workflow-and-versioning/SKILL.md) **AND** all `.agents/skills/git-workflow-and-versioning/sub/*.md` (excluding `README.md`).

## 2. Execution Steps
1. **Detect Modified Sub-Sources & Shared Docs**:
   - Run `git status --short` (or `projectbrain_local_graphify_diff`) and map changed files to registered `source_id`s from `projectbrain_list_sources(project_id="<project_id>")`.
2. **Delta-Sync Each Modified Sub-Source**:
   ```text
   projectbrain_sync_graphify(
     project_id="<project_id>",
     source_id="<source_id>",
     workspace_path="<source_subpath_or_dot>",
     branch="<current_branch>",
     git_dirty_only=True,
     upgrade_mode="delta",
     auto_link_sources=True
   )
   ```
3. **Sync Shared Docs / Contracts (`__shared__`)**:
   - For modified specs, OpenAPI files, or ADRs, call:
     ```text
     projectbrain_upload_document(
       project_id="<project_id>",
       scope="project_shared",
       file_path="<doc_path>"
     )
     ```
4. **Refresh Cross-Source Links & Verify Coverage**:
   - Run `projectbrain_auto_link_sources(project_id="<project_id>", branch="<current_branch>")` if new APIs/schemas were introduced.
   - Optionally check `projectbrain_kb_coverage_report(project_id="<project_id>")` and `projectbrain_cross_source_impact` for any unresolved drift.
5. **Report Summary**: List synced `source_id`s, uploaded shared docs, and any cross-source warnings.
