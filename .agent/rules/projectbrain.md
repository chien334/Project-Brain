# ProjectBrain Agent Rules

This project has a ProjectBrain MCP server (`projectbrain_*` tools) configured. ProjectBrain provides a project-centric long-term memory engine and codebase version diffing control panel. 

### When to prefer ProjectBrain over local search or context

Use ProjectBrain tools to manage, search, and recall high-level context, developer decisions, business requirements, and cross-version structural code updates.

| Task | Tool |
|---|---|
| "Search context/memories for project X" | `projectbrain_query` (use type="contextual" or type="unified") |
| "Search temporal facts for project X" | `projectbrain_query` (use type="factual" or type="unified") |
| "Save a developer decision, guideline, or fact" | `projectbrain_store` (use type="both" or "contextual", specify tags) |
| "List recent context/memories stored" | `projectbrain_list` |
| "Analyze differences between branch A and branch B" | `projectbrain_diff_project_versions` |
| "Sync local codebase graph (symbols) to server" | `projectbrain_sync_codegraph` |
| "Inspect engine statistics or active sectors" | `projectbrain_stats` |

---

### Core Rules of Thumb

1. **Always Scoping by Project ID**:
   - Every tool call that accepts a project ID or user ID (like `projectbrain_query`, `projectbrain_store`, `projectbrain_diff_project_versions`) should target the active project name and version format, e.g., `<project-name>:<version-or-branch>` (for example, `ecommerce-app:main` or `Project-Brain:1.0.0`).
   - Do **NOT** use generic user IDs unless explicitly requested by the user. Project-centric partitioning keeps memory namespaces isolated.

2. **Save Decisions and Critical Context**:
   - Whenever you complete a major refactoring, resolve a critical bug, or make an architectural decision, use `projectbrain_store` to save this knowledge.
   - Example:
     ```python
     await projectbrain_store(
         content="We fixed the test pollution in test_multilingual_dedup.py by immediately cleaning up sys.modules after loading modules.",
         type="contextual",
         user_id="Project-Brain:1.0.0",
         tags=["bug-fix", "tests", "pollution-fix"]
     )
     ```

3. **Codebase Version Comparison**:
   - Before writing code for a feature branch, run `projectbrain_sync_codegraph(project_id="my-project:feature-branch")` to upload the new structure.
   - Use `projectbrain_diff_project_versions(base_project_id="my-project:main", target_project_id="my-project:feature-branch")` to quickly review structural symbol differences (added/deleted/modified functions/classes) and cognitive memory changes side-by-side.

4. **Document Ingestion**:
   - For unstructured data files (PDFs, Word DOCX, Excel, markdown), tell the user to use the dashboard upload panel (`http://localhost:8080/dashboard/`) or use the python CLI / server `/sources/upload` route to parse and ingest them.

---

### Integration Configuration

Ensure the agent has the following configuration in its MCP server list (either stdio or SSE transport):

**Stdio configuration:**
```json
"mcpServers": {
  "projectbrain": {
    "command": "python3",
    "args": ["-m", "projectbrain.main", "mcp"],
    "env": {
      "GEMINI_API_KEY": "YOUR_GEMINI_KEY",
      "OM_METADATA_BACKEND": "sqlite"
    }
  }
}
```

**SSE/HTTP configuration:**
```json
"mcpServers": {
  "projectbrain": {
    "command": "npx",
    "args": [
      "-y",
      "mcp-remote",
      "http://localhost:8080/mcp/sse"
    ]
  }
}
```
