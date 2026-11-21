import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .auth import _enforce_rest_auth_if_configured

router = APIRouter()

_SENSITIVE_KEY_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "AUTH")
_SAFE_STDIO_ENV_KEYS = (
    "OM_DB_PATH",
    "PB_DB_PATH",
    "PB_GRAPH_DB_PATH",
    "OM_GRAPH_DB_PATH",
    "PB_DEFAULT_USER_ID",
    "PB_DEFAULT_TAGS",
    "PB_MODE",
    "PB_URL",
)
_PATH_ENV_KEYS = {"OM_DB_PATH", "PB_DB_PATH", "PB_GRAPH_DB_PATH", "OM_GRAPH_DB_PATH"}
_FORBIDDEN_SHELL_CHARS = set(";|&$`><\n\r")


def _check_read_only() -> None:
    if os.getenv("PB_READ_ONLY", "false").lower() in ("true", "1", "yes") or os.getenv("OM_READ_ONLY", "false").lower() in ("true", "1", "yes"):
        raise HTTPException(
            status_code=403,
            detail="Operation not permitted: Server is running in Read-Only mode."
        )


def _is_sensitive_key(key: str) -> bool:
    upper = (key or "").upper()
    return any(marker in upper for marker in _SENSITIVE_KEY_MARKERS)


def _mask_url_credentials(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    # Mask userinfo in URLs: scheme://user:pass@host -> scheme://****@host
    masked = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@\s]+)@", r"\1****@", value)
    # Mask sensitive query parameter values in URLs (?token=..., &api_key=..., etc.)
    masked = re.sub(
        r"([?&][a-zA-Z0-9_]*(?:key|token|secret|password|pass|auth)[a-zA-Z0-9_]*=)[^&\s\"']+",
        r"\1********",
        masked,
        flags=re.IGNORECASE,
    )
    # Mask CLI inline flag secrets (--api-key=..., --token=...)
    masked = re.sub(
        r"(--?[a-zA-Z0-9_-]*(?:key|token|secret|password|pass|auth)[a-zA-Z0-9_-]*=)\S+",
        r"\1********",
        masked,
        flags=re.IGNORECASE,
    )
    return masked


def _sanitize_mcp_config_for_response(cfg: Any) -> Any:
    """Recursively mask sensitive env values and URL credentials in MCP config dicts."""
    if cfg is None:
        return None
    if isinstance(cfg, str):
        return _mask_url_credentials(cfg)
    if isinstance(cfg, list):
        sanitized_list: List[Any] = []
        mask_next = False
        for item in cfg:
            if mask_next and isinstance(item, str):
                sanitized_list.append("********")
                mask_next = False
                continue
            mask_next = False
            if isinstance(item, str):
                if item.startswith("-") and "=" not in item and _is_sensitive_key(item):
                    mask_next = True
                sanitized_list.append(_mask_url_credentials(item))
            else:
                sanitized_list.append(_sanitize_mcp_config_for_response(item))
        return sanitized_list
    if isinstance(cfg, dict):
        sanitized_dict: Dict[str, Any] = {}
        for k, v in cfg.items():
            if k == "env" and isinstance(v, dict):
                env_out: Dict[str, Any] = {}
                for env_k, env_v in v.items():
                    if _is_sensitive_key(str(env_k)):
                        env_out[env_k] = "********" if env_v else ""
                    elif isinstance(env_v, str):
                        env_out[env_k] = _mask_url_credentials(env_v)
                    else:
                        env_out[env_k] = _sanitize_mcp_config_for_response(env_v)
                sanitized_dict[k] = env_out
            elif _is_sensitive_key(str(k)) and isinstance(v, str):
                sanitized_dict[k] = "********" if v else ""
            else:
                sanitized_dict[k] = _sanitize_mcp_config_for_response(v)
        return sanitized_dict
    return cfg


def _validate_external_command(command: str) -> str:
    if not command or not command.strip():
        raise HTTPException(status_code=400, detail="Invalid command: command cannot be empty.")
    if any(ch in command for ch in _FORBIDDEN_SHELL_CHARS):
        raise HTTPException(status_code=400, detail="Invalid command: shell metacharacters are not permitted.")
    cleaned = command.strip()
    if ".." in Path(cleaned).parts or ".." in cleaned:
        raise HTTPException(status_code=400, detail="Invalid command: path traversal ('..') is not permitted.")
    return cleaned


def _validate_sse_url(sse_url: Optional[str]) -> str:
    if not sse_url or not isinstance(sse_url, str):
        raise HTTPException(status_code=400, detail="Invalid sse_url: must start with 'http://' or 'https://'.")
    cleaned = sse_url.strip()
    if not cleaned.startswith(("http://", "https://")) or any(ch in cleaned for ch in ("\n", "\r", " ")):
        raise HTTPException(status_code=400, detail="Invalid sse_url: must start with 'http://' or 'https://'.")
    return cleaned


class RegisterMCPRequest(BaseModel):
    transport_type: str  # "stdio" or "sse"
    user_id: Optional[str] = "default"
    tags: Optional[str] = "source:mcp"
    sse_url: Optional[str] = "http://localhost:8080/mcp/sse"


class ExternalMCPRequest(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = []
    env: Optional[Dict[str, str]] = {}


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
def get_mcp_status(request: Request = None):
    _enforce_rest_auth_if_configured(request)
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
            "server_config": _sanitize_mcp_config_for_response(server_config),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/register")
def register_mcp(req: RegisterMCPRequest, request: Request = None):
    _enforce_rest_auth_if_configured(request)
    _check_read_only()
    try:
        if req.transport_type not in ("stdio", "sse"):
            raise HTTPException(status_code=400, detail="Invalid transport type. Must be 'stdio' or 'sse'")

        if req.transport_type == "sse" or (req.sse_url is not None and req.sse_url != "http://localhost:8080/mcp/sse"):
            if req.transport_type == "sse" or (req.sse_url and req.sse_url.strip()):
                validated_sse_url = _validate_sse_url(req.sse_url)
            else:
                validated_sse_url = "http://localhost:8080/mcp/sse"
        else:
            validated_sse_url = _validate_sse_url(req.sse_url)

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
            # Only forward safe, non-secret configuration variables
            env_vars: Dict[str, str] = {}
            for k in _SAFE_STDIO_ENV_KEYS:
                v = os.environ.get(k)
                if v:
                    if k in _PATH_ENV_KEYS:
                        env_vars[k] = str(Path(v).resolve())
                    else:
                        env_vars[k] = v
            if req.user_id:
                env_vars["PB_DEFAULT_USER_ID"] = req.user_id
            if req.tags:
                env_vars["PB_DEFAULT_TAGS"] = req.tags

            config["mcpServers"]["projectbrain"] = {
                "command": sys.executable,
                "args": ["-m", "projectbrain.main", "mcp"],
                "env": env_vars,
            }
        else:
            config["mcpServers"]["projectbrain"] = {
                "command": "npx",
                "args": ["-y", "mcp-remote", validated_sse_url],
            }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        return {
            "success": True,
            "message": f"Successfully registered projectbrain via {req.transport_type} transport in Claude Desktop config.",
            "server_config": _sanitize_mcp_config_for_response(config["mcpServers"]["projectbrain"]),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/unregister")
def unregister_mcp(request: Request = None):
    _enforce_rest_auth_if_configured(request)
    _check_read_only()
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/external")
def get_external_mcp_servers(request: Request = None):
    _enforce_rest_auth_if_configured(request)
    try:
        config_path = get_claude_config_path()
        if not config_path.exists():
            return {"servers": {}}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                mcp_servers = config.get("mcpServers", {})
                external_servers = {
                    k: _sanitize_mcp_config_for_response(v)
                    for k, v in mcp_servers.items()
                    if k not in ["projectbrain", "openmemory"]
                }
                return {"servers": external_servers}
        except Exception:
            return {"servers": {}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/external")
def save_external_mcp_server(req: ExternalMCPRequest, request: Request = None):
    _enforce_rest_auth_if_configured(request)
    _check_read_only()
    try:
        name = req.name.strip()
        if not name or name == "projectbrain":
            raise HTTPException(status_code=400, detail="Invalid server name. Cannot be empty or 'projectbrain'")

        validated_command = _validate_external_command(req.command)

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
            "command": validated_command,
            "args": req.args or [],
            "env": req.env or {},
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        return {"success": True, "message": f"Successfully registered external MCP server '{name}'."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/external/{name}")
def delete_external_mcp_server(name: str, request: Request = None):
    _enforce_rest_auth_if_configured(request)
    _check_read_only()
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
