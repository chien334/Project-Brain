---
name: pb-orchestrate
description: Master ProjectBrain Multi-Source Orchestrator - Routes tasks across Spec -> Plan -> Parallel Sub-Source Build -> Test -> Cross-Source Review/Security -> Sync using SKILL.md + sub/*.md overrides
---

# `/pb-orchestrate` — Master Multi-Source Orchestrator

Coordinate end-to-end delivery across one or more ProjectBrain sub-sources (`proj@source:branch`).

## 1. Hub & Skill Override Gate (MANDATORY)
1. **Verify ProjectBrain Hub**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`.
   - If empty or uninitialized, run `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True)`.
2. **Load Orchestrator Skill + Overrides**:
   - Read [`.agents/skills/multi-agent-orchestrator/SKILL.md`](../skills/multi-agent-orchestrator/SKILL.md) **AND** all `.agents/skills/multi-agent-orchestrator/sub/*.md` (excluding `README.md`). Apply `sub/*.md` as project-specific overrides.
3. **Inspect Multi-Source Topology**:
   - Query `projectbrain_database_topology(project_id="<project_id>")` and `projectbrain_hybrid_query(query="<task goal>", project_id="<project_id>", include_shared_docs=True, relation_depth=1)` to map affected sub-sources (`source_id`).

## 2. Dynamic Agent Dispatch Pattern
- **Multi-Agent IDEs** (`invoke_subagent`, `Task`, `Agent`, or Dual-Channel `client.py`): Spawn one dedicated role-based agent per stage/slice using [`pb-create-agent.md`](./pb-create-agent.md), injecting its target `<skill>/SKILL.md`, all `<skill>/sub/*.md` overrides (excluding `README.md`), and scoped ProjectBrain context (`proj@source:branch`).
- **Single-Agent IDEs**: Adopt each stage persona sequentially in one session while honoring the same `SKILL.md` + `sub/*.md` precedence and disjoint slice boundaries.

## 3. Orchestration Pipeline
1. **Stage 1 — Spec** ([`pb-spec.md`](./pb-spec.md)):
   - Skill: `spec-driven-development` (+ `sub/*.md`).
   - Output: `docs/specs/{feature_id}.spec.md` with cross-source contract impact.
2. **Stage 2 — Plan** ([`pb-plan.md`](./pb-plan.md)):
   - Skills: `planning-and-task-breakdown`, `api-and-interface-design` (+ `sub/*.md`).
   - Call `projectbrain_cross_source_impact` before locking API/DTO/event boundaries.
   - Output: `docs/plans/{feature_id}.plan.md` with disjoint file slices grouped by `source_id`.
3. **Stage 3 — Parallel Sub-Source Build** ([`pb-build.md`](./pb-build.md)):
   - Skills: `incremental-implementation`, `frontend-ui-engineering`, `api-and-interface-design` (+ `sub/*.md`).
   - Fan out workers on strictly disjoint files across sub-sources; run local syntax/compile checks.
   - After edits in each `source_id`, run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
4. **Stage 4 — Test** ([`pb-test.md`](./pb-test.md)):
   - Skills: `test-driven-development`, `debugging-and-error-recovery` (+ `sub/*.md`).
   - Output: `docs/reports/test_report.json` (100% pass required; escalate failures via 3-Stage Circuit Breaker).
5. **Stage 5 — Cross-Source Review** ([`pb-review.md`](./pb-review.md)):
   - Skills: `code-review-and-quality`, `doubt-driven-development` (+ `sub/*.md`).
   - Output: `docs/reviews/review_signoff.md` (`APPROVED` / `REQUEST_CHANGES`).
6. **Stage 6 — Security Audit** ([`pb-security.md`](./pb-security.md)):
   - Skill: `security-and-hardening` (+ `sub/*.md`).
   - Never downgraded or offloaded to unvetted channels. Output: `docs/security/security_audit.md`.
7. **Stage 7 — Final Sync** ([`pb-sync.md`](./pb-sync.md)):
   - Sync all modified `source_id`s via `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)` and modified shared specs/ADRs via `projectbrain_upload_document(..., scope="project_shared")`.
