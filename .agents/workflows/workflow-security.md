---
name: workflow-security
description: Stage 6 Security Audit Workflow - Deep OWASP, auth, data privacy, secret, and cross-source trust-boundary audit using security-and-hardening SKILL.md + sub/*.md overrides
---

# Stage 6: Security & Hardening Audit Workflow

## 0. Mandatory ProjectBrain Hub & Skill Override Gate
1. **Ensure ProjectBrain Hub is Initialized**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Project Overrides**:
   - Load [`../skills/security-and-hardening/SKILL.md`](../skills/security-and-hardening/SKILL.md) **AND** all `.agents/skills/security-and-hardening/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific security overrides.
3. **Trace Cross-Source Trust Boundaries**:
   - Call `projectbrain_cross_source_impact(project_id="<project_id>", target="<endpoint_or_auth_symbol>", source_id="<source_id>", relation_depth=2)` and `projectbrain_query_graphify(query="<auth/input entrypoints>", project_id="<project_id>", source_id="<source_id>", action="callers")` to inspect all callers and cross-source entrypoints.

## 1. Objective & Persona
- **Persona**: `security-auditor`
- **Channel & Model Engine (Rule 1 Locked)**: **Channel B — Gemini 4 Carbon** (`invoke_subagent(Model="pro")`, `--model gemini-4-carbon`, `reasoning_effort: high`; `gemini-4-neon` only as primary fallback / optional Stage-1 CWE pre-scan).
  - **Strict Security Invariant**: Security audits, OWASP scans, auth/crypto/CVE/migration reviews NEVER offload to `gemini-desktop-app` and NEVER downgrade under budget modes.
  - **Single-Agent IDEs**: Adopt the `security-auditor` persona directly and execute Section 3 step-by-step.

## 2. Input Contract
- **Prerequisite**: Stage 5 Review `APPROVED` (`docs/reviews/review_signoff.md`).
- **Inputs Required**: Full diff, modified files, config/env usages, and ProjectBrain cross-source trust-boundary trace.

## 3. Execution Steps
1. **Emit Start Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "SEC-{FEATURE}" --agent "security-auditor" --model "gemini-4-carbon" \
     --effort "high" --complexity "critical" --status "running" --progress 20 \
     --scope "Threat Modeling & Vulnerability Scan" \
     --notes "Applying security-and-hardening SKILL.md + sub/*.md overrides"
   ```
2. **Deep Security Inspection**:
   - **Injection Defense**: SQLi, command injection, path traversal, XSS, SSRF.
   - **Auth, RBAC & Cross-Source Trust**: Token validation, tenant isolation (`proj@source:branch`), privilege boundaries across `source_id`s.
   - **Data Privacy & Compliance**: No PII/secrets in logs, errors, or telemetry.
   - **Secret & Credential Leakage**: No hardcoded keys, tokens, or credentials.
   - **Denial of Service**: Bounded loops, payload/timeout/rate limits.
3. **Artifact Generation & Telemetry**:
   - Produce `docs/security/security_audit.md` following Section 4.
   - Update orchestrator telemetry (`--status completed --progress 100`) if active.

## 4. Output Contract (`docs/security/security_audit.md`)
```markdown
# Security & Hardening Audit Report: {Feature Name}

- **Auditor**: `security-auditor`
- **Model Engine**: `gemini-4-carbon` (effort: high)
- **Verdict**: `PASSED` | `FAILED`

## Threat Model & Findings
| Vulnerability Class | Risk Level | Status | Notes |
|---|---|---|---|
| OWASP Injection | Critical | PASS | Parameterized queries & input validation verified |
| Auth, RBAC & Cross-Source Trust | High | PASS | Tenant & role checks verified |
| Secret Leakage | Critical | PASS | Zero plaintext tokens in source code |

## Sign-Off Signature
- **Security Checksum**: `{SHA256}`
- **Release Approved**: `YES` | `NO`
```

> **Handoff**: Unlocks final delta sync (`pb-sync.md`) and mission audit export.
