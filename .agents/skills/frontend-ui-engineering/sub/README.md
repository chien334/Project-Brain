# Project-Specific Overrides for `frontend-ui-engineering`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `frontend-ui-engineering` (for example, `sub/design-system.md`, `sub/state-and-routing.md`, or `sub/a11y-gates.md`) without modifying `SKILL.md`.

Typical uses in `frontend-ui-engineering/sub/`:
- Project UI framework & component library rules (e.g., React + TypeScript + Mantine UI, React Query, Zustand, React Router)
- Exact component directory structure, styling approach (CSS modules vs. Tailwind vs. Mantine theme tokens), and icon libraries
- Frontend `source_id`, backend API client conventions, and automated accessibility test commands (`axe`, Playwright)

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical component architecture, WCAG 2.1 AA, and ProjectBrain sync workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as Tailwind examples vs. Mantine UI components), while keeping WCAG accessibility and ProjectBrain MCP steps intact (`projectbrain_query`, `projectbrain_hybrid_query`, `projectbrain_sync_graphify`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/frontend-ui-engineering/sub/design-system.md`:

```markdown
# Project Override: UI Stack & Design System

> Applies to: `.agents/skills/frontend-ui-engineering/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `source_id=web-app`

## Overrides (Replaces Base Defaults)
- **Component & Styling Stack:** Use Mantine UI v7 components and theme tokens instead of raw Tailwind utility classes.
- **State Stack:** Use React Query for server state and Zustand for client UI state; keep separate auth contexts for `client` and `admin/collaborator` roles.

## Extensions (Adds Project Context)
- **API Type Alignment:** Before implementing any data-fetching view, run `projectbrain_hybrid_query(project_id="<project_id>", source_id="web-app", include_shared_docs=True, relation_depth=1)` to confirm exact backend DTO fields.
```
