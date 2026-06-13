# ProjectBrain - Project-Centric Cognitive Engine & Version Diff Control Panel

> **Real long-term cognitive storage and codebase version diffing for AI agents, LLM applications, and Product Managers. A self-hosted engine supporting SQLite & PostgreSQL, with built-in Model Context Protocol (MCP) and a premium glassmorphic dashboard.**

---

## 🚀 Key Features

- **📁 Project-Centric Memory Partitioning**: All system statistics, memory feeds, semantic searches, and PM document generation are globally isolated by the active project (replacing legacy user-id inputs).
- **📊 Codebase Structure Graph (Codegraph)**: Synchronize and store project structure symbols (classes, methods, functions, files, and dependencies) to visualize code relationships dynamically using Vis.js.
- **🔄 Unified Code Version Diffing**: Compare different branches or versions of a project (e.g., `my-project:main` vs `my-project:feature-branch`). Generates a comprehensive diff comparing:
  - **Structural Code Changes**: Added, deleted, and modified code symbols (with changes in signatures, docstrings, or line ranges).
  - **Cognitive Memory Changes**: Context memories, design guidelines, or requirements unique to each version.
- **⚡ Model Context Protocol (MCP)**:
  - Supports local **Stdio transport** (`python3 -m projectbrain.main mcp`).
  - Supports **HTTP/SSE (Server-Sent Events) transport** mounted directly to the server.
  - Native **Claude Desktop & GitHub Copilot Integration**.
- **🤖 Gemini PM Documentation Copilot**: Leverages Gemini (with thought-part filtering for clean outputs) to compile project memories, structural diffs, and developer guidelines into roadmaps, PRDs, or status reports.
- **📁 Local Document Upload & Parsing**: Upload PDF, Word (`.docx`), Excel (`.xlsx`), and Text/Markdown files directly via the dashboard. Files are parsed locally to clean Markdown and ingested into the active project's RAG index.

---

## 🛠️ Setup & Configuration

### 1. Installation
Clone the repository and install dependencies in editable mode:
```bash
git clone https://github.com/CaviraOSS/ProjectBrain.git
cd ProjectBrain
pip3 install -e .
```

### 2. Environment Variables
Copy `.env.example` to `.env` and configure:
```bash
cp .env.example .env
```
Key settings in `.env`:
- `GEMINI_API_KEY`: Your Gemini API key for semantic RAG search and document generation.
- `OM_METADATA_BACKEND`: `sqlite` (default) or `postgres`.
- Database details (`OM_PG_HOST`, `OM_PG_PORT`, etc.) if using PostgreSQL.

---

## 🏃 Running the Application

### 1. Start the Server & Web Dashboard
Start the FastAPI server (serves the REST API, Mounted SSE MCP server, and Web Dashboard):
```bash
python3 -m projectbrain.main serve
```
Once started, open your web browser and navigate to:
👉 **[http://localhost:8080/dashboard/](http://localhost:8080/dashboard/)**

---

## 🔄 Synchronizing & Diffing Codebase Versions

### 1. Indexing & Syncing Codebase Structure
ProjectBrain integrates with [Code Graph](https://github.com/colbymchenry/codegraph). To index your codebase structure and sync it to the ProjectBrain server under a specific project and version name (e.g., `my-project:main`):

```bash
# 1. Run codegraph in your repository root to generate local database (.codegraph/codegraph.db)
codegraph init

# 2. Synchronize to the ProjectBrain server under a project and version name
python3 -m projectbrain.main codegraph-sync <project_id:version> [server_url] [project_path]

# Example:
python3 -m projectbrain.main codegraph-sync ecommerce-app:main http://localhost:8080
```
When working on a feature branch, run `codegraph-sync` again with the branch suffix (e.g., `ecommerce-app:feature-checkout`):
```bash
python3 -m projectbrain.main codegraph-sync ecommerce-app:feature-checkout http://localhost:8080
```

### 2. Comparing Versions
You can compare base and target versions (`ecommerce-app:main` vs `ecommerce-app:feature-checkout`) using three methods:

#### Method A: Web Dashboard (UI)
1. Go to the **Code Graph** tab on the Web Dashboard.
2. In the **Compare Codebase Versions & Branches** section, select your **Base Version** and **Target Version**.
3. Click **Run Comparison** to view structural code changes (added, deleted, modified symbols) and memory modifications side-by-side.

#### Method B: Model Context Protocol (AI Agents)
AI agents connecting to ProjectBrain via MCP can call the tool `projectbrain_diff_project_versions` to analyze changes. The tool returns a beautifully formatted markdown report summarizing what has changed.

#### Method C: REST API
```bash
curl "http://localhost:8080/codegraph/diff?base_project_id=ecommerce-app:main&target_project_id=ecommerce-app:feature-checkout"
```

---

## ⚡ MCP Tool Reference

When integrated with AI clients (e.g. Cursor, Claude Desktop), the following tools are exposed:

### 1. `projectbrain_diff_project_versions`
Compare codebase structures and context memories between two versions of a project.
- **Arguments**: `base_project_id` (str), `target_project_id` (str)
- **Returns**: Markdown comparison report.

### 2. `projectbrain_query`
Query ProjectBrain for contextual memories (semantic search) and/or temporal facts.
- **Arguments**: `query` (str), `type` (`"contextual"`, `"factual"`, or `"unified"`), `user_id` (project ID), `k` (limit).

### 3. `projectbrain_store`
Persist a new memory or temporal fact.
- **Arguments**: `content` (str), `type` (`"contextual"`, `"factual"`, or `"both"`), `user_id` (project ID), `tags` (list of str).

### 4. `projectbrain_sync_codegraph`
Synchronize the local `.codegraph/codegraph.db` in the current working directory directly to the server.
- **Arguments**: `project_id` (str)

---

## 🧩 Claude Desktop Configuration

Configure Claude Desktop to connect to ProjectBrain using the **MCP Integration** tab in the dashboard (1-Click setup), or add the following manually to your `claude_desktop_config.json`:

### Stdio Transport (Local execution)
```json
"mcpServers": {
  "projectbrain": {
    "command": "python3",
    "args": ["-m", "projectbrain.main", "mcp"],
    "env": {
      "GEMINI_API_KEY": "your-gemini-key",
      "OM_METADATA_BACKEND": "sqlite"
    }
  }
}
```

### SSE Transport (HTTP Server proxy)
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

---

## 🤖 GitHub Copilot Configuration
Add the following configuration to your `~/.copilot/mcp-config.json` file to enable Copilot Chat and CLI tools:

```json
{
  "mcpServers": {
    "projectbrain": {
      "type": "http",
      "url": "http://localhost:8080/mcp/sse"
    }
  }
}
```

---

## 📜 License
Apache 2.0 License.
