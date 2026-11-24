# `/pb-create-agent` — Dynamic Role-Based Agent Creator

Instantiate a specialized role agent or persona from any `.agents/skills/<skill>/SKILL.md` + `sub/*.md` + ProjectBrain MCP context.

## 1. Hub & Skill Assembly
1. **Verify ProjectBrain Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Overrides**:
   - Read `.agents/skills/<skill>/SKILL.md`.
   - Read **all** `.agents/skills/<skill>/sub/*.md` files (excluding `README.md`), applying `sub/*.md` as project-specific overrides.
3. **Fetch Scoped Context**:
   - Call `projectbrain_hybrid_query(query="<task>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")` if contracts/APIs may change.

## 2. Role Brief & Execution
- **If subagents are available**: Spawn a role-based subagent injected with:
  - Role persona & tenant scope (`<project_id>@<source_id>:<branch>`)
  - `<skill>/SKILL.md` + `<skill>/sub/*.md` overrides
  - ProjectBrain `hybrid_query` / `cross_source_impact` findings
  - Disjoint allowed files + output contract
- **If single-agent**: Adopt the assembled role persona directly and execute step-by-step.
- **Mandatory Post-Edit Sync**: After editing code in `<source_id>`, run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
