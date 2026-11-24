# Project-Specific Overrides for `code-review-and-quality`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `code-review-and-quality` (for example, `sub/review-checklist.md`, `sub/file-size-limits.md`, or `sub/dependency-policy.md`) without modifying `SKILL.md`.

Typical uses in `code-review-and-quality/sub/`:
- Custom file size thresholds, PR diff size limits, and architecture boundary rules (e.g., forbidden cross-module imports)
- Repository-specific presumptive blockers (for example, unpinned dependencies, missing migration rollbacks, missing Mantine theme usage)
- Required ProjectBrain drift checks across specific `source_id` boundaries

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical 5-axis review, change sizing, and ProjectBrain contract-drift checks.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping ProjectBrain MCP verification steps intact (`projectbrain_trace_code_and_docs`, `projectbrain_cross_source_impact`, `projectbrain_auto_link_sources`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/code-review-and-quality/sub/review-checklist.md`:

```markdown
# Project Override: Code Review Gates & Presumptive Blockers

> Applies to: `.agents/skills/code-review-and-quality/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Max PR Diff:** Flag PRs exceeding 400 changed lines (excluding lockfiles/snapshots) for splitting.

## Extensions (Adds Project Context)
- **Contract Drift Gate:** Any change to API routes or Pydantic/TS schemas MUST pass `projectbrain_auto_link_sources` and `projectbrain_cross_source_impact` with zero `drifted` or `stale` edges before approval.
- **Database Migrations:** Require both upgrade and safe rollback paths in the PR verification summary.
```
