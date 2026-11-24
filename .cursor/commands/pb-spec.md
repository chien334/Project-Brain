# `/pb-spec` — Multi-Source Specification Engineering

Create a structured feature specification grounded in ProjectBrain multi-source code and shared docs.

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Overrides**: Read `.agents/skills/spec-driven-development/SKILL.md` **AND** all `.agents/skills/spec-driven-development/sub/*.md` (excluding `README.md`). Also load `api-and-interface-design` (+ `sub/*.md`) for cross-source contracts.
3. **Query ProjectBrain**:
   - `projectbrain_hybrid_query(query="<feature>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`
   - `projectbrain_trace_code_and_docs(target="<domain/route>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)`
   - `projectbrain_cross_source_impact(project_id="<project_id>", target="<target>", source_id="<source_id>")`
4. **Execute**: Spawn a `spec-engineer` subagent (if supported) or adopt the `spec-engineer` persona with `SKILL.md` + `sub/*.md` + ProjectBrain context.
5. **Deliver & Sync**: Surface assumptions first, write `docs/specs/{feature_id}.spec.md` (Objective, Affected Sub-Sources, Formal Invariants, Cross-Source Contract Impact, Gherkin ACs, Edge-Case Matrix), and sync shared docs via `projectbrain_upload_document(..., scope="project_shared")` or `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)`.
