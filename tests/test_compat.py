import pytest
import os
import json
from unittest.mock import patch, MagicMock
from pathlib import Path
from projectbrain.ai.mcp import projectbrain_query, projectbrain_store
from projectbrain.server.routes.mcp_registry import (
    get_mcp_status, register_mcp, unregister_mcp, get_external_mcp_servers, RegisterMCPRequest
)

# Test environment fallback logic
@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.mem.search")
async def test_mcp_env_fallbacks_query(mock_search):
    mock_search.return_value = []
    
    import shutil
    import subprocess
    git_bin = shutil.which("git")
    expected_branch = ""
    if git_bin and os.path.exists(".git"):
        res = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        if res.returncode == 0:
            expected_branch = res.stdout.strip()
    suffix = f":{expected_branch}" if expected_branch and expected_branch != "HEAD" else ""
    
    # Test 1: Using PB_DEFAULT_USER_ID
    with patch.dict(os.environ, {"PB_DEFAULT_USER_ID": "pb-user", "OM_DEFAULT_USER_ID": "om-user"}, clear=True):
        await projectbrain_query("test query")
        mock_search.assert_called_with("test query", user_id=f"pb-user{suffix}", limit=10)
        
    # Test 2: Using OM_DEFAULT_USER_ID as fallback
    with patch.dict(os.environ, {"OM_DEFAULT_USER_ID": "om-user"}, clear=True):
        await projectbrain_query("test query")
        mock_search.assert_called_with("test query", user_id=f"om-user{suffix}", limit=10)

@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.mem.add")
async def test_mcp_env_fallbacks_store(mock_add):
    mock_add.return_value = {"id": "123", "primary_sector": "test"}
    
    import shutil
    import subprocess
    git_bin = shutil.which("git")
    expected_branch = ""
    if git_bin and os.path.exists(".git"):
        res = subprocess.run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        if res.returncode == 0:
            expected_branch = res.stdout.strip()
    suffix = f":{expected_branch}" if expected_branch and expected_branch != "HEAD" else ""
    
    # Test 1: Using PB_DEFAULT_TAGS and PB_DEFAULT_USER_ID
    with patch.dict(os.environ, {"PB_DEFAULT_USER_ID": "pb-user", "PB_DEFAULT_TAGS": "pb-tag,other-tag"}, clear=True):
        await projectbrain_store("content to store")
        
        # Extract metadata and tags from mock call
        args, kwargs = mock_add.call_args
        assert kwargs["user_id"] == f"pb-user{suffix}"
        assert "pb-tag" in kwargs["tags"]
        assert "other-tag" in kwargs["tags"]

# Test registry compat routes
def test_registry_compat_status_openmemory_only(tmp_path):
    mock_config = tmp_path / "claude_config.json"
    
    # Setup openmemory only
    config_data = {
        "mcpServers": {
            "openmemory": {
                "command": "python3",
                "args": ["-m", "openmemory.main", "mcp"],
                "env": {"OM_DEFAULT_USER_ID": "user1"}
            }
        }
    }
    mock_config.write_text(json.dumps(config_data), encoding="utf-8")
    
    with patch("projectbrain.server.routes.mcp_registry.get_claude_config_path", return_value=mock_config):
        status = get_mcp_status()
        assert status["registered"] is True
        assert status["server_config"]["command"] == "python3"
        assert "openmemory" in status["server_config"]["args"][1]

def test_registry_compat_register_migrates_openmemory(tmp_path):
    mock_config = tmp_path / "claude_config.json"
    
    # Setup openmemory only
    config_data = {
        "mcpServers": {
            "openmemory": {
                "command": "python3",
                "args": ["-m", "openmemory.main", "mcp"]
            },
            "other-server": {
                "command": "node"
            }
        }
    }
    mock_config.write_text(json.dumps(config_data), encoding="utf-8")
    
    with patch("projectbrain.server.routes.mcp_registry.get_claude_config_path", return_value=mock_config):
        req = RegisterMCPRequest(transport_type="stdio", user_id="new-user", tags="new-tag")
        res = register_mcp(req)
        
        assert res["success"] is True
        
        # Read back config file
        saved_config = json.loads(mock_config.read_text(encoding="utf-8"))
        mcp_servers = saved_config.get("mcpServers", {})
        
        assert "openmemory" not in mcp_servers
        assert "projectbrain" in mcp_servers
        assert "other-server" in mcp_servers
        assert mcp_servers["projectbrain"]["env"]["PB_DEFAULT_USER_ID"] == "new-user"

def test_registry_compat_unregister_deletes_both(tmp_path):
    mock_config = tmp_path / "claude_config.json"
    
    # Setup both projectbrain and openmemory
    config_data = {
        "mcpServers": {
            "openmemory": {"command": "python3"},
            "projectbrain": {"command": "python3"}
        }
    }
    mock_config.write_text(json.dumps(config_data), encoding="utf-8")
    
    with patch("projectbrain.server.routes.mcp_registry.get_claude_config_path", return_value=mock_config):
        res = unregister_mcp()
        assert res["success"] is True
        
        saved_config = json.loads(mock_config.read_text(encoding="utf-8"))
        mcp_servers = saved_config.get("mcpServers", {})
        assert "openmemory" not in mcp_servers
        assert "projectbrain" not in mcp_servers

def test_registry_compat_external_filters_openmemory(tmp_path):
    mock_config = tmp_path / "claude_config.json"
    
    # Setup config with openmemory, projectbrain, and other servers
    config_data = {
        "mcpServers": {
            "openmemory": {"command": "python3"},
            "projectbrain": {"command": "python3"},
            "my-external-server": {"command": "node"}
        }
    }
    mock_config.write_text(json.dumps(config_data), encoding="utf-8")
    
    with patch("projectbrain.server.routes.mcp_registry.get_claude_config_path", return_value=mock_config):
        res = get_external_mcp_servers()
        servers = res["servers"]
        
        assert "openmemory" not in servers
        assert "projectbrain" not in servers
        assert "my-external-server" in servers
