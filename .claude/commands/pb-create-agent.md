---
description: Dynamically instantiate a specialized subagent from any .agents/skills/<skill>/SKILL.md + sub/*.md + ProjectBrain MCP context
argument-hint: "<skill-name> <source_id> <task>"
---

# `/pb-create-agent` — Dynamic Role-Based Agent Creator

Request: `$ARGUMENTS`

## 1. Hub & Skill Assembly
1. **Verify ProjectBrain Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, run `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Project Overrides**:
   - Read `.agents/skills/<skill>/SKILL.md`.
   - Read **all** `.agents/skills/<skill>/sub/*.md` files (excluding `README.md`). Apply `sub/*.md` as overrides over `SKILL.md`.
3. **Fetch Scoped Context**:
   - Call `projectbrain_hybrid_query(query="<task>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - If modifying contracts/APIs/schemas, call `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")`.

## 2. Spawn Subagent (`Agent` / `Task` tool) or Adopt Persona
Inject this brief into the spawned `Agent` / `Task` subagent (or follow it directly if running single-agent):
- **Role & Tenant**: `<role>` scoped to `<project_id>@<source_id>:<branch>`.
- **Skill & Overrides**: Contents/paths of `.agents/skills/<skill>/SKILL.md` and `.agents/skills/<skill>/sub/*.md` (excluding `README.md`).
- **ProjectBrain Context**: Scoped `hybrid_query` hits, `__shared__` specs, and `cross_source_impact` results.
- **Disjoint Scope & Verification**: Exact target files and required local checks.
- **Post-Edit Sync**: After modifying code in `<source_id>`, run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
