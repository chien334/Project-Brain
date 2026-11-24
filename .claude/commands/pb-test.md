---
description: Author and execute spec-driven tests using test-driven-development + debugging-and-error-recovery (+ sub/*.md)
argument-hint: "<feature, spec path, or module>"
---

# `/pb-test` — Spec-Driven Verification & Root-Cause Triage

Target: `$ARGUMENTS`

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read `.agents/skills/test-driven-development/SKILL.md` + all `.agents/skills/test-driven-development/sub/*.md` (excluding `README.md`).
   - On test failures, load `.agents/skills/debugging-and-error-recovery/SKILL.md` + all its `sub/*.md` (excluding `README.md`).
3. **Trace Code & Test Links**:
   - Call `projectbrain_trace_code_and_docs(target="$ARGUMENTS", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
4. **Execute**: Spawn a `test-engineer` subagent via `Agent`/`Task` injected with `test-driven-development/SKILL.md` + `sub/*.md` + ProjectBrain context (or adopt the persona directly). Write unit/integration/contract tests, run them to 100% pass, and save `docs/reports/test_report.json`.
5. **Post-Edit Sync**: Run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)` after test edits.
