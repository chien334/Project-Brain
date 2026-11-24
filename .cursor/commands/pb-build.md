# `/pb-build` — Scoped Sub-Source Implementation

Implement disjoint plan slices inside their target `source_id` with zero cross-source contract drift.

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill(s) + Overrides**:
   - Read `.agents/skills/incremental-implementation/SKILL.md` + all `.agents/skills/incremental-implementation/sub/*.md` (excluding `README.md`).
   - Also load `frontend-ui-engineering` (+ `sub/*.md`) for UI work and `api-and-interface-design` (+ `sub/*.md`) for API/schema contracts.
3. **Pre-Edit Context & Impact Check**:
   - Call `projectbrain_hybrid_query(query="<task slice>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - Before modifying APIs, DTOs, or events, call `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")`.
4. **Execute**: Spawn a `builder` subagent per disjoint slice (if supported) or adopt the `builder` persona sequentially with `<skill>/SKILL.md` + `sub/*.md` + ProjectBrain context.
5. **Verify & Post-Edit Sync**: Run local syntax/compile checks, then run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", workspace_path="<subpath_or_dot>", upgrade_mode="delta", auto_link_sources=True)`.
