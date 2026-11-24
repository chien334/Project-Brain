---
name: pb-test
description: ProjectBrain Test Command - Author and run tests using test-driven-development + debugging-and-error-recovery (+ sub/*.md) and sync Graphify delta
---

# `/pb-test` — Spec-Driven Verification & Root-Cause Triage

Verify behavior against spec acceptance criteria and cross-source contracts.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read [`.agents/skills/test-driven-development/SKILL.md`](../skills/test-driven-development/SKILL.md) **AND** all `.agents/skills/test-driven-development/sub/*.md` (excluding `README.md`).
   - On failures, also load [`.agents/skills/debugging-and-error-recovery/SKILL.md`](../skills/debugging-and-error-recovery/SKILL.md) + all its `sub/*.md` (excluding `README.md`).
   - Apply `sub/*.md` as project-specific overrides.
3. **Trace Affected Code & Tests**:
   - Call `projectbrain_trace_code_and_docs(target="<module_or_route>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` to locate existing tests and spec invariants.

## 2. Execution (Subagent or Single-Agent Persona)
- **If subagents are supported** (`invoke_subagent` / `Task` / `Agent`): Spawn a `test-engineer` subagent (`Model="flash"`; escalate per 3-Stage Circuit Breaker to `Model="pro"` on `retry >= 2`) injected with `test-driven-development/SKILL.md` + `sub/*.md` + ProjectBrain context.
- **If single-agent**: Adopt the `test-engineer` persona step-by-step.

## 3. Deliverables & Post-Edit Sync
1. Author unit, integration, and cross-source contract tests covering every acceptance criterion and edge case.
2. Execute the test suite until all relevant tests pass without skipped/relaxed assertions; write `docs/reports/test_report.json`.
3. **Post-Edit Sync**: After adding or modifying tests in `<source_id>`, run:
   ```text
   projectbrain_sync_graphify(
     project_id="<project_id>",
     source_id="<source_id>",
     upgrade_mode="delta",
     auto_link_sources=True
   )
   ```
