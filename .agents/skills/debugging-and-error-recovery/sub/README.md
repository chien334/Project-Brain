# Project-Specific Overrides for `debugging-and-error-recovery`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `debugging-and-error-recovery` (for example, `sub/triage-runbook.md`, `sub/log-locations.md`, or `sub/known-gotchas.md`) without modifying `SKILL.md`.

Typical uses in `debugging-and-error-recovery/sub/`:
- Local log paths, health-check endpoints, and database inspection commands
- Service-specific error codes, safe reproduction environments, and test isolation flags
- Rules on when to escalate recurring incidents to `projectbrain_v3_rca_triage` and `projectbrain_store`

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical Stop-the-Line rule, 6-step triage checklist, and ProjectBrain RCA workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping reproduction-first discipline and ProjectBrain MCP verification steps intact (`projectbrain_hybrid_query`, `projectbrain_query_codegraph`, `projectbrain_v3_rca_triage`, `projectbrain_v3_script_cache_lookup`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/debugging-and-error-recovery/sub/triage-runbook.md`:

```markdown
# Project Override: Triage & Log Runbook

> Applies to: `.agents/skills/debugging-and-error-recovery/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **Diagnostic Entry Points:** Inspect server stderr logs and run `projectbrain_hardware_status()` before assuming GPU/OCR crashes are code regressions.
- **Cross-Source Integration Errors:** On HTTP 422/500 across frontend/backend boundaries, always run `projectbrain_cross_source_impact` and `projectbrain_query_codegraph` on the failing route first.
- **Post-Fix Memory:** Log every non-trivial fix in `projectbrain_store(..., tags=["rca", "bugfix"])` with the regression test path.
```
