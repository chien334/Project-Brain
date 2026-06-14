"""Render PDF pages to PNG images for OCR.

Primary backend is PyMuPDF (``fitz``): a pip-installable wheel with no system
dependencies, fast and reliable on Windows. Falls back to ``pdfplumber``'s
``Page.to_image`` when PyMuPDF is not installed.

Used for OCR of scanned / image-only PDF pages where there is no extractable
text layer.
"""

import io


def _has_fitz() -> bool:
    try:
        import fitz  # noqa: F401  (PyMuPDF)

        return True
    except Exception:
        return False


def is_available() -> bool:
    """True if a PDF page renderer is usable (PyMuPDF or pdfplumber.to_image)."""
    if _has_fitz():
        return True
    try:
        import pdfplumber  # noqa: F401

        return True
    except Exception:
        return False


def page_count(file_bytes: bytes) -> int:
    """Number of pages in the PDF (0 if it cannot be opened)."""
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        pass
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


def render_page_png(file_bytes: bytes, page_index: int, dpi: int = 200) -> bytes | None:
    """Render a single page (0-based) to PNG bytes, or None on failure."""
    # PyMuPDF: fast, no system deps.
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            if page_index < 0 or page_index >= doc.page_count:
                return None
            zoom = dpi / 72.0
            pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception:
        pass
    # Fallback: pdfplumber's rasteriser (needs its own image backend).
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if page_index < 0 or page_index >= len(pdf.pages):
                return None
            pil = pdf.pages[page_index].to_image(resolution=dpi).original
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return None


def render_all_png(file_bytes: bytes, dpi: int = 200) -> list[bytes | None]:
    """Render every page to PNG bytes (entries may be None if a page fails)."""
    return [render_page_png(file_bytes, i, dpi) for i in range(page_count(file_bytes))]
