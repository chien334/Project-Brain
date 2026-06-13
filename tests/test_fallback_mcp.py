import pytest
import os
import json
from unittest.mock import MagicMock, patch, AsyncMock
from projectbrain.ai.mcp import mcp_server
from extensions_mcp.codebase_migration_helper.server import translate_code_comments, recommend_refactor
from extensions_mcp.codebase_migration_helper.batch_scan_helper import analyze_business_logic, run_batch_scan_logic

def test_mcp_tools_registration():
    """Verify registration of the new tools."""
    tools = mcp_server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    assert "projectbrain_register_mcp" in tool_names
    assert "projectbrain_register_external_mcp" in tool_names

@pytest.mark.asyncio
@patch("projectbrain.server.routes.mcp_registry.register_mcp")
async def test_projectbrain_register_mcp_tool(mock_register):
    """Verify tool wrapper correctly invokes registry function."""
    mock_register.return_value = {"success": True, "message": "registered"}
    
    # Retrieve the tool wrapper
    tools = mcp_server._tool_manager.list_tools()
    tool = next(t for t in tools if t.name == "projectbrain_register_mcp")
    
    res_str = await tool.fn(transport_type="stdio", user_id="tester")
    res = json.loads(res_str)
    
    mock_register.assert_called_once()
    assert res["success"] is True
    assert res["message"] == "registered"

@pytest.mark.asyncio
@patch("projectbrain.server.routes.mcp_registry.save_external_mcp_server")
async def test_projectbrain_register_external_mcp_tool(mock_save):
    """Verify external tool wrapper correctly invokes registry function."""
    mock_save.return_value = {"success": True, "message": "saved"}
    
    tools = mcp_server._tool_manager.list_tools()
    tool = next(t for t in tools if t.name == "projectbrain_register_external_mcp")
    
    res_str = await tool.fn(name="test-server", command="npx", args=["mcp-test"], env={"K": "V"})
    res = json.loads(res_str)
    
    mock_save.assert_called_once()
    assert res["success"] is True
    assert res["message"] == "saved"

@pytest.mark.asyncio
@patch("extensions_mcp.codebase_migration_helper.server.call_gemini", new_callable=AsyncMock)
@patch("os.path.isfile")
@patch("builtins.open", create=True)
async def test_translate_comments_fallback_to_client(mock_open, mock_isfile, mock_call_gemini):
    """Verify tool returns fallback_to_client=True when API fails."""
    mock_isfile.return_value = True
    mock_open.return_value.__enter__.return_value.read.return_value = "print('こんにちは')"
    mock_call_gemini.side_effect = RuntimeError("Rate Limit 429")
    
    res = await translate_code_comments("dummy.py", "ja2en")
    assert res["fallback_to_client"] is True
    assert "Dummy" in res["message"] or "translation" in res["message"] or "dummy" in res["message"]
    assert res["prompt"] == "print('こんにちは')"
    assert "Failed to translate" in res["error"]

@pytest.mark.asyncio
@patch("extensions_mcp.codebase_migration_helper.server.call_gemini", new_callable=AsyncMock)
@patch("os.path.isfile")
@patch("builtins.open", create=True)
async def test_recommend_refactor_fallback_to_client(mock_open, mock_isfile, mock_call_gemini):
    """Verify recommend_refactor returns fallback_to_client=True when API fails."""
    mock_isfile.return_value = True
    mock_open.return_value.__enter__.return_value.read.return_value = "legacy code"
    mock_call_gemini.side_effect = RuntimeError("API down")
    
    res = await recommend_refactor("dummy.py", "React")
    assert res["fallback_to_client"] is True
    assert "refactoring" in res["message"]
    assert "legacy code" in res["prompt"]

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_analyze_business_logic_fallback_chain(mock_post):
    """Verify business logic analyzer loops through fallback chain on errors."""
    mock_res_fail = MagicMock()
    mock_res_fail.status_code = 429
    mock_res_fail.text = "Too Many Requests"
    
    # Let all models fail
    mock_post.return_value = mock_res_fail
    
    with pytest.raises(RuntimeError) as exc_info:
        await analyze_business_logic(
            api_key="mock_key",
            model="primary-model",
            code_snippet="def foo(): pass",
            file_path="foo.py",
            function_name="foo",
            language="python"
        )
    assert "All models in fallback chain failed" in str(exc_info.value)
    # 4 models tried in fallback chain: primary-model, gemini-1.5-flash, gemini-2.5-flash, gemma-4-26b-a4b-it
    assert mock_post.call_count == 4

@pytest.mark.asyncio
@patch("extensions_mcp.codebase_migration_helper.batch_scan_helper.get_project_functions")
@patch("extensions_mcp.codebase_migration_helper.batch_scan_helper.extract_function_code")
@patch("extensions_mcp.codebase_migration_helper.batch_scan_helper.analyze_business_logic", new_callable=AsyncMock)
@patch("os.path.exists")
@patch("os.makedirs")
@patch("builtins.open", create=True)
async def test_batch_scan_partial_fallback(mock_open, mock_makedirs, mock_exists, mock_analyze, mock_extract, mock_get_fns):
    """Verify run_batch_scan_logic returns failed_items and partial_success_fallback_needed."""
    mock_exists.return_value = True
    mock_get_fns.return_value = [
        {
            "id": 1,
            "kind": "function",
            "name": "foo",
            "qualified_name": "foo",
            "file_path": "foo.py",
            "start_line": 1,
            "end_line": 5,
            "docstring": "",
            "signature": "def foo()"
        }
    ]
    mock_extract.return_value = "def foo(): pass"
    mock_analyze.side_effect = RuntimeError("Mock API failure")
    
    with patch.dict(os.environ, {"LLM_API_KEY": "dummy_key"}):
        res = await run_batch_scan_logic("/dummy/root", "dummy-project")
        
    assert res["status"] == "partial_success_fallback_needed"
    assert len(res["failed_items"]) == 1
    assert res["failed_items"][0]["function_name"] == "foo"
