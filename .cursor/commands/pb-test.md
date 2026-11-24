# `/pb-test` — Spec-Driven Verification & Root-Cause Triage

Author and run tests covering spec acceptance criteria and cross-source contracts.

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read `.agents/skills/test-driven-development/SKILL.md` + all `.agents/skills/test-driven-development/sub/*.md` (excluding `README.md`).
   - On failures, load `.agents/skills/debugging-and-error-recovery/SKILL.md` + all its `sub/*.md` (excluding `README.md`).
3. **Trace Code & Test Links**:
   - Call `projectbrain_trace_code_and_docs(target="<module_or_route>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
4. **Execute**: Spawn a `test-engineer` subagent (if supported) or adopt the `test-engineer` persona with `SKILL.md` + `sub/*.md` + ProjectBrain context. Author tests, run them to 100% pass, and save `docs/reports/test_report.json`.
5. **Post-Edit Sync**: Run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)` after test edits.
