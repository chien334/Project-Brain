"""Smoke test for the local PaddleOCR integration.

Usage:
    python scripts/check_ocr.py                 # engine status only
    python scripts/check_ocr.py path/to/img.png # OCR a single image
    python scripts/check_ocr.py path/to/doc.pdf # OCR / parse a PDF
"""
import asyncio
import sys
from pathlib import Path


async def main() -> None:
    from extensions_mcp.ocr_local.server import (
        ocr_engine_status,
        ocr_image_to_markdown,
        ocr_pdf_to_markdown,
    )

    status = await ocr_engine_status()
    print("== OCR engine status ==")
    for k, v in status.items():
        print(f"  {k}: {v}")

    if len(sys.argv) < 2:
        print("\n(no file given — status only)")
        return

    target = Path(sys.argv[1]).expanduser()
    if not target.is_file():
        print(f"\nFile not found: {target}")
        return

    print(f"\n== OCR: {target.name} ==")
    if target.suffix.lower() == ".pdf":
        res = await ocr_pdf_to_markdown(str(target), dpi=200, force_ocr=False)
        print(f"pages={res['pages']} ocr_pages={res['ocr_pages']}")
        print(res["markdown"][:1500])
    else:
        res = await ocr_image_to_markdown(str(target))
        print(f"chars={res['chars']}")
        print(res["markdown"][:1500])


if __name__ == "__main__":
    asyncio.run(main())
