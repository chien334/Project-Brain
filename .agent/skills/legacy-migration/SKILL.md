---
name: legacy-migration
description: Manages legacy codebase maintenance and technology migration (especially for Japanese legacy systems). Use when translating Japanese code/docs, refactoring legacy components, mapping call graphs, or migrating tech stacks.
---

# Legacy Codebase Migration & Maintenance Skill

This skill guides the AI agent through the step-by-step lifecycle of analyzing, refactoring, and migrating legacy codebases (particularly Japanese projects containing Japanese specifications or comments).

---

## 🛠️ Activation Scenarios

Activate this skill when:
- The user requests to migrate a codebase from old tech (Struts, WebForms, etc.) to new tech.
- Translating Japanese code comments, schemas, or design documents.
- Tracing legacy dependency graphs or identifying dead code.
- Implementing refactored modules with strict regression testing.

---

## 🏃 Lifecycle Workflow

Always follow these stages sequentially:

### Stage 1: Define & Translate (Specification Mapping)
1. **Translate Specs**: If design documents (PDFs, Word docs) are in Japanese, translate them to English and ingest them into ProjectBrain.
2. **Translate Code Comments**:
   Call the `migration_translate_code_comments` tool on legacy source files to create a readable English codebase:
   ```bash
   python3 extensions_mcp/codebase_migration_helper/client.py migration_translate_code_comments '{"file_path": "<legacy_file_path>"}'
   ```
3. **Verify Translation**: Confirm the translated code is structurally identical and only comments are modified.

### Stage 2: Planning & Dependency Analysis
1. **Build Call Graph**: Ensure CodeGraph is initialized and synced for the base project branch:
   ```bash
   codegraph init
   python3 -m projectbrain.main codegraph-sync <project_id>:<base_branch>
   ```
2. **Analyze Call Sites**: Use `codegraph_callers` to find all entry points calling the legacy class.
3. **Create Migration ADR**: Write an Architecture Decision Record (ADR) detailing the refactoring plan and list it under ProjectBrain.

### Stage 3: Building & Refactoring
1. **Generate Recommendations**: Use `migration_recommend_refactor` to get structural mappings and snippets for the target stack:
   ```bash
   python3 extensions_mcp/codebase_migration_helper/client.py migration_recommend_refactor '{"file_path": "<legacy_file>", "tech_stack": "<target_stack>"}'
   ```
2. **Implement Incremental Changes**: Implement controllers, service classes, or APIs step-by-step. Keep variables and constraints mapped correctly.

### Stage 4: Verify & Ship
1. **Write Regression Tests**: Ensure the migrated code matches the exact behavior of the legacy implementation.
2. **Review Version Diff**: Run `projectbrain_diff_project_versions` to review added structural symbols and ensure no accidental deletions of unrelated code.
