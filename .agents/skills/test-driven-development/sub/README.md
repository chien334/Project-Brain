# Project-Specific Overrides for `test-driven-development`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `test-driven-development` (for example, `sub/testing-stack.md`, `sub/fixtures.md`, or `sub/e2e-policy.md`) without modifying `SKILL.md`.

Typical uses in `test-driven-development/sub/`:
- Exact focused-test and full-suite commands per language or `source_id` (`pytest`, `vitest`, `playwright`, `cargo test`, `go test`)
- Database/Redis/server fixture isolation rules and test environment variables
- ProjectBrain v3 script-cache (`platform`, `intent_id`) conventions and minimum coverage thresholds

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical RED -> GREEN -> REFACTOR cycle, Prove-It pattern, and ProjectBrain tooling.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as test runner commands or directory placement), while keeping failing-test-first discipline and ProjectBrain MCP steps intact (`projectbrain_hybrid_query`, `projectbrain_query_codegraph`, `projectbrain_v3_rca_triage`, `projectbrain_v3_script_cache_lookup`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/test-driven-development/sub/testing-stack.md`:

```markdown
# Project Override: Test Runners & Fixture Rules

> Applies to: `.agents/skills/test-driven-development/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Focused Test Command:**
  - Backend: `pytest tests/unit/test_<module>.py -q`
  - Frontend: `npx vitest run src/components/<Name>.test.tsx`
- **Full Suite Command:** `pytest -q && npm test`

## Extensions (Adds Project Context)
- **Fixture Isolation:** Use temporary SQLite/test DB fixtures; never target staging or production URLs during unit/integration tests.
- **E2E Script Cache:** Look up existing UI flows via `projectbrain_v3_script_cache_lookup` before authoring duplicate Playwright/UDCP steps.
```
