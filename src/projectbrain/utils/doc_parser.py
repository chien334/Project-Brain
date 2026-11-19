import asyncio
import hashlib
import io
import os
from typing import Any, Dict, List, Optional, Tuple
import pypdf
import mammoth
import markdownify

async def _ocr_raw(image_bytes: bytes, mime_type: str) -> str:
    """Run OCR on raw image bytes, returning plain transcribed text ("" on failure).

    Engine is chosen by the OCR_ENGINE env var (auto|paddle|vision|off):
      - auto  : try local PaddleOCR first, fall back to cloud vision (default)
      - paddle: local PaddleOCR only (offline, no API key)
      - vision: cloud vision only (needs LLM_API_KEY)
      - off   : skip OCR entirely
    """
    engine = (os.getenv("OCR_ENGINE", "auto").strip().lower() or "auto")
    if engine == "off":
        return ""

    # 1) Local PaddleOCR (CPU, offline, free)
    if engine in ("auto", "paddle"):
        try:
            from extensions_mcp.image_to_markdown.paddle_client import (
                extract_text_local,
                is_available,
            )
            if is_available():
                text = await extract_text_local(image_bytes, mime_type)
                if text and text.strip():
                    return text
        except Exception:
            pass
        if engine == "paddle":
            return ""

    # 2) Cloud vision fallback (Gemini / OpenAI-compatible)
    if engine in ("auto", "vision"):
        try:
            from extensions_mcp.image_to_markdown.vision_client import (
                extract_text_from_image,
                DEFAULT_PROMPT,
            )
            from extensions_mcp.image_to_markdown.config import CONFIG
            if getattr(CONFIG, "api_key", None):
                text = await extract_text_from_image(
                    image_bytes, mime_type, DEFAULT_PROMPT, CONFIG.model
                )
                if text and text.strip():
                    return text
        except Exception:
            pass
    return ""


async def call_ocr_if_available(image_bytes: bytes, mime_type: str) -> str:
    """Transcribe an embedded image to a Markdown snippet, or a placeholder.

    Tries local PaddleOCR first and cloud vision second (see _ocr_raw). The result
    is wrapped in OCR markers so downstream parsing can identify image-derived text.
    """
    text = await _ocr_raw(image_bytes, mime_type)
    if text and text.strip():
        return f"\n\n<!-- Start Embedded Image OCR -->\n{text.strip()}\n<!-- End Embedded Image OCR -->\n\n"
    return "![Image]"


async def _ocr_full_pdf_page(file_bytes: bytes, page_index: int) -> str:
    """Render a full PDF page to an image and OCR it (for scanned/image-only pages)."""
    try:
        from .pdf_render import render_page_png, is_available as render_ok
        if not render_ok():
            return ""
        try:
            dpi = int(os.getenv("OCR_PADDLE_DPI", "200") or "200")
        except ValueError:
            dpi = 200
        png = await asyncio.to_thread(render_page_png, file_bytes, page_index, dpi)
        if not png:
            return ""
        return await _ocr_raw(png, "image/png")
    except Exception:
        return ""


def _crop_page_images_from_pdfplumber_page(page: Any) -> List[Optional[bytes]]:
    """Synchronously crop embedded images on a pdfplumber page into PNG bytes (None on crop error)."""
    cropped_list: List[Optional[bytes]] = []
    if hasattr(page, "images") and page.images:
        for img in page.images:
            if img.get("width", 0) > 0 and img.get("height", 0) > 0:
                try:
                    x0 = img.get("x0")
                    top = img.get("top")
                    x1 = img.get("x1")
                    bottom = img.get("bottom")
                    if None not in (x0, top, x1, bottom) and x1 > x0 and bottom > top:
                        cropped = page.crop((x0, top, x1, bottom))
                        img_obj = cropped.to_image(resolution=150)
                        pil_img = img_obj.original
                        img_buf = io.BytesIO()
                        pil_img.save(img_buf, format="PNG")
                        cropped_list.append(img_buf.getvalue())
                except Exception:
                    cropped_list.append(None)
    return cropped_list


def _crop_pdf_page_images_sync(file_bytes: bytes, page_index: int) -> List[Optional[bytes]]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        if 0 <= page_index < len(pdf.pages):
            return _crop_page_images_from_pdfplumber_page(pdf.pages[page_index])
    return []


def _extract_pdfplumber_pages_sync(file_bytes: bytes) -> List[Dict[str, Any]]:
    import pdfplumber
    pages_data: List[Dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            base_parts: List[str] = [f"## Page {page_num}\n"]

            text = page.extract_text(x_tolerance=2)
            page_has_text = bool(text and len(text.strip()) >= 15)
            if text:
                base_parts.append(text + "\n")

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if table and len(table) > 0:
                        col_count = max((len(row) for row in table), default=0)
                        if col_count > 0:
                            def normalize_row(row):
                                cells = [
                                    str(row[i] if i < len(row) and row[i] is not None else "")
                                    .replace("|", "\\|")
                                    .replace("\n", " ")
                                    for i in range(col_count)
                                ]
                                return "| " + " | ".join(cells) + " |"

                            header = normalize_row(table[0])
                            separator = "| " + " | ".join(["---"] * col_count) + " |"
                            body_rows = [normalize_row(row) for row in table[1:]]
                            body = "\n".join(body_rows) if body_rows else ""
                            base_parts.append(f"\n{header}\n{separator}\n{body}\n")

            has_images = bool(hasattr(page, "images") and page.images)
            cropped_images = (
                _crop_page_images_from_pdfplumber_page(page)
                if (page_has_text and has_images)
                else None
            )
            pages_data.append({
                "page_index": page_num - 1,
                "base_parts": base_parts,
                "page_has_text": page_has_text,
                "has_images": has_images,
                "cropped_images": cropped_images,
            })
    return pages_data


def _extract_pypdf_pages_sync(file_bytes: bytes) -> List[Dict[str, Any]]:
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    pages_data: List[Dict[str, Any]] = []
    for page_num, page in enumerate(reader.pages):
        text = page.extract_text()
        images: List[Optional[Tuple[bytes, str]]] = []
        if hasattr(page, "images") and page.images:
            for img_file in page.images:
                try:
                    img_bytes = getattr(img_file, "data", None)
                    if img_bytes:
                        name = getattr(img_file, "name", "image.png")
                        mime_type = (
                            "image/jpeg"
                            if name.lower().endswith((".jpg", ".jpeg"))
                            else "image/png"
                        )
                        images.append((img_bytes, mime_type))
                except Exception:
                    images.append(None)
        pages_data.append({
            "page_num": page_num + 1,
            "text": text,
            "images": images,
        })
    return pages_data


async def pdf_to_markdown(file_bytes: bytes) -> str:
    try:
        import pdfplumber  # noqa: F401
        pages_data = await asyncio.to_thread(_extract_pdfplumber_pages_sync, file_bytes)
        text_parts: List[str] = []
        for pdata in pages_data:
            text_parts.extend(pdata["base_parts"])
            full_page_ocred = False

            if not pdata["page_has_text"]:
                page_ocr = await _ocr_full_pdf_page(file_bytes, pdata["page_index"])
                if page_ocr and page_ocr.strip():
                    text_parts.append(
                        "\n<!-- Start Page OCR -->\n"
                        + page_ocr.strip()
                        + "\n<!-- End Page OCR -->\n"
                    )
                    full_page_ocred = True

            if not full_page_ocred and pdata["has_images"]:
                cropped_images = pdata["cropped_images"]
                if cropped_images is None:
                    cropped_images = await asyncio.to_thread(
                        _crop_pdf_page_images_sync, file_bytes, pdata["page_index"]
                    )
                for img_bytes in cropped_images:
                    if img_bytes is None:
                        text_parts.append("![Image]\n")
                    else:
                        ocr_result = await call_ocr_if_available(img_bytes, "image/png")
                        text_parts.append(ocr_result + "\n")

        return "\n".join(text_parts)
    except ImportError:
        try:
            pypdf_pages = await asyncio.to_thread(_extract_pypdf_pages_sync, file_bytes)
            text_parts = []
            for pdata in pypdf_pages:
                page_text: List[str] = []
                if pdata["text"]:
                    page_text.append(pdata["text"])
                for img_item in pdata["images"]:
                    if img_item is None:
                        page_text.append("![Image]\n")
                    else:
                        img_bytes, mime_type = img_item
                        ocr_result = await call_ocr_if_available(img_bytes, mime_type)
                        page_text.append(ocr_result)
                if page_text:
                    text_parts.append(f"## Page {pdata['page_num']}\n\n" + "\n".join(page_text))
            return "\n\n".join(text_parts)
        except Exception as e:
            raise ValueError(f"Failed to parse PDF: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to parse PDF: {str(e)}")


def _docx_to_markdown_sync(file_bytes: bytes) -> str:
    file_obj = io.BytesIO(file_bytes)
    result = mammoth.convert_to_html(file_obj)
    html = result.value
    return markdownify.markdownify(html)


async def docx_to_markdown(file_bytes: bytes) -> str:
    try:
        return await asyncio.to_thread(_docx_to_markdown_sync, file_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse DOCX: {str(e)}")


def _excel_to_markdown_sync(file_bytes: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheets = []
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            sheets.append(f"## Sheet: {name}\n")
            all_rows = list(ws.iter_rows(values_only=True))
            max_cols = 0
            for row in all_rows:
                if row and any(row[i] is not None for i in range(len(row))):
                    max_cols = max(max_cols, len(row))

            if max_cols == 0:
                sheets.append("_Empty sheet_\n")
                continue

            first_row = True
            for row in all_rows:
                if not row or not any(row[i] is not None for i in range(len(row))):
                    continue
                padded_row = [str(row[i]) if i < len(row) and row[i] is not None else "" for i in range(max_cols)]
                cleaned_row = [val.replace("|", "\\|").replace("\n", " ") for val in padded_row]
                row_str = "| " + " | ".join(cleaned_row) + " |"

                if first_row:
                    sheets.append(row_str)
                    separator = "| " + " | ".join(["---"] * max_cols) + " |"
                    sheets.append(separator)
                    first_row = False
                else:
                    sheets.append(row_str)
            sheets.append("")
        return "\n".join(sheets)
    finally:
        wb.close()


async def excel_to_markdown(file_bytes: bytes) -> str:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return "Excel parsing requires 'openpyxl'. Run: pip install openpyxl"
    try:
        return await asyncio.to_thread(_excel_to_markdown_sync, file_bytes)
    except Exception as e:
        raise ValueError(f"Failed to parse Excel: {str(e)}")


def _extract_pptx_items_sync(file_bytes: bytes) -> List[Tuple[str, Any]]:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(file_bytes))
    items: List[Tuple[str, Any]] = []
    for i, slide in enumerate(prs.slides):
        items.append(("text", f"\n## Slide {i+1}\n"))
        shapes = sorted(slide.shapes, key=lambda x: x.top)
        for shape in shapes:
            if shape.has_text_frame:
                if shape.is_placeholder and shape.placeholder_format.type in [1, 13, 14, 15, 16]:
                    if shape.text:
                        items.append(("text", f"# {shape.text.strip()}\n"))
                else:
                    for paragraph in shape.text_frame.paragraphs:
                        para_text = "".join(run.text for run in paragraph.runs).strip()
                        if not para_text:
                            continue
                        prefix = ""
                        if paragraph.level > 0:
                            prefix = "  " * paragraph.level + "* "
                        elif para_text.startswith(("•", "–", "-")):
                            prefix = "* "
                            para_text = para_text[1:].lstrip()
                        items.append(("text", f"{prefix}{para_text}"))
                    items.append(("text", ""))
            elif hasattr(shape, "image"):
                try:
                    img_bytes = shape.image.blob
                    mime_type = shape.image.content_type
                    items.append(("image", (img_bytes, mime_type)))
                except Exception:
                    items.append(("text", "![Image]\n"))
    return items


async def pptx_to_markdown(file_bytes: bytes) -> str:
    try:
        import pptx  # noqa: F401
    except ImportError:
        return "PowerPoint parsing requires 'python-pptx'. Run: pip install python-pptx"
    try:
        items = await asyncio.to_thread(_extract_pptx_items_sync, file_bytes)
        markdown_text: List[str] = []
        for kind, payload in items:
            if kind == "text":
                markdown_text.append(payload)
            elif kind == "image":
                img_bytes, mime_type = payload
                ocr_result = await call_ocr_if_available(img_bytes, mime_type)
                markdown_text.append(ocr_result)
        return "\n".join(markdown_text).strip()
    except Exception as e:
        raise ValueError(f"Failed to parse PPTX: {str(e)}")


def inspect_pdf_pages_pre_ocr(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Inspect PDF pages without rendering PNGs, cropping images, or running OCR."""
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    pages: List[Dict[str, Any]] = []
    for i, page in enumerate(reader.pages):
        try:
            raw_text = (page.extract_text() or "").strip()
        except Exception:
            raw_text = ""
        img_hashes: List[str] = []
        try:
            for im in getattr(page, "images", []):
                data = getattr(im, "data", None)
                if data:
                    img_hashes.append(hashlib.sha256(data).hexdigest())
        except Exception:
            pass

        raw_stream_hash = ""
        if raw_text == "" and not img_hashes:
            try:
                contents = page.get_contents()
                if contents is not None:
                    stream_data = contents.get_data()
                    if stream_data:
                        raw_stream_hash = hashlib.sha256(stream_data).hexdigest()
            except Exception:
                raw_stream_hash = ""

        digest_input = f"{raw_text}|{','.join(img_hashes)}|{raw_stream_hash}"
        unit_sha256 = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        pages.append({
            "page_index": i,
            "page_num": i + 1,
            "unit_title": f"Page {i + 1}",
            "unit_sha256": unit_sha256,
        })
    return pages


def _extract_pdfplumber_single_page_sync(file_bytes: bytes, page_index: int) -> Optional[Dict[str, Any]]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        if page_index < 0 or page_index >= len(pdf.pages):
            return None
        page = pdf.pages[page_index]
        page_num = page_index + 1
        base_parts: List[str] = [f"## Page {page_num}\n"]

        text = page.extract_text(x_tolerance=2)
        page_has_text = bool(text and len(text.strip()) >= 15)
        if text:
            base_parts.append(text + "\n")

        tables = page.extract_tables()
        if tables:
            for table in tables:
                if table and len(table) > 0:
                    col_count = max((len(row) for row in table), default=0)
                    if col_count > 0:
                        def normalize_row(row):
                            cells = [
                                str(row[i] if i < len(row) and row[i] is not None else "")
                                .replace("|", "\\|")
                                .replace("\n", " ")
                                for i in range(col_count)
                            ]
                            return "| " + " | ".join(cells) + " |"

                        header = normalize_row(table[0])
                        separator = "| " + " | ".join(["---"] * col_count) + " |"
                        body_rows = [normalize_row(row) for row in table[1:]]
                        body = "\n".join(body_rows) if body_rows else ""
                        base_parts.append(f"\n{header}\n{separator}\n{body}\n")

        has_images = bool(hasattr(page, "images") and page.images)
        cropped_images = (
            _crop_page_images_from_pdfplumber_page(page)
            if (page_has_text and has_images)
            else None
        )
        return {
            "page_index": page_index,
            "base_parts": base_parts,
            "page_has_text": page_has_text,
            "has_images": has_images,
            "cropped_images": cropped_images,
        }


def _extract_pypdf_single_page_sync(file_bytes: bytes, page_index: int) -> Optional[Dict[str, Any]]:
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    if page_index < 0 or page_index >= len(reader.pages):
        return None
    page = reader.pages[page_index]
    text = page.extract_text()
    images: List[Optional[Tuple[bytes, str]]] = []
    if hasattr(page, "images") and page.images:
        for img_file in page.images:
            try:
                img_bytes = getattr(img_file, "data", None)
                if img_bytes:
                    name = getattr(img_file, "name", "image.png")
                    mime_type = (
                        "image/jpeg"
                        if name.lower().endswith((".jpg", ".jpeg"))
                        else "image/png"
                    )
                    images.append((img_bytes, mime_type))
            except Exception:
                images.append(None)
    return {
        "page_num": page_index + 1,
        "text": text,
        "images": images,
    }


async def parse_pdf_page_markdown(file_bytes: bytes, page_index: int) -> str:
    """Extract Markdown for a single PDF page without touching/rendering/OCR-ing other pages."""
    try:
        import pdfplumber  # noqa: F401
        pdata = await asyncio.to_thread(_extract_pdfplumber_single_page_sync, file_bytes, page_index)
        if pdata is None:
            return ""
        text_parts: List[str] = list(pdata["base_parts"])
        full_page_ocred = False

        if not pdata["page_has_text"]:
            page_ocr = await _ocr_full_pdf_page(file_bytes, pdata["page_index"])
            if page_ocr and page_ocr.strip():
                text_parts.append(
                    "\n<!-- Start Page OCR -->\n"
                    + page_ocr.strip()
                    + "\n<!-- End Page OCR -->\n"
                )
                full_page_ocred = True

        if not full_page_ocred and pdata["has_images"]:
            cropped_images = pdata["cropped_images"]
            if cropped_images is None:
                cropped_images = await asyncio.to_thread(
                    _crop_pdf_page_images_sync, file_bytes, pdata["page_index"]
                )
            for img_bytes in cropped_images:
                if img_bytes is None:
                    text_parts.append("![Image]\n")
                else:
                    ocr_result = await call_ocr_if_available(img_bytes, "image/png")
                    text_parts.append(ocr_result + "\n")

        return "\n".join(text_parts).strip()
    except ImportError:
        try:
            pdata = await asyncio.to_thread(_extract_pypdf_single_page_sync, file_bytes, page_index)
            if pdata is None:
                return ""
            page_text: List[str] = []
            if pdata["text"]:
                page_text.append(pdata["text"])
            for img_item in pdata["images"]:
                if img_item is None:
                    page_text.append("![Image]\n")
                else:
                    img_bytes, mime_type = img_item
                    ocr_result = await call_ocr_if_available(img_bytes, mime_type)
                    page_text.append(ocr_result)
            if page_text:
                return f"## Page {pdata['page_num']}\n\n" + "\n".join(page_text)
            return ""
        except Exception as e:
            raise ValueError(f"Failed to parse PDF page {page_index}: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to parse PDF page {page_index}: {str(e)}")


def inspect_pptx_slides_pre_ocr(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Inspect PPTX slides and hash text + raw image blobs BEFORE calling OCR."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(file_bytes))
    slides: List[Dict[str, Any]] = []
    for i, slide in enumerate(prs.slides):
        items: List[Tuple[str, Any]] = [("text", f"\n## Slide {i + 1}\n")]
        text_parts_for_hash: List[str] = []
        img_hashes: List[str] = []
        slide_title = f"Slide {i + 1}"
        title_found = False

        shapes = sorted(slide.shapes, key=lambda x: x.top)
        for shape in shapes:
            if shape.has_text_frame:
                if shape.is_placeholder and shape.placeholder_format.type in [1, 13, 14, 15, 16]:
                    if shape.text and shape.text.strip():
                        clean_t = shape.text.strip()
                        if not title_found:
                            slide_title = clean_t
                            title_found = True
                        items.append(("text", f"# {clean_t}\n"))
                        text_parts_for_hash.append(f"# {clean_t}")
                else:
                    for paragraph in shape.text_frame.paragraphs:
                        para_text = "".join(run.text for run in paragraph.runs).strip()
                        if not para_text:
                            continue
                        prefix = ""
                        if paragraph.level > 0:
                            prefix = "  " * paragraph.level + "* "
                        elif para_text.startswith(("•", "–", "-")):
                            prefix = "* "
                            para_text = para_text[1:].lstrip()
                        line = f"{prefix}{para_text}"
                        items.append(("text", line))
                        text_parts_for_hash.append(line)
                    items.append(("text", ""))
            elif hasattr(shape, "image"):
                try:
                    img_bytes = shape.image.blob
                    mime_type = shape.image.content_type
                    items.append(("image", (img_bytes, mime_type)))
                    if img_bytes:
                        img_hashes.append(hashlib.sha256(img_bytes).hexdigest())
                except Exception:
                    items.append(("text", "![Image]\n"))

        digest_input = "\n".join(text_parts_for_hash) + "|" + ",".join(img_hashes)
        unit_sha256 = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        slides.append({
            "unit_index": i,
            "unit_title": slide_title,
            "unit_sha256": unit_sha256,
            "items": items,
        })
    return slides


async def parse_pptx_slide_markdown(slide_info: Dict[str, Any]) -> str:
    """Run OCR only on a single slide's images and return Markdown."""
    markdown_text: List[str] = []
    for kind, payload in slide_info.get("items", []):
        if kind == "text":
            markdown_text.append(payload)
        elif kind == "image":
            img_bytes, mime_type = payload
            ocr_result = await call_ocr_if_available(img_bytes, mime_type)
            markdown_text.append(ocr_result)
    return "\n".join(markdown_text).strip()


def format_excel_sheet_markdown(sheet_name: str, raw_rows: List[Tuple[Any, ...]]) -> str:
    """Format raw openpyxl row tuples for a single sheet into Markdown."""
    lines: List[str] = [f"## Sheet: {sheet_name}\n"]
    max_cols = 0
    for row in raw_rows:
        if row and any(row[i] is not None for i in range(len(row))):
            max_cols = max(max_cols, len(row))

    if max_cols == 0:
        lines.append("_Empty sheet_\n")
        return "\n".join(lines)

    first_row = True
    for row in raw_rows:
        if not row or not any(row[i] is not None for i in range(len(row))):
            continue
        padded_row = [str(row[i]) if i < len(row) and row[i] is not None else "" for i in range(max_cols)]
        cleaned_row = [val.replace("|", "\\|").replace("\n", " ") for val in padded_row]
        row_str = "| " + " | ".join(cleaned_row) + " |"

        if first_row:
            lines.append(row_str)
            separator = "| " + " | ".join(["---"] * max_cols) + " |"
            lines.append(separator)
            first_row = False
        else:
            lines.append(row_str)
    lines.append("")
    return "\n".join(lines)


def inspect_excel_sheets_raw(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Inspect Excel sheets via openpyxl and hash raw cell tuples before Markdown formatting."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheets: List[Dict[str, Any]] = []
    try:
        for idx, name in enumerate(wb.sheetnames):
            ws = wb[name]
            raw_rows = [tuple(row) for row in ws.iter_rows(values_only=True)]
            normalized_rows = [
                tuple("" if cell is None else str(cell) for cell in row)
                for row in raw_rows
                if row and any(cell is not None for cell in row)
            ]
            digest_payload = f"{name}|" + repr(normalized_rows)
            unit_sha256 = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
            sheets.append({
                "unit_index": idx,
                "unit_title": name,
                "unit_sha256": unit_sha256,
                "raw_rows": raw_rows,
            })
    finally:
        wb.close()
    return sheets


async def parse_document(filename: str, file_bytes: bytes) -> str:
    _, ext = os.path.splitext(filename.lower())
    if ext == ".pdf":
        return await pdf_to_markdown(file_bytes)
    elif ext == ".docx":
        return await docx_to_markdown(file_bytes)
    elif ext in [".xlsx", ".xls"]:
        return await excel_to_markdown(file_bytes)
    elif ext == ".pptx":
        return await pptx_to_markdown(file_bytes)
    elif ext in [".txt", ".md", ".json", ".yml", ".yaml", ".ini", ".conf"]:
        return file_bytes.decode("utf-8", errors="ignore")
    else:
        return file_bytes.decode("utf-8", errors="ignore")
