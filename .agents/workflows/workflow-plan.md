---
name: workflow-plan
description: Stage 2 Planning Workflow - Ingest docs/specs/{feature_id}.spec.md, load SKILL.md + sub/*.md overrides, analyze cross-source impact via ProjectBrain, and produce docs/plans/{feature_id}.plan.md
---

# Stage 2: Architecture & Task DAG Planning Workflow

## 0. Mandatory ProjectBrain Hub & Skill Override Gate
1. **Ensure ProjectBrain Hub is Initialized**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Project Overrides**:
   - Load [`../skills/planning-and-task-breakdown/SKILL.md`](../skills/planning-and-task-breakdown/SKILL.md) **AND** all `.agents/skills/planning-and-task-breakdown/sub/*.md` (excluding `README.md`).
   - Load [`../skills/api-and-interface-design/SKILL.md`](../skills/api-and-interface-design/SKILL.md) **AND** all `.agents/skills/api-and-interface-design/sub/*.md` (excluding `README.md`).
   - Apply `sub/*.md` files as project-specific overrides over `SKILL.md` defaults.
3. **Query Multi-Source Topology & Impact**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<api/schema/symbol>", source_id="<source_id>", relation_depth=2)` and `projectbrain_query_graphify(query="<feature architecture>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` to map affected sub-sources (`proj@source:branch`).

## 1. Objective & Persona
- **Persona**: `task-planner`
- **Execution Mode**:
  - **Multi-Agent IDEs**: Spawn a `task-planner` subagent (`invoke_subagent` / `Task` / `Agent`) injected with both skills, their `sub/*.md` overrides, and ProjectBrain cross-source context.
  - **Dual-Channel Orchestrator (`HYBRID_60_DESKTOP`)**: Dispatch via `run_command` (`WaitMsBeforeAsync: 500`):
    ```bash
    python3 ~/.gemini/tools/orchestrator/client.py desktop-ask \
      --task "PLAN-{FEATURE}" --agent task-planner --workspace . \
      --output "docs/reports/desktop_worker/PLAN-{FEATURE}.md" \
      --prompt "Read docs/specs/{feature_id}.spec.md. Apply planning-and-task-breakdown + api-and-interface-design SKILL.md and sub/*.md overrides. Design system topology, cross-source contracts, and a disjoint-file Task DAG for docs/plans/{feature_id}.plan.md. Read-only synthesis only."
    ```
  - **Channel B (`API_ONLY_100`)**: `invoke_subagent(Model="pro")` (`gemini-4-neon`, `reasoning_effort: medium | high`; escalate to `gemini-4-carbon` for security/critical architecture).
  - **Single-Agent IDEs**: Adopt the `task-planner` persona directly and execute Section 3 step-by-step.

## 2. Input Contract
- **Prerequisite**: Stage 1 complete (`docs/specs/{feature_id}.spec.md`).
- **Context Provided**: Registered sub-sources (`projectbrain_list_sources`), cross-source blast radius, and spec acceptance criteria.

## 3. Execution Steps
1. **Emit Start Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "PLAN-{FEATURE}" --agent "task-planner" --model "gemini-desktop-app" \
     --complexity "high" --status "running" --progress 20 \
     --scope "docs/specs/{feature_id}.spec.md" \
     --notes "Designing cross-source contracts and Task DAG"
   ```
2. **Architecture & Cross-Source Contract Modeling**:
   - Define database schemas, API signatures, event payloads, and module boundaries per `source_id`.
3. **DAG Breakdown & Disjoint File Scope Allocation**:
   - Split tasks into minimal blast-radius slices scoped to a single `source_id` where possible.
   - Assign `parallel_group` to tasks with strictly disjoint target files (Decoupled File Scope Invariant).
   - Tag prerequisite dependencies using `depends_on` (e.g., shared schema/contract tasks before consumer slices).
   - If using the Dual-Channel CLI, run `python3 ~/.gemini/tools/orchestrator/client.py allocate-dag --target-desktop-share 0.60 --apply`.
   - Define 3-Stage Fast-Retry Circuit Breaker thresholds (`retry == 1` Hydrogen `xhigh` -> `retry == 2` Neon `high`/`xhigh` -> `retry >= 3` Carbon `high`).
4. **Artifact Generation & Sync**:
   - Save `docs/plans/{feature_id}.plan.md` using Section 4.
   - If shared contracts/docs were updated, sync via `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)` or `projectbrain_upload_document(..., scope="project_shared")`.
5. **Emit Completion Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "PLAN-{FEATURE}" --agent "task-planner" --model "gemini-desktop-app" \
     --status "completed" --progress 100 --scope "docs/plans/{feature_id}.plan.md" \
     --notes "Plan DAG finalized with sub-source scopes and disjoint worker slices"
   ```

## 4. Output Contract (`docs/plans/{feature_id}.plan.md`)
```markdown
# Architectural Plan: {Feature Name}

## 1. System Topology & Cross-Source Contracts
- **Sub-Sources Involved**: `source_a`, `source_b` (`proj@source:branch`)
- **Database / Schema Changes**: ...
- **Interface Signatures**: ...

## 2. Disjoint File Scope Allocation
| Task ID | Persona | Source ID | Channel & Model | Parallel Group | Depends On | Disjoint Target Files |
|---|---|---|---|---|---|---|
| `BUILD-01` | `builder` | `backend` | Ch-A `gemini-desktop-app` / `gemini-4-neon` | `BUILD-PARALLEL` | `PLAN-{FEATURE}` | `src/backend/{a,b,c,d}.py` |
| `BUILD-02` | `builder` | `frontend` | Ch-B `gemini-4-hydrogen` (`flash`) | `BUILD-PARALLEL` | `PLAN-{FEATURE}` | `src/frontend/ViewA.tsx` |
| `TEST-01` | `test-engineer` | `backend` | Ch-B `gemini-4-hydrogen` (`flash`) | - | `BUILD-01, BUILD-02` | `tests/test_feature.py` |

## 3. 3-Stage Circuit Breaker Policy
- **Stage 1 (`retry_count == 1`)**: `gemini-4-hydrogen` (`flash`, `xhigh` effort)
- **Stage 2 (`retry_count == 2`)**: `gemini-4-neon` (`pro`, `high` / `xhigh` effort)
- **Stage 3 (`retry_count >= 3`)**: `gemini-4-carbon` (`pro`, `high` effort)
```

> **Handoff**: Unblocks Stage 3 parallel builders ([`workflow-build.md`](./workflow-build.md)).
