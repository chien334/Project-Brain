import pytest
import os
import subprocess
from unittest.mock import MagicMock, patch
from projectbrain.ai.mcp import projectbrain_sync_codegraph

@pytest.mark.asyncio
@patch("os.path.exists")
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
    
    # Mock sqlite3 query results
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn
    
    mock_sync.return_value = {"status": "success", "nodes_count": 0, "edges_count": 0}
    
    res = await projectbrain_sync_codegraph("test-project", "tester")
    
    # Assertions
    mock_exists.assert_called_once()
    mock_which.assert_not_called()
    mock_run.assert_not_called()
    mock_connect.assert_called_once()
    mock_sync.assert_called_once()
    assert "success" in res


@pytest.mark.asyncio
@patch("os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("sqlite3.connect")
@patch("projectbrain.server.routes.codegraph.sync_codegraph_data")
async def test_sync_db_missing_cli_exists(mock_sync, mock_connect, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database is missing, but codegraph CLI is installed.
    It should call 'codegraph init' and then proceed with synchronization.
    """
    # First check: exists=False, second check: exists=True (after init)
    mock_exists.side_effect = [False, True]
    mock_which.return_value = "/mock/bin/codegraph"
    
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
    
    res = await projectbrain_sync_codegraph("test-project", "tester")
    
    # Assertions
    assert mock_exists.call_count == 2
    mock_which.assert_called_once_with("codegraph")
    mock_run.assert_called_once_with(["/mock/bin/codegraph", "init"], cwd=os.getcwd(), capture_output=True, text=True)
    mock_connect.assert_called_once()
    assert "success" in res


@pytest.mark.asyncio
@patch("os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
@patch("sqlite3.connect")
@patch("projectbrain.server.routes.codegraph.sync_codegraph_data")
async def test_sync_db_missing_cli_missing_install_success(mock_sync, mock_connect, mock_run, mock_which, mock_exists):
    """
    Test scenario: Database and codegraph CLI are both missing.
    It should run 'npm install -g @colbymchenry/codegraph', then 'codegraph init', and then sync.
    """
    mock_exists.side_effect = [False, True]
    # First check: None, second check (after npm install): '/mock/bin/codegraph'
    mock_which.side_effect = [None, "/mock/bin/codegraph"]
    
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
    
    res = await projectbrain_sync_codegraph("test-project", "tester")
    
    # Assertions
    assert mock_which.call_count == 2
    assert mock_run.call_count == 2
    mock_run.assert_any_call(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True)
    mock_run.assert_any_call(["/mock/bin/codegraph", "init"], cwd=os.getcwd(), capture_output=True, text=True)
    assert "success" in res


@pytest.mark.asyncio
@patch("os.path.exists")
@patch("shutil.which")
@patch("subprocess.run")
async def test_sync_db_missing_cli_missing_install_fails(mock_run, mock_which, mock_exists):
    """
    Test scenario: Database and codegraph CLI are missing, and npm install fails (permissions or other errors).
    It should return a descriptive error instructing the user on how to install manually.
    """
    mock_exists.return_value = False
    mock_which.return_value = None
    
    mock_npm_res = MagicMock()
    mock_npm_res.returncode = 1
    mock_npm_res.stdout = ""
    mock_npm_res.stderr = "EACCES: permission denied"
    mock_run.return_value = mock_npm_res
    
    res = await projectbrain_sync_codegraph("test-project", "tester")
    
    # Assertions
    mock_which.assert_called_once_with("codegraph")
    mock_run.assert_called_once_with(["npm", "install", "-g", "@colbymchenry/codegraph"], capture_output=True, text=True)
    assert "EACCES" in res
    assert "npm install -g @colbymchenry/codegraph" in res
    assert "sudo npm install" in res
