import os
import sys
import json
import logging
import traceback
import importlib.util
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("mcp")

def load_extensions(mcp_server) -> None:
    """
    Scans the extensions_mcp/ directory for valid MCP extensions and dynamically
    registers their tools on the main FastMCP server.
    """
    # Load environment variables
    load_dotenv()
    
    extensions_dir = Path(__file__).parent.resolve()
    
    logger.info(f"Scanning for MCP extensions in {extensions_dir}...")
    
    for item in os.listdir(extensions_dir):
        # Skip templates, utility files, or system directories
        if item in {"template", "__pycache__"} or item.startswith("."):
            continue
            
        item_path = extensions_dir / item
        if not item_path.is_dir():
            continue
            
        metadata_file = item_path / "extension.json"
        if not metadata_file.exists():
            # If extension.json doesn't exist, skip or check for default server.py
            logger.debug(f"Skipping directory {item}: extension.json not found")
            continue
            
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                
            ext_name = meta.get("name", item)
            entrypoint = meta.get("entrypoint", "server.py")
            entry_path = item_path / entrypoint
            
            if not entry_path.exists():
                logger.warning(f"Extension '{ext_name}' specifies missing entrypoint: {entrypoint}")
                continue
                
            # Check environment variables
            missing_env = [var for var in meta.get("env_vars", []) if not os.getenv(var)]
            if missing_env:
                logger.warning(f"Skipping extension '{ext_name}' due to missing environment variables: {missing_env}")
                continue
                
            # Dynamically load module using standard package import
            entry_module_name = entrypoint.replace(".py", "")
            module_path = f"extensions_mcp.{item}.{entry_module_name}"
            
            try:
                # Add project root to sys.path if not present to allow extensions_mcp package lookup
                project_root = str(Path(__file__).parent.parent.resolve())
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
                
                module = importlib.import_module(module_path)
            except Exception as import_err:
                logger.error(f"Failed to import module {module_path}: {import_err}")
                logger.error(traceback.format_exc())
                continue
                    
            if not hasattr(module, "mcp"):
                logger.warning(f"Extension '{ext_name}' entrypoint does not export 'mcp' FastMCP instance.")
                continue
                
            ext_mcp = getattr(module, "mcp")
            registered_count = 0
            
            for tool in ext_mcp._tool_manager.list_tools():
                # Prevent name collision with existing tools
                existing_names = [t.name for t in mcp_server._tool_manager.list_tools()]
                if tool.name in existing_names:
                    logger.warning(f"Tool '{tool.name}' from extension '{ext_name}' already registered. Skipping.")
                    continue
                    
                mcp_server.add_tool(
                    fn=tool.fn,
                    name=tool.name,
                    title=tool.title,
                    description=tool.description,
                    annotations=tool.annotations,
                    icons=tool.icons,
                    meta=tool.meta,
                )
                registered_count += 1
                
            logger.info(f"Successfully registered {registered_count} tools from extension '{ext_name}'")
            
        except Exception as e:
            logger.error(f"Failed to dynamically load extension '{item}': {e}")
            logger.error(traceback.format_exc())
