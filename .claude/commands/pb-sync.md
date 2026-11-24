---
description: Incrementally sync modified sub-sources (Graphify delta), upload shared docs, and refresh cross-source links
argument-hint: "[source_id or 'all']"
---

# `/pb-sync` — Incremental Multi-Source Hub Sync

Scope: `$ARGUMENTS`

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read `.agents/skills/documentation-and-adrs/SKILL.md` + all `.agents/skills/documentation-and-adrs/sub/*.md` (excluding `README.md`).
   - Read `.agents/skills/git-workflow-and-versioning/SKILL.md` + all `.agents/skills/git-workflow-and-versioning/sub/*.md` (excluding `README.md`).
3. **Delta-Sync Changed Sub-Sources**:
   For each modified `source_id` (from `git status --short` or `$ARGUMENTS`), run:
   ```text
   projectbrain_sync_graphify(
     project_id="<project_id>",
     source_id="<source_id>",
     workspace_path="<source_subpath_or_dot>",
     git_dirty_only=True,
     upgrade_mode="delta",
     auto_link_sources=True
   )
   ```
4. **Sync Modified Shared Docs**:
   For changed specs, ADRs, or OpenAPI contracts, call `projectbrain_upload_document(project_id="<project_id>", scope="project_shared", file_path="<doc_path>")`.
5. **Verify Links & Coverage**:
   Run `projectbrain_auto_link_sources(project_id="<project_id>")` and `projectbrain_kb_coverage_report(project_id="<project_id>")` if needed, then summarize synced sources and any cross-source drift warnings.
