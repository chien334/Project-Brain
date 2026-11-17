# Community 13: Connections

> **Leiden Community Cluster 13**  
> Contains 47 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `evaluation.py` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L1` | Community 13 |
| `parse_evaluation_file()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L56` | Community 13 |
| `extract_xml_content()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L79` | Community 13 |
| `agent_loop()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L86` | Community 13 |
| `evaluate_single_task()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L154` | Community 13 |
| `run_evaluation()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L220` | Community 13 |
| `parse_headers()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L275` | Community 13 |
| `parse_env_vars()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L290` | Community 13 |
| `main()` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L305` | Community 13 |
| `MCP Server Evaluation Harness  This script evaluates MCP servers by running test` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L1` | Community 13 |
| `Parse XML evaluation file with qa_pair elements.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L57` | Community 13 |
| `Extract content from XML tags.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L80` | Community 13 |
| `Run the agent loop with MCP tools.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L93` | Community 13 |
| `Evaluate a single QA pair with the given tools.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L162` | Community 13 |
| `Run evaluation with MCP server tools.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L225` | Community 13 |
| `Parse header strings in format 'Key: Value' into a dictionary.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L276` | Community 13 |
| `Parse environment variable strings in format 'KEY=VALUE' into a dictionary.` | code | `.agents/mcp-builder/scripts/evaluation.py` | `L291` | Community 13 |
| `connections.py` | code | `.agents/mcp-builder/scripts/connections.py` | `L1` | Community 13 |
| `MCPConnection` | code | `.agents/mcp-builder/scripts/connections.py` | `L13` | Community 13 |
| `ABC` | code | `.agents/mcp-builder/scripts/connections.py` | `.agents/mcp-builder/scripts/connections.py:1` | Community 13 |
| `.__init__()` | code | `.agents/mcp-builder/scripts/connections.py` | `L16` | Community 13 |
| `_create_context()` | code | `.agents/mcp-builder/scripts/connections.py` | `L21` | Community 13 |
| `.__aenter__()` | code | `.agents/mcp-builder/scripts/connections.py` | `L24` | Community 13 |
| `.__aexit__()` | code | `.agents/mcp-builder/scripts/connections.py` | `L48` | Community 13 |
| `.list_tools()` | code | `.agents/mcp-builder/scripts/connections.py` | `L55` | Community 13 |
| `.call_tool()` | code | `.agents/mcp-builder/scripts/connections.py` | `L67` | Community 13 |
| `MCPConnectionStdio` | code | `.agents/mcp-builder/scripts/connections.py` | `L73` | Community 13 |
| `.__init__()` | code | `.agents/mcp-builder/scripts/connections.py` | `L76` | Community 13 |
| `._create_context()` | code | `.agents/mcp-builder/scripts/connections.py` | `L82` | Community 13 |
| `MCPConnectionSSE` | code | `.agents/mcp-builder/scripts/connections.py` | `L88` | Community 13 |
| `.__init__()` | code | `.agents/mcp-builder/scripts/connections.py` | `L91` | Community 13 |
| `._create_context()` | code | `.agents/mcp-builder/scripts/connections.py` | `L96` | Community 13 |
| `MCPConnectionHTTP` | code | `.agents/mcp-builder/scripts/connections.py` | `L100` | Community 13 |
| `.__init__()` | code | `.agents/mcp-builder/scripts/connections.py` | `L103` | Community 13 |
| `._create_context()` | code | `.agents/mcp-builder/scripts/connections.py` | `L108` | Community 13 |
| `create_connection()` | code | `.agents/mcp-builder/scripts/connections.py` | `L112` | Community 13 |
| `Lightweight connection handling for MCP servers.` | code | `.agents/mcp-builder/scripts/connections.py` | `L1` | Community 13 |
| `Base class for MCP server connections.` | code | `.agents/mcp-builder/scripts/connections.py` | `L14` | Community 13 |
| `Create the connection context based on connection type.` | code | `.agents/mcp-builder/scripts/connections.py` | `L22` | Community 13 |
| `Initialize MCP server connection.` | code | `.agents/mcp-builder/scripts/connections.py` | `L25` | Community 13 |
| `Clean up MCP server connection resources.` | code | `.agents/mcp-builder/scripts/connections.py` | `L49` | Community 13 |
| `Retrieve available tools from the MCP server.` | code | `.agents/mcp-builder/scripts/connections.py` | `L56` | Community 13 |
| `Call a tool on the MCP server with provided arguments.` | code | `.agents/mcp-builder/scripts/connections.py` | `L68` | Community 13 |
| `MCP connection using standard input/output.` | code | `.agents/mcp-builder/scripts/connections.py` | `L74` | Community 13 |
| `MCP connection using Server-Sent Events.` | code | `.agents/mcp-builder/scripts/connections.py` | `L89` | Community 13 |
| `MCP connection using Streamable HTTP.` | code | `.agents/mcp-builder/scripts/connections.py` | `L101` | Community 13 |
| `Factory function to create the appropriate MCP connection.      Args:         tr` | code | `.agents/mcp-builder/scripts/connections.py` | `L120` | Community 13 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `evaluation` | `imports_from` | `connections` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_parse_evaluation_file` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_extract_xml_content` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_agent_loop` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_evaluate_single_task` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_run_evaluation` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_parse_headers` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_parse_env_vars` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_main` | EXTRACTED | 1.0 |
| `evaluation` | `imports_from` | `connections` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_parse_evaluation_file` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_extract_xml_content` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_agent_loop` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_evaluate_single_task` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_run_evaluation` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_parse_headers` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_parse_env_vars` | EXTRACTED | 1.0 |
| `evaluation` | `contains` | `evaluation_main` | EXTRACTED | 1.0 |
| `evaluation_evaluate_single_task` | `calls` | `evaluation_agent_loop` | EXTRACTED | 1.0 |
| `evaluation_evaluate_single_task` | `calls` | `evaluation_extract_xml_content` | EXTRACTED | 1.0 |
| `evaluation_evaluate_single_task` | `calls` | `evaluation_agent_loop` | EXTRACTED | 1.0 |
| `evaluation_evaluate_single_task` | `calls` | `evaluation_extract_xml_content` | EXTRACTED | 1.0 |
| `evaluation_run_evaluation` | `calls` | `evaluation_parse_evaluation_file` | EXTRACTED | 1.0 |
| `evaluation_run_evaluation` | `calls` | `evaluation_evaluate_single_task` | EXTRACTED | 1.0 |
| `evaluation_run_evaluation` | `calls` | `evaluation_parse_evaluation_file` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `evaluation` | `argparse` | `imports` | EXTRACTED |
| `evaluation` | `asyncio` | `imports` | EXTRACTED |
| `evaluation` | `json` | `imports` | EXTRACTED |
| `evaluation` | `re` | `imports` | EXTRACTED |
| `evaluation` | `sys` | `imports` | EXTRACTED |
| `evaluation` | `time` | `imports` | EXTRACTED |
| `evaluation` | `traceback` | `imports` | EXTRACTED |
| `evaluation` | `xml_etree_elementtree` | `imports` | EXTRACTED |
| `evaluation` | `pathlib` | `imports_from` | EXTRACTED |
| `evaluation` | `typing` | `imports_from` | EXTRACTED |
| `evaluation` | `anthropic` | `imports_from` | EXTRACTED |
| `evaluation` | `argparse` | `imports` | EXTRACTED |
| `evaluation` | `asyncio` | `imports` | EXTRACTED |
| `evaluation` | `json` | `imports` | EXTRACTED |
| `evaluation` | `re` | `imports` | EXTRACTED |
| `evaluation` | `sys` | `imports` | EXTRACTED |
| `evaluation` | `time` | `imports` | EXTRACTED |
| `evaluation` | `traceback` | `imports` | EXTRACTED |
| `evaluation` | `xml_etree_elementtree` | `imports` | EXTRACTED |
| `evaluation` | `pathlib` | `imports_from` | EXTRACTED |
| `evaluation` | `typing` | `imports_from` | EXTRACTED |
| `evaluation` | `anthropic` | `imports_from` | EXTRACTED |
| `connections` | `contextlib` | `imports_from` | EXTRACTED |
| `connections` | `typing` | `imports_from` | EXTRACTED |
| `connections` | `mcp` | `imports_from` | EXTRACTED |
