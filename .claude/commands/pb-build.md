---
description: Implement disjoint sub-source changes using incremental-implementation / frontend-ui-engineering / api-and-interface-design (+ sub/*.md)
argument-hint: "<task slice, plan path, or feature>"
---

# `/pb-build` — Scoped Sub-Source Implementation

Target: `$ARGUMENTS`

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill(s) + Overrides**:
   - Read `.agents/skills/incremental-implementation/SKILL.md` + all `.agents/skills/incremental-implementation/sub/*.md` (excluding `README.md`).
   - Also load `frontend-ui-engineering` (+ `sub/*.md`) for UI tasks and `api-and-interface-design` (+ `sub/*.md`) for API/schema tasks.
3. **Pre-Edit Context & Impact Check**:
   - Call `projectbrain_hybrid_query(query="$ARGUMENTS", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - Before modifying APIs, DTOs, or events, call `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")`.
4. **Execute**: Spawn a `builder` subagent per disjoint slice via `Agent`/`Task` injected with `<skill>/SKILL.md` + `sub/*.md` + ProjectBrain context (or implement sequentially as `builder`).
5. **Verify & Post-Edit Sync**: Run local syntax/compile checks, then run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", workspace_path="<subpath_or_dot>", upgrade_mode="delta", auto_link_sources=True)`.
