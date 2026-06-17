import os
os.environ["PB_USE_PURE_PYTHON_PARSER"] = "false"

import pytest
import subprocess
from unittest.mock import MagicMock, patch
from projectbrain.ai.mcp import projectbrain_sync_codegraph

@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("sqlite3.connect")
@patch("projectbrain.server.routes.codegraph.sync_codegraph_data")
async def test_sync_db_exists(mock_sync, mock_connect, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database already exists.
    No installation or initialization commands should be executed.
    """
    mock_exists.return_value = True
    
    # Mock which to return None for git
    def which_side_effect(cmd):
        if cmd == "git":
            return None
        return "/mock/bin/codegraph"
    mock_which.side_effect = which_side_effect
    
    # Mock sqlite3 query results
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn
    
    mock_sync.return_value = {"status": "success", "nodes_count": 0, "edges_count": 0}
    
    res = await projectbrain_sync_codegraph("test-project", sync_memories=False)
    
    # Assertions
    mock_exists.assert_called_once()
    mock_which.assert_called_once_with("git")
    mock_run.assert_not_called()
    mock_connect.assert_called_once()
    mock_sync.assert_called_once()
    assert "success" in res


@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("sqlite3.connect")
@patch("projectbrain.server.routes.codegraph.sync_codegraph_data")
async def test_sync_db_missing_cli_exists(mock_sync, mock_connect, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database is missing, but codegraph CLI is installed.
    It should call 'codegraph init' and then proceed with synchronization.
    """
    # First check: exists=False, subsequent checks: exists=True
    exists_called = False
    def exists_side_effect(path):
        nonlocal exists_called
        if not exists_called:
            exists_called = True
            return False
        return True
    mock_exists.side_effect = exists_side_effect
    
    # Mock which side effect
    def which_side_effect(cmd):
        if cmd == "git":
            return None
        return "/mock/bin/codegraph"
    mock_which.side_effect = which_side_effect
    
    # Mock successful run of 'codegraph init'
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run_res.stdout = "Initialized"
    mock_run.return_value = mock_run_res
    
    # Mock sqlite3 query results
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn
    
    mock_sync.return_value = {"status": "success", "nodes_count": 0, "edges_count": 0}
    
    res = await projectbrain_sync_codegraph("test-project", sync_memories=False)
    
    # Assertions
    assert mock_exists.call_count >= 2
    assert mock_which.call_count == 2
    mock_which.assert_any_call("codegraph")
    mock_which.assert_any_call("git")
    mock_run.assert_called_once_with(["/mock/bin/codegraph", "init"], cwd=os.getcwd(), capture_output=True, text=True)
    mock_connect.assert_called_once()
    assert "success" in res


@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("sqlite3.connect")
@patch("projectbrain.server.routes.codegraph.sync_codegraph_data")
@patch("extensions_mcp.codebase_migration_helper.python_codegraph.main")
async def test_sync_db_missing_cli_missing_install_success(mock_py_main, mock_sync, mock_connect, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database and codegraph CLI are both missing.
    It should run 'npm install -g @colbymchenry/codegraph', then 'codegraph init', and then sync.
    """
    exists_called = False
    def exists_side_effect(path):
        nonlocal exists_called
        if not exists_called:
            exists_called = True
            return False
        return True
    mock_exists.side_effect = exists_side_effect
    
    which_calls = []
    def which_side_effect(cmd):
        which_calls.append(cmd)
        if cmd == "git":
            return None
        if len(which_calls) == 1:
            return None
        return "/mock/bin/codegraph"
    mock_which.side_effect = which_side_effect
    
    # Mock runs: npm install, then codegraph init
    mock_npm_res = MagicMock()
    mock_npm_res.returncode = 0
    mock_init_res = MagicMock()
    mock_init_res.returncode = 0
    mock_run.side_effect = [mock_npm_res, mock_init_res]
    
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn
    
    mock_sync.return_value = {"status": "success"}
    
    with patch.dict(os.environ, {"PB_ALLOW_AUTO_INSTALL": "true"}):
        res = await projectbrain_sync_codegraph("test-project", sync_memories=False)
    
    # Assertions
    assert mock_which.call_count == 3
    assert mock_run.call_count == 2
    mock_run.assert_any_call(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True)
    mock_run.assert_any_call(["/mock/bin/codegraph", "init"], cwd=os.getcwd(), capture_output=True, text=True)
    assert "success" in res


@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("extensions_mcp.codebase_migration_helper.python_codegraph.main")
async def test_sync_db_missing_cli_missing_install_fails(mock_py_main, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database and codegraph CLI are missing, and npm install fails.
    It should fallback to pure-Python codebase parser.
    """
    mock_exists.return_value = False
    mock_which.return_value = None
    
    mock_npm_res = MagicMock()
    mock_npm_res.returncode = 1
    mock_npm_res.stdout = ""
    mock_npm_res.stderr = "EACCES: permission denied"
    mock_run.return_value = mock_npm_res
    
    with patch.dict(os.environ, {"PB_ALLOW_AUTO_INSTALL": "true"}):
        res = await projectbrain_sync_codegraph("test-project", sync_memories=False)
    
    # Assertions
    mock_which.assert_called_once_with("codegraph")
    mock_run.assert_called_once_with(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True)
    mock_py_main.assert_called_once()
    assert "Error: Finished running 'codegraph init'" in res


@pytest.mark.asyncio
@patch("projectbrain.ai.mcp.os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("extensions_mcp.codebase_migration_helper.python_codegraph.main")
async def test_sync_db_missing_cli_missing_install_disabled(mock_py_main, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database and codegraph CLI are missing, but auto-install is disabled.
    It should directly fallback to the pure-Python parser.
    """
    mock_exists.return_value = False
    mock_which.return_value = None
    
    res = await projectbrain_sync_codegraph("test-project", sync_memories=False)
    
    # Assertions
    mock_which.assert_called_once_with("codegraph")
    mock_run.assert_not_called()
    mock_py_main.assert_called_once()
    assert "Error: Finished running 'codegraph init'" in res
