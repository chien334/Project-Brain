# Project-Specific Overrides for `api-and-interface-design`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `api-and-interface-design` (for example, `sub/api-conventions.md`, `sub/error-envelope.md`, or `sub/pagination.md`) without modifying `SKILL.md`.

Typical uses in `api-and-interface-design/sub/`:
- Standard JSON response envelopes, error codes, auth header conventions, and API base path prefixes (`/api/v1`)
- Pagination/filtering query parameter conventions (cursor vs. offset) and idempotency header names
- OpenAPI / Protobuf / TypeScript client generation paths and `source_id` ownership (`backend-api` vs. `web-app`)

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical interface design rules and ProjectBrain contract-tracking workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as response envelope shapes or URL versioning conventions), while keeping ProjectBrain MCP contract checks intact (`projectbrain_query`, `projectbrain_hybrid_query`, `projectbrain_sync_graphify`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/api-and-interface-design/sub/api-conventions.md`:

```markdown
# Project Override: Project API Conventions

> Applies to: `.agents/skills/api-and-interface-design/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `source_id=backend-api`

## Overrides (Replaces Base Defaults)
- **Pagination:** Use keyset cursor pagination (`cursor`, `limit`, max `100`) on all list endpoints.
- **Error Envelope:** Always return `{"error": {"code": "<MACHINE_CODE>", "message": "...", "request_id": "..."}}`.

## Extensions (Adds Project Context)
- **Contract Sync:** Regenerate `docs/openapi.yaml` and upload via `projectbrain_upload_document(scope="project_shared", doc_role="api_contract")` before updating frontend API clients.
- **Cross-Source Consumer Check:** Run `projectbrain_cross_source_impact(project_id="<project_id>", target="<route>", source_id="backend-api")` before changing any response field.
```
