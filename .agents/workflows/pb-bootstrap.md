---
name: pb-bootstrap
description: Zero-touch ProjectBrain Multi-Source Hub setup - Discover workspace sub-sources & shared docs, bootstrap hub, and verify topology
---

# `/pb-bootstrap` — Zero-Touch ProjectBrain Hub Setup

Initialize or repair the ProjectBrain Multi-Source Hub for the current workspace in one clean sequence.

## 1. Execution Steps
1. **Fetch Onboarding Playbook**:
   ```text
   projectbrain_get_agent_setup_playbook(project_id="<optional_project_id>", workspace_root=".")
   ```
   Use the returned `project_id` (or `suggested_project_id`) if the user did not supply one.
2. **Discover Sub-Sources & Shared Docs (Read-Only)**:
   ```text
   projectbrain_discover_workspace_sources(workspace_root=".")
   ```
   Inspect detected `sources` (e.g., `backend`, `frontend`, `worker`, `infra`) and `shared_docs` (specs, OpenAPI, ADRs, READMEs).
3. **Bootstrap the Multi-Source Hub**:
   ```text
   projectbrain_bootstrap_project_hub(
     project_id="<project_id>",
     workspace_root=".",
     auto_discover=True,
     sync_graphs=True,
     auto_link=True,
     upgrade_mode="delta"
   )
   ```
   This provisions the project DB, registers sub-sources, extracts Graphify ASTs + HSG memories, ingests `__shared__` docs, and auto-links cross-source contracts.
4. **Verify Registered Sources & Topology**:
   - Call `projectbrain_list_sources(project_id="<project_id>")` and `projectbrain_database_topology(project_id="<project_id>")`.
   - Confirm each sub-source (`proj@source:main`) and `__shared__` binding is present.

## 2. Skill & Rule Alignment
- Ensure the agent loads `.agents/skills/<skill>/SKILL.md` + `.agents/skills/<skill>/sub/*.md` (excluding `README.md`) for subsequent tasks, or creates project overrides via [`pb-create-sub-skill.md`](./pb-create-sub-skill.md).
- Report the active `project_id`, discovered `source_id`s, shared doc bindings, and next suggested command (`/pb-spec`, `/pb-plan`, or `/pb-orchestrate`).
