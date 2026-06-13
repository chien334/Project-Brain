import pytest
from projectbrain.ai.mcp import mcp_server

def test_mcp_tools_registration():
    """
    Verify that the image-to-markdown MCP tools are registered on mcp_server.
    """
    tools = mcp_server._tool_manager.list_tools()
    tool_names = [tool.name for tool in tools]
    
    # Check that standard tools are there
    assert "projectbrain_query" in tool_names
    assert "projectbrain_store" in tool_names
    assert "projectbrain_sync_codegraph" in tool_names
    
    # Check that external tools are registered
    assert "img2md_extract_folder" in tool_names
    assert "img2md_list_images" in tool_names
    assert "img2md_extract_image" in tool_names

    # Check that all_tool_demo tools are registered
    assert "excel_list_sheets" in tool_names
    assert "excel_convert_to_markdown" in tool_names
    assert "excel_convert_sheet_to_markdown" in tool_names
    assert "markdown_convert_to_excel" in tool_names

    assert "docx_extract_text" in tool_names
    assert "docx_extract_tables" in tool_names
    assert "docx_create_document" in tool_names

    assert "pdf_extract_text" in tool_names
    assert "pdf_extract_tables" in tool_names
    assert "pdf_extract_images" in tool_names
    assert "pdf_convert_to_markdown" in tool_names
    assert "pdf_get_page_count" in tool_names
    assert "pdf_extract_page_text" in tool_names

    assert "pptx_extract_text" in tool_names
    assert "pptx_create_presentation" in tool_names
    assert "pptx_extract_images" in tool_names
    assert "pptx_to_markdown" in tool_names

    # Check that codebase-migration-helper tools are registered
    assert "migration_translate_code_comments" in tool_names
    assert "migration_recommend_refactor" in tool_names
    assert "migration_batch_scan_logic" in tool_names

    # Check annotations and description properties of one of the registered tools
    img_folder_tool = next(t for t in tools if t.name == "img2md_extract_folder")
    assert img_folder_tool.description is not None
    assert "folder_path" in img_folder_tool.parameters["properties"]

    excel_tool = next(t for t in tools if t.name == "excel_list_sheets")
    assert excel_tool.description is not None
    assert "file_path" in excel_tool.parameters["properties"]

