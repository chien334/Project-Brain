# Excel ↔ Markdown MCP Server Template

This template provides a complete MCP server implementation for bidirectional conversion between Excel/CSV files and Markdown tables.

## Features

- ✅ Excel/CSV → Markdown conversion
- ✅ Markdown → Excel conversion
- ✅ Multiple sheet support
- ✅ Pagination for large files
- ✅ Both HTTP and STDIO transports
- ✅ Comprehensive error handling
- ✅ MCP best practices compliant

## Quick Start

### 1. Copy Template Files

```bash
# Copy entire template to your project
cp -r .claude/templates/excel-md-mcp-server/* /path/to/your/project/
```

### 2. Install Dependencies

```bash
cd /path/to/your/project
python -m venv .venv
.venv/Scripts/activate  # Windows
# or
source .venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
```

### 3. Run Server

**STDIO mode (recommended for Claude Code):**
```bash
python server_stdio.py
```

**HTTP mode (recommended for VS Code Copilot):**
```bash
python server.py
```

## Template Structure

```
excel-md-mcp-server/
├── server.py              # HTTP transport server
├── server_stdio.py        # STDIO transport server
├── requirements.txt       # Python dependencies
├── README.md             # Documentation
├── .mcp.json             # Claude Code config (HTTP)
├── .vscode/
│   └── mcp.json          # VS Code Copilot config
└── .codevistarules/
    └── rules.md          # Project-specific rules
```

## Customization Guide

### Adding New Tools

1. **Define helper functions** in the Helpers section
2. **Create tool function** with `@mcp.tool` decorator
3. **Add annotations** (readOnlyHint, destructiveHint, etc.)
4. **Update README.md** with tool documentation

Example:
```python
@mcp.tool(
    name="your_tool_name",
    annotations={
        "title": "Your Tool Title",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def your_tool_name(param1: str, param2: int = 0) -> str:
    """Tool description.
    
    Returns: Description of return value
    On error returns: "Error: <message>"
    """
    try:
        # Implementation
        return result
    except Exception as e:
        return f"Error: {e}"
```

### Modifying File Support

To add support for new file formats:

1. **Add reader function** in Helpers section:
```python
def _read_newformat(path: Path) -> dict[str, list[list]]:
    """Read new format and return sheets data."""
    # Implementation
    return sheets_data
```

2. **Update `_load_file` dispatcher**:
```python
def _load_file(file_path: str) -> dict[str, list[list]]:
    ext = path.suffix.lower()
    if ext == ".newformat":
        return _read_newformat(path)
    # ... existing code
```

3. **Update documentation** in README.md

### Changing Transport

**To use only STDIO:**
- Remove `server.py`
- Update configs to use `server_stdio.py`

**To use only HTTP:**
- Remove `server_stdio.py`
- Update configs to use `server.py`

**To change HTTP port:**
```python
# In server.py
PORT = int(os.getenv("PORT", "YOUR_PORT"))
```

Or set environment variable:
```bash
export PORT=7000  # Linux/Mac
$env:PORT=7000    # Windows PowerShell
```

## Configuration Files

### .mcp.json (Claude Code - HTTP)

```json
{
  "mcpServers": {
    "excel-to-md": {
      "type": "http",
      "url": "http://127.0.0.1:6000/mcp"
    }
  }
}
```

### .claude/settings.local.json (Claude Code - STDIO)

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

### .vscode/mcp.json (VS Code Copilot)

```json
{
  "servers": {
    "excel-to-md": {
      "type": "http",
      "url": "http://localhost:6000/mcp"
    }
  }
}
```

Or for STDIO:
```json
{
  "servers": {
    "excel-to-md": {
      "type": "stdio",
      "command": "python",
      "args": ["server_stdio.py"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

## Best Practices

### 1. Error Handling

Always return errors in format: `"Error: <message>"`

```python
try:
    # Operation
    return result
except FileNotFoundError as e:
    return f"Error: File not found - {e}"
except ValueError as e:
    return f"Error: Invalid value - {e}"
except Exception as e:
    return f"Error: Unexpected error - {type(e).__name__}: {e}"
```

### 2. Tool Naming

Use consistent prefixes:
- `excel_*` for Excel operations
- `markdown_*` for Markdown operations
- `csv_*` for CSV-specific operations

### 3. Annotations

Set appropriate hints:
- `readOnlyHint: true` - Tool only reads data
- `destructiveHint: true` - Tool creates/modifies files
- `idempotentHint: true` - Same input = same output
- `openWorldHint: false` - Tool works with known data

### 4. Documentation

Keep README.md updated with:
- Tool descriptions
- Parameter tables
- Usage examples
- Troubleshooting guide

### 5. Testing

Test with MCP Inspector before deployment:
```bash
npx @modelcontextprotocol/inspector python server_stdio.py
```

## Dependencies

### Core Dependencies

```
mcp>=1.0.0           # MCP SDK
openpyxl>=3.1.0      # Excel file handling
pydantic>=2.0.0      # Data validation
```

### HTTP Transport Additional

```
uvicorn>=0.27.0      # ASGI server
```

## Deployment

### Local Development

1. Use STDIO transport for simplicity
2. Test with MCP Inspector
3. Verify all tools work independently

### Production

1. Use HTTP transport for scalability
2. Set up proper logging
3. Configure environment variables
4. Use process manager (systemd, supervisor, etc.)

Example systemd service:
```ini
[Unit]
Description=Excel-MD MCP Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/server
Environment="PORT=6000"
ExecStart=/path/to/.venv/bin/python server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### Server won't start

**Check Python version:**
```bash
python --version  # Should be 3.11+
```

**Verify dependencies:**
```bash
pip list | grep mcp
pip list | grep openpyxl
```

**Check port availability:**
```bash
netstat -ano | findstr :6000  # Windows
lsof -i :6000                 # Linux/Mac
```

### Tools not appearing

**Reload MCP servers:**
- Claude Code: Run `/mcp` command
- VS Code: Reload window (Ctrl+Shift+P → "Developer: Reload Window")

**Verify configuration:**
- Check file paths in config
- Ensure server is running (HTTP mode)
- Check logs for errors

### Conversion errors

**File not found:**
- Use absolute paths
- Verify file exists
- Check file permissions

**Invalid format:**
- Verify file extension
- Check file is not corrupted
- Ensure proper Markdown format

## License

MIT License - Feel free to use and modify for your projects.

## Support

For issues and questions:
1. Check troubleshooting section
2. Review MCP best practices
3. Test with MCP Inspector
4. Check server logs

## Related Resources

- [MCP Documentation](https://modelcontextprotocol.io)
- [FastMCP Documentation](https://github.com/modelcontextprotocol/python-sdk)
- [openpyxl Documentation](https://openpyxl.readthedocs.io)
