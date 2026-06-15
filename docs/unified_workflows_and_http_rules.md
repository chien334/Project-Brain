# Comprehensive MCP Workflows & HTTP Client Integration Rules

This guide outlines every Model Context Protocol (MCP) tool available in ProjectBrain, provides step-by-step interconnected workflows combining multiple extensions, and lists strict integration rules for developers building custom HTTP/JSON-RPC clients.

---

## 🧰 1. Comprehensive Directory of Available MCP Tools

All custom tools are grouped by their specific domain/extension.

### A. Core ProjectBrain RAG & Graph Memory (`projectbrain-mcp`)
These tools interface directly with the hybrid semantic-graph (HSG) and point-in-time temporal memory engines.
*   **`projectbrain_query`**: Queries contextual/semantic memories or temporal facts.
    *   *Arguments:* `query` (string), `type` (optional string: `contextual`|`factual`|`unified`), `fact_pattern` (optional object), `at` (optional ISO timestamp), `k` (optional int).
*   **`projectbrain_store`**: Persists text into semantic memory or inserts temporal facts into the database.
    *   *Arguments:* `content` (string), `type` (optional string: `contextual`|`factual`|`both`), `facts` (optional array of subject/predicate/object facts), `user_id` (optional string).
*   **`projectbrain_get`**: Fetches a single memory detail by ID.
    *   *Arguments:* `id` (string).
*   **`projectbrain_delete`**: Deletes a memory by ID for the authorized user.
    *   *Arguments:* `id` (string), `user_id` (optional string).
*   **`projectbrain_list`**: Retrieves the history of recent memories.
    *   *Arguments:* `limit` (optional int), `user_id` (optional string).
*   **`projectbrain_reinforce`**: Triggers salience reinforcement on a specific memory node.
    *   *Arguments:* `id` (string).
*   **`projectbrain_delete_all`**: Wipes all memories associated with a given user ID.
    *   *Arguments:* `user_id` (optional string).
*   **`projectbrain_stats`**: Returns summary statistics of the RAG engine (e.g., total memories, temporal facts, active sectors).
    *   *Arguments:* `user_id` (optional string).
*   **`projectbrain_ingest`**: Connects and pulls data from external connectors (GitHub, Notion, Web Crawlers, Google Drive).
    *   *Arguments:* `source` (string), `creds` (optional object), `filters` (optional object), `user_id` (optional string).
*   **`projectbrain_sync_codegraph`**: Parses a codebase and synchronizes structural AST definitions (nodes and edges) along with files into RAG.
    *   *Arguments:* `project_id` (string), `project_path` (optional string), `branch` (optional string), `sync_memories` (optional bool).
*   **`projectbrain_diff_project_versions`**: Generates a version-to-version semantic and structural diff report between two branches/states.
    *   *Arguments:* `base_project_id` (string), `target_project_id` (string).
*   **`projectbrain_register_mcp`** & **`projectbrain_register_external_mcp`**: Register MCP servers in Claude Desktop's local config.

### B. Document Conversion & Parsing Tools
These offline tools parse non-text formats into raw text or cleanly formatted Markdown.
*   **Microsoft Word (`docx_mcp`)**:
    *   `docx_extract_text`: Extracts raw text content.
    *   `docx_extract_tables`: Extracts tables as structured JSON lists.
    *   `docx_create_document`: Saves content into a new `.docx` file.
*   **Microsoft Excel (`excel_mcp`)**:
    *   `excel_list_sheets`: Lists all sheet names inside an `.xlsx`/`.xlsm`/`.csv` file.
    *   `excel_convert_to_markdown`: Converts selected sheets to Markdown tables.
    *   `excel_convert_sheet_to_markdown`: Converts a specific sheet with support for pagination (`row_offset`, `row_limit`).
    *   `markdown_convert_to_excel`: Renders Markdown tables back to a native `.xlsx` spreadsheet.
*   **Portable Document Format (`pdf_mcp`)**:
    *   `pdf_get_page_count`: Returns total pages in a PDF.
    *   `pdf_extract_page_text`: Extracts raw text from a single page.
    *   `pdf_extract_text`: Extracts text from all pages into a JSON mapping.
    *   `pdf_extract_tables`: Parses tables from pages into JSON.
    *   `pdf_extract_images`: Extracts all embedded image assets into a directory.
    *   `pdf_convert_to_markdown`: Converts all text, tables, and images to a single Markdown file.
*   **Microsoft PowerPoint (`pptx_mcp`)**:
    *   `pptx_extract_text`: Extracts text from all textframes across slides.
    *   `pptx_extract_images`: Extracts all media images from the presentation.
    *   `pptx_to_markdown`: Formats slides and outlines into Markdown structure.
    *   `pptx_create_presentation`: Builds a basic PPTX presentation with a title and content.

### C. Offline OCR & Cloud Vision OCR Tools
Processes visual documents, scans, charts, and diagrams.
*   **Local Offline OCR (`ocr_local_mcp`)**:
    *   `ocr_engine_status`: Reports status of local dependencies (PaddleOCR, PyMuPDF, etc.).
    *   `ocr_image_to_markdown`: Processes a local image file using local CPU-friendly PaddleOCR.
    *   `ocr_pdf_to_markdown`: Renders PDF pages to images and runs local PaddleOCR on them (ideal for scanned/non-digital PDFs).
*   **Gemini Vision OCR (`image_to_markdown_mcp`)**:
    *   `img2md_list_images`: Previews natural sorted order of images in a folder.
    *   `img2md_extract_image`: Translates a single image (UI layout, schema diagram) to clean Markdown using Gemini Vision API.
    *   `img2md_extract_folder`: Recursively runs Gemini Vision in parallel (using throttled concurrency) on a folder of images to generate combined or per-image Markdown.

### D. Code Migration & Refactoring Assistant (`migration_mcp`)
Aides in code modernization, legacy stack parsing, and documentation translation.
*   **`migration_translate_code_comments`**: Translates source code comments between Japanese (`ja`) and English (`en`) without breaking syntax.
*   **`migration_recommend_refactor`**: Analyzes a file and provides modern refactoring structure suggestions (e.g. converting Struts to FastAPI).
*   **`migration_batch_scan_logic`**: Performs an initial scan of a project folder to extract raw business logic rules draft.
*   **`migration_plan_execution_phases`**: Partitions business logic scan reports into logical step-by-step refactoring tasks.

---

## 🔀 2. Interconnected Multi-Tool Workflows

These workflows combine tools sequentially to achieve complex, automated results.

### Workflow A: Visual Specification RAG Ingestion
*Scenario: An engineer has a PDF specification containing complex flowcharts, UML diagrams, and Japanese text layers that need to be parsed and stored in the RAG knowledge base for conversational querying.*

```
                 ┌──────────────────────┐
                 │  Scanned spec.pdf    │
                 └──────────┬───────────┘
                            │
                            ▼
               (pdf_convert_to_markdown)
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
     [Markdown Text]               (pdf_extract_images)
            │                               │
            │                               ▼
            │                     [Images in output_dir/]
            │                               │
            │                      (img2md_extract_folder)
            │                               │
            │                               ▼
            │                     [Visual Transcriptions]
            │                               │
            └───────────────┬───────────────┘
                            │ (Merge Markdown)
                            ▼
                 (projectbrain_store)
                            │
                            ▼
              ┌───────────────────────────┐
              │ ProjectBrain RAG Storage  │
              └───────────────────────────┘
```

1.  **Extract Base Text & Layout:** Call `pdf_convert_to_markdown` with `include_images=False` to get a structured markdown representation of the text-heavy sections.
2.  **Extract Diagrams:** Call `pdf_extract_images` on the file and specify an output directory (`/tmp/extracted-images`).
3.  **Transcribe Diagrams:** Call `img2md_extract_folder` pointing to `/tmp/extracted-images` to parse flowcharts/diagrams into text descriptions using Gemini Vision.
4.  **Collate & Store:** Merge the markdown text and visual descriptions. Call `projectbrain_store` with `type="contextual"` to save it as RAG memory.

---

### Workflow B: Modernization & Regression Check of Legacy Code
*Scenario: Porting a legacy Japanese C# MVC application to FastAPI, ensuring no business rules or API contracts are missed during modernization.*

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      Legacy Codebase                        │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
              (migration_translate_code_comments)
                                │
                                ▼
                  (migration_batch_scan_logic)
                                │
                                ▼
                (migration_plan_execution_phases)
                                │
                        [Generate Code]
                                │
                                ▼
                  (projectbrain_sync_codegraph)
                                │
                                ▼
             (projectbrain_diff_project_versions)
                                │
                                ▼
                     ┌─────────────────────┐
                     │ Regression Report   │
                     └─────────────────────┘
```

1.  **Translate Source Comments:** Call `migration_translate_code_comments` (using `direction="ja2en"`) on legacy controller files to understand original constraints.
2.  **Scan Business Logic:** Run `migration_batch_scan_logic` to draft a complete outline of the business rules.
3.  **Partition Migration Phases:** Call `migration_plan_execution_phases` to break the tasks into structured development phases.
4.  **Modernize & Write FastAPI Code:** Implement the modernized services.
5.  **Parse & Sync New Branch:** Execute `projectbrain_sync_codegraph` on the new codebase.
6.  **Verify & Compare AST/Memories:** Run `projectbrain_diff_project_versions` comparing the legacy code graph with the FastAPI code graph to verify that all endpoints, parameters, and logical models map 1:1.

---

## 🔌 3. Remote Execution Rules for HTTP Client (JSON-RPC)

When executing MCP tools remotely via the ProjectBrain SSE backend using a standard HTTP Client, follow these rules:

### Rule 1: Target Endpoint and JSON-RPC Wrapper
Remote tool executions are handled via single POST requests to the MCP HTTP endpoint.
*   **Method:** `POST`
*   **URL:** `http://<server_ip>:8080/mcp-http/mcp`
*   **Headers:**
    *   `Content-Type: application/json`
    *   `Mcp-Session-Id: <id>` (Required for maintaining session state across sequential calls)
*   **Request Envelope:**
    ```json
    {
      "jsonrpc": "2.0",
      "method": "tools/call",
      "params": {
        "name": "extension-tool_name",
        "arguments": {
          "param1": "value1"
        }
      },
      "id": 1
    }
    ```
    *Note: Server tool naming maps to standard folder prefixes, e.g. `image_to_markdown-img2md_extract_image` or `projectbrain-projectbrain_query`.*

### Rule 2: Keep-Alive & SSE Stream Headers (Behind Nginx/Reverse Proxy)
When connecting to the SSE stream endpoint (`GET /mcp/sse`), intermediate reverse proxies (like Nginx) will buffer responses, killing the SSE real-time updates.
*   **Ensure the Server Returns Headers:** The backend must return:
    *   `X-Accel-Buffering: no`
    *   `Cache-Control: no-cache`
*   **DNS Protection:** If hosting inside a private virtual network, host binding requires `enable_dns_rebinding_protection=False` on the FastMCP instance, otherwise connections from external proxies will be rejected with HTTP code 421.

### Rule 3: Session ID Propagation
The server generates and returns an `Mcp-Session-Id` header in the HTTP response of tool calls.
*   **Rule:** The client **must** extract the `Mcp-Session-Id` value from the response headers and include it in the header of all subsequent requests in that session. If the session header is omitted, the server instantiates a fresh context, losing path caching, workspace configurations, and temporal states.

### Rule 4: Configured Client Timeout Policy
Many conversion and analysis tools run heavy processes (such as PDF page rendering, local PaddleOCR CPU processing, or Gemini Vision API queries).
*   **Rule:** Do **not** use default HTTP client timeouts (which default to 5s or 10s). Set a client timeout of **at least 120 seconds** (`timeout=120.0`) to avoid premature connection dropouts.

### Rule 5: Handling the `fallback_to_client` Protocol
If the remote server hits rate limits, credential errors, or API quota failures, it returns a soft fallback payload instead of throwing an HTTP 500 error:
```json
{
  "fallback_to_client": true,
  "system_prompt": "...",
  "prompt": "...",
  "message": "Remote vision service unavailable. Please fall back to local model."
}
```
*   **Rule:** The HTTP client must check for `"fallback_to_client": true` in the JSON response. If true, the client should capture the `system_prompt` and `prompt` and process them locally using its own integrated model (e.g. Cursor's internal LLM, Ollama, or local desktop model), returning the final response back to the user.
