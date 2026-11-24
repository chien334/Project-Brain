---
name: workflow-spec
description: Stage 1 Spec Workflow - Multi-source requirements analysis with ProjectBrain Hub, SKILL.md + sub/*.md overrides, producing docs/specs/{feature_id}.spec.md
---

# Stage 1: Specification Engineering Workflow

## 0. Mandatory ProjectBrain Hub & Skill Override Gate
1. **Ensure ProjectBrain Hub is Initialized**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`.
   - If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True)`.
2. **Load Skill + Project Overrides**:
   - Load [`../skills/spec-driven-development/SKILL.md`](../skills/spec-driven-development/SKILL.md) **AND** all `.agents/skills/spec-driven-development/sub/*.md` (excluding `README.md`), applying `sub/*.md` as project-specific overrides.
   - If designing cross-source API/event contracts, also load [`../skills/api-and-interface-design/SKILL.md`](../skills/api-and-interface-design/SKILL.md) + its `sub/*.md` (excluding `README.md`).
3. **Gather Multi-Source ProjectBrain Context**:
   - Query `projectbrain_hybrid_query(query="<feature keywords>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` and `projectbrain_trace_code_and_docs(target="<domain/route/symbol>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True)`.
   - Run `projectbrain_cross_source_impact(project_id="<project_id>", target="<target>", source_id="<source_id>")` to detect impacted upstream/downstream sources.

## 1. Objective & Persona
- **Persona**: `spec-engineer`
- **Execution Mode**:
  - **Multi-Agent IDEs**: Spawn a dedicated `spec-engineer` subagent (`invoke_subagent` / `Task` / `Agent`) injected with `spec-driven-development/SKILL.md`, all `sub/*.md` overrides, and ProjectBrain context.
  - **Dual-Channel Orchestrator (`HYBRID_60_DESKTOP`, non-security)**: Dispatch via `run_command` (`WaitMsBeforeAsync: 500`):
    ```bash
    python3 ~/.gemini/tools/orchestrator/client.py desktop-ask \
      --task "SPEC-{FEATURE}" \
      --agent spec-engineer \
      --workspace . \
      --output "docs/reports/desktop_worker/SPEC-{FEATURE}.md" \
      --prompt "Analyze requirements and ProjectBrain multi-source context for {FEATURE}. Apply spec-driven-development/SKILL.md + sub/*.md overrides. Produce a complete specification (Background, Formal Invariants, Gherkin Acceptance Criteria, Cross-Source Contract Impact, Edge-Case Matrix) to save as docs/specs/{feature_id}.spec.md. Read-only analysis only."
    ```
  - **Channel B (`API_ONLY_100`)**: `invoke_subagent(Model="pro")` (`gemini-4-neon`, `reasoning_effort: high`).
  - **Security / Critical Specs (`has_security_impact=True` or `complexity == "critical"`)**: Always Channel B `invoke_subagent(Model="pro")` (`gemini-4-carbon`, `reasoning_effort: high`). Never offload security-critical specs to Desktop.
  - **Single-Agent IDEs**: Adopt the `spec-engineer` persona directly and follow Section 3 step-by-step.

## 2. Input Contract
- **Source**: User request, bug description, or feature brief.
- **Context Provided**:
  * ProjectBrain multi-source hits (`proj@source:branch`, `__shared__` specs, cross-source relations).
  * Codebase pointers gathered via fast scouts (`invoke_subagent(Model="flash")`) or `projectbrain_query_graphify`.

## 3. Execution Steps
1. **Emit Start Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "SPEC-{FEATURE}" --agent "spec-engineer" --model "gemini-desktop-app" \
     --complexity "high" --status "running" --progress 20 \
     --scope "Requirements & Multi-Source Discovery" \
     --notes "Loaded spec-driven-development + sub/*.md overrides"
   ```
2. **Ambiguity Resolution & Multi-Source Contract Analysis**:
   - Surface assumptions explicitly before writing spec sections.
   - Identify affected sub-sources (`source_id`), shared schemas/APIs (`__shared__`), non-functional requirements, and edge cases.
   - Formulate formal verification invariants and Gherkin acceptance scenarios.
3. **Artifact Generation & Hub Sync**:
   - Produce `docs/specs/{feature_id}.spec.md` following Section 4.
   - If the spec updates a project-wide contract or shared doc, sync it via `projectbrain_upload_document(project_id="<project_id>", scope="project_shared", file_path="docs/specs/{feature_id}.spec.md")` or `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`.
4. **Emit Completion Telemetry** *(if orchestrator CLI is active)*:
   ```bash
   python3 ~/.gemini/tools/orchestrator/client.py update \
     --task "SPEC-{FEATURE}" --agent "spec-engineer" --model "gemini-desktop-app" \
     --status "completed" --progress 100 --scope "docs/specs/{feature_id}.spec.md" \
     --notes "Spec complete with acceptance criteria, cross-source impact, and invariants"
   ```

## 4. Output Contract (`docs/specs/{feature_id}.spec.md`)
```markdown
# Feature Specification: {Feature Name}

## 1. Background & Business Value
- **Problem Statement**: ...
- **Target Persona**: ...
- **Affected Sub-Sources**: `source_a`, `source_b` (`proj@source:branch`)

## 2. Formal Verification Invariants
- **Invariant 1**: ...

## 3. Cross-Source Contract Impact
- **Impacted APIs / Schemas / Events**: ...

## 4. User Stories & Acceptance Criteria (Gherkin format)
```gherkin
Feature: Feature Name
  Scenario: Happy path execution
    Given ...
    When ...
    Then ...
```

## 5. Edge-Case Matrix
| Scenario | Boundary Condition | Expected Behavior |
|---|---|---|
| ... | ... | ... |
```

> **Handoff**: Pass `docs/specs/{feature_id}.spec.md` as a pointer to **Stage 2 ([`workflow-plan.md`](./workflow-plan.md))**.
