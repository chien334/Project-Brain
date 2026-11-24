# `/pb-review` — 5-Axis & Cross-Source Code Review

Audit changes across Correctness, Architecture, Maintainability, Performance, Error Handling, and cross-source contract drift.

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read `.agents/skills/code-review-and-quality/SKILL.md` + all `.agents/skills/code-review-and-quality/sub/*.md` (excluding `README.md`).
   - Load `.agents/skills/doubt-driven-development/SKILL.md` + its `sub/*.md` (excluding `README.md`) for non-trivial diffs.
3. **Check Cross-Source Drift**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<changed_api_or_symbol>", source_id="<source_id>")` and `projectbrain_trace_code_and_docs(target="<changed_target>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)`.
4. **Execute**: Spawn a `code-reviewer` subagent (if supported) or adopt the `code-reviewer` persona with both skills, `sub/*.md` overrides, git diff, plan, test report, and ProjectBrain impact output.
5. **Deliver**: Save `docs/reviews/review_signoff.md` (`APPROVED` or `REQUEST_CHANGES` with exact `file:line` findings). If follow-up edits occur, run `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)`.
