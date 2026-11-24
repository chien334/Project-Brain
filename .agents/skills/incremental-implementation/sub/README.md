# Project-Specific Overrides for `incremental-implementation`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `incremental-implementation` (for example, `sub/definition-of-done.md`, `sub/slice-rules.md`, or `sub/source-paths.md`) without modifying `SKILL.md`.

Typical uses in `incremental-implementation/sub/`:
- Per-`source_id` working directories, build/lint/test commands, and commit-slice rules
- Repository-specific Definition of Done checks and feature-flag conventions
- Rules governing when `projectbrain_sync_graphify` runs per commit vs. per phase checkpoint

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical vertical-slice increment cycle and ProjectBrain MCP sync steps.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as maximum diff size, feature flag naming, or commit cadence), while keeping ProjectBrain MCP retrieval and delta-sync checks intact (`projectbrain_query`, `projectbrain_hybrid_query`, `projectbrain_sync_graphify`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/incremental-implementation/sub/slice-rules.md`:

```markdown
# Project Override: Slice & Verification Rules

> Applies to: `.agents/skills/incremental-implementation/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Max Increment Size:** Keep each committed increment <= 150 changed lines across <= 3 files.

## Extensions (Adds Project Context)
- **Per-Source Test Commands:**
  - `backend-api`: `pytest tests/unit -q`
  - `web-app`: `npm run typecheck && npm test`
- **Definition of Done:** Every completed increment must pass lint/typecheck and run `projectbrain_sync_graphify(source_id="<source_id>", git_dirty_only=True, upgrade_mode="delta", auto_link_sources=True)`.
```
