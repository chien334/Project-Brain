---
name: pb-build
description: ProjectBrain Build Command - Implement disjoint sub-source changes using incremental-implementation / frontend-ui-engineering / api-and-interface-design (+ sub/*.md) and sync Graphify delta
---

# `/pb-build` — Scoped Sub-Source Implementation

Implement a feature or plan slice cleanly inside its target `source_id` with zero cross-source contract drift.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Target Skill(s) + Overrides**:
   - Load [`.agents/skills/incremental-implementation/SKILL.md`](../skills/incremental-implementation/SKILL.md) **AND** all `.agents/skills/incremental-implementation/sub/*.md` (excluding `README.md`).
   - For UI work, also load [`../skills/frontend-ui-engineering/SKILL.md`](../skills/frontend-ui-engineering/SKILL.md) + its `sub/*.md`.
   - For API/schema contracts, also load [`../skills/api-and-interface-design/SKILL.md`](../skills/api-and-interface-design/SKILL.md) + its `sub/*.md`.
   - Apply `sub/*.md` as project-specific overrides.
3. **Pre-Edit Context & Impact Check**:
   - Call `projectbrain_hybrid_query(query="<target symbols/files>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - Before changing any API route, DTO, event, or shared schema, call `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")`.

## 2. Execution (Subagent or Single-Agent Persona)
- **If subagents are supported** (`invoke_subagent` / `Task` / `Agent`): Spawn one `builder` subagent per disjoint slice (`Model="flash"` for `<=3` files; `Model="pro"` for `>=4` files or security-sensitive code) injected with `<skill>/SKILL.md` + `sub/*.md` + ProjectBrain context.
- **If single-agent**: Adopt the `builder` persona sequentially per disjoint slice.

## 3. Deliverables & Post-Edit Sync
1. Edit ONLY the assigned disjoint files for `{source_id}` and run local syntax/type/compile checks (`tsc --noEmit`, `py_compile`, etc.).
2. **MANDATORY Post-Edit Sync**: After completing edits in `<source_id>`, run:
   ```text
   projectbrain_sync_graphify(
     project_id="<project_id>",
     source_id="<source_id>",
     workspace_path="<subpath_or_dot>",
     upgrade_mode="delta",
     auto_link_sources=True
   )
   ```
