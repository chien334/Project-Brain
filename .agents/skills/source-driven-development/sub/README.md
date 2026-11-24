# Project-Specific Overrides for `source-driven-development`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `source-driven-development` (for example, `sub/approved-sources.md`, `sub/stack-versions.md`, or `sub/migration-policies.md`) without modifying `SKILL.md`.

Typical uses in `source-driven-development/sub/`:
- Pinned library versions and official doc URLs (e.g., React, FastAPI, Mantine, Graphify, Pydantic v2)
- Allowed external documentation domains or air-gapped local vendor doc paths
- Rules on when framework pattern upgrades require a shared ADR via `projectbrain_upload_document(scope="project_shared", doc_role="adr")`

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical `DETECT -> FETCH -> IMPLEMENT -> CITE` workflow and ProjectBrain KB integration.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as approved doc sources or conflict preferences), while keeping official verification and ProjectBrain MCP steps intact.
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/source-driven-development/sub/approved-sources.md`:

```markdown
# Project Override: Pinned Stack & Official Documentation Registry

> Applies to: `.agents/skills/source-driven-development/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **Authoritative Sources for This Repo:**
  - FastAPI & Pydantic v2: `https://fastapi.tiangolo.com/` and `https://docs.pydantic.dev/latest/`
  - React & Mantine UI: `https://react.dev/reference/react` and `https://mantine.dev/`
- **Conflict Default:** When existing code uses a deprecated pattern and official docs show a newer API, log an ADR via `projectbrain_upload_document(scope="project_shared", doc_role="adr")` before migrating.

## Additional Verification Checks
- [ ] Citations persisted in ProjectBrain via `projectbrain_store` or `projectbrain_upload_document(scope="project_shared", doc_role="adr")`.
```
