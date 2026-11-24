# Project-Specific Overrides for `spec-driven-development`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `spec-driven-development` (for example, `sub/spec.md`, `sub/prd-conventions.md`, or `sub/domain-boundaries.md`) without modifying `SKILL.md`.

Typical uses in `spec-driven-development/sub/`:
- Project-wide `project_id`, `source_id` module map, and shared spec storage location (e.g., `docs/specs/`)
- Mandatory repo-specific spec sections, compliance criteria, or sign-off owners
- Exact `Build` / `Test` / `Lint` / `Dev` commands and directory conventions for Phase 1 specs

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical gated workflow (`Phase 0`–`Phase 4`) and ProjectBrain MCP steps.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as spec template sections, output folder paths, or boundaries), while keeping all ProjectBrain MCP verification steps intact (`projectbrain_query(include_shared_docs=True)`, `projectbrain_upload_document`, `projectbrain_bind_shared_doc`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/spec-driven-development/sub/spec.md`:

```markdown
# Project Override: Project Specification Conventions

> Applies to: `.agents/skills/spec-driven-development/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `scope=project_shared`

## Overrides (Replaces Base Defaults)
- **Spec Output Location:** Save approved feature specs to `docs/specs/<feature-id>.spec.md` and publish with `doc_key="spec_<feature_id>"`.

## Extensions (Adds Project Context)
- **Tech Stack & Commands:**
  - Test: `pytest -q` (backend) / `npm test` (frontend)
  - Lint: `ruff check src tests`
- **Mandatory Spec Sections:** Always include an explicit **Cross-Source Impact** section referencing `projectbrain_list_sources(project_id="<project_id>")`.
- **Approval Gate:** Backend + API contract specs require human sign-off before Phase 2 planning.

## Additional Verification Checks
- [ ] Approved spec uploaded with `projectbrain_upload_document(scope="project_shared", doc_role="prd")` and bound via `projectbrain_bind_shared_doc(target_source_id="*")`.
```
