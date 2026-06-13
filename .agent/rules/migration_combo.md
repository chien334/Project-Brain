# Legacy Codebase Maintenance & Migration Rules

Use these rules when maintaining legacy systems (particularly Japanese codebases) or migrating code from legacy technology stacks (Struts, WebForms, COBOL) to modern ones (Spring Boot, React, FastAPI).

---

### Core Rules for Legacy Workflows

1. **Document Translation & Ingestion First**:
   - Never start modifying legacy Japanese code without first translating its comments, docstrings, and design documentation.
   - Use `migration_translate_code_comments` to convert inline code comments from Japanese to English.
   - Index translated specifications in ProjectBrain using `projectbrain_store` (always scoped to the active project ID).

2. **Conduct Structural Call Tracing**:
   - Before editing or deprecating a legacy method/class, you MUST analyze its dependencies.
   - Use CodeGraph tools (`codegraph_callers`, `codegraph_impact`) to trace all call sites and identify what components will be affected by the migration.

3. **Incremental Migration Plan**:
   - Write a migration plan detailing how you will transition code module-by-module.
   - Refer to the detailed phase checklists in [MIGRATION_RUNBOOK.md](file:///Users/macbbook/SourceCodes/OpenMemory/MIGRATION_RUNBOOK.md).

4. **Preserve Legacy Logic (Zero Regressions)**:
   - When refactoring, keep variable logic, boundary conditions, and database validation checks identical to the legacy implementation.
   - Map legacy database table structures and inline SQL queries to modern equivalents (e.g. SQLAlchemy ORM) carefully.
   - Write extensive unit tests in the new tech stack matching the original inputs and outputs.
