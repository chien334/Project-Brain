#!/usr/bin/env python3
"""Local offline OCR MCP extension backed by PaddleOCR (CPU-friendly).

Exposes tools to OCR a single image or a whole PDF (render each page -> OCR)
without any cloud API key. Heavy dependencies (paddleocr / pymupdf) are imported
lazily inside each tool so the extension always loads, even when they are not yet
installed — the tools then return a clear install hint instead of crashing.
"""

from pathlib import Path
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

mcp = FastMCP("ocr_local_mcp")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Local OCR engine status",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def ocr_engine_status() -> dict:
    """Report local OCR engine status: PaddleOCR/PyMuPDF installation, language, and engine settings.

    Returns:
        dict: {
            "ocr_engine": str,              # OCR_ENGINE value (auto|paddle|vision|off)
            "paddle_langs": str,            # OCR_PADDLE_LANG value
            "paddle_dpi": str,              # OCR_PADDLE_DPI value
            "paddleocr_available": bool,    # is paddleocr installed?
            "pdf_renderer_available": bool, # can we render PDFs? (PyMuPDF/pdfplumber)
            "cloud_vision_key_set": bool    # is LLM_API_KEY set for cloud fallback?
        }
    """
    import os

    status: dict = {
        "ocr_engine": os.getenv("OCR_ENGINE", "auto"),
        "paddle_langs": os.getenv("OCR_PADDLE_LANG", "ch"),
        "paddle_dpi": os.getenv("OCR_PADDLE_DPI", "200"),
    }
    try:
        from extensions_mcp.image_to_markdown.paddle_client import is_available

        status["paddleocr_available"] = bool(is_available())
    except Exception as e:  # noqa: BLE001
        status["paddleocr_available"] = False
        status["paddle_error"] = str(e)
    try:
        from projectbrain.utils.pdf_render import is_available as render_ok

        status["pdf_renderer_available"] = bool(render_ok())
    except Exception as e:  # noqa: BLE001
        status["pdf_renderer_available"] = False
        status["render_error"] = str(e)
    try:
        from extensions_mcp.image_to_markdown.config import CONFIG

        status["cloud_vision_key_set"] = bool(getattr(CONFIG, "api_key", None))
    except Exception:  # noqa: BLE001
        status["cloud_vision_key_set"] = False
    return status


@mcp.tool(
    annotations=ToolAnnotations(
        title="OCR an image file to Markdown (local)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
async def ocr_image_to_markdown(
    image_path: Annotated[str, Field(description="Path to the image file")],
    output_path: Annotated[
        Optional[str], Field(description="If provided, writes the Markdown output to this file path")
    ] = None,
) -> dict:
    """OCR a single image file using local PaddleOCR (no API key required).

    Args:
        image_path: Path to the image (png/jpg/webp/bmp/tiff...).
        output_path: If provided, writes the recognized Markdown result to this path.

    Returns:
        dict: {"file": str, "markdown": str, "chars": int}

    Error Handling:
        Raises ValueError if the image is not found.
        Raises RuntimeError if PaddleOCR is not installed.
    """
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise ValueError(f"Image not found: {p}")
    from extensions_mcp.image_to_markdown.paddle_client import (
        extract_text_local,
        is_available,
    )

    if not is_available():
        raise RuntimeError(
            "PaddleOCR is not installed. Please run: pip install paddleocr paddlepaddle"
        )
    text = await extract_text_local(p.read_bytes(), "image/png")
    if output_path:
        op = Path(output_path).expanduser()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(text or "", encoding="utf-8")
    return {"file": p.name, "markdown": text or "", "chars": len(text or "")}


@mcp.tool(
    annotations=ToolAnnotations(
        title="OCR a PDF to Markdown (render pages, local)",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
async def ocr_pdf_to_markdown(
    pdf_path: Annotated[str, Field(description="Path to the PDF file")],
    dpi: Annotated[
        int, Field(ge=72, le=400, description="DPI resolution for rendering pages (default: 200)")
    ] = 200,
    force_ocr: Annotated[
        bool, Field(description="True: OCR all pages even if a text layer exists")
    ] = False,
    output_path: Annotated[
        Optional[str], Field(description="If provided, writes the Markdown output to this file path")
    ] = None,
) -> dict:
    """Convert PDF to Markdown by rendering each page and running local PaddleOCR on the images.

    This workflow is suitable for scanned PDFs or image-based PDFs (which don't have a digital text layer).
    For digital PDFs with a built-in text layer, set force_ocr=False to extract the digital text directly (much faster & more accurate), only running OCR on pages that lack digital text.

    Args:
        pdf_path: Path to the PDF file.
        dpi: Rendering resolution (72-400, default: 200). Higher values yield higher accuracy but run slower.
        force_ocr: True to force OCR on all pages, bypassing the digital text layer. Useful when the digital text layer is corrupted.
        output_path: Optional path to save the generated Markdown.

    Returns:
        dict: {
            "file": str, "pages": int, "ocr_pages": int,
            "markdown": str, "output": str | None
        }

    Error Handling:
        Raises ValueError if the PDF is not found.
        Raises RuntimeError if PyMuPDF (rendering) or PaddleOCR is missing.
    """
    p = Path(pdf_path).expanduser()
    if not p.is_file():
        raise ValueError(f"PDF not found: {p}")
    data = p.read_bytes()

    from projectbrain.utils.pdf_render import (
        page_count,
        render_page_png,
        is_available as render_ok,
    )

    if not render_ok():
        raise RuntimeError("PDF renderer missing. Please run: pip install pymupdf")
    from extensions_mcp.image_to_markdown.paddle_client import (
        extract_text_local,
        is_available as paddle_ok,
    )

    if not paddle_ok():
        raise RuntimeError(
            "PaddleOCR is not installed. Please run: pip install paddleocr paddlepaddle"
        )

    # Pre-extract the digital text layer (when not forcing OCR).
    layer: dict[int, str] = {}
    if not force_ocr:
        try:
            import io
            import pdfplumber

            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for i, page in enumerate(pdf.pages):
                    layer[i] = page.extract_text(x_tolerance=2) or ""
        except Exception:
            layer = {}

    n = page_count(data)
    parts: list[str] = []
    ocr_pages = 0
    for i in range(n):
        parts.append(f"## Page {i + 1}\n")
        txt = layer.get(i, "")
        if (not force_ocr) and txt and len(txt.strip()) >= 15:
            parts.append(txt.strip() + "\n")
        else:
            png = render_page_png(data, i, dpi)
            ocr_txt = await extract_text_local(png, "image/png") if png else ""
            parts.append((ocr_txt.strip() if ocr_txt else "_(no text detected)_") + "\n")
            ocr_pages += 1

    markdown = "\n".join(parts).strip()
    if output_path:
        op = Path(output_path).expanduser()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(markdown, encoding="utf-8")
    return {
        "file": p.name,
        "pages": n,
        "ocr_pages": ocr_pages,
        "markdown": markdown,
        "output": output_path,
    }


def main() -> None:
    mcp.run()  # stdio (phu hop Claude Desktop/local)


if __name__ == "__main__":
    main()
