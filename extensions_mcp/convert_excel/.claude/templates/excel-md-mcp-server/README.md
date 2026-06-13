# Excel ↔ Markdown MCP Server Template

A production-ready template for creating MCP servers that convert between Excel/CSV and Markdown formats.

## What's Included

This template provides everything you need to deploy a bidirectional Excel ↔ Markdown conversion MCP server:

### Core Features
- ✅ **Bidirectional Conversion**: Excel/CSV → Markdown and Markdown → Excel
- ✅ **Multiple Transports**: Both HTTP and STDIO implementations
- ✅ **Pagination Support**: Handle large files efficiently
- ✅ **Multi-sheet Support**: Work with complex workbooks
- ✅ **Error Handling**: Comprehensive error messages
- ✅ **MCP Compliant**: Follows all MCP best practices

### Files Included

```
template/
├── server.py              # HTTP transport (port 6000)
├── server_stdio.py        # STDIO transport
├── requirements.txt       # Python dependencies
├── README.md             # User documentation
├── template.md           # This customization guide
├── .mcp.json             # Claude Code config
├── .vscode/
│   └── mcp.json          # VS Code Copilot config
└── .codevistarules/
    └── rules.md          # Development rules
```

## Quick Deploy

### 1. Copy Template

```bash
# Create your project directory
mkdir my-excel-converter
cd my-excel-converter

# Copy all template files
cp -r /path/to/.claude/templates/excel-md-mcp-server/* .
```

### 2. Setup Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/Mac)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Test Server

```bash
# Test STDIO mode
python server_stdio.py

# Or test HTTP mode
python server.py
```

### 4. Configure Client

**For Claude Code (STDIO):**

Edit `.claude/settings.local.json`:
```json
{
  "mcpServers": {
    "excel-to-md": {
      "type": "stdio",
      "command": "/absolute/path/to/.venv/Scripts/python.exe",
      "args": ["/absolute/path/to/server_stdio.py"]
    }
  }
}
```

**For VS Code Copilot (HTTP):**

The `.vscode/mcp.json` is already configured. Just start the server:
```bash
python server.py
```

## Available Tools

| Tool | Description |
|------|-------------|
| `excel_list_sheets` | List all sheets in Excel/CSV file |
| `excel_convert_to_markdown` | Convert entire workbook or specific sheets |
| `excel_convert_sheet_to_markdown` | Convert single sheet with pagination |
| `markdown_convert_to_excel` | Convert Markdown tables to Excel |

## Customization

See [template.md](./template.md) for detailed customization guide including:
- Adding new tools
- Supporting new file formats
- Changing transport settings
- Deployment configurations

## Use Cases

### Documentation Generation
```
Excel data → Markdown tables → GitHub/GitLab wiki
```

### Data Analysis Workflow
```
Markdown data → Excel → Analysis → Markdown report
```

### Version Control
```
Excel → Markdown (commit to Git) → Excel (local work)
```

### Content Management
```
Spreadsheet CMS → Markdown → Static site generator
```

## Requirements

- Python 3.11 or higher
- Dependencies in `requirements.txt`
- For HTTP mode: Available port (default 6000)

## Testing

Test with MCP Inspector:
```bash
npx @modelcontextprotocol/inspector python server_stdio.py
```

## Support

- **Template Guide**: See [template.md](./template.md)
- **MCP Documentation**: https://modelcontextprotocol.io
- **Skill Guide**: `.claude/skills/excel-md-converter/SKILL.md`

## License

MIT License - Free to use and modify for your projects.
