# `/pb-bootstrap` — Zero-Touch ProjectBrain Hub Setup

Initialize or verify the ProjectBrain Multi-Source Hub for the active workspace.

## Steps
1. **Fetch Setup Playbook**:
   Call `projectbrain_get_agent_setup_playbook(project_id="<optional_project_id>", workspace_root=".")` to resolve the target `project_id` and setup sequence.
2. **Discover Workspace Sub-Sources & Shared Docs**:
   Call `projectbrain_discover_workspace_sources(workspace_root=".")` to inspect detected sub-sources and shared docs.
3. **Bootstrap Hub**:
   Call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True, sync_graphs=True, auto_link=True, upgrade_mode="delta")`.
4. **Verify**:
   Call `projectbrain_list_sources(project_id="<project_id>")` and `projectbrain_database_topology(project_id="<project_id>")`.
5. Report registered `source_id`s (`proj@source:main`), `__shared__` doc bindings, and remind subsequent turns to load `.agents/skills/<skill>/SKILL.md` + `sub/*.md` overrides.
