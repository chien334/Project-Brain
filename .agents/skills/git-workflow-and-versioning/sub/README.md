# Project-Specific Overrides for `git-workflow-and-versioning`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `git-workflow-and-versioning` (for example, `sub/branch-policy.md`, `sub/commit-format.md`, or `sub/release-process.md`) without modifying `SKILL.md`.

Typical uses in `git-workflow-and-versioning/sub/`:
- Custom branch prefixes (`feat/`, `fix/`, `hotfix/`) and trunk/release branch names (`main` vs. `develop`)
- Commit message format (Conventional Commits, ticket ID requirements) and merge strategy (squash vs. rebase)
- Release tagging rules, `CHANGELOG.md` path, and ProjectBrain `branch` / `source_version` conventions

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical commit/branch/worktree/release workflow and ProjectBrain version-diff steps.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping secret-hygiene checks and ProjectBrain MCP verification steps intact (`projectbrain_sync_graphify`, `projectbrain_diff_project_versions`, `projectbrain_store`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/git-workflow-and-versioning/sub/branch-policy.md`:

```markdown
# Project Override: Branching, Commits & Release Tagging

> Applies to: `.agents/skills/git-workflow-and-versioning/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Commit Convention:** Use Conventional Commits (`feat(scope): ...`, `fix(scope): ...`) referencing the ticket ID when present.
- **Worktree Location:** Create parallel agent worktrees under `.worktrees/<branch-slug>`.

## Extensions (Adds Project Context)
- **Branch Graph Sync:** Before opening a PR, run `projectbrain_sync_graphify(branch="<branch>", base_branch="main", git_dirty_only=True, upgrade_mode="delta", auto_link_sources=True)` and review `projectbrain_diff_project_versions`.
```
