# Project-Specific Overrides for `security-and-hardening`

## Purpose

The `sub/` directory stores **repository-specific overrides and rules** for `security-and-hardening` (for example, `sub/auth-boundaries.md`, `sub/rbac-matrix.md`, or `sub/supply-chain.md`) without modifying `SKILL.md`.

Typical uses in `security-and-hardening/sub/`:
- Authentication mechanisms (separate client vs. admin/collaborator auth, JWT/cookie lifetimes, CSRF/CORS allowlists)
- Role-based access control (RBAC) invariants, tenant isolation checks, and SSRF allowlists
- Package manager pin policies (`uv`, `pip`, `npm`, `pnpm`), approved registries, and PII retention limits

## Precedence & Upgrade-Safety Rule

1. Agents always load `../SKILL.md` first for the canonical threat-modeling, OWASP prevention, and ProjectBrain security-trace workflow.
2. Before executing the skill, agents read **all `.md` files in `sub/` (excluding `README.md`) in alphabetical order**.
3. Rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (such as specific auth headers or lockfile managers), while never weakening security boundaries or skipping ProjectBrain MCP checks (`projectbrain_trace_code_and_docs`, `projectbrain_cross_source_impact`, `projectbrain_auto_link_sources`).
4. **Never overwritten on upgrade:** ProjectBrain client-kit upgrades overwrite `SKILL.md` and `sub/README.md`, and **never touch** custom `sub/<topic>.md` files. Never edit `SKILL.md` for project-only rules — write them in `sub/<topic>.md` (or use `create-project-skill`).

## Example Override Template

Create `.agents/skills/security-and-hardening/sub/auth-boundaries.md`:

```markdown
# Project Override: Auth & Tenant Isolation Boundaries

> Applies to: `.agents/skills/security-and-hardening/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>`

## Extensions (Adds Project Context)
- **Dual Auth Contexts:** Maintain strict separation between client and admin/collaborator authentication tokens and route guards.
- **Secret Hygiene:** Never pass `.env`, API keys, or bearer tokens into `projectbrain_store` or desktop worker prompts.
- **Cross-Source Auth Verification:** After changing auth middleware or role schemas, run `projectbrain_cross_source_impact` and `projectbrain_auto_link_sources` to confirm zero `drifted` or `stale` auth-contract edges.
```
