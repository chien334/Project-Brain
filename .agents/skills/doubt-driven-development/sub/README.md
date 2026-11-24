# Project-Specific Overrides for `doubt-driven-development`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `doubt-driven-development` (for example, `sub/high-stakes-triggers.md`, `sub/cross-model-policy.md`, or `sub/invariants.md`) without modifying `SKILL.md`.

Typical uses in `doubt-driven-development/sub/`:
- Domain-specific high-stakes triggers that always require a doubt cycle (e.g., billing math, tenant isolation, schema migrations, auth middleware)
- Approved external CLI flags or model-routing rules for cross-model adversarial escalation
- Mandatory system invariants that must always be included in the `CONTRACT` passed to the reviewer

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical `CLAIM -> EXTRACT -> DOUBT -> RECONCILE -> STOP` discipline and ProjectBrain verification steps.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as domain triggers or stop budgets), while keeping fresh-context adversarial isolation (`ARTIFACT + CONTRACT` only) and ProjectBrain MCP checks intact (`projectbrain_trace_code_and_docs`, `projectbrain_cross_source_impact`, `projectbrain_auto_link_sources`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/doubt-driven-development/sub/high-stakes-triggers.md`:

```markdown
# Project Override: High-Stakes Doubt Triggers & Invariants

> Applies to: `.agents/skills/doubt-driven-development/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **Always-Doubt Modules:** Any change touching multi-tenant DB isolation, auth token validation, or cross-source linker reconciliation requires a fresh-context DOUBT pass.
- **Mandatory Contract Check:** Before invoking the reviewer, attach outputs from `projectbrain_trace_code_and_docs` and `projectbrain_cross_source_impact` to the `CONTRACT` block (without revealing the author's reasoning).
```
