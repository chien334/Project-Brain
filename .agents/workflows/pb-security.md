---
name: pb-security
description: ProjectBrain Security Command - Deep OWASP, auth/RBAC, secret, and cross-source trust-boundary audit using security-and-hardening (+ sub/*.md), producing docs/security/security_audit.md
---

# `/pb-security` — Security & Cross-Source Trust-Boundary Audit

Perform a rigorous security audit across modified files and cross-source boundaries.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Overrides**:
   - Read [`.agents/skills/security-and-hardening/SKILL.md`](../skills/security-and-hardening/SKILL.md) **AND** all `.agents/skills/security-and-hardening/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific security overrides.
3. **Trace Callers & Trust Boundaries**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<endpoint_or_auth_symbol>", source_id="<source_id>", relation_depth=2)`.
   - Call `projectbrain_query_graphify(query="<auth/input entrypoints>", project_id="<project_id>", source_id="<source_id>", action="callers")`.

## 2. Execution (Subagent or Single-Agent Persona)
- **Strict Security Invariant**: Never downgrade security audits or offload them to unvetted external channels.
- **If subagents are supported** (`invoke_subagent(Model="pro")` / `Task` / `Agent`): Spawn a frontier `security-auditor` subagent injected with `security-and-hardening/SKILL.md` + `sub/*.md` + ProjectBrain trace context.
- **If single-agent**: Adopt the `security-auditor` persona step-by-step.

## 3. Deliverables
1. Audit Injection (SQLi, XSS, SSRF, path traversal, command injection), Auth/RBAC & Tenant Isolation (`proj@source:branch`), Data Privacy/PII, Secret Leakage, and DoS limits.
2. Produce `docs/security/security_audit.md` with `PASSED` or `FAILED` verdict and remediation items.
3. If security fixes are applied to `<source_id>`, run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
