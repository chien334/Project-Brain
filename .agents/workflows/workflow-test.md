---
name: workflow-test
description: Stage 4 Testing Workflow - Implement and run tests matching docs/specs/{feature_id}.spec.md using test-driven-development SKILL.md + sub/*.md overrides and ProjectBrain context
---

# Stage 4: Automated Verification & Testing Workflow

## 0. Mandatory ProjectBrain Hub & Skill Override Gate
1. **Ensure ProjectBrain Hub is Initialized**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Project Overrides**:
   - Load [`../skills/test-driven-development/SKILL.md`](../skills/test-driven-development/SKILL.md) **AND** all `.agents/skills/test-driven-development/sub/*.md` (excluding `README.md`).
   - If debugging failures, also load [`../skills/debugging-and-error-recovery/SKILL.md`](../skills/debugging-and-error-recovery/SKILL.md) + its `sub/*.md` (excluding `README.md`).
   - Apply `sub/*.md` as project-specific overrides over `SKILL.md` defaults.
3. **Trace Code, Docs & Test Links**:
   - Query `projectbrain_trace_code_and_docs(target="<changed_module_or_route>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` and `projectbrain_hybrid_query(...)` to identify all affected tests and cross-source contract assertions.

## 1. Objective & Persona
- **Persona**: `test-engineer`
- **Execution Mode**:
  - **Multi-Agent IDEs**: Spawn a `test-engineer` subagent (`invoke_subagent(Model="flash")` / `Task` / `Agent`) injected with `test-driven-development/SKILL.md` + `sub/*.md` + ProjectBrain context (`gemini-4-hydrogen` primary; `gemini-4-neon` in `quality_first`).
  - **3-Stage Circuit Breaker**: `retry == 1` Hydrogen (`flash`, `xhigh`) -> `retry == 2` Neon (`pro`, `high`/`xhigh`) -> `retry >= 3` or security boundary -> Carbon (`pro`, `high`). Retries never offload to Desktop.
  - **Single-Agent IDEs**: Adopt the `test-engineer` persona directly and run Section 3 step-by-step.

## 2. Input Contract
- **Prerequisite**: Stage 3 builders completed and delta-synced to ProjectBrain.
- **Inputs Required**:
  1. Acceptance Criteria & Gherkin Scenarios from `docs/specs/{feature_id}.spec.md`.
  2. Modified files from Stage 3 and existing test conventions (`sub/*.md`).

## 3. Execution Steps
1. **Emit Start Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "TEST-{FEATURE}" --agent "test-engineer" --model "gemini-4-hydrogen" \
     --effort "medium" --complexity "medium" --status "running" --progress 20 \
     --scope "tests/test_{feature}.py" --notes "Writing tests with SKILL.md + sub/*.md overrides"
   ```
2. **Implement Test Scenarios**:
   - Write unit, integration, and cross-source contract tests covering every acceptance criterion and edge case in the spec.
3. **Execute Test Suite & 3-Stage Fast-Retry Circuit Breaker**:
   - Run the project test suite (`pytest`, `vitest`, `jest`, etc.).
   - If tests fail, triage root cause using `debugging-and-error-recovery/SKILL.md` + `sub/*.md` (plus `projectbrain_v3_rca_triage` / `projectbrain_hybrid_query` if applicable) and escalate per the 3-Stage Circuit Breaker.
   - Require 100% pass rate on relevant suites before advancing.
4. **Post-Edit ProjectBrain Delta Sync**:
   - After adding/updating test files in a sub-source, run:
     ```text
     projectbrain_sync_graphify(
       project_id="<project_id>",
       source_id="<source_id>",
       upgrade_mode="delta",
       auto_link_sources=True
     )
     ```
5. **Artifact Generation & Completion Telemetry**:
   - Save `docs/reports/test_report.json` following Section 4.
   - Update orchestrator telemetry (`--status completed --progress 100`) if active.

## 4. Output Contract (`docs/reports/test_report.json`)
```json
{
  "feature_id": "{feature_id}",
  "timestamp": "{ISO_8601_TIMESTAMP}",
  "tester": "test-engineer",
  "engine": "gemini-4-hydrogen",
  "summary": {
    "total_tests": 12,
    "passed": 12,
    "failed": 0,
    "skipped": 0,
    "duration_seconds": 1.15
  },
  "test_suites": [
    {
      "name": "tests/test_{feature}.py",
      "status": "PASSED",
      "assertions": 28
    }
  ],
  "acceptance_criteria_verified": [
    "AC-1: Successful payload processing"
  ],
  "verdict": "PASSED"
}
```

> **Handoff**: Pass `docs/reports/test_report.json` and the git diff to **Stage 5 ([`workflow-review.md`](./workflow-review.md))**.
