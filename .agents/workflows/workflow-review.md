---
name: workflow-review
description: Stage 5 Code Review Workflow - Conduct 5-axis and cross-source drift review using code-review-and-quality SKILL.md + sub/*.md overrides, producing docs/reviews/review_signoff.md
---

# Stage 5: Multi-Axis Code Review Workflow

## 0. Mandatory ProjectBrain Hub & Skill Override Gate
1. **Ensure ProjectBrain Hub is Initialized**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skills + Project Overrides**:
   - Load [`../skills/code-review-and-quality/SKILL.md`](../skills/code-review-and-quality/SKILL.md) **AND** all `.agents/skills/code-review-and-quality/sub/*.md` (excluding `README.md`).
   - Optionally load [`../skills/doubt-driven-development/SKILL.md`](../skills/doubt-driven-development/SKILL.md) + its `sub/*.md` for adversarial review on non-trivial changes.
   - Apply `sub/*.md` as project-specific overrides over `SKILL.md` defaults.
3. **Cross-Source Drift & Contract Verification**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<changed_api_or_symbol>", source_id="<source_id>")` and `projectbrain_trace_code_and_docs(target="<changed_target>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)` to verify no unacknowledged consumer drift or spec divergence.

## 1. Objective & Persona
- **Persona**: `code-reviewer`
- **Execution Mode**:
  - **Multi-Agent IDEs**: Spawn a `code-reviewer` subagent (`invoke_subagent` / `Task` / `Agent`) injected with `code-review-and-quality/SKILL.md` + `sub/*.md` + ProjectBrain context.
  - **Dual-Channel Orchestrator (`HYBRID_60_DESKTOP`, non-security)**: Dispatch via `run_command` (`WaitMsBeforeAsync: 500`, omit `--allow-edits`):
    ```bash
    python3 ~/.gemini/tools/orchestrator/client.py desktop-ask \
      --task "REVIEW-{FEATURE}" --agent code-reviewer --workspace . \
      --output "docs/reports/desktop_worker/REVIEW-{FEATURE}.md" \
      --prompt "Conduct a 5-axis review (Correctness, Architecture, Maintainability, Performance, Error Handling) + cross-source drift check for {FEATURE} against docs/plans/{feature_id}.plan.md and docs/reports/test_report.json, applying code-review-and-quality/SKILL.md + sub/*.md overrides. Return APPROVED or REQUEST_CHANGES with exact file/line findings. Read-only inspection only."
    ```
  - **Channel B (`API_ONLY_100`)**: `invoke_subagent(Model="pro")` (`gemini-4-neon`, `reasoning_effort: high`); security-sensitive subsystems stay on `gemini-4-carbon` (`Model="pro"`).
  - **Single-Agent IDEs**: Adopt the `code-reviewer` persona directly and execute Section 3 step-by-step.

## 2. Input Contract
- **Prerequisite**: Stage 4 tests passing (`docs/reports/test_report.json`).
- **Inputs Required**: Git diff (`git diff`), `docs/plans/{feature_id}.plan.md`, `docs/reports/test_report.json`, and ProjectBrain cross-source impact report.

## 3. Execution Steps
1. **Emit Start Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "REVIEW-{FEATURE}" --agent "code-reviewer" --model "gemini-desktop-app" \
     --complexity "high" --status "running" --progress 20 \
     --scope "Git Diff & Cross-Source Review" --notes "Analyzing diff across 5 axes"
   ```
2. **5-Axis + Cross-Source Review Inspection**:
   - **Correctness**: Implements spec/plan invariants without regressions.
   - **Architecture & Cross-Source Contracts**: Respects module boundaries, `sub/*.md` conventions, and downstream `source_id` consumers.
   - **Maintainability & Simplicity**: Clean, minimal, no dead code or duplicated logic.
   - **Performance**: No N+1 queries, blocking I/O, or unbounded scans.
   - **Error Handling**: Edge cases handled cleanly without silent failures.
3. **Verdict & Artifact Generation**:
   - Produce `docs/reviews/review_signoff.md` with `APPROVED` or `REQUEST_CHANGES` (exact `file:line` findings).
4. **Emit Completion Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "REVIEW-{FEATURE}" --agent "code-reviewer" --model "gemini-desktop-app" \
     --status "completed" --progress 100 --scope "docs/reviews/review_signoff.md" \
     --notes "Review completed with verdict"
   ```

## 4. Output Contract (`docs/reviews/review_signoff.md`)
```markdown
# Code Review Sign-Off: {Feature Name}

- **Reviewer**: `code-reviewer`
- **Model Engine**: `gemini-desktop-app` (or `gemini-4-neon` in `API_ONLY_100`)
- **Verdict**: `APPROVED` | `REQUEST_CHANGES`

## 5-Axis & Cross-Source Assessment
1. **Correctness**: ...
2. **Architecture & Cross-Source Contracts**: ...
3. **Maintainability**: ...
4. **Performance**: ...
5. **Error Handling**: ...

## Sign-Off Signature
- **Sign-Off Hash**: `{GIT_HEAD_SHA}`
- **Ready for Security Audit**: `YES` | `NO`
```

> **Handoff**: When `APPROVED`, proceed to **Stage 6 ([`workflow-security.md`](./workflow-security.md))**.
