# `/pb-create-sub-skill` — Project-Specific Sub-Skill Creator

Create or update project-specific custom rules in `.agents/skills/<skill>/sub/<topic>.md` without touching `SKILL.md`.

## Steps
1. **Verify ProjectBrain Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Inspect Target Skill**:
   - Confirm `.agents/skills/<skill>/SKILL.md` exists (do **NOT** edit `SKILL.md`).
   - Review existing `.agents/skills/<skill>/sub/*.md` files (`README.md` is documentation-only and excluded when agents load overrides).
3. **Ground in Repo Context**:
   - Call `projectbrain_hybrid_query(query="<topic conventions>", project_id="<project_id>", include_shared_docs=True)` to align rules with real codebase patterns.
4. **Create `.agents/skills/<skill>/sub/<topic>.md`**:
   Include YAML frontmatter (`parent_skill`, `topic`, `applies_to_sources`), **Override Rules (Takes Precedence Over SKILL.md)**, **Project Conventions & Commands**, **Boundaries (Always / Ask first / Never)**, and a **Verification Checklist**.
5. Optionally store durable architectural conventions with `projectbrain_store`.
