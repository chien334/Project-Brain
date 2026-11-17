# Community 13: Test Multilingual Dedup

> **Leiden Community Cluster 13**  
> Contains 9 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `test_multilingual_dedup.py` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L1` | Community 13 |
| `_ensure_pkg()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L34` | Community 13 |
| `_stub_module()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L43` | Community 13 |
| `_load_module()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L50` | Community 13 |
| `_load_text_and_hsg()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L59` | Community 13 |
| `test_tokenize_expands_cjk_bigrams()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L121` | Community 13 |
| `test_canonical_tokens_keep_distinct_chinese_content()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L125` | Community 13 |
| `test_compute_simhash_avoids_constant_hash_for_distinct_chinese_inputs()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L134` | Community 13 |
| `test_compute_simhash_uses_stable_fallback_when_tokenizer_finds_nothing()` | code | `tests/test_multilingual_dedup.py/test_multilingual_dedup.py` | `L141` | Community 13 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_ensure_pkg` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_stub_module` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_load_module` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_load_text_and_hsg` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_test_tokenize_expands_cjk_bigrams` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_test_canonical_tokens_keep_distinct_chinese_content` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_test_compute_simhash_avoids_constant_hash_for_distinct_chinese_inputs` | EXTRACTED | 1.0 |
| `test_multilingual_dedup` | `contains` | `test_multilingual_dedup_test_compute_simhash_uses_stable_fallback_when_tokenizer_finds_nothing` | EXTRACTED | 1.0 |
| `test_multilingual_dedup_load_text_and_hsg` | `calls` | `test_multilingual_dedup_ensure_pkg` | EXTRACTED | 1.0 |
| `test_multilingual_dedup_load_text_and_hsg` | `calls` | `test_multilingual_dedup_load_module` | EXTRACTED | 1.0 |
| `test_multilingual_dedup_load_text_and_hsg` | `calls` | `test_multilingual_dedup_stub_module` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `test_multilingual_dedup` | `importlib_util` | `imports` | EXTRACTED |
| `test_multilingual_dedup` | `sys` | `imports` | EXTRACTED |
| `test_multilingual_dedup` | `types` | `imports` | EXTRACTED |
| `test_multilingual_dedup` | `pathlib` | `imports_from` | EXTRACTED |
| `test_multilingual_dedup` | `pytest` | `imports` | EXTRACTED |
