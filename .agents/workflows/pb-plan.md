---
name: pb-plan
description: ProjectBrain Plan Command - Run planning-and-task-breakdown + api-and-interface-design (+ sub/*.md) with cross-source impact to produce docs/plans/{feature_id}.plan.md
---

# `/pb-plan` — Multi-Source Architecture & Task DAG Planning

Transform a specification into an architectural contract and disjoint-file Task DAG across sub-sources.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read [`.agents/skills/planning-and-task-breakdown/SKILL.md`](../skills/planning-and-task-breakdown/SKILL.md) **AND** all `.agents/skills/planning-and-task-breakdown/sub/*.md` (excluding `README.md`).
   - Read [`.agents/skills/api-and-interface-design/SKILL.md`](../skills/api-and-interface-design/SKILL.md) **AND** all `.agents/skills/api-and-interface-design/sub/*.md` (excluding `README.md`).
   - Apply `sub/*.md` overrides over `SKILL.md` defaults.
3. **Analyze Cross-Source Blast Radius**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<api/schema/symbol>", source_id="<source_id>", relation_depth=2)`.
   - Call `projectbrain_query_graphify(query="<architecture/endpoints>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.

## 2. Execution (Subagent or Single-Agent Persona)
- **If subagents are supported** (`invoke_subagent` / `Task` / `Agent`): Spawn a `task-planner` subagent injected with both skills, their `sub/*.md` overrides, `docs/specs/{feature_id}.spec.md`, and ProjectBrain cross-source context.
- **If single-agent**: Adopt the `task-planner` persona step-by-step.

## 3. Deliverables & Post-Edit Sync
1. Save `docs/plans/{feature_id}.plan.md` containing:
   - System Topology & Cross-Source Contracts (`proj@source:branch`).
   - Disjoint Task DAG table (`Task ID`, `Persona`, `Source ID`, `Parallel Group`, `Depends On`, `Disjoint Target Files`).
   - Verification & 3-Stage Circuit Breaker policy.
2. If shared contracts/docs were updated, sync via `projectbrain_upload_document(project_id="<project_id>", scope="project_shared", file_path="...")` or `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
