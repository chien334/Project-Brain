---
name: pb-create-sub-skill
description: Create or update project-specific rule overrides in .agents/skills/<skill>/sub/<topic>.md without modifying SKILL.md
---

# `/pb-create-sub-skill` — Project-Specific Sub-Skill Creator

Capture repository-specific conventions, stacks, boundaries, and overrides in `.agents/skills/<skill>/sub/<topic>.md` while keeping the upstream `SKILL.md` untouched.

## 1. Hub & Existing Skill Check
1. **Verify ProjectBrain Hub**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, call `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Inspect Target Skill & Existing Overrides**:
   - Verify `.agents/skills/<skill>/SKILL.md` exists (do **NOT** edit `SKILL.md`).
   - List existing files in `.agents/skills/<skill>/sub/` to avoid duplicating rules. Remember `README.md` is informational only and excluded during agent loading.
3. **Gather Repo Context**:
   - Query `projectbrain_hybrid_query(query="<topic conventions>", project_id="<project_id>", include_shared_docs=True)` to align overrides with actual codebase patterns.

## 2. Sub-Skill File Template
Create `.agents/skills/<skill>/sub/<topic>.md` (kebab-case `<topic>`, e.g., `spec.md`, `react-mantine.md`, `auth-boundaries.md`):

```markdown
---
parent_skill: <skill>
topic: <topic>
applies_to_sources: [<source_id_or_all>]
---

# <Skill> Override: <Topic>

## Override Rules (Takes Precedence Over SKILL.md)
- ...

## Project Conventions & Commands
- ...

## Boundaries (Always / Ask First / Never)
- **Always**: ...
- **Ask first**: ...
- **Never**: ...

## Verification Checklist
- [ ] ...
```

## 3. Persist & Verify
- Save `.agents/skills/<skill>/sub/<topic>.md`.
- Optionally record key architectural constraints in ProjectBrain memory (`projectbrain_store`) or sync docs if appropriate.
- Confirm the file is concise, non-contradictory with sibling `sub/*.md` files, and clearly specifies what it overrides in `SKILL.md`.
