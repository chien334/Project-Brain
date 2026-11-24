# Project-Specific Overrides for `ci-cd-and-automation`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `ci-cd-and-automation` (for example, `sub/ci-gates.md`, `sub/runner-env.md`, or `sub/deploy-targets.md`) without modifying `SKILL.md`.

Typical uses in `ci-cd-and-automation/sub/`:
- CI provider (GitHub Actions, GitLab CI, Jenkins) and exact workflow file locations
- Required matrix versions (Python, Node, PostgreSQL, Redis) and runner hardware limits (`--device cpu`)
- Branch protection rules, secret manager namespaces, and preview/staging deploy hooks

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical quality-gate pipeline, CI failure feedback loops, and ProjectBrain CI integration.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as CI provider syntax or timeout targets), while keeping quality gates and ProjectBrain MCP steps intact (`projectbrain_hardware_status`, `projectbrain_sync_graphify`, `projectbrain_diff_project_versions`, `projectbrain_store`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/ci-cd-and-automation/sub/ci-gates.md`:

```markdown
# Project Override: CI Pipeline Gates & Runner Constraints

> Applies to: `.agents/skills/ci-cd-and-automation/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Stack Gates:** Run `ruff check`, `pytest -q`, and `npm run build && npm test` on every PR targeting `main`.

## Extensions (Adds Project Context)
- **Headless CPU Runners:** CI runners lack GPUs; pass `--device cpu --semantic-engine tfidf` to `graphify-sync` and check `projectbrain_hardware_status` in diagnostics.
- **Pipeline Records:** Store CI runner topology changes via `projectbrain_store(..., tags=["ci-cd", "pipeline"])`.
```
