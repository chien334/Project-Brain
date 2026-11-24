# ProjectBrain Client Kit — Universal Agent Instructions

Universal entry-point for **Codex**, **Cursor**, **Claude Code**, and **Antigravity** agents operating in a ProjectBrain-enabled repository.

---

## 1. Zero-Touch ProjectBrain Hub Gate
Before planning or editing code:
1. Call `projectbrain_list_sources(project_id="<project_id>")`.
2. If empty or uninitialized, run zero-touch setup:
   - *(Optional preview)* `projectbrain_discover_workspace_sources(workspace_root=".")`
   - `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True)`
   - *(Playbook reference)* `projectbrain_get_agent_setup_playbook(workspace_root=".")`

---

## 2. Multi-Source Tenant Conventions (`proj@source:branch`)
- **Composite IDs**: Scope queries and edits using `project_id` + `source_id` + `branch` (e.g., `my-app@backend-api:main`, `my-app@frontend-web:main`).
- **Shared Knowledge**: Project-wide specs, OpenAPI contracts, and ADRs live under `__shared__` (`my-app@__shared__`).
- **Scoped Retrieval**: Always pass `source_id`, `include_shared_docs=True`, and `relation_depth=1` to `projectbrain_hybrid_query` or `projectbrain_query`.
- **Cross-Source Impact Gate**: Before changing any API route, DTO, schema, or event, call `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")`.
- **Post-Edit Delta Sync (MANDATORY)**: After modifying code in any sub-source, run:
  ```text
  projectbrain_sync_graphify(
    project_id="<project_id>",
    source_id="<source_id>",
    upgrade_mode="delta",
    auto_link_sources=True
  )
  ```
  After modifying shared specs or ADRs, run `projectbrain_upload_document(project_id="<project_id>", scope="project_shared", file_path="<doc_path>")`.

---

## 3. Skill & Sub-Skill Precedence (`SKILL.md` + `sub/*.md`)
1. Always load the target skill `.agents/skills/<skill>/SKILL.md` **AND** all `.agents/skills/<skill>/sub/*.md` files (excluding `README.md`).
2. **Precedence**: Rules in `sub/*.md` are project-specific overrides and **take precedence** over defaults in `SKILL.md`.
3. Never edit `SKILL.md` for repo-specific rules—add or update `.agents/skills/<skill>/sub/<topic>.md` instead (see [`pb-create-sub-skill.md`](./.agents/workflows/pb-create-sub-skill.md)).

---

## 4. Dynamic Agent & Persona Execution
- **Multi-Agent IDEs** (`invoke_subagent` in Antigravity, `Agent`/`Task` in Claude Code, subagents in Cursor): Spawn a dedicated role-based agent injected with `<skill>/SKILL.md`, all `<skill>/sub/*.md` overrides (excluding `README.md`), scoped ProjectBrain context (`proj@source:branch`), and disjoint target files (see [`pb-create-agent.md`](./.agents/workflows/pb-create-agent.md)).
- **Single-Agent IDEs** (e.g., Codex CLI or single-turn sessions): Adopt the same role persona step-by-step with identical skill precedence and post-edit `projectbrain_sync_graphify` sync.

---

## 5. Available `/pb-*` Commands & Workflows
Available across Antigravity ([`.agents/workflows/`](./.agents/workflows/)), Claude Code ([`.claude/commands/`](./.claude/commands/)), and Cursor ([`.cursor/commands/`](./.cursor/commands/)):

| Command | Purpose | Primary Skill(s) (`+ sub/*.md`) |
|---|---|---|
| `/pb-bootstrap` | Zero-touch workspace discovery & ProjectBrain hub setup | Hub MCP Playbook |
| `/pb-orchestrate` | Master multi-source pipeline (`Spec -> Plan -> Build -> Test -> Review -> Security -> Sync`) | `multi-agent-orchestrator` |
| `/pb-create-agent` | Dynamically instantiate a role agent from any `SKILL.md` + `sub/*.md` + ProjectBrain context | Any `.agents/skills/<skill>` |
| `/pb-create-sub-skill` | Add/update project overrides in `.agents/skills/<skill>/sub/<topic>.md` | Target `<skill>/sub/*.md` |
| `/pb-spec` | Produce `docs/specs/{feature_id}.spec.md` with cross-source contract impact | `spec-driven-development` |
| `/pb-plan` | Produce `docs/plans/{feature_id}.plan.md` with disjoint-file Task DAG | `planning-and-task-breakdown`, `api-and-interface-design` |
| `/pb-build` | Implement disjoint sub-source edits + delta Graphify sync | `incremental-implementation`, `frontend-ui-engineering`, `api-and-interface-design` |
| `/pb-test` | Author & run spec-driven tests -> `docs/reports/test_report.json` | `test-driven-development`, `debugging-and-error-recovery` |
| `/pb-review` | 5-axis + cross-source drift review -> `docs/reviews/review_signoff.md` | `code-review-and-quality`, `doubt-driven-development` |
| `/pb-security` | OWASP, auth/RBAC, secret & trust-boundary audit -> `docs/security/security_audit.md` | `security-and-hardening` |
| `/pb-sync` | Delta-sync modified sub-sources, shared docs & cross-source links | `documentation-and-adrs`, `git-workflow-and-versioning` |
