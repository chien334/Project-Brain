# Legacy Codebase Maintenance & Technology Migration Runbook

This runbook outlines the architectural model, workflows, and tools combo to automate legacy system maintenance and technology migrations—with a specific focus on legacy Japanese software projects (e.g. Cobol,Struts, ASP.NET WebForms).

---

## 🏗️ The Migration Combo Model

We combine **four distinct layers** of capability to solve migrations safely with zero regression:

```
  +-------------------------------------------------------------------------+
  |                   AI AGENT LIFECYCLE (Define -> Ship)                    |
  +-------------------------------------------------------------------------+
                                       |
                                       v
  +-------------------------------------------------------------------------+
  |              PROJECTBRAIN (RAG)           |        CODEGRAPH (AST)      |
  |  - Ingest Japanese specifications         |  - Map call hierarchy       |
  |  - Index database schemas                 |  - Track code dependencies  |
  |  - Search translated ADRs & design docs   |  - Conduct impact analysis  |
  +-------------------------------------------------------------------------+
                                       |
                                       v
  +-------------------------------------------------------------------------+
  |                       CODEBASE MIGRATION HELPER                         |
  |  - Translate Japanese comments & symbols (`migration_translate_code`)   |
  |  - Suggest modern refactoring designs (`migration_recommend_refactor`)   |
  +-------------------------------------------------------------------------+
```

---

## 🏃 Legacy Migration Workflow (Step-by-Step)

### Phase 1: Define & Translate (ProjectBrain + Migration Helper)
Legacy Japanese projects often lack English documentation, or their source code contains Japanese comments (Kanji/Kana) that block non-Japanese speaking developers.

1. **Ingest Japanese Documentation**: Upload `.pdf`, `.docx`, or `.xlsx` specification files to the ProjectBrain server via the dashboard at `http://localhost:8080/dashboard/`.
2. **Translate Source Code Comments**:
   Use `migration_translate_code_comments` to translate comments of legacy files in bulk:
   ```bash
   python3 extensions_mcp/codebase_migration_helper/client.py migration_translate_code_comments '{"file_path": "legacy/StaffService.java"}'
   ```
3. **Persist Learnings**: Store translated design patterns or functional specifications into ProjectBrain:
   ```bash
   python3 -m projectbrain.main store "StaffService.java handles staff permissions logic..." --tags "documentation,translated" --user_id "my-project:migration"
   ```

---

### Phase 2: Structural Analysis & Dependency Mapping (CodeGraph)
Before changing legacy code, you must understand its dependencies and side effects.

1. **Scan Codebase**: Initialize CodeGraph to parse symbols (classes, methods, functions):
   ```bash
   codegraph init
   python3 -m projectbrain.main codegraph-sync my-project:migration
   ```
2. **Trace Callers & Callees**: Find which modules depend on the class or function you are migrating:
   *   MCP Tool: `codegraph_callers` or `codegraph_impact`.
   *   *Example*: Determine if changing `db_fetch_user` will break billing or inventory modules.

---

### ⚠️ Mandatory Planning & User Approval Gate (CRITICAL)
Before starting Phase 3, the AI Agent or developer **MUST STOP** and present the migration architecture/plan. **DO NOT** execute any refactoring or make code changes in the target workspace until the user reviews, edits, and grants explicit approval.

---

### Phase 3: Incremental Refactoring & Migration (Only After Approval)
Now we generate the migrated codebase in the target stack (e.g. converting Struts JSP to React, or inline SQL to SQLAlchemy) based on the approved plan.

1. **Generate Refactoring Blueprint**: Run `migration_recommend_refactor` to get detailed mappings and snippets:
   ```bash
   python3 extensions_mcp/codebase_migration_helper/client.py migration_recommend_refactor '{"file_path": "legacy/StaffService.java", "tech_stack": "Spring Boot"}'
   ```
2. **Write Refactored Code**: Write the modern equivalent classes/controllers using the suggested blueprint.

---

### Phase 4: Verification & Regression Testing
Japanese projects require strict quality gates with zero regressions.

1. **Write Unit Tests**: Generate tests for the new stack (Spring Boot / React / FastAPI) mapping to the legacy business logic.
2. **Execute & Compare**: Run the new test suite to verify matching output results.
3. **Compare Versions**: Use the Version Comparison dashboard to review the structural code diff (added symbols in new stack, deleted/deprecated symbols in old stack) and memory diff.
