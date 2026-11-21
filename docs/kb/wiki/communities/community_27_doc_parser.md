# Community 27: Doc Parser

> **Leiden Community Cluster 27**  
> Contains 55 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `vectors.py` | code | `src/projectbrain/utils/vectors.py` | `L1` | Community 27 |
| `now()` | code | `src/projectbrain/utils/vectors.py` | `L9` | Community 27 |
| `rid()` | code | `src/projectbrain/utils/vectors.py` | `L12` | Community 27 |
| `cos_sim()` | code | `src/projectbrain/utils/vectors.py` | `L15` | Community 27 |
| `j()` | code | `src/projectbrain/utils/vectors.py` | `L26` | Community 27 |
| `p()` | code | `src/projectbrain/utils/vectors.py` | `L29` | Community 27 |
| `vec_to_buf()` | code | `src/projectbrain/utils/vectors.py` | `L32` | Community 27 |
| `buf_to_vec()` | code | `src/projectbrain/utils/vectors.py` | `L35` | Community 27 |
| `chunking.py` | code | `src/projectbrain/utils/chunking.py` | `L1` | Community 27 |
| `Chunk` | code | `src/projectbrain/utils/chunking.py` | `L5` | Community 27 |
| `TypedDict` | code | `src/projectbrain/utils/chunking.py` | `src/projectbrain/utils/chunking.py:1` | Community 27 |
| `est()` | code | `src/projectbrain/utils/chunking.py` | `L12` | Community 27 |
| `chunk_text()` | code | `src/projectbrain/utils/chunking.py` | `L15` | Community 27 |
| `agg_vec()` | code | `src/projectbrain/utils/chunking.py` | `L54` | Community 27 |
| `join_chunks()` | code | `src/projectbrain/utils/chunking.py` | `L70` | Community 27 |
| `keyword.py` | code | `src/projectbrain/utils/keyword.py` | `L1` | Community 27 |
| `extract_keywords()` | code | `src/projectbrain/utils/keyword.py` | `L6` | Community 27 |
| `compute_keyword_overlap()` | code | `src/projectbrain/utils/keyword.py` | `L26` | Community 27 |
| `exact_phrase_match()` | code | `src/projectbrain/utils/keyword.py` | `L38` | Community 27 |
| `compute_bm25_score()` | code | `src/projectbrain/utils/keyword.py` | `L41` | Community 27 |
| `keyword_filter_memories()` | code | `src/projectbrain/utils/keyword.py` | `L69` | Community 27 |
| `pdf_render.py` | code | `src/projectbrain/utils/pdf_render.py` | `L1` | Community 27 |
| `_has_fitz()` | code | `src/projectbrain/utils/pdf_render.py` | `L14` | Community 27 |
| `is_available()` | code | `src/projectbrain/utils/pdf_render.py` | `L23` | Community 27 |
| `page_count()` | code | `src/projectbrain/utils/pdf_render.py` | `L35` | Community 27 |
| `render_page_png()` | code | `src/projectbrain/utils/pdf_render.py` | `L56` | Community 27 |
| `render_all_png()` | code | `src/projectbrain/utils/pdf_render.py` | `L88` | Community 27 |
| `Render PDF pages to PNG images for OCR.  Primary backend is PyMuPDF (``fitz``):` | code | `src/projectbrain/utils/pdf_render.py` | `L1` | Community 27 |
| `True if a PDF page renderer is usable (PyMuPDF or pdfplumber.to_image).` | code | `src/projectbrain/utils/pdf_render.py` | `L24` | Community 27 |
| `Number of pages in the PDF (0 if it cannot be opened).` | code | `src/projectbrain/utils/pdf_render.py` | `L36` | Community 27 |
| `Render a single page (0-based) to PNG bytes, or None on failure.` | code | `src/projectbrain/utils/pdf_render.py` | `L57` | Community 27 |
| `Render every page to PNG bytes (entries may be None if a page fails).` | code | `src/projectbrain/utils/pdf_render.py` | `L89` | Community 27 |
| `text.py` | code | `src/projectbrain/utils/text.py` | `L1` | Community 27 |
| `_expand_cjk_token()` | code | `src/projectbrain/utils/text.py` | `L43` | Community 27 |
| `tokenize()` | code | `src/projectbrain/utils/text.py` | `L48` | Community 27 |
| `stem()` | code | `src/projectbrain/utils/text.py` | `L58` | Community 27 |
| `canonicalize_token()` | code | `src/projectbrain/utils/text.py` | `L66` | Community 27 |
| `canonical_tokens_from_text()` | code | `src/projectbrain/utils/text.py` | `L73` | Community 27 |
| `synonyms_for()` | code | `src/projectbrain/utils/text.py` | `L81` | Community 27 |
| `build_search_doc()` | code | `src/projectbrain/utils/text.py` | `L85` | Community 27 |
| `build_fts_query()` | code | `src/projectbrain/utils/text.py` | `L95` | Community 27 |
| `canonical_token_set()` | code | `src/projectbrain/utils/text.py` | `L101` | Community 27 |
| `stable_text_fallback_hash()` | code | `src/projectbrain/utils/text.py` | `L105` | Community 27 |
| `doc_parser.py` | code | `src/projectbrain/utils/doc_parser.py` | `L1` | Community 27 |
| `_ocr_raw()` | code | `src/projectbrain/utils/doc_parser.py` | `L7` | Community 27 |
| `call_ocr_if_available()` | code | `src/projectbrain/utils/doc_parser.py` | `L55` | Community 27 |
| `_ocr_full_pdf_page()` | code | `src/projectbrain/utils/doc_parser.py` | `L67` | Community 27 |
| `pdf_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L84` | Community 27 |
| `docx_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L183` | Community 27 |
| `excel_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L193` | Community 27 |
| `pptx_to_markdown()` | code | `src/projectbrain/utils/doc_parser.py` | `L236` | Community 27 |
| `parse_document()` | code | `src/projectbrain/utils/doc_parser.py` | `L278` | Community 27 |
| `Run OCR on raw image bytes, returning plain transcribed text ("" on failure).` | code | `src/projectbrain/utils/doc_parser.py` | `L8` | Community 27 |
| `Transcribe an embedded image to a Markdown snippet, or a placeholder.      Tries` | code | `src/projectbrain/utils/doc_parser.py` | `L56` | Community 27 |
| `Render a full PDF page to an image and OCR it (for scanned/image-only pages).` | code | `src/projectbrain/utils/doc_parser.py` | `L68` | Community 27 |

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
