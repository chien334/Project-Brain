# Project-Specific Overrides for `planning-and-task-breakdown`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `planning-and-task-breakdown` (for example, `sub/tracker.md`, `sub/definition-of-done.md`, or `sub/slicing-rules.md`) without modifying `SKILL.md`.

Typical uses in `planning-and-task-breakdown/sub/`:
- Designating an external task list target (Linear, OpenProject, Jira, GitHub Issues, or `bd`/beads) instead of `tasks/todo.md`
- Customizing `tasks/plan.md` paths, phase gates, or repository-wide Definition of Done items
- Recording canonical `project_id` and `source_id` mappings for multi-source dependency ordering

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical vertical-slicing and dependency-mapping workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as task list destination or checkpoint cadence), while keeping ProjectBrain MCP discovery and impact checks intact (`projectbrain_discover_workspace_sources`, `projectbrain_list_sources`, `projectbrain_cross_source_impact`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/planning-and-task-breakdown/sub/tracker.md`:

```markdown
# Project Override: Task Tracker & Definition of Done

> Applies to: `.agents/skills/planning-and-task-breakdown/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Task List Target:** Record tasks in OpenProject (or Linear) work packages and keep `tasks/plan.md` as an ordered index of ticket IDs rather than duplicating checklists in `tasks/todo.md`.

## Extensions (Adds Project Context)
- **Sub-Source Ordering:** Always schedule `api` contract updates before `web` and `mobile` client tasks, verified with `projectbrain_cross_source_impact`.
- **Definition of Done:** Every task requires unit tests, updated shared docs if contracts changed, and `projectbrain_sync_graphify(upgrade_mode="delta")`.
```
