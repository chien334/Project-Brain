"""Local OCR engine backed by PaddleOCR (CPU-friendly, fully offline).

Drop-in alternative to the cloud ``vision_client``. The PaddleOCR engine is heavy
to construct, so it is lazy-loaded and cached one instance per language. All
blocking inference is dispatched to a worker thread so async callers never block
the event loop.

Designed to degrade gracefully: if ``paddleocr`` is not installed, ``is_available()``
returns False and ``extract_text_local()`` returns "" so the caller can fall back
to cloud vision (or a placeholder).

Configuration (env vars):
    OCR_PADDLE_LANG    Comma-separated language codes. Multiple languages run as
                       separate passes and the highest-confidence result wins.
                       Common codes: ch (Chinese+English), en, vi, japan, korean,
                       latin. Default: "ch".
    OCR_PADDLE_USE_GPU "true" to use a GPU build of paddlepaddle. Default: false.
"""

import asyncio
import io
import os
import threading

# paddlepaddle 3.x (PIR executor) raises NotImplementedError on some ops via the
# oneDNN/MKLDNN backend (ConvertPirAttribute2RuntimeAttribute). Disable MKLDNN
# globally before paddle is imported; plain CPU kernels are correct and lighter.
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# lang -> PaddleOCR engine instance
_ENGINES: dict[str, object] = {}
_ENGINE_LOCK = threading.Lock()
_AVAILABLE: bool | None = None


def _langs() -> list[str]:
    raw = os.getenv("OCR_PADDLE_LANG", "ch").strip()
    langs = [x.strip() for x in raw.split(",") if x.strip()]
    return langs or ["ch"]


def is_available() -> bool:
    """True if the ``paddleocr`` package can be imported (checked once, cached)."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            import paddleocr  # noqa: F401

            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def _build_engine(lang: str):
    """Construct a PaddleOCR engine, tolerating 2.x/3.x constructor differences."""
    from paddleocr import PaddleOCR

    use_gpu = os.getenv("OCR_PADDLE_USE_GPU", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Lightweight CPU setup: disable MKLDNN (see module top) and the heavy doc
    # preprocessing models (orientation classify + unwarping + textline orientation)
    # that PaddleOCR 3.x loads by default — we only need detection + recognition.
    # The constructor signature changed across releases (use_angle_cls/show_log were
    # removed in 3.x for use_textline_orientation/device), so fall back on TypeError.
    light_3x = dict(
        lang=lang,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    attempts = [
        dict(**light_3x, enable_mkldnn=False, device="gpu" if use_gpu else "cpu"),
        dict(**light_3x, enable_mkldnn=False),
        dict(**light_3x),
        dict(lang=lang, enable_mkldnn=False, use_angle_cls=False, show_log=False),
        dict(lang=lang, use_angle_cls=True),
        dict(lang=lang),
    ]
    last_err: Exception | None = None
    for kw in attempts:
        try:
            return PaddleOCR(**kw)
        except TypeError as e:
            last_err = e
        except Exception as e:
            last_err = e
            break
    raise RuntimeError(f"Cannot initialise PaddleOCR(lang={lang!r}): {last_err}")


def _get_engine(lang: str):
    eng = _ENGINES.get(lang)
    if eng is None:
        with _ENGINE_LOCK:  # avoid two threads building the same heavy engine
            eng = _ENGINES.get(lang)
            if eng is None:
                eng = _build_engine(lang)
                _ENGINES[lang] = eng
    return eng


def _to_ndarray(image_bytes: bytes):
    """Decode image bytes to a BGR numpy array (what PaddleOCR/OpenCV expect)."""
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as im:
        arr = np.asarray(im.convert("RGB"))  # H x W x 3, RGB
    return arr[:, :, ::-1].copy()  # -> BGR


def _poly_to_box(poly) -> tuple[float, float, float, float]:
    """Reduce a detection polygon (4 points) or flat box to (x0, y0, x1, y1)."""
    if poly is None:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        try:  # already a flat [x0, y0, x1, y1]
            v = [float(x) for x in list(poly)[:4]]
            return (v[0], v[1], v[2], v[3])
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)


def _parse_v3(res):
    """Parse PaddleOCR 3.x ``predict()`` output. Returns None if shape is unexpected."""
    if not res:
        return []
    items: list[tuple[str, float, tuple]] = []
    for page in res:
        d = page
        # OCRResult subclasses dict in 3.x; some builds nest data under "res".
        get = d.get if hasattr(d, "get") else None
        if get is None:
            inner = getattr(d, "res", None)
            if inner is None:
                return None
            d = inner
            get = d.get if hasattr(d, "get") else None
            if get is None:
                return None
        texts = get("rec_texts")
        if texts is None:
            return None
        scores = get("rec_scores") or [1.0] * len(texts)
        polys = get("rec_polys") or get("dt_polys") or get("rec_boxes") or [None] * len(texts)
        for t, s, p in zip(texts, scores, polys):
            if t is None:
                continue
            items.append((str(t), float(s), _poly_to_box(p)))
    return items


def _parse_v2(res):
    """Parse PaddleOCR 2.x ``ocr()`` output: [[ [box, (text, conf)], ... ]]."""
    def parse_page(page):
        out = []
        for line in (page or []):
            try:
                box = line[0]
                text = line[1][0]
                conf = float(line[1][1])
                if text:
                    out.append((str(text), conf, _poly_to_box(box)))
            except Exception:
                continue
        return out

    items: list[tuple[str, float, tuple]] = []
    if not res:
        return items
    first = res[0]
    # Detect whether `res` is a single page or a list of pages.
    single_page = False
    try:
        if first and isinstance(first[1], (list, tuple)) and isinstance(first[1][0], str):
            single_page = True
    except Exception:
        pass
    pages = [res] if single_page else res
    for page in pages:
        items.extend(parse_page(page))
    return items


def _infer(engine, img) -> list[tuple[str, float, tuple]]:
    """Run inference, supporting both the 3.x predict() and 2.x ocr() APIs."""
    if hasattr(engine, "predict"):
        try:
            parsed = _parse_v3(engine.predict(img))
            if parsed is not None:
                return parsed
        except Exception:
            pass
    try:
        res = engine.ocr(img)
    except TypeError:
        res = engine.ocr(img, cls=True)  # older 2.x signature
    return _parse_v2(res)


def _to_text(items: list[tuple[str, float, tuple]]) -> str:
    """Reconstruct reading order: group detections into lines top->bottom, left->right."""
    if not items:
        return ""
    items = sorted(items, key=lambda it: (it[2][1], it[2][0]))
    heights = sorted(max(1.0, it[2][3] - it[2][1]) for it in items)
    line_h = heights[len(heights) // 2] or 1.0
    lines: list[list[tuple[float, str]]] = []
    cur: list[tuple[float, str]] = []
    cur_y: float | None = None
    for text, _conf, (x0, y0, _x1, _y1) in items:
        if cur_y is None or abs(y0 - cur_y) <= line_h * 0.6:
            cur.append((x0, text))
            if cur_y is None:
                cur_y = y0
        else:
            lines.append(cur)
            cur = [(x0, text)]
            cur_y = y0
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda t: t[0])
        joined = " ".join(t for _, t in ln if t).strip()
        if joined:
            out.append(joined)
    return "\n".join(out).strip()


def _ocr_blocking(image_bytes: bytes) -> str:
    """Blocking OCR over all configured languages; best average-confidence wins."""
    img = _to_ndarray(image_bytes)
    best_text = ""
    best_score = -1.0
    for lang in _langs():
        try:
            items = _infer(_get_engine(lang), img)
        except Exception:
            continue
        if not items:
            continue
        score = sum(c for _, c, _ in items) / len(items)
        if score > best_score:
            best_score = score
            best_text = _to_text(items)
    return best_text


async def extract_text_local(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """Async wrapper: OCR an image with PaddleOCR. Returns "" if unavailable/failed.

    ``mime_type`` is accepted for signature parity with the cloud client; the image
    is decoded from bytes regardless of format.
    """
    if not is_available():
        return ""
    try:
        return await asyncio.to_thread(_ocr_blocking, image_bytes)
    except Exception:
        return ""
