# ProjectBrain: Local-to-Server Data Synchronization Guide

This guide explains how to synchronize your local project data—both codebase AST structure (classes, methods, relationships) and file content memories (for RAG query support)—to the remote ProjectBrain server (e.g., `http://localhost:8080`).

---

## ⚙️ 1. Prerequisites & Environment Setup

Before executing any synchronization commands, configure your local environment to point to the remote server and authenticate.

Set the following environment variables in your terminal session or local `.env` file:

```bash
# Set mode to remote so local CLI tools route API calls to the server
export PB_MODE=remote
export OM_MODE=remote

# Set the URL of your remote ProjectBrain server
export PB_URL="http://localhost:8080"
export OM_URL="http://localhost:8080"

# Optional: Set default authorization API key if authentication is enabled
export PB_API_KEY="your-secret-api-key"
export OM_API_KEY="your-secret-api-key"
```

---

## 🔀 2. Method 1: Syncing Codebase & AST Graphs via CLI (Recommended)

The `codegraph-sync` CLI command parses your local codebase, identifies nodes (functions, classes, interfaces) and edges (dependencies, callers, callees), and posts them to the server database. It also uploads source files as semantic memories.

### Command Syntax
```bash
python -m projectbrain.main codegraph-sync <project_id> [server_url] [project_path] [branch] [-m]
```

### Argument Details:
*   `<project_id>`: The unique identifier for your project on the server (e.g., `ecommerce-portal`).
*   `[server_url]`: (Optional) The URL of the remote server. Defaults to local host or environment variables if omitted.
*   `[project_path]`: (Optional) The absolute path to your local project directory. Defaults to your current working directory.
*   `[branch]`: (Optional) The branch name to scope the sync under. If omitted, the tool automatically reads your local active Git branch.
*   `[-m]` or `--sync-memories`: (Optional Flag) If passed, it performs a complete scan of all text-based source files and uploads them into RAG memory storage.

### Step-by-Step Example:
1.  Navigate to your local repository directory:
    ```bash
    cd /Users/macbbook/SourceCodes/my-local-app
    ```
2.  Install the global AST parser helper (if Node.js is installed):
    ```bash
    npm install -g @colbymchenry/codegraph
    ```
    *Note: If `codegraph` CLI is not found or npm is missing, ProjectBrain automatically falls back to a pure-Python codebase AST parser.*
3.  Run the synchronization command:
    ```bash
    python -m projectbrain.main codegraph-sync my-app-id http://localhost:8080 . main -m
    ```
    *This parses the current directory (`.`), associates the data with branch `main`, uploads the graph to `http://localhost:8080`, and ingests source files as RAG memories (`-m`).*

---

### 3.1 Method 2a: Ingesting Raw Text Files in Bulk
If you want to index local text-based files (logs, code files, configs) in bulk from a directory into the RAG memory pool without generating an AST code graph, use the `ingest-files` command.

#### Command Syntax
```bash
python -m projectbrain.main ingest-files <project_id> <dir_path>
```

#### Step-by-Step Example:
1.  Configure your remote environment parameters (see Section 1).
2.  Execute the ingestion pointing to your directory of documents:
    ```bash
    python -m projectbrain.main ingest-files my-app-id /Users/macbbook/Documents/specifications
    ```
    *Because `PB_MODE=remote` is set, all text documents in the folder will be scanned and pushed directly via API POST requests to the remote RAG server.*

### 3.2 Method 2b: Uploading a Single Binary or Text Document (PDF, XLSX, DOCX, etc.)
Because `ingest-files` only parses text files in bulk and skips binary structures, use the `upload-file` command to upload and parse any single complex document file (PDF, Excel, Word, PPTX, or text) directly to the server's parser engine.

#### Command Syntax
```bash
python -m projectbrain.main upload-file <project_id> <file_path> [server_url] [tags] [author]
```

#### Step-by-Step Example:
1.  Execute the upload pointing to your document file:
    ```bash
    python -m projectbrain.main upload-file my-app-id /Users/macbbook/Documents/architecture.pdf http://localhost:8080 "design,pdf" "john-doe"
    ```
    *This reads `architecture.pdf` locally, guesses its MIME type, and uploads it via the `/sources/upload` API endpoint on the server. The server automatically parses the PDF format (including tables/layout) and stores it in the RAG memory.*

---


## 🔌 4. Method 3: Remote Sync Trigger via HTTP JSON-RPC Client

If you are developing a custom client interface (like a VS Code plugin, Chrome extension, or administrative script) and want to trigger a sync on the server remotely, call the `projectbrain_sync_codegraph` tool via JSON-RPC.

### JSON-RPC Payload:
*   **Endpoint:** `POST http://localhost:8080/mcp-http/mcp`
*   **Headers:**
    *   `Content-Type: application/json`
    *   `Mcp-Session-Id: <id>` (Required if executing in a continuous session context)
*   **Payload:**
    ```json
    {
      "jsonrpc": "2.0",
      "method": "tools/call",
      "params": {
        "name": "projectbrain-projectbrain_sync_codegraph",
        "arguments": {
          "project_id": "my-app-id",
          "project_path": "/path/on/server/to/project",
          "branch": "main",
          "sync_memories": true
        }
      },
      "id": 1
    }
    ```
    *Important: When calling this remotely, the `project_path` must refer to a directory structure available **on the server** machine. If the files reside on your local machine, use Method 1 (CLI sync) instead.*

---

## 🔍 5. Verification: Checking that Sync Succeeded

To check if your local data has successfully uploaded to the server, you can query the database statistics using the CLI or a remote tool call.

### Check Stats via CLI
```bash
python -m projectbrain.main serve --stats
```

### Check Stats via Remote MCP Tool
Call `projectbrain-projectbrain_stats` via JSON-RPC to inspect current node counts:
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "projectbrain-projectbrain_stats",
    "arguments": {
      "user_id": "my-app-id:main"
    }
  },
  "id": 2
}
```
**Expected Response:**
```json
{
  "total_memories": 142,
  "total_temporal_facts": 0,
  "sectors": {
    "lexical": 50,
    "semantic": 92
  },
  "user_id": "my-app-id:main"
}
```
