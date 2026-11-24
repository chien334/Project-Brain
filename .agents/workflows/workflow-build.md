---
name: workflow-build
description: Stage 3 Implementation Workflow - Execute disjoint sub-source build slices with SKILL.md + sub/*.md overrides, ProjectBrain impact checks, and delta Graphify sync
---

# Stage 3: Parallel Software Implementation Workflow

## 0. Mandatory ProjectBrain Hub & Skill Override Gate
1. **Ensure ProjectBrain Hub is Initialized**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Target Skill + Project Overrides**:
   - Load the slice's primary skill (`incremental-implementation`, `frontend-ui-engineering`, or `api-and-interface-design` in `../skills/<skill>/SKILL.md`) **AND** all `.agents/skills/<skill>/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific overrides.
3. **Pre-Edit Sub-Source Context & Impact Check**:
   - Query `projectbrain_hybrid_query(query="<task slice symbols/contracts>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - Before modifying any API route, DTO, event, or shared schema, run `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol/route>", source_id="<source_id>")`.

## 1. Objective & Persona
- **Persona**: `builder` (`builder-fe`, `builder-be`, `builder-db`, `ui-builder`)
- **Execution Mode**:
  - **Multi-Agent IDEs**: Spawn a `builder` subagent (`invoke_subagent` / `Task` / `Agent`) per disjoint slice injected with `<skill>/SKILL.md` + `<skill>/sub/*.md` + scoped ProjectBrain context.
  - **Dual-Channel Orchestrator (`HYBRID_60_DESKTOP`, max 1 heavy slice per `parallel_group`)**:
    ```bash
    python3 ~/.gemini/tools/orchestrator/client.py desktop-ask \
      --task "{TASK_ID}" --agent builder --workspace . --allow-edits \
      --prompt "Implement {TASK_ID} per docs/plans/{feature_id}.plan.md using <skill>/SKILL.md + sub/*.md overrides. Scope is strictly limited to {TARGET_FILES} in source_id={SOURCE_ID}. Run local syntax/compile checks and report exact modified files."
    ```
  - **Channel B Sibling Slices**: `invoke_subagent(Model="flash")` (`gemini-4-hydrogen`, `<=3` files) or `invoke_subagent(Model="pro")` (`gemini-4-neon`, `>=4` files; `gemini-4-carbon` for security-sensitive auth/crypto/migrations).
  - **Single-Agent IDEs**: Adopt the `builder` persona sequentially per task slice.

## 2. Input Contract
- **Prerequisite**: Stage 2 Plan completed and `depends_on` tasks completed.
- **Input Slice**: Task ID from `docs/plans/{feature_id}.plan.md` with target `source_id` and disjoint `TARGET_FILES`.

## 3. Execution Steps
1. **Emit Start Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "{TASK_ID}" --agent "builder" --model "gemini-4-hydrogen" \
     --reasoning-effort "high" --group "{PARALLEL_GROUP}" --status "running" --progress 10 \
     --scope "{TARGET_FILES}" --notes "Implementing disjoint slice in {SOURCE_ID}"
   ```
2. **Minimal Blast-Radius Implementation**:
   - Follow `SKILL.md` + `sub/*.md` rules and plan interface contracts strictly.
   - Touch ONLY `{TARGET_FILES}` assigned to this slice.
   - Run local syntax/type/compile checks (`python3 -m py_compile`, `tsc --noEmit`, etc.).
3. **Post-Edit ProjectBrain Delta Sync (MANDATORY)**:
   - After code edits in `{SOURCE_ID}`, run:
     ```text
     projectbrain_sync_graphify(
       project_id="<project_id>",
       source_id="<source_id>",
       workspace_path="<subpath_or_dot>",
       upgrade_mode="delta",
       auto_link_sources=True
     )
     ```
4. **3-Stage Circuit Breaker (Channel B API Only)**:
   - Stage 1 (`retry_count == 1`): `gemini-4-hydrogen` (`flash`, `xhigh`).
   - Stage 2 (`retry_count == 2`): `gemini-4-neon` (`pro`, `high`).
   - Stage 3 (`retry_count >= 3` or security impact): `gemini-4-carbon` (`pro`, `high`).
5. **Emit Completion Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "{TASK_ID}" --agent "builder" --model "gemini-4-hydrogen" \
     --reasoning-effort "high" --group "{PARALLEL_GROUP}" --status "completed" --progress 100 \
     --scope "{TARGET_FILES}" --notes "Implementation complete, syntax passed, ProjectBrain synced"
   ```

## 4. Output Contract
- Modified/created source files on disk + fresh delta Graphify index for `{SOURCE_ID}`.
- Worker handoff summary listing exact modified files, syntax check status, and ProjectBrain sync confirmation.

> **Handoff**: Once all parallel builders complete fan-in, trigger **Stage 4 ([`workflow-test.md`](./workflow-test.md))**.
