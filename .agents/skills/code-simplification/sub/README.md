# Project-Specific Overrides for `code-simplification`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `code-simplification` (for example, `sub/style-conventions.md`, `sub/refactor-boundaries.md`, or `sub/lint-rules.md`) without modifying `SKILL.md`.

Typical uses in `code-simplification/sub/`:
- Language-specific style conventions, formatter/linter commands (`ruff`, `eslint`, `prettier`), and typing strictness rules
- Canonical shared helper directories that agents must reuse instead of inventing local wrappers
- Protected modules or public contracts that must never have signatures reshaped during cleanup passes

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical behavior-preserving simplification workflow and ProjectBrain verification steps.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as Python/TS idioms or max nesting limits), while keeping test-preservation and ProjectBrain MCP steps intact (`projectbrain_sync_graphify`, `projectbrain_diff_project_versions`, `projectbrain_store`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/code-simplification/sub/style-conventions.md`:

```markdown
# Project Override: Code Style & Protected Contracts

> Applies to: `.agents/skills/code-simplification/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **Python Style:** Prefer explicit `from __future__ import annotations`, early returns, max 3 levels of nesting, and `ruff check` clean before finishing.
- **Public Signatures:** Never rename or alter `@mcp_server.tool` parameters or REST response keys during a simplification pass; verify via `projectbrain_diff_project_versions`.
```
