# Project-Specific Overrides for `create-project-skill`

## Purpose

The `sub/` directory stores **repository-specific overrides and extensions** for `create-project-skill` (for example, `sub/override-policy.md` or `sub/naming-conventions.md`) without modifying `SKILL.md`.

Typical uses in `create-project-skill/sub/`:
- Organization-wide naming rules and numeric-prefix conventions for `sub/*.md` files
- Required approval gates or ProjectBrain `doc_key` / `tags` conventions when publishing overrides
- Standard project-header metadata templates for monorepos with multiple `source_id`s

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical ProjectBrain workflow and MCP gates.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping all ProjectBrain MCP verification steps intact.
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades update `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Do not edit `SKILL.md` for project-only rules — add or update `sub/<topic>.md` instead.

## Example Override Template

Create a file such as `.agents/skills/create-project-skill/sub/override-policy.md`:

```markdown
# Project Override: Override Governance Policy

> Extends `.agents/skills/create-project-skill/SKILL.md`

## Extensions
- **Shared Sync Required:** Any new `sub/*.md` affecting API contracts or security boundaries must be uploaded via `projectbrain_upload_document(scope="project_shared", doc_role="reference")` and bound to `target_source_id="*"`.
- **Naming Prefix:** Prefix cross-cutting monorepo overrides with `00-` and service-specific overrides with `<source_id>-`.

## Additional Verification Checks
- [ ] Verify `project_id` and `source_id` match `projectbrain_list_sources(project_id=...)`.
```
