---
name: create-project-skill
description: Creates or updates project-specific skill overrides inside .agents/skills/<skill-name>/sub/<topic>.md (such as spec.md or api-conventions.md) without editing base SKILL.md files. Use when tailoring ProjectBrain skills to a repository's stack, conventions, commands, boundaries, or domain rules so customizations survive client-kit upgrades.
---

# Create Project Skill Override

## Overview

ProjectBrain skills use a two-layer architecture so repository customizations never collide with client-kit upgrades:

1. **Base Skill (`SKILL.md`)** — Canonical `addyosmani/agent-skills` engineering workflow integrated with ProjectBrain MCP/CLI verification gates. Maintained and overwritten during client-kit upgrades.
2. **Project Overrides (`sub/<topic>.md`)** — Repository-specific stack details, commands, architectural conventions, boundaries, and domain rules (for example, `spec-driven-development/sub/spec.md` or `api-and-interface-design/sub/api-conventions.md`). **Never overwritten** by client-kit upgrades.

Never edit a skill's `SKILL.md` to add project-only rules. Write or update them inside `.agents/skills/<skill-name>/sub/<topic>.md` instead.

## When to Use

- Onboarding a new repository or multi-source workspace to ProjectBrain skills
- Defining repo-specific commands, directory layouts, tech stack versions, or Definition of Done
- Recording project API conventions, design-system tokens, auth boundaries, testing frameworks, or deployment checklists
- Updating existing `sub/*.md` rules when team standards change

**When NOT to use:**
- Ephemeral task lists or feature plans (put those in `tasks/plan.md`, `tasks/todo.md`, or `projectbrain_store`)
- Universal workflow or ProjectBrain MCP changes that apply across all repositories (those belong in the base client-kit `SKILL.md`)

## Project-Specific Overrides (sub/*.md)

**Mandatory rule:** Before executing this skill, check `.agents/skills/create-project-skill/sub/*.md`. Read all `.md` files in `sub/` (except `README.md`) alphabetically. Project rules in `sub/*.md` **extend or override** defaults in `SKILL.md` (while keeping ProjectBrain MCP verification steps intact). Never edit `SKILL.md` for project-only rules — write them in `sub/<name>.md` so `sub/` survives system skill upgrades.

## ProjectBrain Tooling Integration

Before authoring a `sub/<topic>.md` override, ground the rules in ProjectBrain's actual indexed workspace state rather than guessing:

1. **Discover Workspace Sources & Shared Docs**:
   - `projectbrain_discover_workspace_sources(workspace_root=".")` — detect services, frontends, workers, infra, and existing shared docs (`README.md`, `docs/`, OpenAPI specs, ADRs).
   - `projectbrain_list_sources(project_id="<project_id>")` — verify canonical `source_id` names, `tech_stack`, and `workspace_subpath`.
2. **Query Existing Conventions & Decisions**:
   - `projectbrain_query(query="architecture conventions tech stack commands boundaries", user_id="<project_id>", include_shared_docs=True, relation_depth=1)` — fetch HSG memories and shared docs.
   - `projectbrain_hybrid_query(query="<topic>", project_id="<project_id>", branch="main", source_id="<source_id>", include_shared_docs=True, relation_depth=1)` — inspect AST-backed patterns alongside project memories.
3. **Sync Major Conventions Back to ProjectBrain (Optional / Recommended for Multi-Agent Teams)**:
   - `projectbrain_upload_document(file_path=".agents/skills/<skill-name>/sub/<topic>.md", project_id="<project_id>", branch="main", doc_key="skill_<skill>_<topic>", scope="project_shared", doc_role="reference")` — publish project-wide skill conventions as a shared reference document.
   - `projectbrain_bind_shared_doc(project_id="<project_id>", doc_key="skill_<skill>_<topic>", target_source_id="*", binding_role="reference")` — bind the convention to all sub-sources.
   - `projectbrain_store(content="...", type="contextual", user_id="<project_id>", tags=["project-skill-override", "<skill-name>"])` — record concise convention summaries in HSG memory.

**CLI Equivalents:**
- `python -m projectbrain.main upload-doc <project_id> .agents/skills/<skill-name>/sub/<topic>.md --branch main --doc-key skill_<skill>_<topic>`

## Override Creation Workflow

```
INSPECT TARGET SKILL ──▶ GROUND IN PROJECTBRAIN ──▶ AUTHOR sub/<topic>.md ──▶ VERIFY & SYNC
```

### Step 1: Inspect the Target Skill and Existing Overrides

1. Read `.agents/skills/<skill-name>/SKILL.md` to understand its base workflow, templates, and ProjectBrain verification gates.
2. Check `.agents/skills/<skill-name>/sub/` for existing `.md` files (excluding `README.md`).
3. If an override for the same topic already exists, update it in place rather than creating competing duplicates.

### Step 2: Ground Rules in Workspace Facts

1. Inspect dependency manifests (`package.json`, `pyproject.toml`, `go.mod`, etc.), CI workflows, and ProjectBrain (`projectbrain_discover_workspace_sources`, `projectbrain_list_sources`, `projectbrain_hybrid_query`).
2. Extract real executable commands (exact test, lint, type-check, and build commands), real directory paths, and real `project_id` / `source_id` mappings.
3. Confirm ambiguous policies with the human engineer rather than inventing conventions.

### Step 3: Choose Filename and Precedence Rules

- **Directory:** `.agents/skills/<skill-name>/sub/`
- **Filename:** Short, descriptive kebab-case `.md` (for example, `sub/spec.md`, `sub/api-conventions.md`, `sub/testing-stack.md`). Never use `README.md` or `SKILL.md` as an override filename.
- **Alphabetical Ordering:** Agents read `sub/*.md` alphabetically (excluding `README.md`). Use numeric prefixes (`01-core-stack.md`, `02-service-api.md`) only if a later file must explicitly supersede an earlier override.
- **Additive vs. Override Clarity:** Explicitly mark whether a section **Extends** `SKILL.md` (adds project specifics) or **Overrides** a default in `SKILL.md` (replaces a generic default such as test runner, file path, or branch naming convention).
- **Non-Negotiable Guardrail:** `sub/*.md` may customize paths, tools, frameworks, and thresholds, but must **never disable** ProjectBrain MCP verification checks or security boundaries.

### Step 4: Write `sub/<topic>.md` Using the Template

```markdown
# Project Override: [<skill-name> — <Topic>]

> Applies to: `.agents/skills/<skill-name>/SKILL.md`
> ProjectBrain Scope: `project_id=<project_id>` | `source_id=<source_id or *>`

## Purpose
[One sentence describing what project-specific behavior this override sets.]

## Overrides (Replaces Base Defaults)
- **<Default in SKILL.md>:** [Exact project replacement, e.g. test command, output path, branch prefix]

## Extensions (Adds Project Context)
- **ProjectBrain Identifiers:** `project_id="<project_id>"`, `source_id="<source_id>"`, `branch="main"`
- **Stack & Commands:** [Exact commands and paths]
- **Conventions & Boundaries:** [Always / Ask first / Never rules specific to this repo]

## Additional Verification Checks
- [ ] [Project-specific gate to run alongside base `SKILL.md` verification]
```

### Step 5: Verify & Propagate

1. Confirm `SKILL.md` is unmodified (`git diff .agents/skills/<skill-name>/SKILL.md` is empty if only creating an override).
2. Confirm the new file lives under `.agents/skills/<skill-name>/sub/<topic>.md` and does not clash with `README.md`.
3. If multi-source teammates or remote MCP agents share these conventions, index the override via `projectbrain_upload_document(scope="project_shared", doc_role="reference")` or `projectbrain_store`.

## Common Override Mapping

| Base Skill | Example Override File | Typical Project Rules |
|---|---|---|
| `spec-driven-development` | `sub/spec.md` | Project `project_id`/`source_id` map, PRD directory, mandatory spec sections, approval owners |
| `planning-and-task-breakdown` | `sub/tracker.md` | External issue tracker (`Linear`/`Jira`/`GitHub`), branch prefixes, `tasks/` path conventions |
| `api-and-interface-design` | `sub/api-conventions.md` | Envelope format, error codes, auth headers, OpenAPI file path, versioning policy |
| `frontend-ui-engineering` | `sub/design-system.md` | Component library (e.g., Mantine UI), CSS tokens, breakpoints, state/query stack |
| `frontend-design` | `sub/brand-direction.md` | Brand palette, typography pair, voice/tone guidelines, disallowed defaults |
| `test-driven-development` | `sub/testing-stack.md` | Exact `pytest`/`vitest`/`playwright` commands, fixtures, coverage thresholds |
| `security-and-hardening` | `sub/security-policy.md` | Auth boundaries, RBAC roles, PII classification, approved dependency registries |
| `git-workflow-and-versioning` | `sub/git-conventions.md` | Branch naming, commit format, release tag rules, required CI checks |

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "I'll just add this one project rule straight into `SKILL.md`" | The next ProjectBrain client-kit upgrade will overwrite `SKILL.md` and silently wipe your project rule. Put it in `sub/<topic>.md`. |
| "I'll copy the whole `SKILL.md` into `sub/` and tweak two lines" | Duplicating base instructions creates drift when base workflows improve. Keep `sub/*.md` focused strictly on project deltas. |
| "A README note is enough" | `sub/README.md` is documentation and intentionally excluded from override loading. Only `sub/<topic>.md` files (not `README.md`) are loaded as active rules. |

## Red Flags

- Editing `.agents/skills/<skill-name>/SKILL.md` to hardcode repository-specific paths, URLs, or commands
- Naming an override `README.md` (which agents exclude when loading `sub/*.md`)
- Writing `sub/*.md` rules that instruct agents to skip tests, security checks, or ProjectBrain MCP verification
- Creating overrides from memory without inspecting manifest files or ProjectBrain sources

## Verification

Before finishing a project skill override, confirm:

- [ ] Checked `.agents/skills/create-project-skill/sub/*.md` overrides (if any) before execution
- [ ] Target base `.agents/skills/<skill-name>/SKILL.md` was NOT modified for project-only rules
- [ ] Override file is saved at `.agents/skills/<skill-name>/sub/<topic>.md` (kebab-case, not `README.md`)
- [ ] Override preserves all mandatory ProjectBrain MCP verification steps from `SKILL.md`
- [ ] Commands, paths, and `project_id`/`source_id` values were verified against the repo or ProjectBrain MCP tools
- [ ] Shared team conventions were synced to ProjectBrain (`projectbrain_upload_document` or `projectbrain_store`) when appropriate
