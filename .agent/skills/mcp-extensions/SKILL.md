---
name: mcp-extensions
description: Develop, configure, and debug custom MCP extensions in ProjectBrain.
---

# MCP Extensions Development Skill

This skill guides the AI agent through developing, extending, testing, and debugging Model Context Protocol (MCP) extensions within the ProjectBrain codebase.

---

## 🛠️ Activation & Context

Activate this skill when:
- The user requests to create a new MCP tool or server.
- Modifying, updating, or debugging any existing tools inside `extensions_mcp/`.
- Integrating third-party MCP servers into ProjectBrain.

---

## 📖 Extension Architecture

ProjectBrain loads extensions dynamically from the `extensions_mcp/` directory. Each extension folder must act as a self-contained Python package:

```
extensions_mcp/
└── <extension_name>/
    ├── extension.json        # Metadata config manifest (name, entrypoint, dependencies)
    ├── __init__.py           # Package initializer (empty)
    ├── <entrypoint>.py       # FastMCP entrypoint server (e.g. server.py)
    └── client.py             # JSON-RPC HTTP client wrapper
```

---

## 🚀 Step-by-Step Development Workflow

When tasking this skill, always follow this development loop:

### 1. Scaffolding
Create a new directory under `extensions_mcp/` by cloning `extensions_mcp/template/`. Keep filenames lowercase and snake_case.

### 2. Configuration Manifest (`extension.json`)
Configure the `extension.json` manifest. Ensure all external pip packages are listed under `"dependencies"`, and all required secrets/API keys are defined under `"env_vars"`.

### 3. Implementing Tools
Write the tool function in the entrypoint file. Follow these guidelines:
- Apply type annotations and `Pydantic` validation fields to all parameters.
- Provide descriptive docstrings for both the tool and its parameters (LLMs rely on these docstrings to decide when to call the tool).
- Add error handling inside tool functions. Catch exceptions and return descriptive error strings instead of letting tools crash.

### 4. Dependency Injection
Check and add dependencies to `pyproject.toml` in the main project directory to ensure the packages are installed during setup.

### 5. Verification
Verify that the loader loads the tools without exceptions:
- Run unit tests: `pytest tests/test_mcp_integration.py`
- Run local stdio test: `python3 scratch/test_mcp_stdio.py` (or the corresponding scratch test path).
- Run server and check tools mounting: `python3 -m projectbrain.main serve` and inspect logs.
