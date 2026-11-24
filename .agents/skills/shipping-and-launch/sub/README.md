# Project-Specific Overrides for `shipping-and-launch`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `shipping-and-launch` (for example, `sub/launch-checklist.md`, `sub/rollout-targets.md`, or `sub/rollback-runbook.md`) without modifying `SKILL.md`.

Typical uses in `shipping-and-launch/sub/`:
- Staging and production environments, health-check URLs, and deployment commands
- Numeric error-rate / p99 latency rollback thresholds and feature-flag system conventions
- Required ProjectBrain release sign-off gates (`projectbrain_diff_project_versions`, `projectbrain_promote_branch_graph`)

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical pre-launch checklist, staged rollout, and rollback discipline.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping rollback readiness and ProjectBrain MCP verification steps intact (`projectbrain_sync_graphify`, `projectbrain_diff_project_versions`, `projectbrain_hardware_status`, `projectbrain_store`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/shipping-and-launch/sub/launch-checklist.md`:

```markdown
# Project Override: Production Release & Rollback Rules

> Applies to: `.agents/skills/shipping-and-launch/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **Canary Rollback Trigger:** Roll back immediately if 5xx rate > 0.5% or p95 latency increases > 30% for 5 consecutive minutes.
- **Pre-Deploy Verification:** Confirm `projectbrain_diff_project_versions` and `projectbrain_sync_graphify(upgrade_mode="delta", auto_link_sources=True)` report zero drifted backend/frontend contracts.
- **Post-Deploy Memory:** Record deployed commit SHA, version tag, and rollback trigger in `projectbrain_store(..., tags=["launch", "release"])`.
```
