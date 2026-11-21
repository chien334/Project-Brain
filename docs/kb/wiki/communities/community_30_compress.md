# Community 30: Compress

> **Leiden Community Cluster 30**  
> Contains 34 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `ingest.py` | code | `src/projectbrain/ops/ingest.py` | `L1` | Community 30 |
| `split_text()` | code | `src/projectbrain/ops/ingest.py` | `L16` | Community 30 |
| `mk_root()` | code | `src/projectbrain/ops/ingest.py` | `L31` | Community 30 |
| `mk_child()` | code | `src/projectbrain/ops/ingest.py` | `L68` | Community 30 |
| `link()` | code | `src/projectbrain/ops/ingest.py` | `L79` | Community 30 |
| `ingest_document()` | code | `src/projectbrain/ops/ingest.py` | `L85` | Community 30 |
| `ingest_url()` | code | `src/projectbrain/ops/ingest.py` | `L135` | Community 30 |
| `compress.py` | code | `src/projectbrain/ops/compress.py` | `L1` | Community 30 |
| `MemoryCompressionEngine` | code | `src/projectbrain/ops/compress.py` | `L6` | Community 30 |
| `.__init__()` | code | `src/projectbrain/ops/compress.py` | `L7` | Community 30 |
| `.tok()` | code | `src/projectbrain/ops/compress.py` | `L22` | Community 30 |
| `.sem()` | code | `src/projectbrain/ops/compress.py` | `L28` | Community 30 |
| `.syn()` | code | `src/projectbrain/ops/compress.py` | `L68` | Community 30 |
| `.agg()` | code | `src/projectbrain/ops/compress.py` | `L96` | Community 30 |
| `.compress()` | code | `src/projectbrain/ops/compress.py` | `L127` | Community 30 |
| `.batch()` | code | `src/projectbrain/ops/compress.py` | `L161` | Community 30 |
| `.auto()` | code | `src/projectbrain/ops/compress.py` | `L164` | Community 30 |
| `.empty()` | code | `src/projectbrain/ops/compress.py` | `L175` | Community 30 |
| `.hash()` | code | `src/projectbrain/ops/compress.py` | `L181` | Community 30 |
| `.up()` | code | `src/projectbrain/ops/compress.py` | `L184` | Community 30 |
| `.store()` | code | `src/projectbrain/ops/compress.py` | `L197` | Community 30 |
| `extract.py` | code | `src/projectbrain/ops/extract.py` | `L1` | Community 30 |
| `estimate_tokens()` | code | `src/projectbrain/ops/extract.py` | `L17` | Community 30 |
| `extract_pdf()` | code | `src/projectbrain/ops/extract.py` | `L20` | Community 30 |
| `extract_docx()` | code | `src/projectbrain/ops/extract.py` | `L38` | Community 30 |
| `extract_html()` | code | `src/projectbrain/ops/extract.py` | `L53` | Community 30 |
| `extract_url()` | code | `src/projectbrain/ops/extract.py` | `L66` | Community 30 |
| `extract_audio()` | code | `src/projectbrain/ops/extract.py` | `L74` | Community 30 |
| `extract_video()` | code | `src/projectbrain/ops/extract.py` | `L122` | Community 30 |
| `extract_text()` | code | `src/projectbrain/ops/extract.py` | `L157` | Community 30 |
| `dynamics.py` | code | `src/projectbrain/ops/dynamics.py` | `L1` | Community 30 |
| `calculateCrossSectorResonanceScore()` | code | `src/projectbrain/ops/dynamics.py` | `L33` | Community 30 |
| `applyRetrievalTraceReinforcementToMemory()` | code | `src/projectbrain/ops/dynamics.py` | `L38` | Community 30 |
| `propagateAssociativeReinforcementToLinkedNodes()` | code | `src/projectbrain/ops/dynamics.py` | `L41` | Community 30 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `ingest` | `imports_from` | `extract` | EXTRACTED | 1.0 |
| `ingest` | `contains` | `ingest_split_text` | EXTRACTED | 1.0 |
| `ingest` | `contains` | `ingest_mk_root` | EXTRACTED | 1.0 |
| `ingest` | `contains` | `ingest_mk_child` | EXTRACTED | 1.0 |
| `ingest` | `contains` | `ingest_link` | EXTRACTED | 1.0 |
| `ingest` | `contains` | `ingest_ingest_document` | EXTRACTED | 1.0 |
| `ingest` | `contains` | `ingest_ingest_url` | EXTRACTED | 1.0 |
| `ingest_ingest_document` | `calls` | `ingest_split_text` | EXTRACTED | 1.0 |
| `ingest_ingest_document` | `calls` | `ingest_mk_root` | EXTRACTED | 1.0 |
| `ingest_ingest_document` | `calls` | `ingest_mk_child` | EXTRACTED | 1.0 |
| `ingest_ingest_document` | `calls` | `ingest_link` | EXTRACTED | 1.0 |
| `ingest_ingest_url` | `calls` | `ingest_split_text` | EXTRACTED | 1.0 |
| `ingest_ingest_url` | `calls` | `ingest_mk_root` | EXTRACTED | 1.0 |
| `ingest_ingest_url` | `calls` | `ingest_mk_child` | EXTRACTED | 1.0 |
| `ingest_ingest_url` | `calls` | `ingest_link` | EXTRACTED | 1.0 |
| `compress` | `contains` | `compress_memorycompressionengine` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_init` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_tok` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_sem` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_syn` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_agg` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_compress` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_batch` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_auto` | EXTRACTED | 1.0 |
| `compress_memorycompressionengine` | `method` | `compress_memorycompressionengine_empty` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `ingest` | `asyncio` | `imports` | EXTRACTED |
| `ingest` | `json` | `imports` | EXTRACTED |
| `ingest` | `logging` | `imports` | EXTRACTED |
| `ingest` | `uuid` | `imports` | EXTRACTED |
| `ingest` | `time` | `imports` | EXTRACTED |
| `ingest` | `typing` | `imports_from` | EXTRACTED |
| `ingest` | `core_db` | `imports_from` | EXTRACTED |
| `ingest` | `memory_hsg` | `imports_from` | EXTRACTED |
| `ingest` | `utils_vectors` | `imports_from` | EXTRACTED |
| `compress` | `re` | `imports` | EXTRACTED |
| `compress` | `time` | `imports` | EXTRACTED |
| `compress` | `hashlib` | `imports` | EXTRACTED |
| `compress` | `typing` | `imports_from` | EXTRACTED |
| `extract` | `os` | `imports` | EXTRACTED |
| `extract` | `time` | `imports` | EXTRACTED |
| `extract` | `json` | `imports` | EXTRACTED |
| `extract` | `logging` | `imports` | EXTRACTED |
| `extract` | `asyncio` | `imports` | EXTRACTED |
| `extract` | `tempfile` | `imports` | EXTRACTED |
| `extract` | `re` | `imports` | EXTRACTED |
| `extract` | `typing` | `imports_from` | EXTRACTED |
| `extract` | `httpx` | `imports` | EXTRACTED |
| `extract` | `pypdf` | `imports_from` | EXTRACTED |
| `extract` | `mammoth` | `imports` | EXTRACTED |
| `extract` | `markdownify` | `imports_from` | EXTRACTED |
