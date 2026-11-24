# Project-Specific Overrides for `gemini-desktop-worker`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `gemini-desktop-worker` (for example, `sub/desktop-policy.md`, `sub/workspace-mounts.md`, or `sub/redaction-rules.md`) without modifying `SKILL.md`.

Typical uses in `gemini-desktop-worker/sub/`:
- Default `--driver` (`macos_ax`, `windows_uia`, `orca`, or `mock`), `--timeout`, and workspace mount paths
- Additional repository-specific secret/PII patterns or directories that must never be referenced in desktop prompts
- Rules on persisting `docs/reports/desktop_worker/{job_id}.md` reports into ProjectBrain via `projectbrain_upload_document` or `projectbrain_store`

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical Desktop Worker bridge, Sentinel protocol, and ProjectBrain tooling.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md`, while keeping `SecretRedactor`, Rule 1 Security Guardrails, and ProjectBrain MCP verification steps intact.
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/gemini-desktop-worker/sub/desktop-policy.md`:

```markdown
# Project Override: Gemini Desktop Worker Policy

> Applies to: `.agents/skills/gemini-desktop-worker/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Overrides (Replaces Base Defaults)
- **Timeout & Mode:** Default to `--mode task --background --timeout 240 --strict-secrets` for architecture reviews.

## Extensions (Adds Project Context)
- **Workspace Exclusion:** Never instruct the desktop worker to inspect `.env*`, `secrets/`, or production database dumps.
- **Post-Run Persistence:** Store distilled review takeaways into ProjectBrain via `projectbrain_store(user_id="<project_id>", tags=["desktop-worker", "review"])`.
```
