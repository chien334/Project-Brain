# Community 15: Diag Ocr

> **Leiden Community Cluster 15**  
> Contains 8 member symbols with high intra-cluster cohesive density.

## 1. Community Member Symbols

| Symbol Name | Type | Source File | Location | Community ID |
| :--- | :--- | :--- | :--- | :--- |
| `diag_ocr.py` | code | `scripts/diag_ocr.py` | `L1` | Community 15 |
| `run()` | code | `scripts/diag_ocr.py` | `L15` | Community 15 |
| `Diagnose why an image yields chars=0 — runs predict() WITHOUT swallowing errors.` | code | `scripts/diag_ocr.py` | `L1` | Community 15 |
| `check_ocr.py` | code | `scripts/check_ocr.py` | `L1` | Community 15 |
| `main()` | code | `scripts/check_ocr.py` | `L13` | Community 15 |
| `Smoke test for the local PaddleOCR integration.  Usage:     python scripts/check` | code | `scripts/check_ocr.py` | `L1` | Community 15 |
| `make_sample.py` | code | `scripts/make_sample.py` | `L1` | Community 15 |
| `Generate OCR test fixtures: a text PNG and an image-only ('scanned') PDF.  Outpu` | code | `scripts/make_sample.py` | `L1` | Community 15 |

## 2. Internal Call & Import Relations

| Source Symbol | Relation | Target Symbol | Confidence | Score |
| :--- | :--- | :--- | :--- | :--- |
| `diag_ocr` | `contains` | `diag_ocr_run` | EXTRACTED | 1.0 |
| `diag_ocr_rationale_1` | `rationale_for` | `diag_ocr` | EXTRACTED | 1.0 |
| `check_ocr` | `contains` | `check_ocr_main` | EXTRACTED | 1.0 |
| `check_ocr_rationale_1` | `rationale_for` | `check_ocr` | EXTRACTED | 1.0 |
| `make_sample_rationale_1` | `rationale_for` | `make_sample` | EXTRACTED | 1.0 |

## 3. Cross-Community Bridge Connections

| Source Symbol | Target External Symbol | Relation | Confidence |
| :--- | :--- | :--- | :--- |
| `diag_ocr` | `sys` | `imports` | EXTRACTED |
| `diag_ocr` | `numpy` | `imports` | EXTRACTED |
| `diag_ocr` | `pil` | `imports_from` | EXTRACTED |
| `diag_ocr` | `paddleocr` | `imports_from` | EXTRACTED |
| `check_ocr` | `asyncio` | `imports` | EXTRACTED |
| `check_ocr` | `sys` | `imports` | EXTRACTED |
| `check_ocr` | `pathlib` | `imports_from` | EXTRACTED |
| `make_sample` | `pathlib` | `imports_from` | EXTRACTED |
| `make_sample` | `pil` | `imports_from` | EXTRACTED |
| `make_sample` | `fitz` | `imports` | EXTRACTED |
