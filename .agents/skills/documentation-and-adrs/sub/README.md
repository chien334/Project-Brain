# Project-Specific Overrides for `documentation-and-adrs`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `documentation-and-adrs` (for example, `sub/adr-format.md`, `sub/wiki-layout.md`, or `sub/changelog-rules.md`) without modifying `SKILL.md`.

Typical uses in `documentation-and-adrs/sub/`:
- Repository-specific ADR directory, numbering format (`ADR-0001-...`), and required sections
- `doc_key` naming rules and `doc_role` mapping (`adr`, `architecture`, `api_contract`, `reference`) for `projectbrain_upload_document`
- Required `docs/kb` output formats or coverage thresholds for `projectbrain_kb_coverage_report`

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical ADR/documentation workflow and ProjectBrain KB tooling.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping ProjectBrain MCP verification steps intact (`projectbrain_upload_document`, `projectbrain_build_complete_kb`, `projectbrain_kb_coverage_report`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/documentation-and-adrs/sub/adr-format.md`:

```markdown
# Project Override: ADR Directory & ProjectBrain Publishing

> Applies to: `.agents/skills/documentation-and-adrs/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `scope=project_shared`

## Overrides (Replaces Base Defaults)
- **ADR Location:** Store all ADRs in `docs/adr/ADR-NNNN-kebab-title.md` using MADR 3.0 headings.
- **KB Exports:** Pass `generate_office_exports=False` to `projectbrain_build_complete_kb` unless a release export is requested.

## Extensions (Adds Project Context)
- **ProjectBrain Tagging:** Upload every accepted ADR with `scope="project_shared"`, `doc_role="adr"`, and `doc_key="adr_NNNN"`.
- **Coverage Floor:** Minimum API & MCP documentation coverage in `projectbrain_kb_coverage_report` is 90%.
```
