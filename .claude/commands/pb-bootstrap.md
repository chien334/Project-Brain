---
description: Zero-touch ProjectBrain Multi-Source Hub setup (playbook -> discover -> bootstrap -> verify)
argument-hint: "[project_id]"
---

# `/pb-bootstrap` — Zero-Touch ProjectBrain Hub Setup

Target project (optional): `$ARGUMENTS`

## Steps
1. **Fetch Onboarding Playbook**:
   Call `projectbrain_get_agent_setup_playbook(project_id="$ARGUMENTS" or None, workspace_root=".")` to obtain the suggested `project_id` and MCP sequence.
2. **Discover Sub-Sources & Shared Docs**:
   Call `projectbrain_discover_workspace_sources(workspace_root=".")` and review detected `sources` and `shared_docs`.
3. **Bootstrap Hub**:
   Call `projectbrain_bootstrap_project_hub(project_id="<resolved_project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True, upgrade_mode="delta")`.
4. **Verify**:
   Call `projectbrain_list_sources(project_id="<resolved_project_id>")` and `projectbrain_database_topology(project_id="<resolved_project_id>")`.
5. Summarize registered `source_id`s (`proj@source:main`), `__shared__` docs, and note that all `/pb-*` commands apply `.agents/skills/<skill>/SKILL.md` + `sub/*.md` overrides.
