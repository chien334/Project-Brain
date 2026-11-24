# Project-Specific Overrides for `frontend-design`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `frontend-design` (for example, `sub/brand-direction.md`, `sub/typography.md`, or `sub/copy-voice.md`) without modifying `SKILL.md`.

Typical uses in `frontend-design/sub/`:
- Fixed brand palette hex codes, approved font pairs, and contrast constraints
- Product voice/tone dictionary (domain terminology, banned jargon, localization rules)
- Existing visual signature constraints when extending an established product surface rather than starting greenfield

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical visual design principles and ProjectBrain integration.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (for example, pinning the company's existing palette and typeface instead of inventing new ones), while keeping ProjectBrain MCP verification steps intact (`projectbrain_query`, `projectbrain_hybrid_query`, `projectbrain_sync_graphify`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/frontend-design/sub/brand-direction.md`:

```markdown
# Project Override: Brand Visual & Copy Direction

> Applies to: `.agents/skills/frontend-design/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `source_id=web-app`

## Overrides (Replaces Base Defaults)
- **Fixed Palette:** Primary `#0F172A`, Surface `#F8FAFC`, Accent `#0284C7`, Danger `#DC2626` (do not invent alternative brand accents).
- **Fixed Fonts:** Display `Plus Jakarta Sans`, Body `Inter`, Mono `JetBrains Mono`.

## Extensions (Adds Project Context)
- **Domain Vocabulary:** Always refer to tenants as "Workspaces" and saved records as "Knowledge Entries".
- **Persistence:** Store newly introduced surface tokens in ProjectBrain (`projectbrain_store(..., tags=["frontend-design", "tokens"])`).
```
