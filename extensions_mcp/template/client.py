#!/usr/bin/env python3
"""
HTTP Client Helper for invoking MCP tools remotely when hosted on a server.
"""

import sys
import json
import argparse
import urllib.request
import urllib.error

DEFAULT_SERVER_URL = "http://localhost:8080"

def call_remote_tool(server_url: str, tool_name: str, arguments: dict) -> dict:
    """
    Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.
    """
    url = f"{server_url.rstrip('/')}/mcp-http/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if "error" in res_data:
                raise RuntimeError(f"JSON-RPC Error: {res_data['error']}")
            return res_data.get("result", {})
    except urllib.error.HTTPError as e:
        error_content = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP Error {e.code}: {e.reason}\nDetails: {error_content}")
    except Exception as e:
        raise RuntimeError(f"Connection failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="Invoke MCP tools remotely via HTTP.")
    parser.add_argument("--url", default=DEFAULT_SERVER_URL, help=f"Server base URL (default: {DEFAULT_SERVER_URL})")
    parser.add_argument("tool", help="Name of the tool to invoke")
    parser.add_argument("args", nargs="?", default="{}", help="JSON string of tool arguments")
    
    args = parser.parse_args()
    
    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError:
        print("Error: Arguments must be a valid JSON string. Example: '{\"name\": \"Alice\"}'")
        sys.exit(1)
        
    print(f"Calling tool '{args.tool}' on {args.url} with args: {tool_args}...")
    
    try:
        result = call_remote_tool(args.url, args.tool, tool_args)
        print("\n--- Response ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
