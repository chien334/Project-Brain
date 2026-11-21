# Community 26: Doc Parser

> **Leiden Community Cluster 26**  
> Contains 55 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `vectors.py` | code | `src/projectbrain/utils/vectors.py` | `L1` | Community 26 |
| `now()` | code | `src/projectbrain/utils/vectors.py` | `L9` | Community 26 |
| `rid()` | code | `src/projectbrain/utils/vectors.py` | `L12` | Community 26 |
| `cos_sim()` | code | `src/projectbrain/utils/vectors.py` | `L15` | Community 26 |
| `j()` | code | `src/projectbrain/utils/vectors.py` | `L26` | Community 26 |
| `p()` | code | `src/projectbrain/utils/vectors.py` | `L29` | Community 26 |
| `vec_to_buf()` | code | `src/projectbrain/utils/vectors.py` | `L32` | Community 26 |
| `buf_to_vec()` | code | `src/projectbrain/utils/vectors.py` | `L35` | Community 26 |
| `chunking.py` | code | `src/projectbrain/utils/chunking.py` | `L1` | Community 26 |
| `Chunk` | code | `src/projectbrain/utils/chunking.py` | `L5` | Community 26 |
| `TypedDict` | code | `src/projectbrain/utils/chunking.py` | `src/projectbrain/utils/chunking.py:1` | Community 26 |
| `est()` | code | `src/projectbrain/utils/chunking.py` | `L12` | Community 26 |
| `chunk_text()` | code | `src/projectbrain/utils/chunking.py` | `L15` | Community 26 |
| `agg_vec()` | code | `src/projectbrain/utils/chunking.py` | `L54` | Community 26 |
| `join_chunks()` | code | `src/projectbrain/utils/chunking.py` | `L70` | Community 26 |
| `keyword.py` | code | `src/projectbrain/utils/keyword.py` | `L1` | Community 26 |
| `extract_keywords()` | code | `src/projectbrain/utils/keyword.py` | `L6` | Community 26 |
| `compute_keyword_overlap()` | code | `src/projectbrain/utils/keyword.py` | `L26` | Community 26 |
| `exact_phrase_match()` | code | `src/projectbrain/utils/keyword.py` | `L38` | Community 26 |
| `compute_bm25_score()` | code | `src/projectbrain/utils/keyword.py` | `L41` | Community 26 |
| `keyword_filter_memories()` | code | `src/projectbrain/utils/keyword.py` | `L69` | Community 26 |
| `pdf_render.py` | code | `src/projectbrain/utils/pdf_render.py` | `L1` | Community 26 |
| `_has_fitz()` | code | `src/projectbrain/utils/pdf_render.py` | `L14` | Community 26 |
| `is_available()` | code | `src/projectbrain/utils/pdf_render.py` | `L23` | Community 26 |
| `page_count()` | code | `src/projectbrain/utils/pdf_render.py` | `L35` | Community 26 |
| `render_page_png()` | code | `src/projectbrain/utils/pdf_render.py` | `L56` | Community 26 |
| `render_all_png()` | code | `src/projectbrain/utils/pdf_render.py` | `L88` | Community 26 |
| `Render PDF pages to PNG images for OCR.  Primary backend is PyMuPDF (``fitz``):` | code | `src/projectbrain/utils/pdf_render.py` | `L1` | Community 26 |
| `True if a PDF page renderer is usable (PyMuPDF or pdfplumber.to_image).` | code | `src/projectbrain/utils/pdf_render.py` | `L24` | Community 26 |
| `Number of pages in the PDF (0 if it cannot be opened).` | code | `src/projectbrain/utils/pdf_render.py` | `L36` | Community 26 |
| `Render a single page (0-based) to PNG bytes, or None on failure.` | code | `src/projectbrain/utils/pdf_render.py` | `L57` | Community 26 |
| `Render every page to PNG bytes (entries may be None if a page fails).` | code | `src/projectbrain/utils/pdf_render.py` | `L89` | Community 26 |
| `text.py` | code | `src/projectbrain/utils/text.py` | `L1` | Community 26 |
| `_expand_cjk_token()` | code | `src/projectbrain/utils/text.py` | `L43` | Community 26 |
| `tokenize()` | code | `src/projectbrain/utils/text.py` | `L48` | Community 26 |
| `stem()` | code | `src/projectbrain/utils/text.py` | `L58` | Community 26 |
| `canonicalize_token()` | code | `src/projectbrain/utils/text.py` | `L66` | Community 26 |
| `canonical_tokens_from_text()` | code | `src/projectbrain/utils/text.py` | `L73` | Community 26 |
| `synonyms_for()` | code | `src/projectbrain/utils/text.py` | `L81` | Community 26 |
| `build_search_doc()` | code | `src/projectbrain/utils/text.py` | `L85` | Community 26 |
| `build_fts_query()` | code | `src/projectbrain/utils/text.py` | `L95` | Community 26 |
| `canonical_token_set()` | code | `src/projectbrain/utils/text.py` | `L101` | Community 26 |
| `stable_text_fallback_hash()` | code | `src/projectbrain/utils/text.py` | `L105` | Community 26 |
| `doc_parser.py` | code | `src/projectbrain/utils/doc_parser.py` | `L1` | Community 26 |
| `_ocr_raw()` | code | `src/projectbrain/utils/doc_parser.py` | `L7` | Community 26 |
| `call_ocr_if_available()` | code | `src/projectbrain/utils/doc_parser.py` | `L55` | Community 26 |
| `_ocr_full_pdf_page()` | code | `src/projectbrain/utils/doc_parser.py` | `L67` | Community 26 |
| `pdf_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L84` | Community 26 |
| `docx_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L183` | Community 26 |
| `excel_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L193` | Community 26 |
| `pptx_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L236` | Community 26 |
| `parse_document()` | code | `src/projectbrain/utils/doc_parser.py` | `L278` | Community 26 |
| `Run OCR on raw image bytes, returning plain transcribed text ("" on failure).` | code | `src/projectbrain/utils/doc_parser.py` | `L8` | Community 26 |
| `Transcribe an embedded image to a Markdown snippet, or a placeholder.      Tries` | code | `src/projectbrain/utils/doc_parser.py` | `L56` | Community 26 |
| `Render a full PDF page to an image and OCR it (for scanned/image-only pages).` | code | `src/projectbrain/utils/doc_parser.py` | `L68` | Community 26 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `vectors` | `contains` | `vectors_now` | EXTRACTED | 1.0 |
| `vectors` | `contains` | `vectors_rid` | EXTRACTED | 1.0 |
| `vectors` | `contains` | `vectors_cos_sim` | EXTRACTED | 1.0 |
| `vectors` | `contains` | `vectors_j` | EXTRACTED | 1.0 |
| `vectors` | `contains` | `vectors_p` | EXTRACTED | 1.0 |
| `vectors` | `contains` | `vectors_vec_to_buf` | EXTRACTED | 1.0 |
| `vectors` | `contains` | `vectors_buf_to_vec` | EXTRACTED | 1.0 |
| `chunking` | `contains` | `chunking_chunk` | EXTRACTED | 1.0 |
| `chunking` | `contains` | `chunking_est` | EXTRACTED | 1.0 |
| `chunking` | `contains` | `chunking_chunk_text` | EXTRACTED | 1.0 |
| `chunking` | `contains` | `chunking_agg_vec` | EXTRACTED | 1.0 |
| `chunking` | `contains` | `chunking_join_chunks` | EXTRACTED | 1.0 |
| `chunking_chunk` | `inherits` | `typeddict` | EXTRACTED | 1.0 |
| `chunking_chunk_text` | `calls` | `chunking_est` | EXTRACTED | 1.0 |
| `keyword` | `imports_from` | `text` | EXTRACTED | 1.0 |
| `keyword` | `contains` | `keyword_extract_keywords` | EXTRACTED | 1.0 |
| `keyword` | `contains` | `keyword_compute_keyword_overlap` | EXTRACTED | 1.0 |
| `keyword` | `contains` | `keyword_exact_phrase_match` | EXTRACTED | 1.0 |
| `keyword` | `contains` | `keyword_compute_bm25_score` | EXTRACTED | 1.0 |
| `keyword` | `contains` | `keyword_keyword_filter_memories` | EXTRACTED | 1.0 |
| `keyword_keyword_filter_memories` | `calls` | `keyword_extract_keywords` | EXTRACTED | 1.0 |
| `keyword_keyword_filter_memories` | `calls` | `keyword_exact_phrase_match` | EXTRACTED | 1.0 |
| `keyword_keyword_filter_memories` | `calls` | `keyword_compute_keyword_overlap` | EXTRACTED | 1.0 |
| `keyword_keyword_filter_memories` | `calls` | `keyword_compute_bm25_score` | EXTRACTED | 1.0 |
| `pdf_render` | `contains` | `pdf_render_has_fitz` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `vectors` | `math` | `imports` | EXTRACTED |
| `vectors` | `uuid` | `imports` | EXTRACTED |
| `vectors` | `time` | `imports` | EXTRACTED |
| `vectors` | `json` | `imports` | EXTRACTED |
| `vectors` | `struct` | `imports` | EXTRACTED |
| `vectors` | `numpy` | `imports` | EXTRACTED |
| `vectors` | `typing` | `imports_from` | EXTRACTED |
| `chunking` | `math` | `imports` | EXTRACTED |
| `chunking` | `re` | `imports` | EXTRACTED |
| `chunking` | `typing` | `imports_from` | EXTRACTED |
| `keyword` | `typing` | `imports_from` | EXTRACTED |
| `keyword` | `math` | `imports` | EXTRACTED |
| `keyword` | `core_config` | `imports_from` | EXTRACTED |
| `pdf_render` | `io` | `imports` | EXTRACTED |
| `text` | `re` | `imports` | EXTRACTED |
| `text` | `hashlib` | `imports` | EXTRACTED |
| `text` | `typing` | `imports_from` | EXTRACTED |
| `doc_parser` | `io` | `imports` | EXTRACTED |
| `doc_parser` | `os` | `imports` | EXTRACTED |
| `doc_parser` | `pypdf` | `imports` | EXTRACTED |
| `doc_parser` | `mammoth` | `imports` | EXTRACTED |
| `doc_parser` | `markdownify` | `imports` | EXTRACTED |
