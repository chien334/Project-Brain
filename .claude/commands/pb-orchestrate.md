---
description: Master ProjectBrain Multi-Source Orchestrator (Spec -> Plan -> Parallel Build -> Test -> Review/Security -> Sync)
argument-hint: "<task or feature description>"
---

# `/pb-orchestrate` — Multi-Source ProjectBrain Orchestrator

Task: `$ARGUMENTS`

## 1. Hub & Skill Override Gate
1. **Verify ProjectBrain Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True)`.
2. **Load Orchestrator Skill + Overrides**: Read `.agents/skills/multi-agent-orchestrator/SKILL.md` **AND** all `.agents/skills/multi-agent-orchestrator/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific overrides.
3. **Discover Impacted Sub-Sources**: Call `projectbrain_hybrid_query(query="$ARGUMENTS", project_id="<project_id>", include_shared_docs=True, relation_depth=1)` and `projectbrain_database_topology(project_id="<project_id>")`.

## 2. Dynamic Role Dispatch
- Use Claude Code's `Agent` / `Task` tool to spawn a dedicated role-based subagent per stage/slice, injecting:
  1. Target `.agents/skills/<skill>/SKILL.md` + all `.agents/skills/<skill>/sub/*.md` (excluding `README.md`).
  2. Scoped ProjectBrain context (`proj@source:branch`, `include_shared_docs=True`, and `projectbrain_cross_source_impact` for contract changes).
  3. Disjoint target file boundaries and post-edit `projectbrain_sync_graphify` requirement.
- If subagents are unavailable, adopt each role persona sequentially.

## 3. Pipeline Stages
1. **Spec** (`spec-driven-development` + `sub/*.md`) -> `docs/specs/{feature_id}.spec.md`
2. **Plan** (`planning-and-task-breakdown`, `api-and-interface-design` + `sub/*.md`) -> `docs/plans/{feature_id}.plan.md`
3. **Parallel Sub-Source Build** (`incremental-implementation`, `frontend-ui-engineering`, `api-and-interface-design` + `sub/*.md`) -> disjoint edits + `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)` per modified `source_id`
4. **Test** (`test-driven-development`, `debugging-and-error-recovery` + `sub/*.md`) -> `docs/reports/test_report.json`
5. **Cross-Source Review** (`code-review-and-quality`, `doubt-driven-development` + `sub/*.md`) -> `docs/reviews/review_signoff.md`
6. **Security Audit** (`security-and-hardening` + `sub/*.md`) -> `docs/security/security_audit.md`
7. **Final Sync** (`documentation-and-adrs` + `sub/*.md`) -> `projectbrain_sync_graphify` + `projectbrain_upload_document(..., scope="project_shared")`
