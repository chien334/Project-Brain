# Community 10: Paddle Client

> **Leiden Community Cluster 10**  
> Contains 64 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `server.py` | code | `extensions_mcp/image_to_markdown/server.py` | `L1` | Community 10 |
| `img2md_extract_folder()` | code | `extensions_mcp/image_to_markdown/server.py` | `L25` | Community 10 |
| `img2md_list_images()` | code | `extensions_mcp/image_to_markdown/server.py` | `L98` | Community 10 |
| `img2md_extract_image()` | code | `extensions_mcp/image_to_markdown/server.py` | `L132` | Community 10 |
| `main()` | code | `extensions_mcp/image_to_markdown/server.py` | `L170` | Community 10 |
| `Extract text from all images in a folder and format as Markdown, sorted naturall` | code | `extensions_mcp/image_to_markdown/server.py` | `L46` | Community 10 |
| `List images in natural sorted order within a directory (does not make LLM calls)` | code | `extensions_mcp/image_to_markdown/server.py` | `L102` | Community 10 |
| `Extract text from a single image and return it as Markdown (optionally saves to` | code | `extensions_mcp/image_to_markdown/server.py` | `L140` | Community 10 |
| `config.py` | code | `extensions_mcp/image_to_markdown/config.py` | `L1` | Community 10 |
| `Config` | code | `extensions_mcp/image_to_markdown/config.py` | `L10` | Community 10 |
| `load_config()` | code | `extensions_mcp/image_to_markdown/config.py` | `L22` | Community 10 |
| `require_vision()` | code | `extensions_mcp/image_to_markdown/config.py` | `L59` | Community 10 |
| `Validate cloud-vision config. Goi NGAY TRUOC khi dung vision model.      Tach ri` | code | `extensions_mcp/image_to_markdown/config.py` | `L60` | Community 10 |
| `vision_client.py` | code | `extensions_mcp/image_to_markdown/vision_client.py` | `L1` | Community 10 |
| `_get_client()` | code | `extensions_mcp/image_to_markdown/vision_client.py` | `L19` | Community 10 |
| `extract_text_from_image()` | code | `extensions_mcp/image_to_markdown/vision_client.py` | `L36` | Community 10 |
| `Tra ve client vision da khoi tao, validate config truoc.` | code | `extensions_mcp/image_to_markdown/vision_client.py` | `L20` | Community 10 |
| `Goi vision model trich text tu 1 anh, tra ve Markdown.` | code | `extensions_mcp/image_to_markdown/vision_client.py` | `L39` | Community 10 |
| `client.py` | code | `extensions_mcp/image_to_markdown/client.py` | `L1` | Community 10 |
| `call_remote_tool()` | code | `extensions_mcp/image_to_markdown/client.py` | `L14` | Community 10 |
| `main()` | code | `extensions_mcp/image_to_markdown/client.py` | `L48` | Community 10 |
| `Invokes an MCP tool remotely via the Streamable HTTP JSON-RPC endpoint.` | code | `extensions_mcp/image_to_markdown/client.py` | `L15` | Community 10 |
| `__init__.py` | code | `extensions_mcp/image_to_markdown/__init__.py` | `L1` | Community 10 |
| `image-to-markdown-mcp: trich text tu anh thanh Markdown bang Gemini 3.1 Pro.` | code | `extensions_mcp/image_to_markdown/__init__.py` | `L1` | Community 10 |
| `paddle_client.py` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L1` | Community 10 |
| `_langs()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L36` | Community 10 |
| `is_available()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L42` | Community 10 |
| `_build_engine()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L55` | Community 10 |
| `_get_engine()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L95` | Community 10 |
| `_to_ndarray()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L106` | Community 10 |
| `_poly_to_box()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L116` | Community 10 |
| `_parse_v3()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L132` | Community 10 |
| `_parse_v2()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L161` | Community 10 |
| `_infer()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L193` | Community 10 |
| `_to_text()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L209` | Community 10 |
| `_ocr_blocking()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L239` | Community 10 |
| `extract_text_local()` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L258` | Community 10 |
| `Local OCR engine backed by PaddleOCR (CPU-friendly, fully offline).  Drop-in alt` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L1` | Community 10 |
| `True if the ``paddleocr`` package can be imported (checked once, cached).` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L43` | Community 10 |
| `Construct a PaddleOCR engine, tolerating 2.x/3.x constructor differences.` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L56` | Community 10 |
| `Decode image bytes to a BGR numpy array (what PaddleOCR/OpenCV expect).` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L107` | Community 10 |
| `Reduce a detection polygon (4 points) or flat box to (x0, y0, x1, y1).` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L117` | Community 10 |
| `Parse PaddleOCR 3.x ``predict()`` output. Returns None if shape is unexpected.` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L133` | Community 10 |
| `Parse PaddleOCR 2.x ``ocr()`` output: [[ [box, (text, conf)], ... ]].` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L162` | Community 10 |
| `Run inference, supporting both the 3.x predict() and 2.x ocr() APIs.` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L194` | Community 10 |
| `Reconstruct reading order: group detections into lines top->bottom, left->right.` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L210` | Community 10 |
| `Blocking OCR over all configured languages; best average-confidence wins.` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L240` | Community 10 |
| `Async wrapper: OCR an image with PaddleOCR. Returns "" if unavailable/failed.` | code | `extensions_mcp/image_to_markdown/paddle_client.py` | `L259` | Community 10 |
| `extractor.py` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L1` | Community 10 |
| `ImageResult` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L17` | Community 10 |
| `BaseModel` | code | `extensions_mcp/image_to_markdown/extractor.py` | `extensions_mcp/image_to_markdown/extractor.py:1` | Community 10 |
| `_is_retryable()` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L29` | Community 10 |
| `_extract_one()` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L40` | Community 10 |
| `extract_paths()` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L45` | Community 10 |
| `build_combined_md()` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L62` | Community 10 |
| `run_extract_folder()` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L74` | Community 10 |
| `Chi retry loi mang/429/5xx; bo qua loi dau vao co dinh.` | code | `extensions_mcp/image_to_markdown/extractor.py` | `L30` | Community 10 |
| `utils.py` | code | `extensions_mcp/image_to_markdown/utils.py` | `L1` | Community 10 |
| `natural_key()` | code | `extensions_mcp/image_to_markdown/utils.py` | `L17` | Community 10 |
| `list_images()` | code | `extensions_mcp/image_to_markdown/utils.py` | `L22` | Community 10 |
| `read_image_as_supported()` | code | `extensions_mcp/image_to_markdown/utils.py` | `L31` | Community 10 |
| `write_text()` | code | `extensions_mcp/image_to_markdown/utils.py` | `L43` | Community 10 |
| `Khoa sort tu nhien: '2' < '10' (khong phai '10' < '2').` | code | `extensions_mcp/image_to_markdown/utils.py` | `L18` | Community 10 |
| `Tra ve (bytes, mime). Dinh dang la -> convert PNG.` | code | `extensions_mcp/image_to_markdown/utils.py` | `L32` | Community 10 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `server` | `contains` | `server_main` | EXTRACTED | 1.0 |
| `server` | `imports_from` | `config` | EXTRACTED | 1.0 |
| `server` | `imports_from` | `extractor` | EXTRACTED | 1.0 |
| `server` | `imports_from` | `utils` | EXTRACTED | 1.0 |
| `server` | `imports_from` | `vision_client` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_img2md_extract_folder` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_img2md_list_images` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_img2md_extract_image` | EXTRACTED | 1.0 |
| `server` | `contains` | `server_main` | EXTRACTED | 1.0 |
| `server_rationale_46` | `rationale_for` | `server_img2md_extract_folder` | EXTRACTED | 1.0 |
| `server_rationale_102` | `rationale_for` | `server_img2md_list_images` | EXTRACTED | 1.0 |
| `server_rationale_140` | `rationale_for` | `server_img2md_extract_image` | EXTRACTED | 1.0 |
| `config` | `contains` | `config_config` | EXTRACTED | 1.0 |
| `config` | `contains` | `config_load_config` | EXTRACTED | 1.0 |
| `config` | `contains` | `config_require_vision` | EXTRACTED | 1.0 |
| `config_load_config` | `calls` | `config_config` | EXTRACTED | 1.0 |
| `config_rationale_60` | `rationale_for` | `config_require_vision` | EXTRACTED | 1.0 |
| `vision_client` | `imports_from` | `config` | EXTRACTED | 1.0 |
| `vision_client` | `contains` | `vision_client_get_client` | EXTRACTED | 1.0 |
| `vision_client` | `contains` | `vision_client_extract_text_from_image` | EXTRACTED | 1.0 |
| `vision_client_extract_text_from_image` | `calls` | `vision_client_get_client` | EXTRACTED | 1.0 |
| `vision_client_rationale_20` | `rationale_for` | `vision_client_get_client` | EXTRACTED | 1.0 |
| `vision_client_rationale_39` | `rationale_for` | `vision_client_extract_text_from_image` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_call_remote_tool` | EXTRACTED | 1.0 |
| `client` | `contains` | `client_main` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `server` | `os` | `imports` | EXTRACTED |
| `server` | `httpx` | `imports` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
| `server` | `server_call_gemini` | `contains` | EXTRACTED |
| `server` | `server_translate_code_comments` | `contains` | EXTRACTED |
| `server` | `server_recommend_refactor` | `contains` | EXTRACTED |
| `server` | `server_batch_scan_logic` | `contains` | EXTRACTED |
| `server` | `server_plan_execution_phases` | `contains` | EXTRACTED |
| `server` | `pathlib` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
| `server` | `mcp_server_fastmcp` | `imports_from` | EXTRACTED |
| `server` | `mcp_types` | `imports_from` | EXTRACTED |
| `server` | `pydantic` | `imports_from` | EXTRACTED |
| `server` | `server_ocr_engine_status` | `contains` | EXTRACTED |
| `server` | `server_ocr_image_to_markdown` | `contains` | EXTRACTED |
| `server` | `server_ocr_pdf_to_markdown` | `contains` | EXTRACTED |
| `server` | `csv` | `imports` | EXTRACTED |
| `server` | `json` | `imports` | EXTRACTED |
| `server` | `os` | `imports` | EXTRACTED |
| `server` | `re` | `imports` | EXTRACTED |
| `server` | `pathlib` | `imports_from` | EXTRACTED |
| `server` | `typing` | `imports_from` | EXTRACTED |
| `server` | `openpyxl` | `imports` | EXTRACTED |
