# Project-Specific Overrides for `performance-optimization`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `performance-optimization` (for example, `sub/perf-budgets.md`, `sub/benchmark-commands.md`, or `sub/caching-policy.md`) without modifying `SKILL.md`.

Typical uses in `performance-optimization/sub/`:
- Target Core Web Vitals, bundle size ceilings, and backend endpoint p95/p99 latency budgets
- Standard profiling/benchmark scripts and database `EXPLAIN ANALYZE` commands
- Hardware acceleration policies (`cpu`, `mps`, `cuda`) and ProjectBrain `projectbrain_store` experiment logging rules

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical Measure -> Identify -> Fix -> Verify (Keep/Revert) -> Guard workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as latency SLAs or benchmark commands), while keeping measurement-first proof and ProjectBrain MCP steps intact (`projectbrain_hardware_status`, `projectbrain_sync_graphify`, `projectbrain_diff_project_versions`, `projectbrain_store`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/performance-optimization/sub/perf-budgets.md`:

```markdown
# Project Override: Latency Budgets & Benchmark Commands

> Applies to: `.agents/skills/performance-optimization/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **API Latency SLA:** Read endpoints p95 < 150ms; hybrid Graphify queries p95 < 500ms on warm index.
- **Frontend Bundle Budget:** Keep initial JS route chunks <= 250 KB gzipped.

## Extensions (Adds Project Context)
- **Hardware Baseline:** Run `projectbrain_hardware_status()` before tuning Graphify `workers` or `semantic_engine` throughput.
- **Experiment Log:** Record both kept and reverted benchmark runs in `projectbrain_store(..., tags=["performance", "benchmark"])`.
```
