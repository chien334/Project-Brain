---
description: Run deep OWASP, auth/RBAC, secret, and cross-source trust-boundary audit using security-and-hardening (+ sub/*.md)
argument-hint: "<diff, module, or feature>"
---

# `/pb-security` — Security & Cross-Source Trust-Boundary Audit

Target: `$ARGUMENTS`

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Overrides**:
   - Read `.agents/skills/security-and-hardening/SKILL.md` **AND** all `.agents/skills/security-and-hardening/sub/*.md` (excluding `README.md`).
3. **Trace Callers & Trust Boundaries**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<endpoint_or_auth_symbol>", source_id="<source_id>", relation_depth=2)`.
   - Call `projectbrain_query_graphify(query="$ARGUMENTS", project_id="<project_id>", source_id="<source_id>", action="callers")`.
4. **Execute**: Spawn a frontier `security-auditor` subagent via `Agent`/`Task` injected with `security-and-hardening/SKILL.md` + `sub/*.md` + ProjectBrain trace context (or audit step-by-step directly). Audit Injection, Auth/RBAC & Tenant Isolation (`proj@source:branch`), PII/Secrets, and DoS limits.
5. **Deliver & Sync**: Save `docs/security/security_audit.md` (`PASSED` or `FAILED`). If fixes are applied, run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
