---
description: Create a multi-source feature specification using spec-driven-development (+ sub/*.md) and ProjectBrain context
argument-hint: "<feature description or id>"
---

# `/pb-spec` — Multi-Source Specification Engineering

Feature: `$ARGUMENTS`

## Steps
1. **Verify Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Overrides**: Read `.agents/skills/spec-driven-development/SKILL.md` **AND** all `.agents/skills/spec-driven-development/sub/*.md` (excluding `README.md`). Load `api-and-interface-design` (+ `sub/*.md`) as well for cross-source contracts.
3. **Query ProjectBrain**:
   - `projectbrain_hybrid_query(query="$ARGUMENTS", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`
   - `projectbrain_trace_code_and_docs(target="<domain/route>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)`
   - `projectbrain_cross_source_impact(project_id="<project_id>", target="<target>", source_id="<source_id>")`
4. **Execute**: Spawn a `spec-engineer` subagent via `Agent`/`Task` injected with the skill, `sub/*.md` overrides, and ProjectBrain context (or adopt the persona directly).
5. **Deliver & Sync**: Surface assumptions first, save `docs/specs/{feature_id}.spec.md` (with Formal Invariants, Cross-Source Contract Impact, Gherkin ACs, Edge-Case Matrix), and sync shared specs via `projectbrain_upload_document(..., scope="project_shared")` or `projectbrain_sync_graphify(..., upgrade_mode="delta", auto_link_sources=True)`.
