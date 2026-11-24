---
description: Create an architectural plan and disjoint-file Task DAG using planning-and-task-breakdown + api-and-interface-design (+ sub/*.md)
argument-hint: "<spec path or feature_id>"
---

# `/pb-plan` — Multi-Source Architecture & Task DAG Planning

Target: `$ARGUMENTS`

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read `.agents/skills/planning-and-task-breakdown/SKILL.md` + all `.agents/skills/planning-and-task-breakdown/sub/*.md` (excluding `README.md`).
   - Read `.agents/skills/api-and-interface-design/SKILL.md` + all `.agents/skills/api-and-interface-design/sub/*.md` (excluding `README.md`).
3. **Analyze Cross-Source Impact**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<api/schema/symbol>", source_id="<source_id>", relation_depth=2)`.
   - Call `projectbrain_query_graphify(query="$ARGUMENTS", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
4. **Execute**: Spawn a `task-planner` subagent via `Agent`/`Task` injected with both skills, `sub/*.md` overrides, spec, and ProjectBrain context (or adopt the persona directly).
5. **Deliver & Sync**: Save `docs/plans/{feature_id}.plan.md` with cross-source contracts and a disjoint-file Task DAG scoped by `source_id`. Sync any modified shared docs/contracts via `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)` or `projectbrain_upload_document(..., scope="project_shared")`.
