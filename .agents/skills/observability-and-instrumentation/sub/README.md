# Project-Specific Overrides for `observability-and-instrumentation`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `observability-and-instrumentation` (for example, `sub/logging-format.md`, `sub/metrics-slo.md`, or `sub/alert-routing.md`) without modifying `SKILL.md`.

Typical uses in `observability-and-instrumentation/sub/`:
- Approved logging library (`structlog`, `pino`, `logging`), correlation header name (`X-Request-Id`, `traceparent`), and PII redaction allowlists
- Metrics backend, OpenTelemetry collector endpoints, and SLO thresholds (`p95`, error rate %)
- Alert runbook URLs and ProjectBrain `projectbrain_store` / `projectbrain_v3_index_artifact` conventions

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical structured logging, RED/USE metrics, tracing, and ProjectBrain telemetry workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as logger SDK or bucket thresholds), while keeping secret-redaction and ProjectBrain MCP verification steps intact (`projectbrain_sync_graphify`, `projectbrain_diff_project_versions`, `projectbrain_hardware_status`, `projectbrain_store`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/observability-and-instrumentation/sub/logging-format.md`:

```markdown
# Project Override: Structured Logging & Alerting Policy

> Applies to: `.agents/skills/observability-and-instrumentation/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Backend Logger:** Use Python `structlog` / JSON `logging` formatter with `request_id`, `project_id`, and `source_id` bound on every event.

## Extensions (Adds Project Context)
- **Redaction Rule:** Strip `Authorization`, `X-API-Key`, cookies, and DSN credentials before emitting logs or calling `projectbrain_v3_index_artifact`.
- **Runbook Persistence:** Store every new symptom alert's query and escalation path via `projectbrain_store(..., tags=["observability", "runbook"])`.
```
