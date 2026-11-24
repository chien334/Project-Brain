# `/pb-orchestrate` — Master Multi-Source ProjectBrain Orchestrator

Route a multi-source task through **Spec -> Plan -> Parallel Sub-Source Build -> Test -> Cross-Source Review/Security -> Sync**.

## 1. Hub & Skill Override Gate
1. **Verify ProjectBrain Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True)`.
2. **Load Orchestrator Skill + Overrides**: Read `.agents/skills/multi-agent-orchestrator/SKILL.md` **AND** all `.agents/skills/multi-agent-orchestrator/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific overrides.
3. **Inspect Topology**: Call `projectbrain_database_topology(project_id="<project_id>")` and `projectbrain_hybrid_query(query="<task goal>", project_id="<project_id>", include_shared_docs=True, relation_depth=1)` to map affected sub-sources (`proj@source:branch`).

## 2. Dynamic Role Dispatch
- If your Cursor harness supports subagents, spawn a dedicated role-based agent per stage/slice injected with `<skill>/SKILL.md`, all `<skill>/sub/*.md` overrides (excluding `README.md`), scoped ProjectBrain context, and disjoint file boundaries.
- Otherwise, adopt each stage persona sequentially step-by-step.

## 3. Pipeline Stages
1. **Spec** (`spec-driven-development` + `sub/*.md`) -> `docs/specs/{feature_id}.spec.md`
2. **Plan** (`planning-and-task-breakdown`, `api-and-interface-design` + `sub/*.md`; check `projectbrain_cross_source_impact`) -> `docs/plans/{feature_id}.plan.md`
3. **Parallel Sub-Source Build** (`incremental-implementation`, `frontend-ui-engineering`, `api-and-interface-design` + `sub/*.md`) -> disjoint edits + `projectbrain_sync_graphify(project_id=..., source_id=..., upgrade_mode="delta", auto_link_sources=True)` per modified `source_id`
4. **Test** (`test-driven-development`, `debugging-and-error-recovery` + `sub/*.md`) -> `docs/reports/test_report.json`
5. **Cross-Source Review** (`code-review-and-quality`, `doubt-driven-development` + `sub/*.md`) -> `docs/reviews/review_signoff.md`
6. **Security Audit** (`security-and-hardening` + `sub/*.md`) -> `docs/security/security_audit.md`
7. **Final Sync** (`documentation-and-adrs` + `sub/*.md`) -> `projectbrain_sync_graphify` + `projectbrain_upload_document(..., scope="project_shared")`
