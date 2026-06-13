from mcp.server.fastmcp import FastMCP
from pydantic import Field
from typing import Annotated

# Every extension entrypoint must define 'mcp' as a FastMCP instance
mcp = FastMCP("template-extension")

@mcp.tool(name="template_hello_world")
async def hello_world(
    name: Annotated[str, Field(description="Name to greet")] = "World"
) -> str:
    """A template greeting tool."""
    return f"Hello, {name}! This is a template MCP extension tool."
