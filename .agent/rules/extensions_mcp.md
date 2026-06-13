# Custom MCP Extensions Agent Rules

This workspace features a modular MCP Extensions architecture located in `extensions_mcp/`. Any folder placed in this directory is dynamically detected, loaded, and registered to the main ProjectBrain FastMCP server.

### Custom Extensions Directory & Tools

When writing code in this repository, keep in mind the following active extensions:

| Extension Folder | Extension Name | Tool Prefix / Namespace | Key Purpose |
|---|---|---|---|
| `image_to_markdown/` | `image-to-markdown` | `img2md_*` | OCR text & markdown extraction from images using Gemini Vision |
| `convert_excel/` | `convert-excel` | `excel_*` / `markdown_convert_*` | Excel / CSV to markdown and markdown to Excel tables |
| `convert_docx/` | `convert-docx` | `docx_*` | Extract text and tables from Word DOCX, and create DOCX files |
| `convert_pdf/` | `convert-pdf` | `pdf_*` | Extract text, tables, images, and page counts from PDF files |
| `convert_pptx/` | `convert-pptx` | `pptx_*` | PowerPoint PPTX text/image extractor, presentation creator |

---

### Core Rules for Developers

1. **Standard Directory Structure**:
   Every custom extension must be a subdirectory under `extensions_mcp/` containing:
   - `extension.json` (metadata config manifest)
   - `__init__.py` (empty file to make it a valid Python package)
   - `<entrypoint>.py` (typically `server.py` or `server_stdio.py` exposing the `mcp` FastMCP instance)
   - `client.py` (HTTP client helper using the template)

2. **Metadata Specifications (`extension.json`)**:
   Always include a valid JSON metadata manifest specifying:
   - `name`: Lowercase, hyphen-separated name.
   - `version`: SemVer version string.
   - `entrypoint`: Relative path to the python file declaring the FastMCP instance.
   - `env_vars`: List of required environment variables (e.g., `["LLM_API_KEY"]`).
   - `dependencies`: List of python pip packages required (e.g., `["pdfplumber"]`).

3. **FastMCP Instance Declaration**:
   - The entrypoint file must instantiate a `FastMCP` server named exactly `mcp` (e.g., `mcp = FastMCP("my-extension")`).
   - The modular loader `loader.py` searches specifically for the variable named `mcp` in the loaded module.

4. **Relative Imports Compliance**:
   - To support relative imports (e.g., `from .config import CONFIG`) within nested files of an extension, the loader imports modules natively via `importlib.import_module`.
   - Never use absolute imports pointing to the project root unless absolutely necessary. Keep extension files self-contained.

5. **Error Isolation**:
   - The extensions loader isolates failures. If an extension fails to load (e.g., due to a missing environment variable or dependency), the error is logged, but the main ProjectBrain server proceeds to start.
   - Always wrap execution logic inside extension tools in `try/except` blocks to return clean error messages to MCP clients rather than raising uncaught exceptions.

---

### HTTP Client Remoting

Every extension includes a `client.py` script. This client utilizes the Streamable HTTP JSON-RPC endpoint (`/mcp-http/mcp`) to run tools remotely from other systems:
- Command: `python3 extensions_mcp/<extension_folder>/client.py <tool_name> '<arguments_json>'`
- Example:
  ```bash
  python3 extensions_mcp/convert_pdf/client.py pdf_get_page_count '{"file_path": "manual.pdf"}'
  ```
