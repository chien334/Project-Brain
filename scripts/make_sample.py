"""Generate OCR test fixtures: a text PNG and an image-only ('scanned') PDF.

Outputs into scripts/sample/:
    sample_text.png   - a white image with several lines of text
    sample_scan.pdf   - a PDF whose only content is that image (no text layer)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "sample"
OUT.mkdir(parents=True, exist_ok=True)

LINES = [
    "ProjectBrain OCR Test",
    "Hello world - line one",
    "Invoice No: 12345   Total: $678.90",
    "The quick brown fox jumps over the lazy dog",
]

# --- Render a PNG with text ---
W, H = 900, 320
img = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("arial.ttf", 36)
except Exception:
    font = ImageFont.load_default()

y = 30
for line in LINES:
    draw.text((40, y), line, fill="black", font=font)
    y += 64

png_path = OUT / "sample_text.png"
img.save(png_path, "PNG")
print("wrote", png_path)

# --- Build an image-only PDF (no text layer) to exercise the scanned-PDF path ---
try:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(fitz.Rect(0, 0, W, H), filename=str(png_path))
    pdf_path = OUT / "sample_scan.pdf"
    doc.save(str(pdf_path))
    doc.close()
    print("wrote", pdf_path)
except Exception as e:
    print("skip PDF (PyMuPDF not available):", e)
