---
name: pb-create-agent
description: Dynamically instantiate a specialized Role Agent/Subagent from any .agents/skills/<skill>/SKILL.md + sub/*.md overrides + ProjectBrain MCP context
---

# `/pb-create-agent` — Dynamic Role-Based Agent Creator

Instantiate a specialized agent for any skill, even in IDEs without pre-configured agent definitions.

## 1. Hub & Skill Assembly Protocol
1. **Verify ProjectBrain Hub**:
   - Call `projectbrain_list_sources(project_id="<project_id>")`. If empty, run `projectbrain_bootstrap_project_hub(project_id="<project_id>", workspace_root=".", auto_discover=True)`.
2. **Load Skill + Project Overrides**:
   - Read `.agents/skills/<skill>/SKILL.md`.
   - List and read **all** `.agents/skills/<skill>/sub/*.md` files (excluding `README.md`).
   - Merge order: `sub/*.md` overrides conflicting defaults in `SKILL.md`.
3. **Fetch Scoped ProjectBrain Context**:
   - Run `projectbrain_hybrid_query(query="<task summary>", project_id="<project_id>", source_id="<source_id>", include_shared_docs=True, relation_depth=1)`.
   - If the task touches APIs, schemas, or events, run `projectbrain_cross_source_impact(project_id="<project_id>", target="<symbol_or_route>", source_id="<source_id>")`.

## 2. Prompt Injection Template
Use this exact structure when spawning a subagent or adopting the persona:

```markdown
You are acting as `<role_name>` scoped to `project_id=<project_id>`, `source_id=<source_id>` (`<proj>@<source>:<branch>`).

## Base Skill
<contents or file pointer of .agents/skills/<skill>/SKILL.md>

## Project-Specific Overrides (Precedence Over Base Skill)
<contents of all .agents/skills/<skill>/sub/*.md excluding README.md, or "None">

## ProjectBrain Multi-Source Context
- Registered sources: <projectbrain_list_sources summary>
- Hybrid query & shared docs: <relevant hits / pointers>
- Cross-source impact: <impacted upstream/downstream consumers>

## Task & Boundaries
- Objective: <clear goal>
- Allowed files (disjoint scope): <exact files or directory>
- Output contract: <expected artifact or diff summary>
- Mandatory Post-Edit Sync: If you modify code in `<source_id>`, run:
  `projectbrain_sync_graphify(project_id="<project_id>", source_id="<source_id>", upgrade_mode="delta", auto_link_sources=True)`
```

## 3. Host IDE Execution Adapter
- **Antigravity**: Call `invoke_subagent(TypeName="self" | "<role>", Role="<Role Title>", Model="flash" | "pro", Prompt=...)`. (Use `Model="pro"` for security, architecture, or complex debugging; `Model="flash"` for bounded builds `<=3` files and tests.)
- **Claude Code**: Spawn a subagent with the `Agent` / `Task` tool using the assembled prompt above.
- **Cursor**: Delegate to a subagent if enabled; otherwise adopt the assembled persona directly in the current turn.
- **Codex / Single-Agent IDEs**: Adopt the assembled persona directly and execute each step sequentially, finishing with `projectbrain_sync_graphify`.
