---
description: Create or update project-specific custom rules in .agents/skills/<skill>/sub/<topic>.md without touching SKILL.md
argument-hint: "<skill-name> <topic> <rule summary>"
---

# `/pb-create-sub-skill` — Project-Specific Sub-Skill Creator

Request: `$ARGUMENTS`

## Steps
1. **Verify ProjectBrain Hub**: Call `projectbrain_list_sources(project_id="<project_id>")`; if empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Inspect Target Skill**:
   - Verify `.agents/skills/<skill>/SKILL.md` exists. Do **NOT** modify `SKILL.md`.
   - Check existing `.agents/skills/<skill>/sub/*.md` files to prevent conflicts (`README.md` is informational and ignored during execution).
3. **Ground in Repo Context**:
   - Query `projectbrain_hybrid_query(query="<topic conventions>", project_id="<project_id>", include_shared_docs=True)` so overrides match real codebase conventions.
4. **Write `.agents/skills/<skill>/sub/<topic>.md`**:
   Include frontmatter (`parent_skill`, `topic`, `applies_to_sources`), **Override Rules (Takes Precedence Over SKILL.md)**, **Project Conventions & Commands**, **Boundaries (Always / Ask first / Never)**, and a **Verification Checklist**.
5. Optionally store durable architectural decisions via `projectbrain_store`.
