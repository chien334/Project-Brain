# Community 0: Loader

> **Leiden Community Cluster 0**  
> Contains 4 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `__init__.py` | code | `extensions_mcp/__init__.py` | `L1` | Community 0 |
| `loader.py` | code | `extensions_mcp/loader.py` | `L1` | Community 0 |
| `load_extensions()` | code | `extensions_mcp/loader.py` | `L12` | Community 0 |
| `Scans the extensions_mcp/ directory for valid MCP extensions and dynamically` | code | `extensions_mcp/loader.py` | `L13` | Community 0 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `loader` | `contains` | `loader_load_extensions` | EXTRACTED | 1.0 |
| `loader_rationale_13` | `rationale_for` | `loader_load_extensions` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `init` | `base` | `imports_from` | EXTRACTED |
| `init` | `python` | `imports_from` | EXTRACTED |
| `init` | `cobol` | `imports_from` | EXTRACTED |
| `init` | `vb6` | `imports_from` | EXTRACTED |
| `init` | `pascal` | `imports_from` | EXTRACTED |
| `init` | `fortran` | `imports_from` | EXTRACTED |
| `init` | `st_xml` | `imports_from` | EXTRACTED |
| `init` | `generic` | `imports_from` | EXTRACTED |
| `init` | `main` | `imports_from` | EXTRACTED |
| `init` | `trace` | `imports_from` | EXTRACTED |
| `init` | `base` | `imports_from` | EXTRACTED |
| `init` | `langchain` | `imports_from` | EXTRACTED |
| `init` | `agents` | `imports_from` | EXTRACTED |
| `init` | `google_drive` | `imports_from` | EXTRACTED |
| `init` | `google_sheets` | `imports_from` | EXTRACTED |
| `init` | `google_slides` | `imports_from` | EXTRACTED |
| `init` | `notion` | `imports_from` | EXTRACTED |
| `init` | `onedrive` | `imports_from` | EXTRACTED |
| `init` | `github` | `imports_from` | EXTRACTED |
| `init` | `web_crawler` | `imports_from` | EXTRACTED |
| `init` | `postgres` | `imports_from` | EXTRACTED |
| `init` | `valkey` | `imports_from` | EXTRACTED |
| `init` | `store` | `imports_from` | EXTRACTED |
| `init` | `query` | `imports_from` | EXTRACTED |
| `init` | `timeline` | `imports_from` | EXTRACTED |
