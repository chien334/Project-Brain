import io
import re
from pathlib import Path

import PIL.Image

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
# Gemini ho tro truc tiep: png, jpeg, webp. Cac dinh dang khac -> convert sang PNG.
_NATIVE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def natural_key(name: str):
    """Khoa sort tu nhien: '2' < '10' (khong phai '10' < '2')."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def list_images(
    folder: Path, recursive: bool = False, exts: set[str] | None = None
) -> list[Path]:
    exts = exts or IMAGE_EXTS
    it = folder.rglob("*") if recursive else folder.glob("*")
    files = [p for p in it if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: natural_key(p.name))  # SORT THEO TEN ANH


def read_image_as_supported(path: Path) -> tuple[bytes, str]:
    """Tra ve (bytes, mime). Dinh dang la -> convert PNG."""
    suffix = path.suffix.lower()
    if suffix in _NATIVE_MIME:
        return path.read_bytes(), _NATIVE_MIME[suffix]
    with PIL.Image.open(path) as im:
        im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), "image/png"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
