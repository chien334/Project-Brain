"""Local OCR engine backed by PaddleOCR (GPU/CPU-aware, fully offline).

Drop-in alternative to the cloud ``vision_client``. The PaddleOCR engine is heavy
to construct, so it is lazy-loaded and cached per (lang, device, gpu_id, threads, mkldnn).
All blocking inference is dispatched to a worker thread so async callers never block
the event loop.

Designed to degrade gracefully:
- If ``paddleocr`` is not installed, ``is_available()`` returns False and
  ``extract_text_local()`` returns "" so the caller can fall back.
- Supports automatic GPU -> CPU fallback on OOM / CUDA / RuntimeError when
  ``PB_OCR_FALLBACK_TO_CPU`` is enabled (default: true).
- Smart MKLDNN selection: enabled on x86_64 Linux/Windows, disabled on macOS/ARM64.
"""

import asyncio
import io
import logging
import os
import platform
import threading

logger = logging.getLogger(__name__)

# (lang, resolved_device, gpu_id, cpu_threads, enable_mkldnn) -> PaddleOCR engine instance
_ENGINES: dict[tuple[str, str, int, int, bool], object] = {}
_ENGINE_LOCK = threading.Lock()
_AVAILABLE: bool | None = None


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(raw: str | None, default: int, minimum: int = 0) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        return default


def _should_enable_mkldnn(override: bool | None = None) -> bool:
    """Smart MKLDNN detection: enabled on x86_64 Linux/Windows, disabled on macOS/ARM64."""
    if override is not None:
        return bool(override)
    raw = os.getenv("PB_OCR_ENABLE_MKLDNN", os.getenv("OCR_ENABLE_MKLDNN", "")).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    sys_name = platform.system().lower()
    arch = platform.machine().lower()
    return arch in ("x86_64", "amd64") and sys_name in ("linux", "windows")


def _default_cpu_threads() -> int:
    return _parse_int(
        os.getenv("PB_OCR_CPU_THREADS") or os.getenv("OCR_CPU_THREADS"),
        max(1, min(os.cpu_count() or 4, 8)),
        minimum=1,
    )


def _default_gpu_id() -> int:
    return _parse_int(os.getenv("PB_OCR_GPU_ID") or os.getenv("OCR_GPU_ID"), 0, minimum=0)


def _default_gpu_mem() -> int:
    return _parse_int(os.getenv("PB_OCR_GPU_MEM_MB") or os.getenv("OCR_GPU_MEM_MB"), 500, minimum=64)


def _fallback_to_cpu_enabled() -> bool:
    return _parse_bool(os.getenv("PB_OCR_FALLBACK_TO_CPU") or os.getenv("OCR_FALLBACK_TO_CPU"), True)


def _detect_paddle_cuda_available() -> bool:
    """Best-effort check for Paddle CUDA GPU availability without failing on missing paddle."""
    try:
        from projectbrain.ocr_runtime import ComputeDeviceManager  # type: ignore

        if hasattr(ComputeDeviceManager, "detect_hardware"):
            hw = ComputeDeviceManager.detect_hardware()
            if isinstance(hw, dict):
                return bool(hw.get("paddle_cuda", False))
    except Exception:
        pass

    try:
        import paddle

        if paddle.device.is_compiled_with_cuda():
            try:
                return int(paddle.device.cuda.device_count()) > 0
            except Exception:
                return True
    except Exception:
        pass
    return False


def _resolve_device_and_gpu_id(
    device: str | None = None, gpu_id: int | None = None
) -> tuple[str, int]:
    """Normalize device ('auto'|'gpu'|'cuda:N'|'mps'|'cpu') into ('gpu'|'cpu', gpu_id)."""
    raw = (
        device
        or os.getenv("PB_OCR_DEVICE")
        or os.getenv("OCR_DEVICE")
        or ("gpu" if _parse_bool(os.getenv("OCR_PADDLE_USE_GPU"), False) else "auto")
    ).strip().lower()

    resolved_gpu_id = gpu_id if gpu_id is not None else _default_gpu_id()

    if raw.startswith("cuda:") or raw.startswith("gpu:"):
        _, idx_str = raw.split(":", 1)
        try:
            resolved_gpu_id = max(0, int(idx_str))
        except ValueError:
            pass
        return "gpu", resolved_gpu_id

    if raw in ("gpu", "cuda"):
        return "gpu", resolved_gpu_id

    # PaddleOCR does not run inference on Apple MPS; route MPS -> CPU cleanly.
    if raw in ("cpu", "mps"):
        return "cpu", resolved_gpu_id

    # "auto"
    if _detect_paddle_cuda_available():
        return "gpu", resolved_gpu_id
    return "cpu", resolved_gpu_id


def _langs(lang: str | None = None) -> list[str]:
    raw = (lang if lang is not None else os.getenv("OCR_PADDLE_LANG", "ch")).strip()
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


def _build_engine_single(
    lang: str,
    resolved_device: str,
    gpu_id: int,
    cpu_threads: int,
    enable_mkldnn: bool,
    gpu_mem: int,
):
    """Construct a PaddleOCR engine for a specific resolved device & tuning configuration."""
    from paddleocr import PaddleOCR

    use_gpu = resolved_device == "gpu"
    if not enable_mkldnn:
        # Helps avoid PIR NotImplementedError on older/macOS builds when MKLDNN is off
        os.environ.setdefault("FLAGS_use_mkldnn", "0")

    device_arg = f"gpu:{gpu_id}" if use_gpu else "cpu"
    light_3x = dict(
        lang=lang,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    attempts = [
        dict(**light_3x, enable_mkldnn=enable_mkldnn, cpu_threads=cpu_threads, device=device_arg),
        dict(**light_3x, enable_mkldnn=enable_mkldnn, device="gpu" if use_gpu else "cpu"),
        dict(**light_3x, enable_mkldnn=enable_mkldnn),
        dict(**light_3x),
        dict(
            lang=lang,
            use_gpu=use_gpu,
            gpu_id=gpu_id,
            gpu_mem=gpu_mem,
            cpu_threads=cpu_threads,
            enable_mkldnn=enable_mkldnn,
            use_angle_cls=False,
            show_log=False,
        ),
        dict(
            lang=lang,
            use_gpu=use_gpu,
            enable_mkldnn=enable_mkldnn,
            use_angle_cls=False,
            show_log=False,
        ),
        dict(lang=lang, enable_mkldnn=enable_mkldnn, use_angle_cls=False, show_log=False),
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
    raise RuntimeError(f"Cannot initialise PaddleOCR(lang={lang!r}, device={device_arg!r}): {last_err}")


def _build_engine(
    lang: str,
    device: str | None = None,
    gpu_id: int | None = None,
    cpu_threads: int | None = None,
    enable_mkldnn: bool | None = None,
    gpu_mem: int | None = None,
):
    """Construct a PaddleOCR engine, tolerating 2.x/3.x diffs and GPU->CPU fallback."""
    resolved_dev, eff_gpu_id = _resolve_device_and_gpu_id(device, gpu_id)
    eff_threads = max(1, cpu_threads) if cpu_threads is not None else _default_cpu_threads()
    eff_mkldnn = _should_enable_mkldnn(enable_mkldnn)
    eff_gpu_mem = max(64, gpu_mem) if gpu_mem is not None else _default_gpu_mem()

    try:
        return _build_engine_single(
            lang=lang,
            resolved_device=resolved_dev,
            gpu_id=eff_gpu_id,
            cpu_threads=eff_threads,
            enable_mkldnn=eff_mkldnn,
            gpu_mem=eff_gpu_mem,
        )
    except Exception as exc:
        if resolved_dev == "gpu" and _fallback_to_cpu_enabled():
            logger.warning(
                "PaddleOCR GPU initialization failed (%s); falling back to CPU.", exc
            )
            return _build_engine_single(
                lang=lang,
                resolved_device="cpu",
                gpu_id=eff_gpu_id,
                cpu_threads=eff_threads,
                enable_mkldnn=eff_mkldnn,
                gpu_mem=eff_gpu_mem,
            )
        if eff_mkldnn:
            logger.warning(
                "PaddleOCR initialization with MKLDNN failed (%s); retrying with MKLDNN disabled.",
                exc,
            )
            return _build_engine_single(
                lang=lang,
                resolved_device=resolved_dev,
                gpu_id=eff_gpu_id,
                cpu_threads=eff_threads,
                enable_mkldnn=False,
                gpu_mem=eff_gpu_mem,
            )
        raise


def _get_engine(
    lang: str,
    device: str | None = None,
    gpu_id: int | None = None,
    cpu_threads: int | None = None,
    enable_mkldnn: bool | None = None,
    gpu_mem: int | None = None,
):
    resolved_dev, eff_gpu_id = _resolve_device_and_gpu_id(device, gpu_id)
    eff_threads = max(1, cpu_threads) if cpu_threads is not None else _default_cpu_threads()
    eff_mkldnn = _should_enable_mkldnn(enable_mkldnn)
    key = (lang, resolved_dev, eff_gpu_id, eff_threads, eff_mkldnn)

    eng = _ENGINES.get(key)
    if eng is None:
        with _ENGINE_LOCK:  # avoid two threads building the same heavy engine
            eng = _ENGINES.get(key)
            if eng is None:
                eng = _build_engine(
                    lang=lang,
                    device=resolved_dev,
                    gpu_id=eff_gpu_id,
                    cpu_threads=eff_threads,
                    enable_mkldnn=eff_mkldnn,
                    gpu_mem=gpu_mem,
                )
                _ENGINES[key] = eng
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
        except (RuntimeError, MemoryError, NotImplementedError):
            raise
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


def _ocr_blocking(
    image_bytes: bytes,
    lang: str | None = None,
    device: str | None = None,
    gpu_id: int | None = None,
    cpu_threads: int | None = None,
) -> str:
    """Blocking OCR over configured languages with GPU->CPU OOM/RuntimeError fallback."""
    img = _to_ndarray(image_bytes)
    resolved_dev, eff_gpu_id = _resolve_device_and_gpu_id(device, gpu_id)
    eff_mkldnn = _should_enable_mkldnn()
    best_text = ""
    best_score = -1.0

    for single_lang in _langs(lang):
        items: list[tuple[str, float, tuple]] = []
        try:
            eng = _get_engine(
                single_lang,
                device=resolved_dev,
                gpu_id=eff_gpu_id,
                cpu_threads=cpu_threads,
                enable_mkldnn=eff_mkldnn,
            )
            items = _infer(eng, img)
        except Exception as exc:
            # Automatic GPU -> CPU OOM / RuntimeError fallback
            if resolved_dev == "gpu" and _fallback_to_cpu_enabled():
                logger.warning(
                    "PaddleOCR GPU inference failed for lang=%s (%s); falling back to CPU.",
                    single_lang,
                    exc,
                )
                try:
                    cpu_eng = _get_engine(
                        single_lang,
                        device="cpu",
                        gpu_id=eff_gpu_id,
                        cpu_threads=cpu_threads,
                        enable_mkldnn=eff_mkldnn,
                    )
                    items = _infer(cpu_eng, img)
                except Exception as cpu_exc:
                    if eff_mkldnn:
                        try:
                            safe_eng = _get_engine(
                                single_lang,
                                device="cpu",
                                gpu_id=eff_gpu_id,
                                cpu_threads=cpu_threads,
                                enable_mkldnn=False,
                            )
                            items = _infer(safe_eng, img)
                        except Exception:
                            logger.debug("PaddleOCR CPU non-MKLDNN fallback failed: %s", cpu_exc)
                            continue
                    else:
                        continue
            elif eff_mkldnn:
                try:
                    safe_eng = _get_engine(
                        single_lang,
                        device=resolved_dev,
                        gpu_id=eff_gpu_id,
                        cpu_threads=cpu_threads,
                        enable_mkldnn=False,
                    )
                    items = _infer(safe_eng, img)
                except Exception:
                    continue
            else:
                continue

        if not items:
            continue
        score = sum(c for _, c, _ in items) / len(items)
        if score > best_score:
            best_score = score
            best_text = _to_text(items)
    return best_text


async def _try_unified_runtime(
    image_bytes: bytes,
    lang: str | None,
    device: str | None,
    gpu_id: int | None,
    cpu_threads: int | None,
) -> str | None:
    """Delegate to UnifiedOCREngine when projectbrain.ocr_runtime is available."""
    if gpu_id is not None or cpu_threads is not None:
        return None
    try:
        from projectbrain.ocr_runtime import UnifiedOCREngine  # type: ignore
    except Exception:
        return None

    try:
        engine_obj = UnifiedOCREngine()
        fn = getattr(engine_obj, "extract_image", None)
        if not callable(fn):
            return None
        res = await fn(
            image_bytes=image_bytes,
            engine="paddle",
            device=device,
            lang=lang,
        )
        if isinstance(res, dict):
            txt = res.get("text") or res.get("markdown") or ""
            if txt:
                return str(txt)
        elif isinstance(res, str) and res:
            return res
    except Exception:
        return None
    return None


async def extract_text_local(
    image_bytes: bytes,
    mime_type: str = "image/png",
    lang: str | None = None,
    device: str | None = None,
    gpu_id: int | None = None,
    cpu_threads: int | None = None,
) -> str:
    """Async wrapper: OCR an image with PaddleOCR. Returns "" if unavailable/failed.

    Supports both ``extract_text_local(image_bytes, "image/png")`` and
    ``extract_text_local(image_bytes, lang="vi", device="gpu", gpu_id=0, cpu_threads=4)``.
    If ``mime_type`` does not contain ``"/"`` and ``lang`` is None, it is treated as ``lang``.
    """
    effective_lang = lang
    if effective_lang is None and mime_type and "/" not in mime_type:
        effective_lang = mime_type

    # Delegate to UnifiedOCREngine when available (TASK-23A), with inline fallback
    unified_text = await _try_unified_runtime(
        image_bytes, effective_lang, device, gpu_id, cpu_threads
    )
    if unified_text:
        return unified_text

    if not is_available():
        return ""
    try:
        return await asyncio.to_thread(
            _ocr_blocking,
            image_bytes,
            effective_lang,
            device,
            gpu_id,
            cpu_threads,
        )
    except Exception:
        return ""
