# Project-Specific Overrides for `multi-agent-orchestrator`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `multi-agent-orchestrator` (for example, `sub/fleet-routing.md`, `sub/source-ownership.md`, or `sub/budget-policy.md`) without modifying `SKILL.md`.

Typical uses in `multi-agent-orchestrator/sub/`:
- Repository `project_id`, canonical `source_id` partitions, and worktree/branch ownership rules
- Custom budget mode defaults (`balanced`, `speed_first`, `hybrid_desktop`, or `--api-only`), concurrency caps, or desktop share ratios
- Project-specific DAG verification gates and mandatory `sub/*.md` delegation checklists for spawned subagents

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical orchestration architecture, `sub/*.md` delegation rules, and ProjectBrain hub tooling.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping Rule 1 Security Guardrails, `sub/*.md` worker propagation, and ProjectBrain MCP verification steps intact (`projectbrain_bootstrap_project_hub`, `projectbrain_list_sources`, `projectbrain_cross_source_impact`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/multi-agent-orchestrator/sub/source-ownership.md`:

```markdown
# Project Override: Multi-Source Fleet Partitioning

> Applies to: `.agents/skills/multi-agent-orchestrator/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **ProjectBrain Source Partitioning:** Map parallel builders strictly to registered sources from `projectbrain_list_sources(project_id="<project_id>")` (`backend-api`, `web-app`, `worker`).
- **Contract Gate:** Any task touching OpenAPI schemas or shared DTOs must run `projectbrain_cross_source_impact(relation_depth=2)` before Fan-Out.
- **Worker Brief Requirement:** Include `Read .agents/skills/<skill>/sub/*.md alphabetically before editing` in every `invoke_subagent` and `desktop-ask` prompt.
```
