import pytest
import os
import json
from unittest.mock import MagicMock, patch, AsyncMock
from projectbrain.ai.mcp import mcp_server
from extensions_mcp.codebase_migration_helper.server import translate_code_comments, recommend_refactor, plan_execution_phases
from extensions_mcp.codebase_migration_helper.batch_scan_helper import analyze_business_logic, run_batch_scan_logic, run_phase_planning

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

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
@patch("os.path.exists")
@patch("os.makedirs")
@patch("builtins.open", create=True)
async def test_run_phase_planning_success(mock_open, mock_makedirs, mock_exists, mock_post):
    """Verify run_phase_planning parses JSON and generates md report."""
    mock_exists.return_value = True
    
    # Mock reading business_logic_drafts.json
    mock_open.return_value.__enter__.return_value.read.return_value = json.dumps([
        {
            "node_id": 1,
            "function_name": "foo",
            "file_path": "foo.py",
            "signature": "def foo()",
            "business_logic_draft": "core helper logic description"
        }
    ])
    
    # Mock LLM API response (valid JSON schema)
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {
        "candidates": [{
            "content": {
                "parts": [{
                    "text": json.dumps({
                        "phases": [
                            {
                                "phase_number": 1,
                                "name": "Utility Phase",
                                "description": "Helpers",
                                "complexity": "low",
                                "functions": [{"node_id": 1, "name": "foo", "file_path": "foo.py"}]
                            }
                        ]
                    })
                }]
            }
        }]
    }
    mock_post.return_value = mock_res
    
    with patch.dict(os.environ, {"LLM_API_KEY": "dummy_key"}):
        res = await run_phase_planning("/dummy/root", 3)
        
    assert res["status"] == "success"
    assert res["phases_count"] == 1
    assert "migration_phases.json" in res["output_json"]
    assert "migration_phases.md" in res["output_md"]

@pytest.mark.asyncio
@patch("extensions_mcp.codebase_migration_helper.batch_scan_helper.run_phase_planning", new_callable=AsyncMock)
@patch("os.path.exists")
@patch("builtins.open", create=True)
async def test_plan_execution_phases_fallback(mock_open, mock_exists, mock_run_planning):
    """Verify plan_execution_phases tool falls back to client when backend API fails."""
    mock_run_planning.side_effect = RuntimeError("API rate limit")
    mock_exists.return_value = True
    
    # Mock reading drafts for prompt construction in fallback handler
    mock_open.return_value.__enter__.return_value.read.return_value = json.dumps([
        {
            "node_id": 1,
            "function_name": "foo",
            "file_path": "foo.py",
            "signature": "def foo()",
            "business_logic_draft": "some logic"
        }
    ])
    
    res = await plan_execution_phases("/dummy/root", 5)
    assert res["fallback_to_client"] is True
    assert "Failed to plan phases" in res["error"]
    assert "foo" in res["prompt"]
    assert "phases" in res["message"]

from projectbrain.utils.doc_parser import parse_document

@patch("pdfplumber.open")
def test_parse_pdf_via_doc_parser(mock_pdfplumber_open):
    # Mock pdfplumber structures
    mock_pdf = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "hello pdf"
    mock_page.extract_tables.return_value = [[["header", "val"], ["v1", "v2"]]]
    mock_pdf.pages = [mock_page]
    mock_pdfplumber_open.return_value.__enter__.return_value = mock_pdf
    
    res = parse_document("test.pdf", b"pdfbytes")
    assert "Page 1" in res or "Page 2" not in res
    assert "hello pdf" in res
    assert "header | val" in res

@patch("openpyxl.load_workbook")
def test_parse_excel_via_doc_parser(mock_load_workbook):
    mock_wb = MagicMock()
    mock_wb.sheetnames = ["Sheet1"]
    mock_ws = MagicMock()
    mock_ws.iter_rows.return_value = [("col1", "col2"), ("v1", "v2")]
    mock_wb.__getitem__.return_value = mock_ws
    mock_load_workbook.return_value = mock_wb
    
    res = parse_document("test.xlsx", b"excelbytes")
    assert "Sheet: Sheet1" in res
    assert "col1 | col2" in res
    assert "v1 | v2" in res

@patch("pptx.Presentation")
def test_parse_pptx_via_doc_parser(mock_presentation):
    mock_prs = MagicMock()
    mock_slide = MagicMock()
    mock_shape = MagicMock()
    mock_shape.has_text_frame = True
    mock_shape.is_placeholder = True
    mock_shape.placeholder_format.type = 1 # Title
    mock_shape.text = "Slide Title"
    mock_shape.top = 10
    mock_slide.shapes = [mock_shape]
    mock_prs.slides = [mock_slide]
    mock_presentation.return_value = mock_prs
    
    res = parse_document("test.pptx", b"pptxbytes")
    assert "Slide 1" in res
    assert "Slide Title" in res


