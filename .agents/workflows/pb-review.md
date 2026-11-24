---
name: pb-review
description: ProjectBrain Review Command - Run 5-axis + cross-source drift review using code-review-and-quality + doubt-driven-development (+ sub/*.md), producing docs/reviews/review_signoff.md
---

# `/pb-review` — 5-Axis & Cross-Source Code Review

Audit diffs for correctness, architectural alignment, maintainability, performance, error handling, and cross-source contract drift.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Overrides**:
   - Read [`.agents/skills/code-review-and-quality/SKILL.md`](../skills/code-review-and-quality/SKILL.md) **AND** all `.agents/skills/code-review-and-quality/sub/*.md` (excluding `README.md`).
   - Load [`.agents/skills/doubt-driven-development/SKILL.md`](../skills/doubt-driven-development/SKILL.md) + its `sub/*.md` (excluding `README.md`) for adversarial verification of non-trivial changes.
   - Apply `sub/*.md` as project-specific overrides.
3. **Check Cross-Source Impact & Spec Drift**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<changed_api_or_symbol>", source_id="<source_id>")` and `projectbrain_trace_code_and_docs(target="<changed_target>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)`.

## 2. Execution (Subagent or Single-Agent Persona)
- **If subagents are supported** (`invoke_subagent` / `Task` / `Agent`): Spawn a `code-reviewer` subagent (`Model="pro"` or `desktop-ask` if non-security) injected with both skills, `sub/*.md` overrides, git diff, plan, test report, and ProjectBrain impact output.
- **If single-agent**: Adopt the `code-reviewer` persona step-by-step (read-only inspection first).

## 3. Deliverables
1. Evaluate the 5 axes (Correctness, Architecture & Cross-Source Contracts, Maintainability, Performance, Error Handling).
2. Produce `docs/reviews/review_signoff.md` with verdict `APPROVED` or `REQUEST_CHANGES` and exact `file:line` findings.
3. If `REQUEST_CHANGES` leads to follow-up code edits in `<source_id>`, re-run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)` afterward.
