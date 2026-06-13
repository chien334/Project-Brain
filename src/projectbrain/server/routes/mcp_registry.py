import sys
import os
import json
from pathlib import Path
from typing import Optional, List, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class RegisterMCPRequest(BaseModel):
    transport_type: str  # "stdio" or "sse"
    user_id: Optional[str] = "default"
    tags: Optional[str] = "source:mcp"
    sse_url: Optional[str] = "http://localhost:8080/mcp/sse"

def get_claude_config_path() -> Path:
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json"))
    elif sys.platform == "win32":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data) / "Claude" / "claude_desktop_config.json"
        return Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:
        return Path(os.path.expanduser("~/.config/Claude/claude_desktop_config.json"))

@router.get("/status")
def get_mcp_status():
    try:
        config_path = get_claude_config_path()
        exists = config_path.exists()
        registered = False
        server_config = None
        
        if exists:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    mcp_servers = config.get("mcpServers", {})
                    if "projectbrain" in mcp_servers or "openmemory" in mcp_servers:
                        registered = True
                        server_config = mcp_servers.get("projectbrain") or mcp_servers.get("openmemory")
            except Exception:
                # config is corrupted or empty
                pass
                
        return {
            "config_file_exists": exists,
            "config_file_path": str(config_path),
            "registered": registered,
            "server_config": server_config
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/register")
def register_mcp(req: RegisterMCPRequest):
    try:
        config_path = get_claude_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        config = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = {}
                
        if "mcpServers" not in config:
            config["mcpServers"] = {}
            
        # Clean up legacy openmemory entry if it exists to prevent conflict
        if "openmemory" in config["mcpServers"]:
            del config["mcpServers"]["openmemory"]
            
        if req.transport_type == "stdio":
            # Collect environment variables starting with PB_, OM_, or GEMINI_
            env_vars = {}
            for k, v in os.environ.items():
                if k.startswith("PB_") or k.startswith("OM_") or k == "GEMINI_API_KEY":
                    if (k == "OM_DB_PATH" or k == "PB_DB_PATH") and v:
                        env_vars[k] = str(Path(v).resolve())
                    else:
                        env_vars[k] = v
            # override default user and tags if provided
            if req.user_id:
                env_vars["PB_DEFAULT_USER_ID"] = req.user_id
            if req.tags:
                env_vars["PB_DEFAULT_TAGS"] = req.tags
                
            config["mcpServers"]["projectbrain"] = {
                "command": sys.executable,
                "args": ["-m", "projectbrain.main", "mcp"],
                "env": env_vars
            }
        elif req.transport_type == "sse":
            config["mcpServers"]["projectbrain"] = {
                "command": "npx",
                "args": ["-y", "mcp-remote", req.sse_url]
            }
        else:
            raise HTTPException(status_code=400, detail="Invalid transport type. Must be 'stdio' or 'sse'")
            
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            
        return {
            "success": True,
            "message": f"Successfully registered projectbrain via {req.transport_type} transport in Claude Desktop config.",
            "server_config": config["mcpServers"]["projectbrain"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/unregister")
def unregister_mcp():
    try:
        config_path = get_claude_config_path()
        if not config_path.exists():
            return {"success": True, "message": "Claude Desktop config file does not exist, nothing to unregister."}
            
        config = {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            return {"success": True, "message": "Could not read Claude Desktop config, nothing to unregister."}
            
        mcp_servers = config.get("mcpServers", {})
        changed = False
        if "projectbrain" in mcp_servers:
            del mcp_servers["projectbrain"]
            changed = True
        if "openmemory" in mcp_servers:
            del mcp_servers["openmemory"]
            changed = True
            
        if changed:
            config["mcpServers"] = mcp_servers
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            return {"success": True, "message": "Successfully removed projectbrain/openmemory server from Claude Desktop config."}
        else:
            return {"success": True, "message": "projectbrain/openmemory server is not registered in Claude Desktop config."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ExternalMCPRequest(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = []
    env: Optional[Dict[str, str]] = {}

@router.get("/external")
def get_external_mcp_servers():
    try:
        config_path = get_claude_config_path()
        if not config_path.exists():
            return {"servers": {}}
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                mcp_servers = config.get("mcpServers", {})
                external_servers = {k: v for k, v in mcp_servers.items() if k not in ["projectbrain", "openmemory"]}
                return {"servers": external_servers}
        except Exception:
            return {"servers": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/external")
def save_external_mcp_server(req: ExternalMCPRequest):
    try:
        name = req.name.strip()
        if not name or name == "projectbrain":
            raise HTTPException(status_code=400, detail="Invalid server name. Cannot be empty or 'projectbrain'")
            
        config_path = get_claude_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        config = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = {}
                
        if "mcpServers" not in config:
            config["mcpServers"] = {}
            
        config["mcpServers"][name] = {
            "command": req.command,
            "args": req.args or [],
            "env": req.env or {}
        }
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            
        return {"success": True, "message": f"Successfully registered external MCP server '{name}'."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/external/{name}")
def delete_external_mcp_server(name: str):
    try:
        name = name.strip()
        if not name or name == "projectbrain":
            raise HTTPException(status_code=400, detail="Invalid server name. Cannot delete 'projectbrain'")
            
        config_path = get_claude_config_path()
        if not config_path.exists():
            return {"success": True, "message": "Config file not found, nothing to delete."}
            
        config = {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            return {"success": True, "message": "Failed to read config, nothing to delete."}
            
        mcp_servers = config.get("mcpServers", {})
        if name in mcp_servers:
            del mcp_servers[name]
            config["mcpServers"] = mcp_servers
            
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            return {"success": True, "message": f"Successfully removed external MCP server '{name}'."}
        else:
            return {"success": True, "message": f"External MCP server '{name}' not found."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
