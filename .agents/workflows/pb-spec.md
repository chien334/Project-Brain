---
name: pb-spec
description: ProjectBrain Spec Command - Run spec-driven-development + sub/*.md overrides with multi-source ProjectBrain context to produce docs/specs/{feature_id}.spec.md
---

# `/pb-spec` — Multi-Source Specification Engineering

Produce a verified feature specification grounded in ProjectBrain multi-source code and shared docs.

## 1. Hub & Skill Override Gate
1. **Ensure Hub Initialized**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Overrides**: Read [`.agents/skills/spec-driven-development/SKILL.md`](../skills/spec-driven-development/SKILL.md) **AND** all `.agents/skills/spec-driven-development/sub/*.md` (excluding `README.md`), applying `sub/*.md` as overrides. (Also load `api-and-interface-design` + `sub/*.md` if defining cross-source contracts.)
3. **Query ProjectBrain Context**:
   - `projectbrain_hybrid_query(query="<feature keywords>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`
   - `projectbrain_trace_code_and_docs(target="<domain/route/symbol>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)`
   - `projectbrain_cross_source_impact(project_id="<project_id>", target="<target>", source_id="<source_id>")`

## 2. Execution (Subagent or Single-Agent Persona)
- **If subagents are supported** (`invoke_subagent` / `Task` / `Agent`): Spawn a `spec-engineer` subagent (`Model="pro"` if security/critical, else `"pro"`/`desktop-ask`) injected with `spec-driven-development/SKILL.md` + `sub/*.md` + ProjectBrain context.
- **If single-agent**: Adopt the `spec-engineer` persona step-by-step.

## 3. Deliverables & Post-Edit Sync
1. Surface explicit assumptions first; resolve ambiguities before finalizing.
2. Write `docs/specs/{feature_id}.spec.md` covering: Objective & Affected Sub-Sources (`proj@source:branch`), Formal Verification Invariants, Cross-Source Contract Impact, Gherkin Acceptance Criteria, and Edge-Case Matrix.
3. If shared specs/contracts were created or modified, upload via `projectbrain_upload_document(project_id="<project_id>", scope="project_shared", file_path="docs/specs/{feature_id}.spec.md")` or run `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
